"""Fetch a device's SSH host key without authenticating (for trust-on-first-use pinning)."""

from __future__ import annotations

import base64
import hashlib
import socket

from app.network.errors import map_exception
from app.network.ports import HostKey


def fingerprint_sha256(public_key_b64: str) -> str:
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def fetch_host_key(host: str, port: int, timeout: float = 10.0) -> HostKey:
    """Perform the SSH key exchange only and return the server's host key."""
    import paramiko

    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise map_exception(exc, host) from exc
    transport = paramiko.Transport(sock)
    try:
        transport.banner_timeout = timeout
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        b64 = key.get_base64()
        return HostKey(key.get_name(), b64, fingerprint_sha256(b64))
    except Exception as exc:
        raise map_exception(exc, host) from exc
    finally:
        transport.close()
        sock.close()
