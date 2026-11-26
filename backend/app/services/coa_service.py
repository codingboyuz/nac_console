"""RFC 5176 Change-of-Authorization client: Disconnect-Request and CoA-Request to the NAS
(switch management IP, UDP 3799), with retries/timeouts and an audit row per operation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import select

from app.core.crypto import CredentialVault
from app.core.database import Database
from app.core.exceptions import DeviceTimeout, NotFound, ServiceUnavailable
from app.domain.identity import Principal, RequestContext
from app.domain.mac import to_huawei, to_ietf
from app.domain.platforms import Platform
from app.models.device import Device
from app.models.nac import NacSession, RadiusServer
from app.repositories.uow import UnitOfWork
from app.services.audit_service import AuditService

DICTIONARY = Path(__file__).resolve().parents[1] / "nac" / "dictionary"


@dataclass(frozen=True, slots=True)
class CoaResult:
    session_id: str
    nas_ip: str
    kind: str  # disconnect | reauthenticate
    acknowledged: bool
    detail: str


def _send(
    nas_ip: str,
    port: int,
    secret: bytes,
    kind: str,
    attrs: dict[str, Any],
    timeout: int,
    retries: int,
) -> tuple[bool, str]:
    """Blocking RFC 5176 exchange (runs in a worker thread)."""
    from pyrad import packet
    from pyrad.dictionary import Dictionary

    from app.nac.udp import RadiusTimeout, exchange

    code = packet.DisconnectRequest if kind == "disconnect" else packet.CoARequest
    request = packet.CoAPacket(code=code, secret=secret, dict=Dictionary(str(DICTIONARY)), **attrs)
    try:
        reply = exchange(nas_ip, port, request, timeout=timeout, retries=retries)
    except RadiusTimeout as exc:
        raise DeviceTimeout(f"no CoA reply from {nas_ip}:{port}") from exc
    ok = reply.code in {packet.DisconnectACK, packet.CoAACK}
    return ok, "ACK" if ok else f"NAK code {reply.code}"


class CoaService:
    def __init__(self, db: Database, vault: CredentialVault, audit: AuditService) -> None:
        self._db = db
        self._vault = vault
        self._audit = audit
        self.sender = _send  # replaceable in tests

    async def _secret(self) -> tuple[bytes, RadiusServer]:
        async with UnitOfWork(self._db) as uow:
            server = await uow.session.scalar(
                select(RadiusServer).where(RadiusServer.enabled.is_(True))
            )
        if server is None:
            raise ServiceUnavailable(
                "no enabled RADIUS server configured (needed for the CoA secret)"
            )
        secret = self._vault.decrypt(server.shared_secret_ciphertext, f"radius:{server.id}")
        return secret, server

    async def _calling_station_id(self, nas_ip: str, mac: str) -> str:
        """MAC in the NAS vendor's Calling-Station-Id format (Huawei 'aabb-ccdd-eeff',
        Cisco / TP-Link IETF 'AA-BB-CC-DD-EE-FF'); unknown NAS -> Huawei format."""
        async with UnitOfWork(self._db) as uow:
            platform = await uow.session.scalar(
                select(Device.platform).where(Device.mgmt_ip == nas_ip)
            )
        if platform is not None and not Platform(platform).is_huawei:
            return to_ietf(mac)
        return to_huawei(mac)

    async def send(
        self, principal: Principal, ctx: RequestContext, session_id: uuid.UUID, kind: str
    ) -> CoaResult:
        async with UnitOfWork(self._db) as uow:
            session = await uow.session.get(NacSession, session_id)
        if session is None:
            raise NotFound("Session not found")
        secret, server = await self._secret()
        attrs: dict[str, Any] = {
            "Acct_Session_Id": session.acct_session_id,
            "Calling_Station_Id": await self._calling_station_id(session.nas_ip, session.mac),
            "NAS_IP_Address": session.nas_ip,
        }
        if session.username:
            attrs["User_Name"] = session.username
        try:
            ok, detail = await anyio.to_thread.run_sync(
                self.sender,
                session.nas_ip,
                server.coa_port,
                secret,
                kind,
                attrs,
                server.timeout,
                server.retries,
            )
        except DeviceTimeout as exc:
            await self._audit.record(
                operation=f"nac.coa.{kind}",
                success=False,
                principal=principal,
                ctx=ctx,
                result_summary=f"{session.mac} {exc.message}",
            )
            raise
        await self._audit.record(
            operation=f"nac.coa.{kind}",
            success=ok,
            principal=principal,
            ctx=ctx,
            result_summary=f"{session.mac} via {session.nas_ip}: {detail}",
        )
        return CoaResult(session.acct_session_id, session.nas_ip, kind, ok, detail)

    async def for_mac(
        self, principal: Principal, ctx: RequestContext, mac_sessions: list[NacSession], kind: str
    ) -> list[CoaResult]:
        results = []
        for session in mac_sessions:
            try:
                results.append(await self.send(principal, ctx, session.id, kind))
            except DeviceTimeout as exc:
                results.append(
                    CoaResult(session.acct_session_id, session.nas_ip, kind, False, exc.message)
                )
        return results
