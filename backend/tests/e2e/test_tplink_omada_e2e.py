"""Phase 18 e2e: an Omada-managed switch through the public API against the mock controller."""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.rbac import Role
from app.network.drivers.omada_api import OmadaApiDriver
from tests.e2e.omada_mock import OMADAC, OmadaState

pytestmark = pytest.mark.e2e


@pytest.fixture
def omada(monkeypatch: pytest.MonkeyPatch) -> OmadaState:
    state = OmadaState()
    monkeypatch.setattr(OmadaApiDriver, "transport", state.transport())
    return state


async def _register(client: AsyncClient, headers: dict[str, str], state: OmadaState) -> str:
    cred = await client.post(
        "/api/v1/credentials",
        json={
            "name": "omada",
            "kind": "omada_api",
            "username": state.client_id,
            "password": state.client_secret,
            "controller_url": "https://omada.test:8043",
            "omadac_id": OMADAC,
            "omada_site": "HQ",
        },
        headers=headers,
    )
    assert cred.status_code == 201, cred.text
    out = cred.json()
    assert out["controller_url"] == "https://omada.test:8043"
    assert state.client_secret not in cred.text
    dev = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "ACC-OMADA-01",
            "mgmt_ip": state.switch_ip,
            "platform": "tplink_omada",
            "protocol": "api",
            "credential_id": out["id"],
            "mgmt_vlan": 100,
            "uplink_interfaces": ["Gi1/0/25"],
        },
        headers=headers,
    )
    assert dev.status_code == 201, dev.text
    assert (dev.json()["vendor"], dev.json()["port"]) == ("TP-Link", 443)
    return str(dev.json()["id"])


async def test_omada_needs_matching_credential_and_protocol(
    client: AsyncClient, auth_headers: Any, omada: OmadaState
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    bad = await client.post(
        "/api/v1/credentials",
        json={"name": "x", "kind": "omada_api", "username": "c", "password": "s"},
        headers=headers,
    )
    assert bad.status_code == 422
    ssh = await client.post(
        "/api/v1/devices",
        json={"hostname": "X", "mgmt_ip": "192.0.2.90", "platform": "tplink_omada"},
        headers=headers,
    )
    assert ssh.status_code == 422  # default protocol ssh is not offered for Omada


async def test_facts_vlans_ports_and_mac(
    client: AsyncClient, auth_headers: Any, omada: OmadaState
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, omada)
    facts = await client.post(f"/api/v1/devices/{dev_id}/test-connection", headers=headers)
    assert facts.status_code == 200, facts.text
    assert facts.json()["model"] == "SG3428"
    vlans = await client.get(f"/api/v1/devices/{dev_id}/vlans", headers=headers)
    assert vlans.status_code == 200, vlans.text
    created = await client.post(
        f"/api/v1/devices/{dev_id}/vlans",
        json={"vlans": [{"id": 300, "name": "LAB"}]},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "applied"
    assert any(n["vlan"] == 300 for n in omada.networks)
    port = await client.put(
        f"/api/v1/devices/{dev_id}/interfaces/GigabitEthernet1/0/5/vlan",
        json={"mode": "access", "access_vlan": 300},
        headers=headers,
    )
    assert port.status_code == 200, port.text
    assert port.json()["lines"] == ["port 5 native 300"]
    assert omada.vlan(omada.port(5)["nativeNetworkId"]) == 300
    macs = await client.post(f"/api/v1/mac-addresses/collect/{dev_id}", headers=headers)
    assert macs.status_code == 200, macs.text
    assert macs.json() == {"entries": 1}  # only clients behind this switch
    routes = await client.get(f"/api/v1/devices/{dev_id}/routing/table", headers=headers)
    assert routes.status_code == 422  # routing stays in the controller


async def test_uplink_lockout_cannot_be_overridden(
    client: AsyncClient, auth_headers: Any, omada: OmadaState
) -> None:
    admin = await auth_headers(Role.ADMIN)
    dev_id = await _register(client, admin, omada)
    url = f"/api/v1/devices/{dev_id}/interfaces/GigabitEthernet1/0/25/admin-state"
    blocked = await client.put(url, json={"enabled": False, "dry_run": True}, headers=admin)
    assert blocked.status_code == 409, blocked.text
    body = {"enabled": False, "dry_run": True, "override_lockout": True, "rollback_minutes": 3}
    refused = await client.put(url, json=body, headers=admin)
    assert refused.status_code == 409, refused.text  # no device-side rollback timer
    assert omada.port(25)["disable"] is False


async def test_backup_snapshot(
    client: AsyncClient, app: Any, auth_headers: Any, omada: OmadaState
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, omada)
    resp = await client.post(f"/api/v1/devices/{dev_id}/backup", headers=headers)
    assert resp.status_code == 202, resp.text
    await app.state.services.inline_dispatcher.drain()
    job = (await client.get(resp.headers["location"], headers=headers)).json()
    assert job["status"] == "SUCCESS", job
    backups = (await client.get(f"/api/v1/backups?device_id={dev_id}", headers=headers)).json()
    detail = (
        await client.get(f"/api/v1/backups/{backups['items'][0]['id']}", headers=headers)
    ).json()
    snapshot = json.loads(detail["content"])
    assert snapshot["switch"]["model"] == "SG3428"
    assert {"vlan": 100, "name": "MGMT"} in snapshot["networks"]
