"""Gentle discovery of explicitly allowed subnets: ICMP echo (rate-limited, concurrency-capped)
-> SNMP sysDescr/sysObjectID with stored credentials -> SSH identification banner.
No port sweeps, no brute force, no credential guessing. Results are proposals only."""

from __future__ import annotations

import asyncio
import ipaddress
import platform
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import Database
from app.core.exceptions import Conflict, DeviceError, NotFound, ValidationFailed
from app.core.logging import get_logger
from app.domain.devices import CredentialKind
from app.domain.identity import Principal, RequestContext
from app.models.device import Credential, Device
from app.models.monitoring import DiscoveredDevice, DiscoveryRun
from app.network.drivers.huawei_snmp import family_from_descr
from app.network.snmp.client import SnmpAuth, SnmpClient, SnmpTarget
from app.network.snmp.collector import collect_system
from app.repositories.uow import UnitOfWork
from app.schemas.devices import DeviceCreate
from app.services.audit_service import AuditService
from app.services.credential_service import CredentialService, ResolvedSecret
from app.services.device_service import DeviceService

log = get_logger(__name__)


class DiscoveryProbes:
    """Network probes; replaced by fakes in tests."""

    async def ping(self, ip: str) -> bool:
        flags = (
            ["-n", "1", "-w", "1000"] if platform.system() == "Windows" else ["-c", "1", "-W", "1"]
        )
        proc = await asyncio.create_subprocess_exec(
            "ping",
            *flags,
            ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return await asyncio.wait_for(proc.wait(), timeout=3) == 0
        except TimeoutError:
            proc.kill()
            return False

    async def snmp_system(self, ip: str, secret: ResolvedSecret) -> dict[str, Any] | None:
        if secret.kind is CredentialKind.SNMP_V2C:
            auth = SnmpAuth("v2c", community=secret.community)
        else:
            auth = SnmpAuth(
                "v3",
                username=secret.username,
                auth_protocol=secret.auth_protocol,
                auth_key=secret.auth_key,
                priv_protocol=secret.priv_protocol,
                priv_key=secret.priv_key,
            )
        client = SnmpClient(SnmpTarget(ip, timeout=1.5, retries=0), auth)
        try:
            snap = await collect_system(client)
        except DeviceError:
            return None
        finally:
            client.close()
        return {
            "sys_name": snap.sys_name,
            "sys_descr": snap.sys_descr,
            "sys_object_id": snap.sys_object_id,
        }

    async def ssh_banner(self, ip: str, port: int = 22) -> str | None:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=3)
        except (OSError, TimeoutError):
            return None
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=3)
            return line.decode(errors="replace").strip()[:255] or None
        except (OSError, TimeoutError):
            return None
        finally:
            writer.close()


RunDispatcher = Callable[[uuid.UUID], Awaitable[None]]


