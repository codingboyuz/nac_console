"""Phase 3: device inventory, credential vault, insecure-protocol gating, secrecy, N+1."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select

from app.core.logging import get_logger
from app.core.rbac import Role
from app.models.device import Credential

SECRET = "Sup3r-Secret-Device-Pw!"
COMMUNITY = "c0mmunity-Str1ng"


async def _cred(client: AsyncClient, headers: dict[str, str], name: str = "core-cli") -> str:
    resp = await client.post(
        "/api/v1/credentials",
        json={"name": name, "kind": "cli", "username": "netops", "password": SECRET},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _device(ip: str, **extra: Any) -> dict[str, Any]:
    suffix = ip.rsplit(".", maxsplit=1)[-1]
    return {"hostname": f"SW-{suffix}", "mgmt_ip": ip, "family": "S", **extra}


async def test_credentials_are_write_only_and_encrypted(
    client: AsyncClient, app: Any, auth_headers: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    headers = await auth_headers(Role.NETWORK_ADMIN)
    cred_id = await _cred(client, headers)
    resp = await client.post(
        "/api/v1/credentials",
        json={"name": "snmp", "kind": "snmp_v2c", "community": COMMUNITY},
        headers=headers,
    )
    assert resp.status_code == 201
    for path in ("/api/v1/credentials", f"/api/v1/credentials/{cred_id}"):
        body = (await client.get(path, headers=headers)).text
        assert SECRET not in body
        assert COMMUNITY not in body
    detail = (await client.get(f"/api/v1/credentials/{cred_id}", headers=headers)).json()
    assert detail["secret_fields"] == ["password"]
    async with app.state.db.session() as s:
        rows = (await s.execute(select(Credential))).scalars().all()
    assert all(SECRET not in r.secret_ciphertext for r in rows)
    resolved = await app.state.services.credentials.resolve(rows[0].id)
    assert SECRET not in repr(resolved)
    # Even an explicit attempt to log the secret is masked by the central redaction.
    get_logger("test").warning("leak_attempt", password=SECRET, detail=f"password {SECRET}")
    assert caplog.records
    assert SECRET not in caplog.text
    assert COMMUNITY not in caplog.text
    assert "REDACTED" in caplog.text


async def test_device_crud_filter_sort(client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    cred_id = await _cred(client, headers)
    for ip, fam in (("192.0.2.11", "S"), ("192.0.2.12", "CE"), ("192.0.2.13", "S")):
        resp = await client.post(
            "/api/v1/devices",
            json=_device(ip, family=fam, credential_id=cred_id, tags=["campus"]),
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["port"] == 22
        assert resp.json()["security_warning"] is None
    resp = await client.get("/api/v1/devices?family=S&sort=-hostname", headers=headers)
    body = resp.json()
    assert body["total"] == 2
    assert [d["hostname"] for d in body["items"]] == ["SW-13", "SW-11"]
    assert (await client.get("/api/v1/devices?tag=campus", headers=headers)).json()["total"] == 3
    assert (await client.get("/api/v1/devices?q=sw-12", headers=headers)).json()["total"] == 1
    assert (await client.get("/api/v1/devices?sort=password", headers=headers)).status_code == 422
    dup = await client.post("/api/v1/devices", json=_device("192.0.2.11"), headers=headers)
    assert dup.status_code == 409
    dev_id = body["items"][0]["id"]
    resp = await client.patch(
        f"/api/v1/devices/{dev_id}",
        json={"description": "core", "uplink_interfaces": ["GE0/0/24"]},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["uplink_interfaces"] == ["GigabitEthernet0/0/24"]
    assert (await client.delete(f"/api/v1/devices/{dev_id}", headers=headers)).status_code == 204
    assert (await client.get(f"/api/v1/devices/{dev_id}", headers=headers)).status_code == 404


async def test_credential_in_use_cannot_be_deleted(client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    cred_id = await _cred(client, headers)
    await client.post(
        "/api/v1/devices", json=_device("192.0.2.20", credential_id=cred_id), headers=headers
    )
    resp = await client.delete(f"/api/v1/credentials/{cred_id}", headers=headers)
    assert resp.status_code == 409


async def test_telnet_requires_flag_and_role(client: AsyncClient, auth_headers: Any) -> None:
    admin = await auth_headers(Role.NETWORK_ADMIN)
    resp = await client.post(
        "/api/v1/devices", json=_device("192.0.2.30", protocol="telnet"), headers=admin
    )
    assert resp.status_code == 422
    resp = await client.post(
        "/api/v1/devices",
        json=_device("192.0.2.30", protocol="telnet", allow_insecure_protocol=True),
        headers=admin,
    )
    assert resp.status_code == 201
    assert resp.json()["port"] == 23
    assert "clear text" in resp.json()["security_warning"]


async def test_operator_cannot_create_devices(client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.OPERATOR)
    resp = await client.post("/api/v1/devices", json=_device("192.0.2.40"), headers=headers)
    assert resp.status_code == 403


async def test_group_scoping_hides_devices(
    client: AsyncClient, app: Any, auth_headers: Any, user_factory: Any, login: Any
) -> None:
    headers = await auth_headers(Role.ADMIN)
    g1 = (await client.post("/api/v1/device-groups", json={"name": "g1"}, headers=headers)).json()
    g2 = (await client.post("/api/v1/device-groups", json={"name": "g2"}, headers=headers)).json()
    d1 = await client.post(
        "/api/v1/devices", json=_device("192.0.2.51", group_id=g1["id"]), headers=headers
    )
    d2 = await client.post(
        "/api/v1/devices", json=_device("192.0.2.52", group_id=g2["id"]), headers=headers
    )
    scoped = await user_factory(Role.OPERATOR)
    resp = await client.patch(
        f"/api/v1/users/{scoped.id}", json={"group_scope": [g1["id"]]}, headers=headers
    )
    assert resp.status_code == 200
    tokens = await login(scoped.username)
    sh = {"Authorization": f"Bearer {tokens['access_token']}"}
    listing = (await client.get("/api/v1/devices", headers=sh)).json()
    assert [d["id"] for d in listing["items"]] == [d1.json()["id"]]
    assert (await client.get(f"/api/v1/devices/{d2.json()['id']}", headers=sh)).status_code == 404


async def test_list_devices_query_count_is_constant(
    client: AsyncClient, app: Any, auth_headers: Any
) -> None:
    headers = await auth_headers(Role.ADMIN)
    group = (
        await client.post("/api/v1/device-groups", json={"name": "gx"}, headers=headers)
    ).json()

    async def count_list_queries() -> int:
        counter = {"n": 0}

        def before(*_: Any) -> None:
            counter["n"] += 1

        engine = app.state.db.engine.sync_engine
        event.listen(engine, "before_cursor_execute", before)
        try:
            resp = await client.get("/api/v1/devices", headers=headers)
            assert resp.status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return counter["n"]

    for i in range(2):
        await client.post(
            "/api/v1/devices",
            json=_device(f"192.0.2.{60 + i}", group_id=group["id"]),
            headers=headers,
        )
    small = await count_list_queries()
    for i in range(2, 12):
        await client.post(
            "/api/v1/devices",
            json=_device(f"192.0.2.{60 + i}", group_id=group["id"]),
            headers=headers,
        )
    assert await count_list_queries() == small


async def test_key_rotation_service(app: Any, client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.ADMIN)
    await _cred(client, headers, "rot")
    # Already on the active key -> nothing to rotate.
    assert await app.state.services.credentials.rotate_keys() == 0
