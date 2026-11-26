"""Phase 14 e2e: Cisco IOS-XE over real Netmiko/SSH against the stateful mock, via the API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.rbac import Role
from tests.e2e.cisco_emulator import CiscoIosEmulator, CiscoState
from tests.e2e.mock_huawei import MockHuaweiSSH

pytestmark = pytest.mark.e2e


@pytest.fixture
def cisco() -> Iterator[tuple[MockHuaweiSSH, CiscoState]]:
    state = CiscoState()
    with MockHuaweiSSH(lambda: CiscoIosEmulator(state)) as srv:
        yield srv, state


async def _register(
    client: AsyncClient, headers: dict[str, str], srv: MockHuaweiSSH, state: CiscoState
) -> str:
    cred = await client.post(
        "/api/v1/credentials",
        json={
            "name": "cisco-lab",
            "kind": "cli",
            "username": srv.username,
            "password": srv.password,
            "super_password": state.enable_secret,
        },
        headers=headers,
    )
    assert cred.status_code == 201, cred.text
    dev = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "ACC-SW-01",
            "mgmt_ip": "127.0.0.1",
            "port": srv.port,
            "platform": "cisco_ios",
            "credential_id": cred.json()["id"],
            "requires_super": True,
            "timeout": 10,
            "mgmt_interface": "Vlan100",
            "mgmt_vlan": 100,
            "uplink_interfaces": ["Te1/1/1"],
        },
        headers=headers,
    )
    assert dev.status_code == 201, dev.text
    assert dev.json()["vendor"] == "Cisco"
    return str(dev.json()["id"])


async def test_connection_facts_and_reads(
    client: AsyncClient, auth_headers: Any, cisco: Any
) -> None:
    srv, state = cisco
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, srv, state)
    resp = await client.post(f"/api/v1/devices/{dev_id}/test-connection", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"] == "C9300-48P"
    assert resp.json()["family"] == "UNKNOWN"
    device = (await client.get(f"/api/v1/devices/{dev_id}", headers=headers)).json()
    assert device["status"] == "ONLINE"
    assert device["platform"] == "cisco_ios"
    assert device["vrp_version"] == "17.09.04a"
    assert "enable" in state.received  # privilege escalation with the enable secret

    vlans = (await client.get(f"/api/v1/devices/{dev_id}/vlans", headers=headers)).json()
    assert {v["vlan_id"] for v in vlans} >= {10, 20, 100}
    ifaces = await client.get(f"/api/v1/devices/{dev_id}/interfaces?refresh=true", headers=headers)
    assert ifaces.status_code == 200, ifaces.text
    names = {i["name"] for i in ifaces.json()}
    assert "TenGigabitEthernet1/1/1" in names
    routes = await client.get(f"/api/v1/devices/{dev_id}/routing/table", headers=headers)
    assert routes.status_code == 200, routes.text
    assert any(r["protocol"] == "BGP" for r in routes.json())


async def test_vlan_and_port_changes(client: AsyncClient, auth_headers: Any, cisco: Any) -> None:
    srv, state = cisco
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, srv, state)
    url = f"/api/v1/devices/{dev_id}/vlans"
    preview = await client.post(
        url, json={"vlans": [{"id": 300, "name": "LAB"}], "dry_run": True}, headers=headers
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["lines"] == ["vlan 300", "name LAB", "exit"]
    applied = await client.post(
        url, json={"vlans": [{"id": 300, "name": "LAB"}], "save": True}, headers=headers
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"
    assert state.vlans[300] == "LAB"
    assert state.saved >= 1  # write memory

    port = await client.put(
        f"/api/v1/devices/{dev_id}/interfaces/GigabitEthernet1/0/5/vlan",
        json={"mode": "access", "access_vlan": 300},
        headers=headers,
    )
    assert port.status_code == 200, port.text
    assert "switchport access vlan 300" in state.interfaces["GigabitEthernet1/0/5"]


async def test_command_classification(client: AsyncClient, auth_headers: Any, cisco: Any) -> None:
    srv, state = cisco
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, admin, srv, state)
    url = f"/api/v1/devices/{dev_id}/execute-command"
    ok = await client.post(url, json={"command": "sh ver"}, headers=admin)
    assert ok.status_code == 200, ok.text
    assert ok.json()["category"] == "SAFE"
    assert "C9300-48P" in ok.json()["output"]
    reload = await client.post(url, json={"command": "reload"}, headers=admin)
    assert reload.status_code == 409  # confirmation required
    auditor = await auth_headers(Role.AUDITOR)  # no device:configure -> WARNING refused
    denied = await client.post(url, json={"command": "write memory"}, headers=auditor)
    assert denied.status_code == 403


async def test_lockout_and_timed_override(
    client: AsyncClient, auth_headers: Any, cisco: Any
) -> None:
    srv, state = cisco
    admin = await auth_headers(Role.ADMIN)
    dev_id = await _register(client, admin, srv, state)
    url = f"/api/v1/devices/{dev_id}/interfaces/TenGigabitEthernet1/1/1/admin-state"
    blocked = await client.put(url, json={"enabled": False, "dry_run": True}, headers=admin)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "lockout_risk"

    body = {"enabled": False, "dry_run": True, "override_lockout": True, "rollback_minutes": 2}
    preview = await client.put(url, json=body, headers=admin)
    assert preview.status_code == 200, preview.text
    token = preview.json()["confirmation_token"]
    body |= {"dry_run": False, "confirm": True, "confirmation_token": token, "reason": "CHG-42"}
    applied = await client.put(url, json=body, headers=admin)
    assert applied.status_code == 200, applied.text
    assert "configure terminal revert timer 2" in state.received
    assert state.confirmed == 1  # 'configure confirm' after verification
    assert "shutdown" in state.interfaces["TenGigabitEthernet1/1/1"]


async def test_backup_is_sanitized(
    client: AsyncClient, app: Any, auth_headers: Any, cisco: Any
) -> None:
    srv, state = cisco
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, srv, state)
    resp = await client.post(f"/api/v1/devices/{dev_id}/backup", headers=headers)
    assert resp.status_code == 202, resp.text
    await app.state.services.inline_dispatcher.drain()
    job = (await client.get(resp.headers["location"], headers=headers)).json()
    assert job["status"] == "SUCCESS", job
    backups = (await client.get(f"/api/v1/backups?device_id={dev_id}", headers=headers)).json()
    assert backups["total"] == 1
    assert backups["items"][0]["status"] == "SUCCESS"
    detail = (
        await client.get(f"/api/v1/backups/{backups['items'][0]['id']}", headers=headers)
    ).json()
    assert "$9$" not in detail["content"]
    assert "S3cretRO" not in detail["content"]
    assert "Last configuration change" not in detail["content"]


async def test_netconf_is_not_offered_for_cisco(client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    resp = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "C-X",
            "mgmt_ip": "192.0.2.77",
            "platform": "cisco_ios",
            "protocol": "netconf",
        },
        headers=headers,
    )
    assert resp.status_code == 422
