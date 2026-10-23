"""Phase 6: guarded L2 changes against the emulated device (dry-run, verify, lockout guard)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.rbac import Role
from tests.e2e.cli_emulator import HuaweiCliEmulator
from tests.e2e.mock_huawei import MockHuaweiSSH, load_outputs

pytestmark = pytest.mark.e2e


class _Lab:
    def __init__(self) -> None:
        self.emulator = HuaweiCliEmulator("SW-01", "S", load_outputs("s5735_v200r019"))
        self.emulator.interfaces["GigabitEthernet0/0/3"] = [
            "port link-type access",
            "port default vlan 10",
        ]
        self.emulator.interfaces["XGigabitEthernet0/0/1"] = [
            "port link-type trunk",
            "port trunk allow-pass vlan 10 20 100",
        ]

    def factory(self) -> HuaweiCliEmulator:
        return self.emulator  # one shared device state across sessions


@pytest.fixture
def lab() -> Iterator[tuple[_Lab, MockHuaweiSSH]]:
    state = _Lab()
    with MockHuaweiSSH(state.factory) as server:
        yield state, server


async def _device(client: AsyncClient, headers: dict[str, str], server: MockHuaweiSSH) -> str:
    cred = await client.post(
        "/api/v1/credentials",
        json={
            "name": "lab",
            "kind": "cli",
            "username": server.username,
            "password": server.password,
        },
        headers=headers,
    )
    dev = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "SW-01",
            "mgmt_ip": "127.0.0.1",
            "port": server.port,
            "family": "S",
            "credential_id": cred.json()["id"],
            "mgmt_interface": "Vlanif100",
            "mgmt_vlan": 100,
            "uplink_interfaces": ["XGE0/0/1"],
            "timeout": 10,
        },
        headers=headers,
    )
    assert dev.status_code == 201, dev.text
    return str(dev.json()["id"])


async def test_vlan_create_dry_run_apply_and_idempotency(
    client: AsyncClient, auth_headers: Any, lab: tuple[_Lab, MockHuaweiSSH]
) -> None:
    state, server = lab
    headers = await auth_headers(Role.OPERATOR)
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, admin, server)
    body = {"vlans": [{"id": 300, "name": "Lab"}], "dry_run": True}
    dry = await client.post(f"/api/v1/devices/{dev}/vlans", json=body, headers=headers)
    assert dry.status_code == 200, dry.text
    assert dry.json()["status"] == "dry_run"
    assert dry.json()["lines"] == ["vlan batch 300", "vlan 300", "name Lab", "quit"]
    assert 300 not in state.emulator.vlans

    body["dry_run"] = False
    resp = await client.post(f"/api/v1/devices/{dev}/vlans", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "applied"
    assert state.emulator.vlans[300] == "Lab"
    assert state.emulator.saved

    again = await client.post(f"/api/v1/devices/{dev}/vlans", json=body, headers=headers)
    assert again.json()["status"] == "noop"
    listing = await client.get(f"/api/v1/devices/{dev}/vlans", headers=headers)
    assert 300 in [v["vlan_id"] for v in listing.json()]


async def test_access_port_vlan_change(
    client: AsyncClient, auth_headers: Any, lab: tuple[_Lab, MockHuaweiSSH]
) -> None:
    state, server = lab
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers, server)
    resp = await client.put(
        f"/api/v1/devices/{dev}/interfaces/GE0/0/3/vlan".replace("GE0/0/3", "GE0%2F0%2F3"),
        json={"mode": "access", "access_vlan": 20},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert "port default vlan 20" in state.emulator.interfaces["GigabitEthernet0/0/3"]


async def test_uplink_shutdown_blocked_by_lockout_guard(
    client: AsyncClient, app: Any, auth_headers: Any, lab: tuple[_Lab, MockHuaweiSSH]
) -> None:
    state, server = lab
    netadmin = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, netadmin, server)
    url = f"/api/v1/devices/{dev}/interfaces/XGE0%2F0%2F1/admin-state"
    resp = await client.put(url, json={"enabled": False, "dry_run": True}, headers=netadmin)
    assert resp.status_code == 409
    assert resp.json()["code"] == "lockout_risk"
    # Even ADMIN needs an explicit override + rollback timer.
    admin = await auth_headers(Role.ADMIN)
    resp = await client.put(url, json={"enabled": False, "override_lockout": True}, headers=admin)
    assert resp.status_code == 409
    dry = await client.put(
        url,
        json={"enabled": False, "override_lockout": True, "rollback_minutes": 5, "dry_run": True},
        headers=admin,
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["category"] == "DANGEROUS"
    token = dry.json()["confirmation_token"]
    resp = await client.put(
        url,
        json={
            "enabled": False,
            "override_lockout": True,
            "rollback_minutes": 5,
            "confirm": True,
            "confirmation_token": token,
            "reason": "planned uplink migration",
        },
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    received = state.emulator.received
    assert any(c.startswith("schedule reboot delay 00:05") for c in received)
    assert "undo schedule reboot" in received
    assert "shutdown" in state.emulator.interfaces["XGigabitEthernet0/0/1"]


async def test_management_vlan_delete_refused(
    client: AsyncClient, auth_headers: Any, lab: tuple[_Lab, MockHuaweiSSH]
) -> None:
    _, server = lab
    headers = await auth_headers(Role.ADMIN)
    dev = await _device(client, headers, server)
    resp = await client.post(
        f"/api/v1/devices/{dev}/vlans/delete", json={"vlans": "100"}, headers=headers
    )
    assert resp.status_code == 422


async def test_failed_verification_rolls_back(
    client: AsyncClient, app: Any, auth_headers: Any, lab: tuple[_Lab, MockHuaweiSSH]
) -> None:
    state, server = lab
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers, server)
    original_apply = state.emulator._apply

    def lossy_apply(view: str, cmd: str) -> None:  # device silently ignores the new VLAN
        if cmd != "vlan batch 400":
            original_apply(view, cmd)

    state.emulator._apply = lossy_apply  # type: ignore[method-assign]
    resp = await client.post(
        f"/api/v1/devices/{dev}/vlans", json={"vlans": [{"id": 400}]}, headers=headers
    )
    assert resp.status_code == 502
    assert "rolled back" in resp.json()["message"]
    assert "undo vlan batch 400" in state.emulator.received


async def test_mac_block_notice_and_rbac(
    client: AsyncClient, auth_headers: Any, lab: tuple[_Lab, MockHuaweiSSH]
) -> None:
    state, server = lab
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, admin, server)
    body = {"device_id": dev, "mac": "00:e0:fc:99:88:77", "vlan": 10}
    reader = await auth_headers(Role.READ_ONLY)
    assert (
        await client.post("/api/v1/mac-addresses/block", json=body, headers=reader)
    ).status_code == 403
    resp = await client.post("/api/v1/mac-addresses/block", json=body, headers=admin)
    assert resp.status_code == 200, resp.text
    assert "not a security control" in resp.json()["notice"]
    assert "mac-address blackhole 00e0-fc99-8877 vlan 10" in state.emulator.received
    collected = await client.post(f"/api/v1/mac-addresses/collect/{dev}", headers=admin)
    assert collected.json()["entries"] > 0
    found = await client.get("/api/v1/mac-addresses", headers=admin)
    assert found.status_code == 200
    assert found.json()
