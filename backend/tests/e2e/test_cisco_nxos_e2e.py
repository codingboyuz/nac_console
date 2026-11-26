"""Phase 15 e2e: Cisco NX-OS over real Netmiko/SSH against the stateful mock, via the API."""

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
def nexus() -> Iterator[tuple[MockHuaweiSSH, CiscoState]]:
    state = CiscoState(hostname="DC-LEAF-01", outputs=load_outputs("n9k_nxos93"))
    state.interfaces = {
        "Ethernet1/1": ["description server-1", "switchport access vlan 10"],
        "Ethernet1/49": [
            "description UPLINK-SPINE",
            "switchport mode trunk",
            "switchport trunk allowed vlan 10,100",
        ],
        "Vlan100": ["ip address 192.0.2.20/24"],
    }
    with MockHuaweiSSH(lambda: CiscoIosEmulator(state, nxos=True)) as srv:
        yield srv, state


async def _register(client: AsyncClient, headers: dict[str, str], srv: MockHuaweiSSH) -> str:
    cred = await client.post(
        "/api/v1/credentials",
        json={"name": "nexus", "kind": "cli", "username": srv.username, "password": srv.password},
        headers=headers,
    )
    assert cred.status_code == 201, cred.text
    dev = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "DC-LEAF-01",
            "mgmt_ip": "127.0.0.1",
            "port": srv.port,
            "platform": "cisco_nxos",
            "credential_id": cred.json()["id"],
            "timeout": 10,
            "mgmt_interface": "Vlan100",
            "mgmt_vlan": 100,
            "uplink_interfaces": ["Ethernet1/49"],
        },
        headers=headers,
    )
    assert dev.status_code == 201, dev.text
    return str(dev.json()["id"])


async def test_facts_reads_and_vlan_change(
    client: AsyncClient, auth_headers: Any, nexus: Any
) -> None:
    srv, state = nexus
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, srv)
    facts = await client.post(f"/api/v1/devices/{dev_id}/test-connection", headers=headers)
    assert facts.status_code == 200, facts.text
    assert facts.json()["model"] == "C93180YC-EX"
    assert "enable" not in state.received
    ifaces = await client.get(f"/api/v1/devices/{dev_id}/interfaces?refresh=true", headers=headers)
    assert ifaces.status_code == 200, ifaces.text
    assert "Ethernet1/49" in {i["name"] for i in ifaces.json()}
    created = await client.post(
        f"/api/v1/devices/{dev_id}/vlans",
        json={"vlans": [{"id": 300, "name": "LAB"}], "save": True},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "applied"
    assert state.vlans[300] == "LAB"
    assert "copy running-config startup-config" in state.received


async def test_lockout_override_refused_without_timer(
    client: AsyncClient, auth_headers: Any, nexus: Any
) -> None:
    srv, state = nexus
    admin = await auth_headers(Role.ADMIN)
    dev_id = await _register(client, admin, srv)
    url = f"/api/v1/devices/{dev_id}/interfaces/Ethernet1/49/admin-state"
    body = {"enabled": False, "dry_run": True, "override_lockout": True, "rollback_minutes": 5}
    resp = await client.put(url, json=body, headers=admin)
    assert resp.status_code == 409, resp.text
    assert "rollback timer" in resp.json()["message"]
    assert "shutdown" not in state.interfaces["Ethernet1/49"]
