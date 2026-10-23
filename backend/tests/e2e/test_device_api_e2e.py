"""API-level: test-connection (TOFU), guarded execute-command, host-key rotation."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.rbac import Role
from tests.e2e.mock_huawei import HuaweiCliEmulator, MockHuaweiSSH, load_outputs

pytestmark = pytest.mark.e2e


@pytest.fixture
def device_server() -> Iterator[MockHuaweiSSH]:
    with MockHuaweiSSH(
        lambda: HuaweiCliEmulator("SW-01", "S", load_outputs("s5735_v200r019"))
    ) as srv:
        yield srv


async def _register(client: AsyncClient, headers: dict[str, str], server: MockHuaweiSSH) -> str:
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
    assert cred.status_code == 201, cred.text
    dev = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "SW-01",
            "mgmt_ip": "127.0.0.1",
            "port": server.port,
            "credential_id": cred.json()["id"],
            "timeout": 10,
        },
        headers=headers,
    )
    assert dev.status_code == 201, dev.text
    return str(dev.json()["id"])


async def test_test_connection_pins_key_and_updates_facts(
    client: AsyncClient, auth_headers: Any, device_server: MockHuaweiSSH
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, device_server)
    resp = await client.post(f"/api/v1/devices/{dev_id}/test-connection", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["family"] == "S"
    device = (await client.get(f"/api/v1/devices/{dev_id}", headers=headers)).json()
    assert device["status"] == "ONLINE"
    assert device["vrp_version"] == "5.170"
    keys = (await client.get(f"/api/v1/devices/{dev_id}/host-keys", headers=headers)).json()
    assert [k["status"] for k in keys] == ["TRUSTED"]
    assert device_server.password not in resp.text


async def test_execute_command_flow(
    client: AsyncClient, app: Any, auth_headers: Any, device_server: MockHuaweiSSH
) -> None:
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, admin, device_server)
    url = f"/api/v1/devices/{dev_id}/execute-command"

    resp = await client.post(url, json={"command": "display version"}, headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["category"] == "SAFE"
    assert "VRP" in resp.json()["output"]

    operator = await auth_headers(Role.OPERATOR)
    resp = await client.post(url, json={"command": "display arp"}, headers=operator)
    assert resp.status_code == 200
    resp = await client.post(url, json={"command": "reset arp dynamic"}, headers=operator)
    assert resp.status_code == 403
    read_only = await auth_headers(Role.READ_ONLY)
    assert (
        await client.post(url, json={"command": "display arp"}, headers=read_only)
    ).status_code == 403

    resp = await client.post(url, json={"command": "display version\nreboot"}, headers=admin)
    assert resp.status_code == 400

    # DANGEROUS: no token -> 409; dry-run -> token; confirm with reason -> executes once.
    resp = await client.post(
        url, json={"command": "reset arp dynamic", "confirm": True, "reason": "x"}, headers=admin
    )
    assert resp.status_code == 409
    dry = await client.post(
        url, json={"command": "reset arp dynamic", "dry_run": True}, headers=admin
    )
    assert dry.status_code == 200
    token = dry.json()["confirmation_token"]
    assert dry.json()["category"] == "DANGEROUS"
    no_reason = await client.post(
        url,
        json={"command": "reset arp dynamic", "confirm": True, "confirmation_token": token},
        headers=admin,
    )
    assert no_reason.status_code == 422
    other_cmd = await client.post(
        url,
        json={
            "command": "reset mac-address",
            "confirm": True,
            "confirmation_token": token,
            "reason": "r",
        },
        headers=admin,
    )
    assert other_cmd.status_code == 409  # token bound to the command hash (and now burned)


async def test_host_key_change_blocks_until_approved(
    client: AsyncClient, auth_headers: Any, device_server: MockHuaweiSSH
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _register(client, headers, device_server)
    assert (
        await client.post(f"/api/v1/devices/{dev_id}/test-connection", headers=headers)
    ).status_code == 200
    # Device is re-imaged: a new server instance presents a different host key.
    with MockHuaweiSSH(device_server.emulator_factory) as restarted:
        resp = await client.patch(
            f"/api/v1/devices/{dev_id}", json={"port": restarted.port}, headers=headers
        )
        assert resp.status_code == 200
        resp = await client.post(f"/api/v1/devices/{dev_id}/test-connection", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == "host_key_mismatch"
        keys = (await client.get(f"/api/v1/devices/{dev_id}/host-keys", headers=headers)).json()
        pending = [k for k in keys if k["status"] == "PENDING"]
        assert len(pending) == 1
        approve = await client.post(
            f"/api/v1/devices/{dev_id}/host-keys/{pending[0]['id']}/approve", headers=headers
        )
        assert approve.status_code == 200
        resp = await client.post(f"/api/v1/devices/{dev_id}/test-connection", headers=headers)
        assert resp.status_code == 200, resp.text
