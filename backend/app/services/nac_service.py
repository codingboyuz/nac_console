"""NAC runtime: RADIUS authorize/accounting callbacks, endpoint inventory and profiling.

Every decision is audited with the matched policy. Engine failures use the configured
fail mode (closed -> quarantine VLAN by default, open per site when configured).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import Database
from app.core.events import EventBus
from app.core.exceptions import NotFound, ValidationFailed
from app.core.logging import get_logger
from app.domain.identity import Principal, RequestContext
from app.domain.mac import InvalidMac, normalize_mac
from app.domain.nac import policy as engine
from app.domain.nac.profiling import ProfileSignals, profile
from app.models.device import Device
from app.models.job import Setting
from app.models.nac import Endpoint, EndpointSource, EndpointStatus, NacSession
from app.repositories.base import ListQuery, Page, Repository
from app.repositories.uow import UnitOfWork
from app.services.audit_service import AuditService
from app.services.nac_policy_service import NacPolicyService

log = get_logger(__name__)
_FAILS = "nac:fails:{}"
FAIL_THRESHOLD = 5
FAIL_WINDOW = 300


class EndpointRepository(Repository[Endpoint]):
    model = Endpoint
    sortable = {"last_seen": Endpoint.last_seen, "mac": Endpoint.mac, "status": Endpoint.status}
    filterable = {
        "status": Endpoint.status,
        "switch_id": Endpoint.switch_id,
        "vlan": Endpoint.vlan,
        "endpoint_group": Endpoint.endpoint_group,
        "source": Endpoint.source,
    }


@dataclass(frozen=True, slots=True)
class RadiusRequest:
    mac: str
    username: str | None
    nas_ip: str | None
    nas_port_id: str | None
    auth_method: str  # dot1x | mab
    user_groups: frozenset[str] = frozenset()
    framed_ip: str | None = None


@dataclass(frozen=True, slots=True)
class AccountingRequest:
    status_type: str  # Start | Interim-Update | Stop
    session_id: str
    mac: str
    username: str | None
    nas_ip: str
    nas_port_id: str | None
    framed_ip: str | None
    input_octets: int = 0
    output_octets: int = 0
    terminate_cause: str | None = None


class NacService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        redis: Redis,
        policies: NacPolicyService,
        audit: AuditService,
        events: EventBus,
    ) -> None:
        self._settings = settings
        self._db = db
        self._redis = redis
        self.policies = policies
        self._audit = audit
        self._events = events

    async def _fail_mode(self, site: str | None) -> str:
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(Setting, "nac")
        overrides = (row.value if row else {}).get("site_fail_modes", {})
        return str(overrides.get(site or "", self._settings.nac_fail_mode))

    async def _switch(self, nas_ip: str | None) -> Device | None:
        if not nas_ip:
            return None
        async with UnitOfWork(self._db) as uow:
            return await uow.session.scalar(select(Device).where(Device.mgmt_ip == nas_ip))

    async def authorize(self, req: RadiusRequest, ctx: RequestContext) -> engine.Decision:
        switch = await self._switch(req.nas_ip)
        site = switch.site if switch else None
        try:
            endpoint = await self._touch_endpoint(req, switch)
            facts = engine.RequestFacts(
                mac=endpoint.mac,
                username=req.username,
                auth_method=req.auth_method,
                user_groups=req.user_groups,
                site=site,
                now=datetime.now(UTC),
                registered=endpoint.registered,
                endpoint_group=endpoint.endpoint_group,
                device_type=endpoint.device_type,
                status=endpoint.status.value,
            )
            decision = engine.evaluate(
                await self.policies.active(),
                facts,
                guest_vlan=self._settings.nac_guest_vlan,
                quarantine_vlan=self._settings.nac_quarantine_vlan,
            )
        except (
            Exception
        ) as exc:  # engine failure -> configured fail mode, never an exception to RADIUS
            log.error("nac_engine_failure", error=type(exc).__name__, mac=req.mac)
            decision = engine.fail_mode_decision(
                await self._fail_mode(site), self._settings.nac_quarantine_vlan
            )
            await self._audit.record(
                operation="nac.decision",
                success=False,
                ctx=ctx,
                result_summary=f"{req.mac} {decision.policy_name}",
                reason=type(exc).__name__,
            )
            return decision
        await self._apply_decision(endpoint.id, decision)
        await self._audit.record(
            operation="nac.decision",
            success=decision.result == "accept",
            ctx=ctx,
            username=req.username,
            device_id=switch.id if switch else None,
            result_summary=(
                f"{endpoint.mac} -> {decision.result} vlan={decision.vlan} "
                f"policy={decision.policy_name}"
            ),
            extra={
                "policy_id": decision.policy_id,
                "policy": decision.policy_name,
                "mac": endpoint.mac,
                "auth_method": req.auth_method,
            },
        )
        if decision.result == "reject":
            await self._count_failure(endpoint.mac, req.username)
        return decision

    async def _touch_endpoint(self, req: RadiusRequest, switch: Device | None) -> Endpoint:
        mac = normalize_mac(req.mac)
        now = datetime.now(UTC)
        prof = profile(ProfileSignals(mac=mac))
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.scalar(select(Endpoint).where(Endpoint.mac == mac))
            if row is None:
                row = Endpoint(
                    mac=mac,
                    status=EndpointStatus.UNKNOWN,
                    first_seen=now,
                    vendor=prof.vendor,
                    device_type=prof.device_type,
                    profile_confidence=prof.confidence,
                    profile_reasons=prof.reasons,
                )
                uow.session.add(row)
            row.last_seen = now
            row.user = req.username if req.auth_method == "dot1x" else row.user
            row.source = EndpointSource(req.auth_method)
            row.switch_id = switch.id if switch else row.switch_id
            row.interface = req.nas_port_id or row.interface
            row.ip = req.framed_ip or row.ip
            await uow.commit()
            return row

    async def _apply_decision(self, endpoint_id: uuid.UUID, decision: engine.Decision) -> None:
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(Endpoint, endpoint_id)
            if row is None:
                return
            if row.status is not EndpointStatus.BLOCKED:  # manual blocks stick
                row.status = EndpointStatus(decision.endpoint_status)
            row.vlan = decision.vlan if decision.vlan is not None else row.vlan
            row.authorization_state = f"{decision.result}:{decision.policy_name}"
            await uow.commit()
        if decision.result == "accept":
            await self._events.emit(
                "endpoint_authorized",
                endpoint_id=str(endpoint_id),
                policy=decision.policy_name,
                vlan=decision.vlan,
            )

    async def _count_failure(self, mac: str, username: str | None) -> None:
        key = _FAILS.format(username or mac)
        count = int(await self._redis.incr(key))
        await self._redis.expire(key, FAIL_WINDOW)
        if count == FAIL_THRESHOLD:
            await self._events.emit(
                "authentication_failures",
                key=username or mac,
                message=f"{count} rejected authentications for {username or mac} in 5 min",
            )

    async def accounting(self, req: AccountingRequest) -> None:
        mac = normalize_mac(req.mac)
        now = datetime.now(UTC)
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.scalar(
                select(NacSession).where(
                    NacSession.acct_session_id == req.session_id, NacSession.nas_ip == req.nas_ip
                )
            )
            if row is None:
                row = NacSession(
                    acct_session_id=req.session_id, mac=mac, nas_ip=req.nas_ip, started_at=now
                )
                uow.session.add(row)
            row.username = req.username or row.username
            row.nas_port_id = req.nas_port_id or row.nas_port_id
            row.framed_ip = req.framed_ip or row.framed_ip
            row.input_octets, row.output_octets, row.updated_at = (
                req.input_octets,
                req.output_octets,
                now,
            )
            if req.status_type == "Stop":
                row.status, row.stopped_at, row.terminate_cause = (
                    "STOPPED",
                    now,
                    req.terminate_cause,
                )
            endpoint = await uow.session.scalar(select(Endpoint).where(Endpoint.mac == mac))
            if endpoint is not None:
                endpoint.last_seen = now
                endpoint.ip = req.framed_ip or endpoint.ip
                row.vlan = endpoint.vlan
            await uow.commit()

    # --- endpoint inventory ------------------------------------------------------------------
    async def endpoints(self, query: ListQuery, mac: str | None = None) -> Page[Endpoint]:
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(EndpointRepository)
            stmt = repo.base_query()
            if mac:
                stmt = stmt.where(Endpoint.mac == normalize_mac(mac))
            return await repo.list(query, stmt)

    async def endpoint(self, mac: str) -> Endpoint:
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.scalar(
                select(Endpoint).where(Endpoint.mac == normalize_mac(mac))
            )
        if row is None:
            raise NotFound("Endpoint not found")
        return row

    async def upsert_endpoint(
        self, principal: Principal, ctx: RequestContext, mac: str, changes: dict[str, Any]
    ) -> Endpoint:
        try:
            canonical = normalize_mac(mac)
        except InvalidMac as exc:
            raise ValidationFailed(str(exc)) from exc
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.scalar(select(Endpoint).where(Endpoint.mac == canonical))
            if row is None:
                row = Endpoint(mac=canonical, source=EndpointSource.MANUAL)
                uow.session.add(row)
            for key, value in changes.items():
                if value is not None:
                    setattr(row, key, EndpointStatus(value) if key == "status" else value)
            await uow.commit()
        await self._audit.record(
            operation="nac.endpoint.update",
            success=True,
            principal=principal,
            ctx=ctx,
            result_summary=canonical,
            extra={k: v for k, v in changes.items() if v is not None},
        )
        return row

    async def ingest_dhcp(
        self, mac: str, prl: str | None, vendor_class: str | None, hostname: str | None
    ) -> Endpoint:
        canonical = normalize_mac(mac)
        prof = profile(
            ProfileSignals(
                mac=canonical, dhcp_prl=prl, vendor_class=vendor_class, hostname=hostname
            )
        )
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.scalar(select(Endpoint).where(Endpoint.mac == canonical))
            if row is None:
                row = Endpoint(mac=canonical)
                uow.session.add(row)
            row.vendor = prof.vendor or row.vendor
            row.device_type = prof.device_type or row.device_type
            row.os = prof.os or row.os
            row.hostname = hostname or row.hostname
            row.profile_confidence = max(row.profile_confidence or 0.0, prof.confidence)
            row.profile_reasons = prof.reasons
            await uow.commit()
            return row

    async def sessions(self, active_only: bool = True, mac: str | None = None) -> list[NacSession]:
        stmt = select(NacSession).order_by(NacSession.updated_at.desc()).limit(500)
        if active_only:
            stmt = stmt.where(NacSession.status == "ACTIVE")
        if mac:
            stmt = stmt.where(NacSession.mac == normalize_mac(mac))
        async with UnitOfWork(self._db) as uow:
            return list((await uow.session.scalars(stmt)).all())
