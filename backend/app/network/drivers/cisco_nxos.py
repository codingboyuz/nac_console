"""Cisco NX-OS CLI driver (Netmiko ``cisco_nxos``).

Role-based CLI: no 'enable' step unless a super password is configured. Changes apply
immediately and persist with 'copy running-config startup-config'. NX-OS has no commit-confirm
or revert timer for CLI sessions, so the platform declares no device-side rollback timer.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import UnsupportedCapability
from app.domain.devices import DeviceFamily
from app.network.drivers.cisco_ios import CiscoIosDriver
from app.network.ports import ConfigResult, DeviceFacts


class CiscoNxosDriver(CiscoIosDriver):
    netmiko_ssh = "cisco_nxos"
    netmiko_telnet = "cisco_nxos"

    def _extra_args(self) -> dict[str, Any]:
        return {}

    def _after_connect(self) -> None:
        return None

    def push_config(
        self, commands: list[str], *, save: bool = False, trial_seconds: int | None = None
    ) -> ConfigResult:
        if trial_seconds:
            raise UnsupportedCapability("NX-OS has no CLI rollback timer")
        return super().push_config(commands, save=save)

    def confirm_trial(self) -> str:
        return ""

    def save(self) -> str:
        conn = self._require()
        out: str = conn.send_command_timing("copy running-config startup-config", read_timeout=120)
        self.raise_on_error("copy running-config startup-config", out)
        return out

    def capabilities(self) -> DeviceFacts:
        from app.network.platforms.cisco_nxos.parsers import parse_version

        info = parse_version(self.run_show("show version").output)
        return DeviceFacts(
            family=DeviceFamily.UNKNOWN,
            vrp_version=info.vrp_version,
            software_version=info.software_version,
            model=info.model,
            uptime_seconds=info.uptime_seconds,
            capabilities={
                "cli": True,
                "commit_model": "immediate",
                "transport": self.protocol.value,
            },
        )
