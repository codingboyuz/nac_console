"""Phase 12: Typer CLI goes through the service layer (RBAC, classifier, audit) as its user."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from typer.testing import CliRunner

from app.cli.main import app as cli
from app.cli.runtime import CliRuntime
from app.core.config import Settings
from app.core.database import Database
from app.services.container import ServiceContainer
from tests.conftest import _truncate_all, make_settings
from tests.fakes import FakeLab, install

runner = CliRunner()


class _App:
    def __init__(self, services: ServiceContainer) -> None:
        self.state = type("S", (), {"services": services})()


@pytest.fixture
def cli_env(migrated_database: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLab]:
    settings = make_settings(migrated_database, cli_username="ops-cli")
    server = FakeServer()
    lab = FakeLab()
    lab.add("192.0.2.1")

    @asynccontextmanager
    async def factory(_: Settings) -> AsyncIterator[ServiceContainer]:
        db = Database(settings.database_url)
        redis = FakeAsyncRedis(server=server, decode_responses=True)
        services = ServiceContainer.build(settings, db, redis, dispatch="inline")
        install(_App(services), lab)
        try:
            yield services
        finally:
            await services.aclose()
            await db.dispose()

    monkeypatch.setattr(CliRuntime, "services_factory", staticmethod(factory))
    monkeypatch.setattr("app.cli.runtime.get_settings", lambda: settings)
    yield lab
    import asyncio

    async def cleanup() -> None:
        db = Database(settings.database_url)
        await _truncate_all(db)
        await db.dispose()

    asyncio.run(cleanup())


def _ok(args: list[str], input_text: str | None = None) -> str:
    result = runner.invoke(cli, args, input=input_text)
    assert result.exit_code == 0, result.output
    return result.stdout


def test_cli_end_to_end(cli_env: FakeLab, tmp_path: Path) -> None:
    out = _ok(
        ["user", "create", "ops-cli", "--role", "NETWORK_ADMIN", "--service-account", "--json"],
        input_text="Str0ng!Passw0rd\nStr0ng!Passw0rd\n",
    )
    assert json.loads(out[out.index("{") :])["role"] == "NETWORK_ADMIN"  # skip password prompts

    async def seed(s: ServiceContainer) -> str:
        from pydantic import SecretStr

        from app.schemas.devices import CredentialCreate, DeviceCreate

        principal = await CliRuntime().principal(s)
        cred = await s.credentials.create(
            CredentialCreate(
                name="c", kind="cli", username="u", password=SecretStr("Fake-Passw0rd!")
            )
        )
        dev = await s.devices.create(
            principal,
            DeviceCreate(hostname="SW-1", mgmt_ip="192.0.2.1", family="S", credential_id=cred.id),
        )
        return str(dev.id)

    device_id = CliRuntime().run(seed)
    listing = json.loads(_ok(["device", "list", "--json"]))
    assert listing[0]["hostname"] == "SW-1"

    show = json.loads(_ok(["device", "command", device_id, "display version", "--json"]))
    assert show["category"] == "SAFE"
    assert "VRP" in show["output"]

    blocked = runner.invoke(cli, ["device", "command", device_id, "reset arp dynamic"])
    assert blocked.exit_code == 2
    assert "confirmation_required" in blocked.output
    dry = json.loads(
        _ok(["device", "command", device_id, "reset arp dynamic", "--dry-run", "--json"])
    )
    assert dry["category"] == "DANGEROUS"
    token = dry["confirmation_token"]
    run = runner.invoke(
        cli,
        [
            "device",
            "command",
            device_id,
            "reset arp dynamic",
            "--confirm",
            token,
            "--reason",
            "lab cleanup",
        ],
    )
    # The emulator rejects 'reset' (unknown in user view) - proves it reached the device only after the gate.
    assert "command_rejected" in run.output

    vlan = json.loads(_ok(["vlan", "create", device_id, "321", "--name", "Cli", "--json"]))
    assert vlan["status"] == "applied"
    assert cli_env.devices["192.0.2.1"].vlans[321] == "Cli"
    backup = json.loads(_ok(["device", "backup", device_id, "--json"]))
    assert backup["status"] == "SUCCESS"

    target = tmp_path / "openapi.json"
    _ok(["openapi", "export", "--output", str(target)])
    spec: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    assert spec["openapi"].startswith("3.1")
    assert "/api/v1/jobs" in spec["paths"]


def test_cli_user_must_exist(cli_env: FakeLab) -> None:
    result = runner.invoke(cli, ["device", "list"])
    assert result.exit_code == 2
    assert "unauthorized" in result.output
