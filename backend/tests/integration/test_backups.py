"""Phase 9: backups (dedup, masking, encryption), change hooks, diff, drift, guarded restore."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.events import DomainEvent
from app.core.rbac import Role
from app.models.backup import Backup
from tests.fakes import FakeLab, install

SECRET = "$1a$SuperSecretHash$"


@pytest.fixture
def lab(app: Any) -> FakeLab:
    fake = FakeLab()
    install(app, fake)
    emu = fake.add("192.0.2.1")
    emu.extra_config = ["#", "aaa", f" local-user admin password irreversible-cipher {SECRET}"]
    return fake


async def _device(
    client: AsyncClient, headers: dict[str, str], *, vrp: str | None = "5.170"
) -> str:
    cred = await client.post(
        "/api/v1/credentials",
        json={"name": "fake", "kind": "cli", "username": "u", "password": "Fake-Passw0rd!"},
        headers=headers,
    )
    resp = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "SW-1",
            "mgmt_ip": "192.0.2.1",
            "family": "S",
            "vrp_version": vrp,
            "credential_id": cred.json()["id"],
        },
        headers=headers,
    )
    return str(resp.json()["id"])


async def _backup(
    client: AsyncClient, app: Any, headers: dict[str, str], dev: str
) -> dict[str, Any]:
    resp = await client.post(f"/api/v1/devices/{dev}/backup", headers=headers)
    assert resp.status_code == 202, resp.text
    await app.state.services.inline_dispatcher.drain()
    job = (await client.get(resp.headers["location"], headers=headers)).json()
    assert job["status"] == "SUCCESS", job
    out: dict[str, Any] = job["per_device_results"][0]["output"]
    return out


async def test_backup_masked_encrypted_and_deduplicated(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers)
    first = await _backup(client, app, headers, dev)
    assert first["created"] is True
    second = await _backup(client, app, headers, dev)
    assert second["created"] is False
    assert second["backup_id"] == first["backup_id"]
    detail = (await client.get(f"/api/v1/backups/{first['backup_id']}", headers=headers)).json()
    assert SECRET not in detail["content"]
    assert "REDACTED" in detail["content"]
    async with app.state.db.session() as s:
        row = (await s.execute(select(Backup))).scalars().one()
    assert SECRET not in (row.content_ciphertext or "")
    assert SECRET in app.state.services.backups.original(row)
    reader = await auth_headers(Role.READ_ONLY)
    assert (
        await client.get(f"/api/v1/backups/{first['backup_id']}", headers=reader)
    ).status_code == 403


async def test_change_creates_pre_and_post_backups_and_diff(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers)
    resp = await client.post(
        f"/api/v1/devices/{dev}/vlans", json={"vlans": [{"id": 300}]}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    listing = (await client.get(f"/api/v1/backups?device_id={dev}", headers=headers)).json()
    triggers = sorted(b["trigger"] for b in listing["items"])
    assert triggers == ["post-change", "pre-change"]
    pre = next(b for b in listing["items"] if b["trigger"] == "pre-change")
    post = next(b for b in listing["items"] if b["trigger"] == "post-change")
    diff = (
        await client.get(f"/api/v1/backups/{pre['id']}/diff/{post['id']}", headers=headers)
    ).json()
    assert "+vlan batch" in diff["diff"]
    assert "300" in diff["diff"]


async def test_pre_change_backup_failure_aborts_change(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers)
    lab.devices["192.0.2.1"].reject.add("display current-configuration")
    resp = await client.post(
        f"/api/v1/devices/{dev}/vlans", json={"vlans": [{"id": 301}]}, headers=headers
    )
    assert resp.status_code == 502
    assert 301 not in lab.devices["192.0.2.1"].vlans


async def test_drift_and_out_of_band_change_event(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    events: list[DomainEvent] = []

    async def capture(event: DomainEvent) -> None:
        events.append(event)

    app.state.services.events.subscribe("configuration_change", capture)
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers)
    first = await _backup(client, app, headers, dev)
    known = await client.post(f"/api/v1/backups/{first['backup_id']}/known-good", headers=headers)
    assert known.json()["known_good"] is True
    assert (await client.get(f"/api/v1/devices/{dev}/drift", headers=headers)).json()[
        "drifted"
    ] is False
    lab.devices["192.0.2.1"].vlans[777] = ""  # someone changed the switch by hand
    report = (await client.get(f"/api/v1/devices/{dev}/drift", headers=headers)).json()
    assert report["drifted"] is True
    assert "777" in report["diff"]
    await _backup(client, app, headers, dev)
    assert [e.name for e in events] == ["configuration_change"]


async def test_guarded_restore(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, admin)
    baseline = await _backup(client, app, admin, dev)
    emu = lab.devices["192.0.2.1"]
    emu.vlans[555] = ""
    url = f"/api/v1/backups/{baseline['backup_id']}/restore"
    operator = await auth_headers(Role.OPERATOR)
    assert (await client.post(url, json={"dry_run": True}, headers=operator)).status_code == 403

    dry = await client.post(url, json={"dry_run": True}, headers=admin)
    assert dry.status_code == 200, dry.text
    body = dry.json()
    assert body["category"] == "DANGEROUS"
    assert "undo vlan batch 555" in body["lines"]
    assert all(SECRET not in line for line in body["lines"])
    assert "555" in body["diff"]
    no_token = await client.post(url, json={"confirm": True, "reason": "rollback"}, headers=admin)
    assert no_token.status_code == 409
    dry = await client.post(url, json={"dry_run": True}, headers=admin)
    resp = await client.post(
        url,
        json={
            "confirm": True,
            "confirmation_token": dry.json()["confirmation_token"],
            "reason": "rollback",
        },
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "applied"
    assert 555 not in emu.vlans
    assert any(c.startswith("schedule reboot delay") for c in emu.received)  # rollback timer armed
    assert "undo schedule reboot" in emu.received


async def test_restore_refuses_version_mismatch(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, admin)
    baseline = await _backup(client, app, admin, dev)
    await client.patch(f"/api/v1/devices/{dev}", json={"vrp_version": "5.180"}, headers=admin)
    resp = await client.post(
        f"/api/v1/backups/{baseline['backup_id']}/restore", json={"dry_run": True}, headers=admin
    )
    assert resp.status_code == 409
    assert "refusing restore" in resp.json()["message"]
