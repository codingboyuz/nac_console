"""Development/testing RADIUS reference server (pyrad, asyncio). NOT for production.

Supports MAC authentication bypass (PAP with User-Name == User-Password == MAC) and
accounting, delegating every decision to the same NacService the FreeRADIUS integration uses.
No EAP: 802.1X in production terminates on FreeRADIUS (see docs/adr/0001).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.domain.identity import RequestContext
from app.domain.mac import InvalidMac, normalize_mac
from app.services.nac_service import AccountingRequest, NacService, RadiusRequest

log = get_logger(__name__)
DICTIONARY = Path(__file__).resolve().parent / "dictionary"


class ReferenceServerDisabled(RuntimeError):
    pass


def _first(pkt: Any, name: str) -> str | None:
    values = pkt.get(name)
    return str(values[0]) if values else None


@dataclass
class _Handler:
    nac: NacService
    secret: bytes
    accounting: bool

    def __post_init__(self) -> None:
        from pyrad.dictionary import Dictionary

        self.dictionary = Dictionary(str(DICTIONARY))

    async def handle(self, data: bytes) -> bytes | None:
        from pyrad import packet

        if self.accounting:
            req = packet.AcctPacket(packet=data, secret=self.secret, dict=self.dictionary)
            if not req.VerifyAcctRequest():
                return None
            await self._accounting(req)
            reply = req.CreateReply()
            return bytes(reply.ReplyPacket())
        req = packet.AuthPacket(packet=data, secret=self.secret, dict=self.dictionary)
        reply = req.CreateReply()
        try:
            mac = normalize_mac(_first(req, "Calling-Station-Id") or _first(req, "User-Name") or "")
            password = req.PwDecrypt(req["User-Password"][0]) if req.get("User-Password") else ""
            if normalize_mac(password) != mac:
                raise InvalidMac("MAB password must equal the MAC")
            decision = await self.nac.authorize(
                RadiusRequest(
                    mac=mac,
                    username=_first(req, "User-Name"),
                    nas_ip=_first(req, "NAS-IP-Address"),
                    nas_port_id=_first(req, "NAS-Port-Id"),
                    auth_method="mab",
                ),
                RequestContext(user_agent="radius-reference"),
            )
        except (InvalidMac, AppError, KeyError) as exc:
            log.info("reference_radius_reject", reason=type(exc).__name__)
            reply.code = packet.AccessReject
            return bytes(reply.ReplyPacket())
        reply.code = packet.AccessAccept if decision.result == "accept" else packet.AccessReject
        for name, value in decision.reply.items():
            reply.AddAttribute(name, int(value) if name == "Session-Timeout" else value)
        return bytes(reply.ReplyPacket())

    async def _accounting(self, req: Any) -> None:
        status_type = _first(req, "Acct-Status-Type") or "Interim-Update"
        nas_ip, session_id = _first(req, "NAS-IP-Address"), _first(req, "Acct-Session-Id")
        mac = _first(req, "Calling-Station-Id")
        if not (nas_ip and session_id and mac):
            return
        await self.nac.accounting(
            AccountingRequest(
                status_type=status_type,
                session_id=session_id,
                mac=mac,
                username=_first(req, "User-Name"),
                nas_ip=nas_ip,
                nas_port_id=_first(req, "NAS-Port-Id"),
                framed_ip=_first(req, "Framed-IP-Address"),
                input_octets=int(_first(req, "Acct-Input-Octets") or 0),
                output_octets=int(_first(req, "Acct-Output-Octets") or 0),
            )
        )


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, handler: _Handler) -> None:
        self.handler = handler
        self.transport: asyncio.DatagramTransport | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        task = asyncio.ensure_future(self._reply(data, addr))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _reply(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            out = await self.handler.handle(data)
        except Exception as exc:  # malformed packets are dropped like a real server would
            log.warning("reference_radius_bad_packet", error=type(exc).__name__)
            return
        if out and self.transport is not None:
            self.transport.sendto(out, addr)


def _factory(nac: NacService, secret: bytes, accounting: bool) -> Any:
    def build() -> _Protocol:
        return _Protocol(_Handler(nac, secret, accounting))

    return build


async def serve(
    settings: Settings,
    nac: NacService,
    secret: str,
    host: str = "127.0.0.1",
    auth_port: int = 1812,
    acct_port: int = 1813,
) -> list[asyncio.DatagramTransport]:
    """Start auth + acct listeners; returns transports (close them to stop)."""
    if not settings.radius_reference_server_enabled or settings.is_production_like:
        raise ReferenceServerDisabled("reference RADIUS server is for development/testing only")
    loop = asyncio.get_running_loop()
    transports = []
    for port, accounting in ((auth_port, False), (acct_port, True)):
        transport, _ = await loop.create_datagram_endpoint(
            _factory(nac, secret.encode(), accounting),
            local_addr=(host, port),
        )
        transports.append(transport)
    log.warning("reference_radius_started", host=host, auth_port=auth_port, acct_port=acct_port)
    return transports
