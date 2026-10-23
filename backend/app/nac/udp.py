"""Portable RADIUS request/response over UDP (pyrad's Client relies on select.poll, which
Windows lacks). Retransmits the identical packet, validates id + response authenticator."""

from __future__ import annotations

import socket
from typing import Any


class RadiusTimeout(Exception):
    pass


def exchange(host: str, port: int, request: Any, *, timeout: float, retries: int) -> Any:
    """Send a pyrad packet and return the verified reply packet (pyrad object)."""
    raw = request.RequestPacket()
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        for _ in range(retries + 1):
            sock.sendto(raw, (host, port))
            try:
                while True:
                    data, _addr = sock.recvfrom(4096)
                    reply = request.CreateReply(packet=data)
                    if request.VerifyReply(reply, data):
                        return reply
            except TimeoutError:
                continue
    raise RadiusTimeout(f"no RADIUS reply from {host}:{port}")
