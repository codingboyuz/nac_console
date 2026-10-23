"""Metric polling (SNMP preferred, CLI fallback), rate computation, storage, live fan-out,
alert evaluation, history queries and partition maintenance/retention."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, func, select, text

from app.core.config import Settings
from app.core.database import Database
from app.core.events import MONITORING_CHANNEL, EventBus
from app.domain.identity import Principal
from app.domain.metrics import InterfaceCounters, interface_rates
from app.models.device import Device
from app.models.monitoring import MetricSample
from app.network.parsers import parse_cpu_usage, parse_interface_brief, parse_memory_usage
from app.network.ports import DeviceDriver
from app.repositories.uow import UnitOfWork
from app.services.alert_service import AlertService
from app.services.collection_service import CollectionService
from app.services.connection_service import DeviceConnector
from app.services.device_service import DeviceService

_LAST = "metrics:if:{}:{}"


@dataclass(frozen=True, slots=True)
class Sample:
    metric: str
    value: float | None
    interface: str | None = None


def _snmp_poll(scope: str) -> Any:
    def poll(driver: DeviceDriver) -> dict[str, Any]:
        out: dict[str, Any] = {"system": driver.system()}  # type: ignore[attr-defined]
        if scope in {"cpu", "all"}:
            out["entity"] = driver.entity()  # type: ignore[attr-defined]
        if scope in {"interfaces", "all"}:
            out["interfaces"] = driver.interfaces()  # type: ignore[attr-defined]
        return out

    return poll


class MonitoringService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        redis: Redis,
        devices: DeviceService,
        connector: DeviceConnector,
        collection: CollectionService,
        alerts: AlertService,
        events: EventBus,
    ) -> None:
        self._settings = settings
        self._db = db
        self._redis = redis
        self._devices = devices
        self._connector = connector
        self._collection = collection
        self._alerts = alerts
        self._events = events

    async def poll(
        self, principal: Principal | None, device: Device, scope: str = "all"
    ) -> list[Sample]:
        if device.snmp_credential_id is not None:
            samples = await self._poll_snmp(principal, device, scope)
        else:
            samples = await self._poll_cli(principal, device, scope)
        await self._store(device, samples)
        return samples

    async def _poll_snmp(
        self, principal: Principal | None, device: Device, scope: str
    ) -> list[Sample]:
        data = await self._connector.run_snmp(device, _snmp_poll(scope), principal)
        samples: list[Sample] = [Sample("uptime", data["system"].uptime_seconds)]
        entity = data.get("entity")
        if entity is not None:
            samples += [Sample("cpu", entity.cpu_percent), Sample("memory", entity.memory_percent)]
            samples += [Sample("temperature", t) for t in entity.temperatures_c[:1]]
            samples += [Sample("fan_ok", float(ok), idx) for idx, ok in entity.fans_ok.items()]
            samples += [Sample("power_ok", float(ok), idx) for idx, ok in entity.power_ok.items()]
        now = time.time()
        for iface in data.get("interfaces", []):
            if iface.admin_up:
                samples.append(Sample("if_oper", 1.0 if iface.oper_up else 0.0, iface.name))
            key = _LAST.format(device.id, iface.if_index)
            previous = await self._redis.get(key)
            c = iface.counters
            counters = [
                c.in_octets,
                c.out_octets,
                c.in_errors,
                c.out_errors,
                c.in_discards,
                c.out_discards,
                c.counter_bits,
            ]
            await self._redis.set(key, json.dumps({"ts": now, "c": counters}), ex=3600)
            if not previous:
                continue
            prev = json.loads(previous)
            rates = interface_rates(InterfaceCounters(*prev["c"]), iface.counters, now - prev["ts"])
            samples += [
                Sample("if_in_bps", rates.in_bps, iface.name),
                Sample("if_out_bps", rates.out_bps, iface.name),
                Sample("if_in_errors_ps", rates.in_errors_ps, iface.name),
                Sample("if_discards_ps", rates.in_discards_ps, iface.name),
            ]
            if iface.speed_mbps and rates.in_bps is not None and rates.out_bps is not None:
                util = max(rates.in_bps, rates.out_bps) / (iface.speed_mbps * 1_000_000) * 100
                samples.append(Sample("if_util_pct", round(util, 2), iface.name))
        return samples

    async def _poll_cli(
        self, principal: Principal | None, device: Device, scope: str
    ) -> list[Sample]:
        commands = []
        if scope in {"cpu", "all"}:
            commands += ["display cpu-usage", "display memory-usage"]
        if scope in {"interfaces", "all"}:
            commands.append("display interface brief")

        def run(driver: DeviceDriver) -> dict[str, str]:
            return {c: driver.run_show(c).output for c in commands}

        outputs = await self._collection.run(device, run, principal)
        samples: list[Sample] = []
        if "display cpu-usage" in outputs:
            samples.append(
                Sample("cpu", parse_cpu_usage(outputs["display cpu-usage"]).current_percent)
            )
            samples.append(
                Sample("memory", parse_memory_usage(outputs["display memory-usage"]).used_percent)
            )
        for row in parse_interface_brief(outputs.get("display interface brief", "")):
            if not row.admin_down:
                samples.append(Sample("if_oper", 1.0 if row.phy == "up" else 0.0, row.name))
            if row.in_util is not None and row.out_util is not None:
                samples.append(Sample("if_util_pct", max(row.in_util, row.out_util), row.name))
            if row.in_errors is not None:
                samples.append(Sample("if_in_errors_total", float(row.in_errors), row.name))
        return samples

    async def _store(self, device: Device, samples: list[Sample]) -> None:
        now = datetime.now(UTC)
        rows = [
            MetricSample(
                ts=now, device_id=device.id, metric=s.metric, interface=s.interface, value=s.value
            )
            for s in samples
            if s.value is not None
        ]
        async with UnitOfWork(self._db) as uow:
            uow.session.add_all(rows)
            await uow.commit()
        for s in samples:
            await self._events.publish(
                MONITORING_CHANNEL,
                {
                    "type": "metric",
                    "device_id": str(device.id),
                    "metric": s.metric,
                    "value": s.value,
                    "labels": {"interface": s.interface} if s.interface else {},
                    "timestamp": now.isoformat(),
                },
            )
            await self._alerts.process_sample(device, s.metric, s.value, s.interface)

    async def history(
        self,
        principal: Principal,
        device_id: uuid.UUID,
        metric: str,
        *,
        since: datetime,
        until: datetime | None = None,
        interface: str | None = None,
        limit: int = 5000,
    ) -> list[MetricSample]:
        await self._devices.get(principal, device_id)
        stmt = select(MetricSample).where(
            MetricSample.device_id == device_id,
            MetricSample.metric == metric,
            MetricSample.ts >= since,
            MetricSample.ts <= (until or datetime.now(UTC)),
        )
        if interface:
            stmt = stmt.where(MetricSample.interface == interface)
        async with UnitOfWork(self._db) as uow:
            return list(
                (await uow.session.scalars(stmt.order_by(MetricSample.ts).limit(limit))).all()
            )

    async def latest(self, principal: Principal, device_id: uuid.UUID) -> dict[str, float | None]:
        await self._devices.get(principal, device_id)
        async with UnitOfWork(self._db) as uow:
            newest = await uow.session.scalar(
                select(func.max(MetricSample.ts)).where(MetricSample.device_id == device_id)
            )
            if newest is None:
                return {}
            rows = await uow.session.scalars(
                select(MetricSample).where(
                    MetricSample.device_id == device_id, MetricSample.ts == newest
                )
            )
            return {
                f"{r.metric}{':' + r.interface if r.interface else ''}": r.value for r in rows.all()
            }

    # --- partitions / retention --------------------------------------------------------------
    @staticmethod
    async def _partition_exists(conn: Any, name: str) -> bool:
        found = await conn.execute(text("SELECT 1 FROM pg_class WHERE relname = :n"), {"n": name})
        return found.first() is not None

    @staticmethod
    async def _create_partition(conn: Any, name: str, start: datetime, end: datetime) -> None:
        """Create a month partition; rows that landed in DEFAULT for that month are moved first,
        because PostgreSQL refuses to attach a range the default partition already holds."""
        bounds = {"a": start, "b": end}
        await conn.execute(
            text(
                f"CREATE TABLE {name} "
                "(LIKE metric_samples INCLUDING DEFAULTS INCLUDING CONSTRAINTS)"
            )
        )
        await conn.execute(
            text(
                f"WITH moved AS (DELETE FROM metric_samples_default WHERE ts >= :a AND ts < :b "  # noqa: S608
                f"RETURNING *) INSERT INTO {name} SELECT * FROM moved"
            ),
            bounds,
        )
        await conn.execute(
            text(
                f"ALTER TABLE metric_samples ATTACH PARTITION {name} "
                f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
            )
        )

    async def maintain(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(days=self._settings.metric_retention_days)
        created: list[str] = []
        dropped: list[str] = []
        async with self._db.engine.begin() as conn:
            if conn.dialect.name != "postgresql":
                await conn.execute(delete(MetricSample).where(MetricSample.ts < cutoff))
                return {"created": created, "dropped": dropped}
            for offset in range(3):
                year, month = divmod(now.month - 1 + offset, 12)
                start = datetime(now.year + year, month + 1, 1, tzinfo=UTC)
                ny, nm = divmod(start.month, 12)
                end = datetime(start.year + ny, nm + 1, 1, tzinfo=UTC)
                name = f"metric_samples_y{start.year}m{start.month:02d}"
                if await self._partition_exists(conn, name):
                    continue
                await self._create_partition(conn, name, start, end)
                created.append(name)
            parts = await conn.execute(
                text(
                    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                    "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'metric_samples'"
                )
            )
            for (name,) in parts.all():
                if not name.startswith("metric_samples_y"):
                    continue
                year, month = int(name[16:20]), int(name[21:23])
                ny, nm = divmod(month, 12)
                if datetime(year + ny, nm + 1, 1, tzinfo=UTC) <= cutoff:
                    await conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
                    dropped.append(name)
            await conn.execute(delete(MetricSample).where(MetricSample.ts < cutoff))
        return {"created": created, "dropped": dropped}
