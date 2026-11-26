"""Phase 16 e2e: Cisco SMB (CBS350) over real Netmiko 'cisco_s300' against the mock, via the API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.rbac import Role
from tests.e2e.cisco_emulator import CiscoIosEmulator, CiscoState, load_outputs
from tests.e2e.mock_huawei import MockHuaweiSSH

pytestmark = pytest.mark.e2e


@pytest.fixture
def cbs() -> Iterator[tuple[MockHuaweiSSH, CiscoState]]:
    state = CiscoState(hostname="ACC-CBS-01", outputs=load_outputs("cbs350_3x"))
    state.interfaces = {
        "GigabitEthernet1/0/1": ["description PC-101", "switchport access vlan 10"],
        "GigabitEthernet1/0/5": [],
        "TenGigabitEthernet1/0/1": ["switchport mode trunk", "switchport trunk native vlan 100"],
    }
    with MockHuaweiSSH(lambda: CiscoIosEmulator(state, smb=True)) as srv:
        yield srv, state


async def _register(client: AsyncClient, headers: dict[str, str], srv: MockHuaweiSSH) -> str:
    cred = await client.post(
        "/api/v1/credentials",
        json={"name": "cbs", "kind": "cli", "username": srv.username, "password": srv.password},
        headers=headers,
    )
    assert cred.status_code == 201, cred.text
    dev = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "ACC-CBS-01",
            "mgmt_ip": "127.0.0.1",
            "port": srv.port,
            "platform": "cisco_smb",
            "credential_id": cred.json()["id"],
            "timeout": 10,
            "mgmt_vlan": 100,
            "uplink_interfaces": ["te1/0/1"],
        },
        headers=headers,
    )
    assert dev.status_code == 201, dev.text
    return str(dev.json()["id"])


async def test_facts_vlan_port_and_save(client: AsyncClient, auth_headers: Any, cbs: Any) -> None:
    srv, state = cbs
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, srv)
    facts = await client.post(f"/api/v1/devices/{dev_id}/test-connection", headers=headers)
    assert facts.status_code == 200, facts.text
    assert facts.json()["model"] == "CBS350-48P-4X"
    assert facts.json()["vrp_version"] == "3.3.0.16"
    assert "terminal datadump" in state.received

    created = await client.post(
        f"/api/v1/devices/{dev_id}/vlans",
        json={"vlans": [{"id": 300, "name": "LAB"}], "save": True},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["lines"][:3] == ["vlan database", "vlan 300", "exit"]
    assert created.json()["status"] == "applied"
    assert state.vlans[300] == "LAB"
    assert state.saved == 1  # 'write memory' + confirmation answered

    port = await client.put(
        f"/api/v1/devices/{dev_id}/interfaces/GigabitEthernet1/0/5/vlan",
        json={"mode": "access", "access_vlan": 300},
        headers=headers,
    )
    assert port.status_code == 200, port.text
    assert "switchport access vlan 300" in state.interfaces["GigabitEthernet1/0/5"]


async def test_unsupported_features_are_clear(
    client: AsyncClient, auth_headers: Any, cbs: Any
) -> None:
    srv, _ = cbs
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, srv)
    ospf = await client.get(f"/api/v1/devices/{dev_id}/routing/ospf/neighbors", headers=headers)
    assert ospf.status_code == 422, ospf.text
    admin = await auth_headers(Role.ADMIN)
    url = f"/api/v1/devices/{dev_id}/interfaces/TenGigabitEthernet1/0/1/admin-state"
    body = {"enabled": False, "dry_run": True, "override_lockout": True, "rollback_minutes": 5}
    blocked = await client.put(url, json=body, headers=admin)
    assert blocked.status_code == 409, blocked.text
