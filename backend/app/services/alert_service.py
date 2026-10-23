"""Alert engine: threshold rules (for-duration, hysteresis), event alerts, dedup, flap
suppression, maintenance windows, ack/resolve lifecycle, recorded deliveries with retries."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select

from app.core.database import Database
from app.core.events import ALERTS_CHANNEL, EventBus
from app.core.exceptions import Conflict, NotFound
from app.core.logging import get_logger
from app.domain import alerting
from app.domain.identity import Principal
from app.models.device import Device
from app.models.monitoring import Alert, AlertDelivery, AlertRule, MaintenanceWindow
from app.repositories.base import ListQuery, Page, Repository
from app.repositories.uow import UnitOfWork
from app.services.alert_delivery import AlertDeliverer, alert_message
from app.services.notifications import Channel

log = get_logger(__name__)
_STATE = "alert:state:{}"
_FLAP = "alert:flap:{}"


class AlertRepository(Repository[Alert]):
    model = Alert
    sortable = {
        "last_seen": Alert.last_seen,
        "severity": Alert.severity,
        "first_seen": Alert.first_seen,
    }
    filterable = {
        "state": Alert.state,
        "severity": Alert.severity,
        "device_id": Alert.device_id,
        "alert_type": Alert.alert_type,
    }


def _device_view(device: Device) -> dict[str, Any]:
    return {
        "device_id": str(device.id),
        "group_id": str(device.group_id) if device.group_id else None,
        "site": device.site,
    }


class AlertService:
    def __init__(
        self, db: Database, redis: Redis, events: EventBus, channels: dict[str, Channel]
    ) -> None:
        self._db = db
        self._redis = redis
        self._events = events
        self.deliverer = AlertDeliverer(db, channels)

    # --- rules -----------------------------------------------------------------------------
    async def rules(self) -> list[AlertRule]:
        async with UnitOfWork(self._db) as uow:
            count = await uow.session.scalar(select(func.count()).select_from(AlertRule))
            if not count:
                uow.session.add_all(
                    AlertRule(**r, channels=["webhook", "telegram", "email"])
                    for r in alerting.DEFAULT_RULES
                )
                await uow.commit()
            return list(
                (
                    await uow.session.scalars(select(AlertRule).where(AlertRule.enabled.is_(True)))
                ).all()
            )

    async def upsert_rule(self, values: dict[str, Any]) -> AlertRule:
        await self.rules()  # seed defaults first
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.scalar(
                select(AlertRule).where(AlertRule.name == values["name"])
            )
            if row is None:
                row = AlertRule(**values)
                uow.session.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            await uow.commit()
            return row

    async def add_maintenance(
        self, values: dict[str, Any], created_by: uuid.UUID
    ) -> MaintenanceWindow:
        async with UnitOfWork(self._db) as uow:
            row = MaintenanceWindow(**values, created_by=created_by)
            uow.session.add(row)
            await uow.commit()
            return row

    async def process_sample(
        self, device: Device, metric: str, value: float | None, interface: str | None = None
    ) -> None:
        now = datetime.now(UTC)
        for rule in await self.rules():
            if (
                rule.metric != metric
                or rule.threshold is None
                or not alerting.in_scope(rule.scope, _device_view(device))
            ):
                continue
            spec = alerting.ThresholdRule(
                rule.operator, rule.threshold, rule.clear_threshold, rule.for_seconds
            )
            key = f"{rule.id}:{device.id}:{interface or '-'}"
            raw = await self._redis.get(_STATE.format(key))
            stored: dict[str, Any] = (
                json.loads(raw) if raw else {"firing": False, "pending_since": None}
            )
            state = alerting.EvalState(
                stored["firing"],
                (
                    datetime.fromisoformat(stored["pending_since"])
                    if stored["pending_since"]
                    else None
                ),
            )
            transition, new_state = alerting.evaluate(spec, value, state, now)
            await self._redis.set(
                _STATE.format(key),
                json.dumps(
                    {
                        "firing": new_state.firing,
                        "pending_since": (
                            new_state.pending_since.isoformat() if new_state.pending_since else None
                        ),
                    }
                ),
            )
            if transition is alerting.Transition.FIRE:
                await self.open(
                    rule.alert_type,
                    device,
                    self._message(rule.alert_type, device, interface, value),
                    severity=rule.severity,
                    interface=interface,
                    value=value,
                    rule=rule,
                )
            elif transition is alerting.Transition.RESOLVE:
                await self.resolve_key(f"{rule.alert_type}:{device.id}:{interface or '-'}")

    @staticmethod
    def _message(
        alert_type: str, device: Device, interface: str | None, value: float | None
    ) -> str:
        if alert_type == "interface_down":
            return f"Huawei {device.hostname} {interface} is DOWN"
        what = alert_type.replace("_", " ")
        where = f" {interface}" if interface else ""
        return (
            f"Huawei {device.hostname}{where}: {what} ({value:.1f})"
            if value is not None
            else f"Huawei {device.hostname}{where}: {what}"
        )

    # --- lifecycle ---------------------------------------------------------------------------
    async def _maintenance(self, device: Device | None, now: datetime) -> bool:
        if device is None:
            return False
        async with UnitOfWork(self._db) as uow:
            rows = await uow.session.scalars(
                select(MaintenanceWindow).where(
                    MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at >= now
                )
            )
            windows = [(w.scope, w.starts_at, w.ends_at) for w in rows.all()]
        return alerting.in_maintenance(windows, _device_view(device), now)

    async def _flapping(self, key: str, now: datetime, rule: AlertRule | None) -> bool:
        window = rule.flap_window_seconds if rule else 600
        limit = rule.flap_max_transitions if rule else 4
        await self._redis.rpush(_FLAP.format(key), now.isoformat())  # type: ignore[misc]
        await self._redis.expire(_FLAP.format(key), window)
        raw: list[str] = await self._redis.lrange(_FLAP.format(key), 0, -1)  # type: ignore[misc]
        stamps = [datetime.fromisoformat(t) for t in raw]
        return alerting.is_flapping(stamps, now, window, limit)

    async def open(
        self,
        alert_type: str,
        device: Device | None,
        message: str,
        *,
        severity: str = "warning",
        interface: str | None = None,
        value: float | None = None,
        rule: AlertRule | None = None,
        key_suffix: str | None = None,
    ) -> Alert:
        now = datetime.now(UTC)
        dedupe = f"{alert_type}:{device.id if device else '-'}:{key_suffix or interface or '-'}"
        async with UnitOfWork(self._db) as uow:
            existing = await uow.session.scalar(
                select(Alert).where(
                    Alert.dedupe_key == dedupe, Alert.state.in_(["FIRING", "ACKNOWLEDGED"])
                )
            )
            if existing is not None:
                existing.last_seen, existing.occurrences, existing.value = (
                    now,
                    existing.occurrences + 1,
                    value,
                )
                await uow.commit()
                return existing
            suppressed = await self._maintenance(device, now) or await self._flapping(
                dedupe, now, rule
            )
            alert = Alert(
                rule_id=rule.id if rule else None,
                alert_type=alert_type,
                dedupe_key=dedupe,
                device_id=device.id if device else None,
                interface=interface,
                severity=severity,
                message=message,
                value=value,
                suppressed=suppressed,
                first_seen=now,
                last_seen=now,
            )
            uow.session.add(alert)
            await uow.session.flush()
            deliveries = (
                []
                if suppressed
                else [
                    AlertDelivery(alert_id=alert.id, channel=c)
                    for c in (rule.channels if rule else list(self.channels))
                    if c in self.channels
                ]
            )
            uow.session.add_all(deliveries)
            await uow.commit()
        await self._events.publish(ALERTS_CHANNEL, alert_message(alert))
        for delivery in deliveries:
            await self.deliverer.dispatch(delivery.id)
        return alert

    async def resolve_key(self, dedupe: str) -> None:
        now = datetime.now(UTC)
        async with UnitOfWork(self._db) as uow:
            alert = await uow.session.scalar(
                select(Alert).where(
                    Alert.dedupe_key == dedupe, Alert.state.in_(["FIRING", "ACKNOWLEDGED"])
                )
            )
            if alert is None:
                return
            alert.state, alert.resolved_at = "RESOLVED", now
            await uow.commit()
        await self._flapping(dedupe, now, None)
        await self._events.publish(ALERTS_CHANNEL, alert_message(alert))

    async def acknowledge(self, principal: Principal, alert_id: uuid.UUID) -> Alert:
        async with UnitOfWork(self._db) as uow:
            alert = await uow.session.get(Alert, alert_id)
            if alert is None:
                raise NotFound("Alert not found")
            if alert.state != "FIRING":
                raise Conflict(f"alert is {alert.state}")
            alert.state, alert.acknowledged_by, alert.acknowledged_at = (
                "ACKNOWLEDGED",
                principal.user_id,
                datetime.now(UTC),
            )
            await uow.commit()
            return alert

    async def resolve(self, principal: Principal, alert_id: uuid.UUID) -> Alert:
        async with UnitOfWork(self._db) as uow:
            alert = await uow.session.get(Alert, alert_id)
            if alert is None:
                raise NotFound("Alert not found")
        await self.resolve_key(alert.dedupe_key)
        async with UnitOfWork(self._db) as uow:
            refreshed = await uow.session.get(Alert, alert_id)
            assert refreshed is not None  # noqa: S101
            return refreshed

    async def search(self, query: ListQuery) -> Page[Alert]:
        async with UnitOfWork(self._db) as uow:
            return await uow.repo(AlertRepository).list(query)

    async def deliveries(self, alert_id: uuid.UUID) -> list[AlertDelivery]:
        async with UnitOfWork(self._db) as uow:
            return list(
                (
                    await uow.session.scalars(
                        select(AlertDelivery).where(AlertDelivery.alert_id == alert_id)
                    )
                ).all()
            )

    # --- delivery (delegated) ----------------------------------------------------------------
    @property
    def channels(self) -> dict[str, Channel]:
        return self.deliverer.channels

    @channels.setter
    def channels(self, value: dict[str, Channel]) -> None:
        self.deliverer.channels = value

    async def deliver(self, delivery_id: uuid.UUID) -> str:
        return await self.deliverer.deliver(delivery_id)

    async def drain(self) -> None:
        await self.deliverer.drain()