class DiscoveryService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        credentials: CredentialService,
        devices: DeviceService,
        audit: AuditService,
        probes: DiscoveryProbes | None = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._credentials = credentials
        self._devices = devices
        self._audit = audit
        self.probes = probes or DiscoveryProbes()
        self._tasks: set[asyncio.Task[None]] = set()
        self.dispatch: RunDispatcher = self._inline

    def validate_cidr(self, cidr: str) -> ipaddress.IPv4Network:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ValidationFailed(f"invalid CIDR {cidr!r}") from exc
        if not isinstance(net, ipaddress.IPv4Network):
            raise ValidationFailed("only IPv4 discovery is supported")
        if net.prefixlen < self._settings.discovery_max_prefix:
            raise ValidationFailed(
                f"CIDR larger than /{self._settings.discovery_max_prefix} is not allowed"
            )
        allowed = [
            ipaddress.ip_network(a, strict=False) for a in self._settings.discovery_allowed_subnets
        ]
        if not any(isinstance(a, ipaddress.IPv4Network) and net.subnet_of(a) for a in allowed):
            raise ValidationFailed("CIDR is outside DISCOVERY_ALLOWED_SUBNETS")
        return net

    async def start(
        self,
        principal: Principal,
        ctx: RequestContext,
        cidr: str,
        snmp_credentials: list[uuid.UUID],
    ) -> DiscoveryRun:
        try:
            net = self.validate_cidr(cidr)
        except ValidationFailed as exc:
            await self._audit.record(
                operation="discovery.start",
                success=False,
                principal=principal,
                ctx=ctx,
                reason=exc.message,
                result_summary=cidr[:64],
            )
            raise
        for cred_id in snmp_credentials:
            cred = await self._credentials.get(cred_id)
            CredentialService.ensure_kind(cred, CredentialKind.SNMP_V2C, CredentialKind.SNMP_V3)
        run = DiscoveryRun(
            cidr=str(net),
            created_by=principal.user_id,
            snmp_credential_ids=[str(c) for c in snmp_credentials],
        )
        async with UnitOfWork(self._db) as uow:
            uow.session.add(run)
            await uow.commit()
        await self._audit.record(
            operation="discovery.start",
            success=True,
            principal=principal,
            ctx=ctx,
            result_summary=f"{net} run {run.id}",
        )
        await self.dispatch(run.id)
        return run

    async def _inline(self, run_id: uuid.UUID) -> None:
        task = asyncio.create_task(self.execute(run_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def execute(self, run_id: uuid.UUID) -> None:
        async with UnitOfWork(self._db) as uow:
            run = await uow.session.get(DiscoveryRun, run_id)
            if run is None or run.status != "PENDING":
                return
            run.status = "RUNNING"
            await uow.commit()
            ids = [uuid.UUID(c) for c in run.snmp_credential_ids or []]
            creds = list(
                (await uow.session.scalars(select(Credential).where(Credential.id.in_(ids)))).all()
            )
        secrets = [self._credentials.resolve_row(c) for c in creds]
        hosts = [str(h) for h in ipaddress.ip_network(run.cidr).hosts()]
        semaphore = asyncio.Semaphore(self._settings.discovery_concurrency)
        interval = 1.0 / max(1, self._settings.discovery_rate_per_second)
        found = 0

        async def probe(index: int, ip: str) -> None:
            nonlocal found
            await asyncio.sleep(index * interval)  # global pacing: N probes per second
            async with semaphore:
                if not await self.probes.ping(ip):
                    return
                info: dict[str, Any] = {}
                for secret in secrets:
                    info = await self.probes.snmp_system(ip, secret) or {}
                    if info:
                        break
                banner = await self.probes.ssh_banner(ip)
                descr = info.get("sys_descr")
                oid = info.get("sys_object_id") or ""
                async with UnitOfWork(self._db) as uow:
                    uow.session.add(
                        DiscoveredDevice(
                            run_id=run_id,
                            ip=ip,
                            reachable=True,
                            sys_name=info.get("sys_name"),
                            sys_descr=descr,
                            sys_object_id=oid or None,
                            family=family_from_descr(descr).value,
                            ssh_banner=banner,
                            is_huawei=oid.startswith("1.3.6.1.4.1.2011")
                            or "huawei" in (banner or "").lower(),
                        )
                    )
                    await uow.commit()
                found += 1

        error = None
        try:
            await asyncio.gather(*(probe(i, ip) for i, ip in enumerate(hosts)))
        except Exception as exc:
            log.exception("discovery_failed", run_id=str(run_id))
            error = type(exc).__name__
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(DiscoveryRun, run_id)
            if row is None:
                raise RuntimeError("invariant violated: row missing")
            row.status, row.completed_at = ("FAILED" if error else "SUCCESS"), datetime.now(UTC)
            row.hosts_scanned, row.hosts_found, row.error = len(hosts), found, error
            await uow.commit()

    async def run(self, run_id: uuid.UUID) -> tuple[DiscoveryRun, list[DiscoveredDevice]]:
        async with UnitOfWork(self._db) as uow:
            run = await uow.session.get(DiscoveryRun, run_id)
            if run is None:
                raise NotFound("Discovery run not found")
            rows = await uow.session.scalars(
                select(DiscoveredDevice).where(DiscoveredDevice.run_id == run_id)
            )
            return run, list(rows.all())

    async def adopt(
        self,
        principal: Principal,
        ctx: RequestContext,
        discovered_id: uuid.UUID,
        data: DeviceCreate,
    ) -> Device:
        async with UnitOfWork(self._db) as uow:
            proposal = await uow.session.get(DiscoveredDevice, discovered_id)
            if proposal is None:
                raise NotFound("Discovered device not found")
            if proposal.status != "PROPOSED":
                raise Conflict(f"proposal is {proposal.status}")
            if str(data.mgmt_ip) != proposal.ip:
                raise ValidationFailed("mgmt_ip must match the discovered address")
        device = await self._devices.create(principal, data)
        async with UnitOfWork(self._db) as uow:
            proposal = await uow.session.get(DiscoveredDevice, discovered_id)
            if proposal is None:
                raise RuntimeError("invariant violated: proposal missing")
            proposal.status, proposal.adopted_device_id = "ADOPTED", device.id
            await uow.commit()
        await self._audit.record(
            operation="discovery.adopt",
            success=True,
            principal=principal,
            ctx=ctx,
            device_id=device.id,
            result_summary=proposal.ip,
        )
        return device

    async def ignore(self, discovered_id: uuid.UUID) -> None:
        async with UnitOfWork(self._db) as uow:
            proposal = await uow.session.get(DiscoveredDevice, discovered_id)
            if proposal is None:
                raise NotFound("Discovered device not found")
            proposal.status = "IGNORED"
            await uow.commit()
