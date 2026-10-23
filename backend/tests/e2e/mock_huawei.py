"""Fake Huawei VRP devices for end-to-end tests: a CLI emulator served over SSH and Telnet.

The emulator replays fixture transcripts for display commands, tracks the CLI view
(user / system / interface / vlan), emulates S-series 'save' and CE-series two-stage 'commit',
and returns genuine VRP error strings for unknown or rejected commands.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path

import asyncssh

from tests.e2e.cli_emulator import UNRECOGNIZED, WRONG_PARAMETER, HuaweiCliEmulator

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "huawei"


def load_outputs(generation: str) -> dict[str, str]:
    """Map 'display version' -> fixture text for every display_*.txt of a generation."""
    outputs: dict[str, str] = {}
    folder = FIXTURES / generation
    for path in folder.glob("display_*.txt"):
        if path.stem.endswith("_paged"):
            continue
        command = path.stem.replace("_", " ")
        command = command.replace("mac address", "mac-address").replace(
            "ip routing table", "ip routing-table"
        )
        command = command.replace("aging time", "aging-time").replace("cpu usage", "cpu-usage")
        command = command.replace("memory usage", "memory-usage").replace("user bind", "user-bind")
        outputs[command] = path.read_text(encoding="utf-8")
    return outputs


class _SSHServer(asyncssh.SSHServer):
    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == self._username and password == self._password


class MockHuaweiSSH:
    """Run the emulator on 127.0.0.1:<random port> in a background event loop thread."""

    def __init__(
        self,
        emulator_factory: Callable[[], HuaweiCliEmulator],
        *,
        username: str = "netops",
        password: str = "Fake-Passw0rd!",
    ) -> None:
        self.emulator_factory = emulator_factory
        self.username = username
        self.password = password
        self.sessions: list[HuaweiCliEmulator] = []
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._server: asyncssh.SSHAcceptor | None = None
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)

    @property
    def public_key_b64(self) -> str:
        return self.host_key.export_public_key("openssh").decode().split()[1]

    async def _process(self, process: asyncssh.SSHServerProcess[str]) -> None:
        emulator = self.emulator_factory()
        self.sessions.append(emulator)
        process.stdout.write(emulator.banner() + emulator.prompt())
        try:
            while True:
                line = await process.stdin.readline()
                if not line:
                    break
                out = emulator.handle(line)
                if line.strip() == "quit" and emulator.view == "user" and not out:
                    break
                process.stdout.write(out + "\r\n" + emulator.prompt())
        except (asyncssh.BreakReceived, asyncssh.TerminalSizeChanged, ConnectionError):
            pass
        finally:
            process.exit(0)

    async def _start(self) -> None:
        self._server = await asyncssh.create_server(
            lambda: _SSHServer(self.username, self.password),
            "127.0.0.1",
            0,
            server_host_keys=[self.host_key],
            process_factory=self._process,
            line_editor=True,
            encoding="utf-8",
        )
        self.port = self._server.sockets[0].getsockname()[1]

    def __enter__(self) -> MockHuaweiSSH:
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._start(), self._loop).result(timeout=10)
        return self

    def __exit__(self, *exc: object) -> None:
        async def stop() -> None:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()

        asyncio.run_coroutine_threadsafe(stop(), self._loop).result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


class MockHuaweiTelnet:
    """Minimal Telnet login + CLI loop (IAC negotiation bytes are ignored)."""

    def __init__(
        self,
        emulator_factory: Callable[[], HuaweiCliEmulator],
        *,
        username: str = "netops",
        password: str = "Fake-Passw0rd!",
    ) -> None:
        self.emulator_factory = emulator_factory
        self.username = username
        self.password = password
        self.sessions: list[HuaweiCliEmulator] = []
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._server: asyncio.Server | None = None
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)

    @staticmethod
    async def _readline(reader: asyncio.StreamReader) -> str | None:
        data = bytearray()
        while True:
            chunk = await reader.read(1)
            if not chunk:
                return None
            if chunk[0] == 0xFF:  # IAC: skip command + option byte
                await reader.read(2)
                continue
            if chunk in {b"\r", b"\n"}:
                if not data and chunk == b"\n":
                    continue  # second half of a CRLF terminator
                return data.decode(errors="replace")
            if chunk != b"\x00":
                data += chunk

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        emulator = self.emulator_factory()
        self.sessions.append(emulator)
        try:
            writer.write(b"\r\nLogin authentication\r\n\r\n\r\nUsername:")
            user = await self._readline(reader)
            writer.write(b"\r\nPassword:")
            pwd = await self._readline(reader)
            if user != self.username or pwd != self.password:
                writer.write(b"\r\nError: Local authentication is rejected.\r\n")
                return
            writer.write((emulator.banner() + emulator.prompt()).encode())
            while (line := await self._readline(reader)) is not None:
                writer.write((line + emulator.handle(line) + "\r\n" + emulator.prompt()).encode())
                await writer.drain()
        finally:
            writer.close()

    async def _start(self) -> None:
        self._server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    def __enter__(self) -> MockHuaweiTelnet:
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._start(), self._loop).result(timeout=10)
        return self

    def __exit__(self, *exc: object) -> None:
        async def stop() -> None:
            if self._server is not None:
                self._server.close()

        asyncio.run_coroutine_threadsafe(stop(), self._loop).result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


__all__ = [
    "UNRECOGNIZED",
    "WRONG_PARAMETER",
    "HuaweiCliEmulator",
    "MockHuaweiSSH",
    "MockHuaweiTelnet",
    "load_outputs",
]
