"""Netmiko-based Huawei CLI driver (SSH). Blocking: run in a worker or threadpool only.

Netmiko device types: ``huawei`` (S-series / AR, changes apply immediately, persist with
``save``) and ``huawei_vrpv8`` (CloudEngine, two-stage config with ``commit``).
"""

from __future__ import annotations

import time

from app.core.exceptions import CommandRejected, DeviceAuthFailed, DeviceError
from app.core.logging import get_logger
from app.domain.commands.sanitize import validate_cli_block
from app.domain.devices import DeviceFamily
from app.network.drivers.base import elapsed_ms
from app.network.drivers.netmiko_cli import NetmikoCliDriver
from app.network.errors import detect_cli_error, map_exception, raise_on_cli_error
from app.network.parsers.version import parse_version
from app.network.ports import ConfigResult, DeviceFacts

log = get_logger(__name__)


class HuaweiCliDriver(NetmikoCliDriver):
    """Huawei VRP dialect over the shared Netmiko CLI transport."""

    # --- connection ----------------------------------------------------------------------
    def device_type(self) -> str:
        return "huawei_vrpv8" if self.params.family is DeviceFamily.CE else "huawei"

    def _after_connect(self) -> None:
        if self.params.requires_super and self.params.super_password is not None:
            self._enter_super()

    def _enter_super(self) -> None:
        """Raise the privilege level with 'super' (VRP-VERSION-DEPENDENT prompt text)."""
        if self.params.super_password is None:
            raise RuntimeError("invariant violated: self.params.super_password missing")
        out = self._conn.send_command_timing("super", strip_prompt=False, strip_command=False)
        if "assword" in out:
            out = self._conn.send_command_timing(
                self.params.super_password.get_secret_value(), strip_prompt=False
            )
        if detect_cli_error(out):
            raise DeviceAuthFailed("privilege escalation ('super') was rejected")

    # --- operations ----------------------------------------------------------------------
    def push_config(
        self, commands: list[str], *, save: bool = False, trial_seconds: int | None = None
    ) -> ConfigResult:
        lines = validate_cli_block(commands)
        conn = self._require()
        start = time.perf_counter()
        uses_commit = self.params.family.uses_commit
        try:
            output: str = conn.send_config_set(
                lines,
                exit_config_mode=not uses_commit,
                read_timeout=self.params.timeout,
                cmd_verify=True,  # wait for each echo + prompt (pattern based, no fixed sleeps)
            )
            error = detect_cli_error(output)
            if error:
                self._discard(uses_commit)
                raise CommandRejected(f"device rejected configuration: {error}")
            committed = False
            if uses_commit:
                output += self._commit(trial_seconds)
                committed = True
            saved = False
            if save and not uses_commit:
                output += conn.save_config()
                raise_on_cli_error("save", output)
                saved = True
        except DeviceError:
            raise
        except Exception as exc:
            raise map_exception(exc, self.params.host) from exc
        return ConfigResult(
            tuple(lines), output, committed, saved, elapsed_ms(start), self.security_warning
        )

    def _commit(self, trial_seconds: int | None) -> str:
        """CE two-stage commit. 'commit trial <s>' auto-rolls back unless confirmed."""
        command = f"commit trial {int(trial_seconds)}" if trial_seconds else "commit"
        out: str = self._conn.send_command_timing(command, strip_prompt=False)
        if detect_cli_error(out):
            self._discard(True)
            raise CommandRejected(f"commit failed: {detect_cli_error(out)}")
        out += self._conn.send_command_timing("return", strip_prompt=False)
        return out

    def _discard(self, uses_commit: bool) -> None:
        """Drop uncommitted candidate changes (CE: 'abort'), then leave system-view."""
        try:
            cmd = "abort" if uses_commit else "return"
            self._conn.send_command_timing(cmd, strip_prompt=False)
        except Exception as exc:
            log.warning("discard_failed", device_id=self.params.device_id, error=type(exc).__name__)

    def confirm_trial(self) -> str:
        """Confirm a pending 'commit trial' (CE) so it is not rolled back."""
        out: str = self._require().send_config_set(["commit"], exit_config_mode=True)
        raise_on_cli_error("commit", out)
        return out

    def save(self) -> str:
        """Persist running config (S/AR 'save'; CE configuration is persisted by commit)."""
        if self.params.family.uses_commit:
            return ""
        out: str = self._require().save_config()
        raise_on_cli_error("save", out)
        return out

    def arm_rollback_timer(self, minutes: int) -> str:
        """S/AR safety net: reboot to the saved config unless disarmed (VRP-VERSION-DEPENDENT).

        Callers must NOT save before disarming; CE uses 'commit trial' instead.
        """
        if self.params.family.uses_commit:
            return ""
        hours, mins = divmod(max(1, min(minutes, 60)), 60)
        conn = self._require()
        out: str = conn.send_command_timing(f"schedule reboot delay {hours:02d}:{mins:02d}")
        if "[Y/N]" in out or "continue" in out.lower():
            out += conn.send_command_timing("y")
        raise_on_cli_error("schedule reboot", out)
        return out

    def disarm_rollback_timer(self) -> str:
        if self.params.family.uses_commit:
            return ""
        conn = self._require()
        out: str = conn.send_command_timing("undo schedule reboot")
        if "[Y/N]" in out or "continue" in out.lower():
            out += conn.send_command_timing("y")
        raise_on_cli_error("undo schedule reboot", out)
        return out

    def capabilities(self) -> DeviceFacts:
        info = parse_version(self.run_show("display version").output)
        family = info.family if info.family is not DeviceFamily.UNKNOWN else self.params.family
        return DeviceFacts(
            family=family,
            vrp_version=info.vrp_version,
            software_version=info.software_version,
            model=info.model,
            uptime_seconds=info.uptime_seconds,
            capabilities={
                "cli": True,
                "commit_model": "two-stage" if family.uses_commit else "immediate",
                "transport": self.protocol.value,
            },
        )
