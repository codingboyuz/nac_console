"""Phase 17 e2e: TP-Link JetStream over real Netmiko 'tplink_jetstream' against the mock."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.rbac import Role
from tests.e2e.cisco_emulator import CiscoIosEmulator, CiscoState, load_tplink_outputs
from tests.e2e.mock_huawei import MockHuaweiSSH

pytestmark = pytest.mark.e2e


@pytest.fixture
def tplink() -> Iterator[tuple[MockHuaweiSSH, CiscoState]]:
    state = CiscoState(hostname="ACC-TP-01", outputs=load_tplink_outputs())
    state.interfaces = {
        "GigabitEthernet1/0/1": [
            "switchport general allowed vlan 10 untagged",
            "switchport pvid 10",
        ],
        "GigabitEthernet1/0/5": ["switchport general allowed vlan 1 untagged", "switchport pvid 1"],
        "GigabitEthernet1/0/25": [
            "switchport general allowed vlan 10,20,100 tagged",
            "switchport pvid 100",
        ],
    }
    with MockHuaweiSSH(lambda: CiscoIosEmulator(state, tplink=True)) as srv:
        yield srv, state


async def _register(client: AsyncClient, headers: dict[str, str], srv: MockHuaweiSSH) -> str:
    cred = await client.post(
        "/api/v1/credentials",
        json={"name": "tp", "kind": "cli", "username": srv.username, "password": srv.password},
        headers=headers,
    )
    assert cred.status_code == 201, cred.text
    dev = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "ACC-TP-01",
            "mgmt_ip": "127.0.0.1",
            "port": srv.port,
            "platform": "tplink_jetstream",
            "credential_id": cred.json()["id"],
            "timeout": 10,
            "mgmt_vlan": 100,
            "uplink_interfaces": ["Gi1/0/25"],
        },
        headers=headers,
    )
    assert dev.status_code == 201, dev.text
    assert dev.json()["vendor"] == "TP-Link"
    return str(dev.json()["id"])


async def test_facts_vlan_and_general_port(
    client: AsyncClient, auth_headers: Any, tplink: Any
) -> None:
    srv, state = tplink
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, srv)
    facts = await client.post(f"/api/v1/devices/{dev_id}/test-connection", headers=headers)
    assert facts.status_code == 200, facts.text
    assert facts.json()["model"] == "T2600G-28TS"
    created = await client.post(
        f"/api/v1/devices/{dev_id}/vlans",
        json={"vlans": [{"id": 300, "name": "LAB"}], "save": True},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "applied"
    assert state.vlans[300] == "LAB"
    assert state.saved == 1
    port = await client.put(
        f"/api/v1/devices/{dev_id}/interfaces/GigabitEthernet1/0/5/vlan",
        json={"mode": "access", "access_vlan": 300},
        headers=headers,
    )
    assert port.status_code == 200, port.text
    assert "interface gigabitEthernet 1/0/5" in port.json()["lines"]
    assert "switchport pvid 300" in state.interfaces["GigabitEthernet1/0/5"]


async def test_lockout_override_uses_reboot_schedule(
    client: AsyncClient, auth_headers: Any, tplink: Any
) -> None:
    srv, state = tplink
    admin = await auth_headers(Role.ADMIN)
    dev_id = await _register(client, admin, srv)
    url = f"/api/v1/devices/{dev_id}/interfaces/GigabitEthernet1/0/25/admin-state"
    blocked = await client.put(url, json={"enabled": False, "dry_run": True}, headers=admin)
    assert blocked.status_code == 409, blocked.text
    body = {"enabled": False, "dry_run": True, "override_lockout": True, "rollback_minutes": 3}
    preview = await client.put(url, json=body, headers=admin)
    assert preview.status_code == 200, preview.text
    body |= {
        "dry_run": False,
        "confirm": True,
        "confirmation_token": preview.json()["confirmation_token"],
        "reason": "CHG-7",
    }
    applied = await client.put(url, json=body, headers=admin)
    assert applied.status_code == 200, applied.text
    assert "reboot-schedule in 3" in state.received
    assert "reboot-schedule cancel" in state.received
    assert state.received.index("reboot-schedule in 3") < state.received.index("shutdown")
