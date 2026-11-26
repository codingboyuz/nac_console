"""TP-Link JetStream / Omada switch CLI driver (Netmiko ``tplink_jetstream`` / telnet).

Netmiko runs 'enable' / 'enable-admin' (the credential's super password is the enable secret)
and enters configuration with 'configure'. Saves with 'copy running-config startup-config'.
Rollback timer: 'reboot-schedule in <minutes>' (global config) reboots into the saved
configuration unless 'reboot-schedule cancel' is sent - callers must not save before disarming.
"""

from __future__ import annotations

from app.core.exceptions import UnsupportedCapability
from app.domain.devices import TELNET_SECURITY_WARNING, DeviceFamily, DeviceProtocol
from app.network.drivers.cisco_ios import CiscoIosDriver, CiscoIosTelnetDriver
from app.network.ports import ConfigResult, DeviceFacts


class TplinkJetstreamDriver(CiscoIosDriver):
    netmiko_ssh = "tplink_jetstream"
    netmiko_telnet = "tplink_jetstream_telnet"

    def _after_connect(self) -> None:
        return None  # Netmiko's session preparation already ran enable / enable-admin

    def _config_mode_command(self, trial_seconds: int | None) -> str:
        return "configure"

    _pending_timer: int | None = None

    def push_config(
        self, commands: list[str], *, save: bool = False, trial_seconds: int | None = None
    ) -> ConfigResult:
        if trial_seconds:
            raise UnsupportedCapability("TP-Link uses a scheduled reboot, not a trial commit")
        if self._pending_timer is not None:
            # The timer goes first in the same configuration session as the change.
            commands = [f"reboot-schedule in {self._pending_timer}", *commands]
            self._pending_timer = None
        self._flush()
        return super().push_config(commands, save=save)

    def confirm_trial(self) -> str:
        return ""

    def save(self) -> str:
        conn = self._require()
        out: str = conn.send_command_timing("copy running-config startup-config", read_timeout=60)
        self.raise_on_error("copy running-config startup-config", out)
        return out

    def arm_rollback_timer(self, minutes: int) -> str:
        """Queue 'reboot-schedule in N' as the first line of the next configuration push."""
        self._pending_timer = max(1, min(int(minutes), 43200))
        return ""

    def _flush(self) -> None:
        """Netmiko's TP-Link exit_config_mode loop reads only up to the first '#', so trailing
        prompts stay buffered and the next 'configure' would see a stale 'host#' and fail."""
        self._require().clear_buffer(backoff=False)

    def disarm_rollback_timer(self) -> str:
        self._flush()
        out: str = self._require().send_config_set(
            ["reboot-schedule cancel"], config_mode_command="configure"
        )
        self.raise_on_error("reboot-schedule cancel", out)
        return out

    def capabilities(self) -> DeviceFacts:
        from app.network.platforms.tplink_jetstream.parsers import parse_version

        info = parse_version(self.run_show("show system-info").output)
        return DeviceFacts(
            family=DeviceFamily.UNKNOWN,
            vrp_version=info.vrp_version,
            software_version=info.software_version,
            model=info.model,
            uptime_seconds=info.uptime_seconds,
            capabilities={
                "cli": True,
                "commit_model": "immediate",
                "rollback_timer": "reboot-schedule",
                "transport": self.protocol.value,
            },
        )


class TplinkJetstreamTelnetDriver(TplinkJetstreamDriver):
    protocol = DeviceProtocol.TELNET
    security_warning = TELNET_SECURITY_WARNING
    _netmiko_args = CiscoIosTelnetDriver._netmiko_args
    _write_known_hosts = CiscoIosTelnetDriver._write_known_hosts
