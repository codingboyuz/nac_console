"""Phase 13: the platform field on the device API."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.core.rbac import Role


async def test_default_platform_and_vendor(client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    resp = await client.post(
        "/api/v1/devices", json={"hostname": "SW-A", "mgmt_ip": "192.0.2.41"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["platform"] == "huawei_vrp"
    assert body["vendor"] == "Huawei"


async def test_cloudengine_family_selects_vrp_v8(client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    resp = await client.post(
        "/api/v1/devices",
        json={"hostname": "CE-A", "mgmt_ip": "192.0.2.42", "family": "CE"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["platform"] == "huawei_vrpv8"
    dev_id = resp.json()["id"]
    resp = await client.patch(f"/api/v1/devices/{dev_id}", json={"family": "S"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["platform"] == "huawei_vrp"


async def test_family_is_huawei_only(client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    resp = await client.post(
        "/api/v1/devices",
        json={"hostname": "C-A", "mgmt_ip": "192.0.2.43", "platform": "cisco_ios", "family": "S"},
        headers=headers,
    )
    assert resp.status_code == 422
    ok = await client.post(
        "/api/v1/devices",
        json={"hostname": "C-A", "mgmt_ip": "192.0.2.43", "platform": "cisco_ios"},
        headers=headers,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["vendor"] == "Cisco"
    moved = await client.patch(
        f"/api/v1/devices/{ok.json()['id']}", json={"platform": "huawei_vrp"}, headers=headers
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["vendor"] == "Huawei"


async def test_platform_catalogue(client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.READ_ONLY)
    resp = await client.get("/api/v1/platforms", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = {r["platform"]: r for r in resp.json()}
    assert len(rows) == 7
    assert rows["huawei_vrp"]["available"] is True
    assert "netconf" in rows["huawei_vrpv8"]["transports"]
    assert rows["cisco_ios"]["rollback_timer"] == "commit-trial"
    assert rows["cisco_nxos"]["rollback_timer"] == "none"
    assert rows["cisco_ios"]["hybrid_ports"] is False


async def test_unknown_platform_value_rejected(client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    resp = await client.post(
        "/api/v1/devices",
        json={"hostname": "X", "mgmt_ip": "192.0.2.44", "platform": "juniper_junos"},
        headers=headers,
    )
    assert resp.status_code == 422
