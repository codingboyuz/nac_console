"""Shared Netmiko CLI plumbing (SSH/Telnet): connect with pinned host keys, retries, show.

Vendor drivers subclass ``NetmikoCliDriver`` and implement the device type, privilege
escalation, configuration commit/save semantics and rollback timers of their CLI dialect.
Blocking: run in a worker or threadpool only.
"""

from __future__ import annotations

import io
import os
import tempfile
import time
from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from app.core.exceptions import CommandRejected
from app.core.logging import get_logger
from app.core.redaction import redact_text
from app.domain.commands.sanitize import validate_cli_line
from app.domain.devices import DeviceProtocol
from app.network.drivers.base import DriverBase, elapsed_ms, retry_connect
from app.network.errors import detect_cli_error, map_exception
from app.network.ports import CommandOutput, ConnectionParams

log = get_logger(__name__)
ConnectHandler = Callable[..., Any]


def _default_connect_handler() -> ConnectHandler:
    from netmiko import ConnectHandler as NetmikoConnectHandler

    handler: ConnectHandler = NetmikoConnectHandler
    return handler


class NetmikoCliDriver(DriverBase):
    """Implements the transport part of DeviceDriver over an interactive CLI session."""

    protocol = DeviceProtocol.SSH
    security_warning: str | None = None

    def __init__(
        self, params: ConnectionParams, *, connect_handler: ConnectHandler | None = None
    ) -> None:
        self.params = params
        self._connect_handler = connect_handler or _default_connect_handler()
        self._conn: Any = None
        self._known_hosts: str | None = None

    # --- dialect hooks -------------------------------------------------------------------
    @abstractmethod
    def device_type(self) -> str:
        """Netmiko device_type for this platform and transport."""

    def _extra_args(self) -> dict[str, Any]:
        """Additional Netmiko arguments (e.g. the enable secret)."""
        return {}

    def _after_connect(self) -> None:
        """Privilege escalation after login (Huawei 'super', Cisco 'enable')."""

    def detect_error(self, output: str) -> str | None:
        return detect_cli_error(output)

    def raise_on_error(self, command: str, output: str) -> None:
        error = self.detect_error(output)
        if error:
            raise CommandRejected(
                f"device rejected command: {error}", details={"command": redact_text(command)}
            )

    # --- connection ----------------------------------------------------------------------
    def _netmiko_args(self) -> dict[str, Any]:
        p = self.params
        args: dict[str, Any] = {
            "device_type": self.device_type(),
            "host": p.host,
            "port": p.port,
            "username": p.username,
            "password": p.password.get_secret_value() if p.password else None,
            "conn_timeout": p.timeout,
            "auth_timeout": p.timeout,
            "banner_timeout": p.timeout,
            "timeout": p.timeout,
            "session_timeout": max(60, p.timeout * 2),
            "fast_cli": True,  # Netmiko 4 default; pattern-based reads stay reliable
            "session_log": None,
            "allow_agent": False,
            "use_keys": False,
            "system_host_keys": False,
        }
        if p.private_key is not None:
            import paramiko

            args["pkey"] = paramiko.PKey.from_private_key(
                io.StringIO(p.private_key.get_secret_value())
            )
            args["use_keys"] = True
        if p.pinned_host_key is not None:
            args.update(ssh_strict=True, alt_host_keys=True, alt_key_file=self._write_known_hosts())
        else:
            # Only reachable when SSH_STRICT_HOST_KEY=false was set explicitly by an operator.
            args["ssh_strict"] = p.strict_host_key
        args.update(self._extra_args())
        return args

    def _write_known_hosts(self) -> str:
        key = self.params.pinned_host_key
        if key is None:
            raise RuntimeError("invariant violated: key missing")
        host = (
            self.params.host
            if self.params.port == 22
            else f"[{self.params.host}]:{self.params.port}"
        )
        fd, path = tempfile.mkstemp(prefix="nac-kh-", suffix=".known_hosts")
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(f"{host} {key.key_type} {key.public_key_b64}\n")
        self._known_hosts = path
        return path

    def connect(self) -> None:
        if self._conn is not None:
            return

        def attempt() -> Any:
            return self._connect_handler(**self._netmiko_args())

        self._conn = retry_connect(self.params, attempt)
        try:
            self._after_connect()
        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.disconnect()
            except Exception as exc:  # closing must never mask the real error
                log.debug("disconnect_failed", error=type(exc).__name__)
        if self._known_hosts:
            try:
                os.unlink(self._known_hosts)
            finally:
                self._known_hosts = None

    def _require(self) -> Any:
        if self._conn is None:
            self.connect()
        return self._conn

    # --- operations ----------------------------------------------------------------------
    def run_show(self, command: str) -> CommandOutput:
        clean = validate_cli_line(command, allow_pipe=True)
        conn = self._require()
        start = time.perf_counter()
        try:
            output: str = conn.send_command(clean, read_timeout=self.params.timeout)
        except Exception as exc:
            raise map_exception(exc, self.params.host) from exc
        self.raise_on_error(clean, output)
        return CommandOutput(clean, output, elapsed_ms(start), self.security_warning)

    def health(self) -> bool:
        conn = self._conn
        return bool(conn is not None and conn.is_alive())
