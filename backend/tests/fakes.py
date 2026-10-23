"""In-process fake driver backed by the CLI emulator (no sockets) for fast service/job tests."""

from __future__ import annotations

import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

from app.domain.commands.sanitize import validate_cli_block, validate_cli_line
from app.domain.devices import DeviceFamily, DeviceProtocol
from app.network.errors import detect_cli_error, raise_on_cli_error
from app.network.parsers import parse_version
from app.network.ports import CommandOutput, ConfigResult, ConnectionParams, DeviceFacts, HostKey
from tests.e2e.cli_emulator import HuaweiCliEmulator
from tests.e2e.mock_huawei import load_outputs

FAKE_KEY = HostKey(
    "ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTestsOnly0000000000000000000", "SHA256:fake"
)


class FakeLab:
    """Maps device mgmt IP -> emulator; registers itself as the SSH driver builder."""

    def __init__(self) -> None:
        self.devices: dict[str, HuaweiCliEmulator] = {}
        self.delay = 0.0
        self.down: set[str] = set()

    def add(self, ip: str, family: str = "S", **kw: Any) -> HuaweiCliEmulator:
        gen = "ce6881_v200r005" if family == "CE" else "s5735_v200r019"
        emu = HuaweiCliEmulator(f"SW-{ip.rsplit('.', 1)[-1]}", family, load_outputs(gen), **kw)
        self.devices[ip] = emu
        return emu

    def builder(self, params: ConnectionParams) -> FakeDriver:
        return FakeDriver(params, self)

    @staticmethod
    def host_key_fetcher() -> Callable[[str, int, float], HostKey]:
        return lambda host, port, timeout: FAKE_KEY


class FakeDriver:
    protocol = DeviceProtocol.SSH

    def __init__(self, params: ConnectionParams, lab: FakeLab) -> None:
        self.params = params
        self.lab = lab

    @property
    def emu(self) -> HuaweiCliEmulator:
        from app.core.exceptions import DeviceUnreachable

        if self.params.host in self.lab.down or self.params.host not in self.lab.devices:
            raise DeviceUnreachable(f"cannot reach {self.params.host}")
        return self.lab.devices[self.params.host]

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self, et: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.disconnect()

    def connect(self) -> None:
        _ = self.emu

    def disconnect(self) -> None:
        return None

    def run_show(self, command: str) -> CommandOutput:
        clean = validate_cli_line(command, allow_pipe=True)
        if self.lab.delay:
            time.sleep(self.lab.delay)
        out = self.emu.handle(clean)
        raise_on_cli_error(clean, out)
        return CommandOutput(clean, out.replace("\r\n", "\n"), 1)

    def push_config(
        self, commands: list[str], *, save: bool = False, trial_seconds: int | None = None
    ) -> ConfigResult:
        lines = validate_cli_block(commands)
        emu = self.emu
        out = emu.handle("system-view")
        for line in lines:
            out += emu.handle(line)
            if detect_cli_error(out):
                emu.handle("abort" if emu.family == "CE" else "return")
                raise_on_cli_error(line, out)
        committed = emu.family == "CE"
        if committed:
            out += emu.handle(f"commit trial {trial_seconds}" if trial_seconds else "commit")
        emu.handle("return")
        if save and not committed:
            out += self.save()
        return ConfigResult(tuple(lines), out, committed, save, 1)

    def save(self) -> str:
        return self.emu.handle("save") + self.emu.handle("y")

    def arm_rollback_timer(self, minutes: int) -> str:
        return self.emu.handle(f"schedule reboot delay 00:{minutes:02d}") + self.emu.handle("y")

    def disarm_rollback_timer(self) -> str:
        return self.emu.handle("undo schedule reboot") + self.emu.handle("y")

    def confirm_trial(self) -> str:
        return ""

    def capabilities(self) -> DeviceFacts:
        info = parse_version(self.run_show("display version").output)
        return DeviceFacts(
            info.family,
            info.vrp_version,
            info.software_version,
            info.model,
            capabilities={"cli": True},
        )

    def health(self) -> bool:
        return True


def install(app: Any, lab: FakeLab) -> None:
    services = app.state.services
    services.factory.register(DeviceProtocol.SSH, lab.builder)
    services.connector._fetch_key = lab.host_key_fetcher()


__all__ = ["DeviceFamily", "FakeLab", "install"]
