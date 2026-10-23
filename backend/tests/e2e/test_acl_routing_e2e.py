"""Phase 7: ACL deploys/gating and routing capability checks against the emulator."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.rbac import Role
from tests.e2e.cli_emulator import HuaweiCliEmulator
from tests.e2e.mock_huawei import MockHuaweiSSH, load_outputs

pytestmark = pytest.mark.e2e


@pytest.fixture
def lab() -> Iterator[tuple[HuaweiCliEmulator, MockHuaweiSSH]]:
    outputs = load_outputs("s5735_v200r019")
    outputs["display ip routing-table protocol static"] = (
        "Route Flags: R - relay\nPublic routing table : Static"
    )
    outputs["display ospf brief"] = "OSPF Process 1 with Router ID 10.0.0.1"
    outputs["display rip"] = "Public VPN-instance\nRIP process : 1"
    outputs["display vrrp brief"] = "Total:0     Master:0     Backup:0     Non-active:0"
    outputs["display route-policy"] = "Route-policy : RP"
    emulator = HuaweiCliEmulator("SW-01", "S", outputs, vty_acl="2000")
    emulator.acls["2000"] = {5: "rule 5 permit source 10.0.0.0 0.0.0.255"}
    with MockHuaweiSSH(lambda: emulator) as server:
        yield emulator, server


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
            "uplink_interfaces": ["XGE0/0/1"],
            "timeout": 10,
        },
        headers=headers,
    )
    assert dev.status_code == 201, dev.text
    return str(dev.json()["id"])


async def test_acl_deploy_rule_crud_and_apply(
    client: AsyncClient, auth_headers: Any, lab: tuple[HuaweiCliEmulator, MockHuaweiSSH]
) -> None:
    emu, server = lab
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, admin, server)
    body = {
        "kind": "advanced",
        "number": 3000,
        "rules": [
            {
                "action": "permit",
                "protocol": "tcp",
                "destination": "192.0.2.10/32",
                "destination_port": {"op": "eq", "start": 443},
            },
            {"action": "deny", "protocol": "ip", "source": "10.9.0.0/16"},
        ],
    }
    resp = await client.put(f"/api/v1/devices/{dev}/acls", json=body, headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "applied"
    assert emu.acls["3000"][5].startswith("rule 5 permit tcp")
    stored = (await client.get(f"/api/v1/devices/{dev}/acls", headers=admin)).json()
    assert stored[0]["identifier"] == "3000"
    assert len(stored[0]["rules"]) == 2

    add = await client.post(
        f"/api/v1/devices/{dev}/acls/3000/rules",
        json={
            "rule": {
                "action": "permit",
                "protocol": "udp",
                "destination_port": {"op": "eq", "start": 53},
            }
        },
        headers=admin,
    )
    assert add.status_code == 200, add.text
    assert 15 in emu.acls["3000"]
    rm = await client.post(
        f"/api/v1/devices/{dev}/acls/3000/rules/10/delete", json={}, headers=admin
    )
    assert rm.status_code == 200
    assert 10 not in emu.acls["3000"]

    applied = await client.post(
        f"/api/v1/devices/{dev}/acls/3000/apply",
        json={"interface": "GE0/0/5", "direction": "inbound"},
        headers=admin,
    )
    assert applied.status_code == 200, applied.text
    assert "traffic-filter inbound acl 3000" in emu.interfaces["GigabitEthernet0/0/5"]
    live = (await client.get(f"/api/v1/devices/{dev}/acls/live", headers=admin)).json()
    assert {a["number"] for a in live} >= {2000, 3000}


async def test_deny_any_needs_confirmation(
    client: AsyncClient, auth_headers: Any, lab: tuple[HuaweiCliEmulator, MockHuaweiSSH]
) -> None:
    _, server = lab
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, admin, server)
    body: dict[str, Any] = {
        "kind": "advanced",
        "number": 3100,
        "rules": [{"action": "deny", "protocol": "ip"}],
    }
    resp = await client.put(f"/api/v1/devices/{dev}/acls", json=body, headers=admin)
    assert resp.status_code == 409
    assert resp.json()["code"] == "confirmation_required" or "reason" in resp.json()["message"]
    operator = await auth_headers(Role.OPERATOR)
    assert (
        await client.put(f"/api/v1/devices/{dev}/acls", json=body, headers=operator)
    ).status_code == 403


async def test_vty_acl_change_that_denies_controller_is_lockout(
    client: AsyncClient, auth_headers: Any, lab: tuple[HuaweiCliEmulator, MockHuaweiSSH]
) -> None:
    _, server = lab
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, admin, server)
    body = {
        "kind": "basic",
        "number": 2000,
        "rules": [{"rule_id": 5, "action": "deny", "source": "10.0.0.0/24"}],
        "dry_run": True,
    }
    resp = await client.put(f"/api/v1/devices/{dev}/acls", json=body, headers=admin)
    assert resp.status_code == 409
    assert resp.json()["code"] == "lockout_risk"


async def test_routing_capabilities_and_static_route(
    client: AsyncClient, auth_headers: Any, lab: tuple[HuaweiCliEmulator, MockHuaweiSSH]
) -> None:
    emu, server = lab
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, admin, server)
    caps = (await client.get(f"/api/v1/devices/{dev}/routing/capabilities", headers=admin)).json()
    assert caps["ospf"] is True
    assert caps["bgp"] is False
    bgp = await client.post(
        f"/api/v1/devices/{dev}/routing/bgp",
        json={"asn": 65001, "peers": [{"address": "192.0.2.2", "remote_as": 65002}]},
        headers=admin,
    )
    assert bgp.status_code == 422
    assert bgp.json()["code"] == "unsupported_capability"

    route = {"prefix": "10.50.0.0/16", "next_hop": "192.0.2.1"}
    resp = await client.post(f"/api/v1/devices/{dev}/routing/static", json=route, headers=admin)
    assert (
        resp.status_code == 502
    )  # route not active afterwards -> verification failed -> rolled back
    assert "undo ip route-static 10.50.0.0 255.255.0.0 192.0.2.1" in emu.received
    resp = await client.post(
        f"/api/v1/devices/{dev}/routing/static", json={**route, "verify": False}, headers=admin
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "applied"
    again = await client.post(
        f"/api/v1/devices/{dev}/routing/static", json={**route, "verify": False}, headers=admin
    )
    assert again.json()["status"] == "noop"
    ospf = await client.post(
        f"/api/v1/devices/{dev}/routing/ospf",
        json={"router_id": "10.0.0.1", "networks": ["10.0.0.0/24"], "dry_run": True},
        headers=admin,
    )
    assert ospf.status_code == 200, ospf.text
    assert ospf.json()["lines"][0] == "ospf 1 router-id 10.0.0.1"
