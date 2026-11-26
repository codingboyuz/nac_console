"""Cisco IOS / IOS-XE CLI driver (Netmiko ``cisco_ios`` / ``cisco_ios_telnet``).

Privilege: the credential's super password is used as the enable secret. Changes apply
immediately inside 'configure terminal' and persist with 'write memory'. The device-side
rollback timer is 'configure terminal revert timer <minutes>' + 'configure confirm', which
needs the configuration archive ('archive' / 'path') set up on the switch.
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.core.exceptions import (
    CommandRejected,
    DeviceAuthFailed,
    DeviceError,
    UnsupportedCapability,
)
from app.core.logging import get_logger
from app.core.redaction import redact_text
from app.domain.commands.sanitize import validate_cli_block
from app.domain.devices import TELNET_SECURITY_WARNING, DeviceFamily, DeviceProtocol
from app.network.drivers.base import elapsed_ms
from app.network.drivers.netmiko_cli import NetmikoCliDriver
from app.network.errors import map_exception
from app.network.ports import ConfigResult, DeviceFacts

log = get_logger(__name__)

# IOS marks rejected input with '%' lines; '% Invalid input detected at '^' marker.'
_ERROR = re.compile(
    r"^\s*(%\s*(Invalid input|Incomplete command|Ambiguous command|Unknown command|"
    r"Unrecognized command|Error|Bad mask|Bad IP|Duplicate|Cannot|Not allowed|"
    r"Command rejected|Access denied|Authorization failed|bad parameter|Wrong number|"
    r".*not supported|.*overlaps).*)$",
    re.IGNORECASE | re.MULTILINE,
)
_ARCHIVE_MISSING = re.compile(r"archive|rollback", re.IGNORECASE)


def detect_ios_error(output: str) -> str | None:
    match = _ERROR.search(output or "")
    return redact_text(match.group(1).strip())[:300] if match else None


class CiscoIosDriver(NetmikoCliDriver):
    netmiko_ssh = "cisco_ios"
    netmiko_telnet = "cisco_ios_telnet"
    show_version = "show version"

    def device_type(self) -> str:
        return self.netmiko_telnet if self.protocol is DeviceProtocol.TELNET else self.netmiko_ssh

    def _extra_args(self) -> dict[str, Any]:
        secret = self.params.super_password
        return {"secret": secret.get_secret_value()} if secret is not None else {}

    def _after_connect(self) -> None:
        if self.params.super_password is None:
            return
        try:
            if not self._conn.check_enable_mode():
                self._conn.enable()
        except Exception as exc:
            raise DeviceAuthFailed("privilege escalation ('enable') was rejected") from exc

    def detect_error(self, output: str) -> str | None:
        return detect_ios_error(output)

    # --- configuration -------------------------------------------------------------------
    def _config_mode_command(self, trial_seconds: int | None) -> str:
        if trial_seconds:
            minutes = max(1, min(120, -(-int(trial_seconds) // 60)))
            return f"configure terminal revert timer {minutes}"
        return "configure terminal"

    def push_config(
        self, commands: list[str], *, save: bool = False, trial_seconds: int | None = None
    ) -> ConfigResult:
        lines = validate_cli_block(commands)
        conn = self._require()
        start = time.perf_counter()
        try:
            output: str = conn.send_config_set(
                lines,
                config_mode_command=self._config_mode_command(trial_seconds),
                exit_config_mode=True,
                read_timeout=self.params.timeout,
                cmd_verify=True,
            )
            error = self.detect_error(output)
            if error:
                if trial_seconds and _ARCHIVE_MISSING.search(error):
                    raise UnsupportedCapability(
                        "'configure terminal revert timer' needs the configuration archive "
                        "('archive' + 'path') on the switch"
                    )
                if trial_seconds:
                    self._revert_now()
                raise CommandRejected(f"device rejected configuration: {error}")
            saved = False
            if save:
                output += self.save()
                saved = True
        except (DeviceError, UnsupportedCapability):
            raise
        except Exception as exc:
            if trial_seconds and "config" in str(exc).lower():
                # Netmiko could not enter config mode: 'revert timer' was refused.
                raise UnsupportedCapability(
                    "'configure terminal revert timer' was refused; configure the archive "
                    "('archive' + 'path') on the switch to use rollback timers"
                ) from exc
            raise map_exception(exc, self.params.host) from exc
        return ConfigResult(
            tuple(lines), output, False, saved, elapsed_ms(start), self.security_warning
        )

    def _revert_now(self) -> None:
        try:
            self._conn.send_command_timing("configure revert now", strip_prompt=False)
        except Exception as exc:  # the timer still reverts on its own
            log.warning("revert_failed", device_id=self.params.device_id, error=type(exc).__name__)

    def confirm_trial(self) -> str:
        """Keep a 'revert timer' change: 'configure confirm' (exec mode)."""
        out: str = self._require().send_command_timing("configure confirm", strip_prompt=False)
        self.raise_on_error("configure confirm", out)
        return out

    def save(self) -> str:
        conn = self._require()
        out: str = conn.send_command_timing("write memory", strip_prompt=False)
        if "[confirm]" in out or "Destination filename" in out:
            out += conn.send_command_timing("\n", strip_prompt=False)
        self.raise_on_error("write memory", out)
        return out

    def capabilities(self) -> DeviceFacts:
        from app.network.platforms.cisco_ios.parsers import parse_version

        info = parse_version(self.run_show(self.show_version).output)
        return DeviceFacts(
            family=DeviceFamily.UNKNOWN,
            vrp_version=info.vrp_version,
            software_version=info.software_version,
            model=info.model,
            uptime_seconds=info.uptime_seconds,
            capabilities={
                "cli": True,
                "commit_model": "immediate",
                "rollback_timer": "configure terminal revert timer",
                "transport": self.protocol.value,
            },
        )


class CiscoIosTelnetDriver(CiscoIosDriver):
    protocol = DeviceProtocol.TELNET
    security_warning = TELNET_SECURITY_WARNING

    def _netmiko_args(self) -> dict[str, Any]:
        args = super()._netmiko_args()
        for key in (
            "use_keys",
            "pkey",
            "allow_agent",
            "system_host_keys",
            "ssh_strict",
            "alt_host_keys",
            "alt_key_file",
            "banner_timeout",
            "auth_timeout",
        ):
            args.pop(key, None)
        return args

    def _write_known_hosts(self) -> str:
        return ""
