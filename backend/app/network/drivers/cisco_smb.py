"""Cisco Small Business CLI driver (Netmiko ``cisco_s300`` / ``cisco_s300_telnet``).

Saves with 'write memory' (Netmiko answers the 'Overwrite file [startup-config]' prompt).
No commit-confirm or revert timer exists, so the platform declares no rollback timer.
Identity combines 'show version' (= show bootvar) with 'show system'.
"""

from __future__ import annotations

from app.core.exceptions import UnsupportedCapability
from app.domain.devices import TELNET_SECURITY_WARNING, DeviceFamily, DeviceProtocol
from app.network.drivers.cisco_ios import CiscoIosDriver, CiscoIosTelnetDriver
from app.network.ports import ConfigResult, DeviceFacts


class CiscoSmbDriver(CiscoIosDriver):
    netmiko_ssh = "cisco_s300"
    netmiko_telnet = "cisco_s300_telnet"

    def push_config(
        self, commands: list[str], *, save: bool = False, trial_seconds: int | None = None
    ) -> ConfigResult:
        if trial_seconds:
            raise UnsupportedCapability("Cisco SMB has no rollback timer")
        return super().push_config(commands, save=save)

    def confirm_trial(self) -> str:
        return ""

    def save(self) -> str:
        out: str = self._require().save_config()
        self.raise_on_error("write memory", out)
        return out

    def capabilities(self) -> DeviceFacts:
        from app.network.platforms.cisco_smb.parsers import parse_version

        text = self.run_show("show version").output + "\n" + self.run_show("show system").output
        info = parse_version(text)
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


class CiscoSmbTelnetDriver(CiscoSmbDriver):
    protocol = DeviceProtocol.TELNET
    security_warning = TELNET_SECURITY_WARNING
    _netmiko_args = CiscoIosTelnetDriver._netmiko_args
    _write_known_hosts = CiscoIosTelnetDriver._write_known_hosts
