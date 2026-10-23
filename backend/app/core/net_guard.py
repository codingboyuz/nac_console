"""Source-network restriction for internal endpoints (RADIUS callbacks, /metrics)."""

from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress

from fastapi import Request

from app.core.exceptions import Forbidden, Unauthorized

# Sentinel for "no peer address"; never used to bind a socket.
UNKNOWN_PEER = "0.0.0.0"  # noqa: S104  # nosec B104


def client_ip(request: Request) -> str:
    """Peer address as seen by the ASGI server (proxies must be configured explicitly)."""
    return request.client.host if request.client else UNKNOWN_PEER


def ip_in_networks(ip: str, networks: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(net, strict=False) for net in networks)


async def require_internal_network(request: Request) -> None:
    """Allow only configured source networks."""
    settings = request.app.state.settings
    if not ip_in_networks(client_ip(request), settings.internal_allowed_networks):
        raise Forbidden("source network not allowed for internal endpoint")


def _supplied_token(request: Request) -> str:
    """``X-Internal-Token`` header, or HTTP Basic password (FreeRADIUS rlm_rest ``auth=basic``)."""
    header = request.headers.get("x-internal-token")
    if header:
        return header
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth[6:], validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            return ""
        return decoded.partition(":")[2]
    return ""


async def require_internal_token(request: Request) -> None:
    """Constant-time shared token check."""
    expected = request.app.state.settings.internal_api_token.get_secret_value()
    supplied = _supplied_token(request)
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise Unauthorized("invalid internal token")
