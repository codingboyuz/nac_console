"""Phase 8: job engine (bulk, partial failure, stop-on-failure, confirmation, idempotency,
cancellation, visibility), WebSocket fan-out, Celery task (eager) and DB-backed schedules."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from app.core.rbac import Role
from tests.fakes import FakeLab, install


@pytest.fixture
def lab(app: Any) -> FakeLab:
    fake = FakeLab()
    install(app, fake)
    return fake


async def _devices(client: AsyncClient, headers: dict[str, str], ips: list[str]) -> list[str]:
    cred = await client.post(
        "/api/v1/credentials",
        json={"name": "fake", "kind": "cli", "username": "u", "password": "Fake-Passw0rd!"},
        headers=headers,
    )
    ids = []
    for ip in ips:
        resp = await client.post(
            "/api/v1/devices",
            json={
                "hostname": f"SW-{ip.split('.')[-1]}",
                "mgmt_ip": ip,
                "family": "S",
                "credential_id": cred.json()["id"],
                "uplink_interfaces": ["XGE0/0/1"],
            },
            headers=headers,
        )
        ids.append(resp.json()["id"])
    return ids


async def _wait(app: Any) -> None:
    await app.state.services.inline_dispatcher.drain()


async def _job(client: AsyncClient, headers: dict[str, str], job_id: str) -> dict[str, Any]:
    resp = await client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_bulk_vlan_partial_failure(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    lab.add("192.0.2.1")
    lab.add("192.0.2.2", reject={"vlan batch 120"})
    lab.add("192.0.2.3")
    ids = await _devices(client, headers, ["192.0.2.1", "192.0.2.2", "192.0.2.3"])
    resp = await client.post(
        "/api/v1/jobs",
        json={
            "type": "vlan.create",
            "target_devices": ids,
            "params": {"vlans": [{"id": 120, "name": "Bulk"}]},
        },
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    assert resp.headers["location"] == f"/api/v1/jobs/{resp.json()['job_id']}"
    await _wait(app)
    job = await _job(client, headers, resp.json()["job_id"])
    assert job["status"] == "PARTIAL"
    assert job["progress"] == 100
    by_status = sorted(r["status"] for r in job["per_device_results"])
    assert by_status == ["FAILED", "SUCCESS", "SUCCESS"]
    failed = next(r for r in job["per_device_results"] if r["status"] == "FAILED")
    assert "command_rejected" in failed["error"]
    assert lab.devices["192.0.2.1"].vlans[120] == "Bulk"


async def test_stop_on_failure_threshold(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    for i in range(1, 5):
        lab.add(f"192.0.2.{i}")
    lab.down.update({"192.0.2.1", "192.0.2.2"})
    ids = await _devices(client, headers, [f"192.0.2.{i}" for i in range(1, 5)])
    resp = await client.post(
        "/api/v1/jobs",
        json={"type": "collect.mac", "target_devices": ids, "concurrency": 1, "stop_on_failure": 2},
        headers=headers,
    )
    await _wait(app)
    job = await _job(client, headers, resp.json()["job_id"])
    statuses = [r["status"] for r in job["per_device_results"]]
    assert statuses.count("FAILED") == 2
    assert statuses.count("SKIPPED") + statuses.count("SUCCESS") == 2
    assert job["status"] in {"FAILED", "PARTIAL"}


async def test_dangerous_bulk_needs_job_confirmation(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    lab.add("192.0.2.1")
    lab.add("192.0.2.2")
    ids = await _devices(client, headers, ["192.0.2.1", "192.0.2.2"])
    payload = {"type": "vlan.delete", "target_devices": ids, "params": {"vlans": "10,20"}}
    unconfirmed = await client.post("/api/v1/jobs", json=payload, headers=headers)
    await _wait(app)
    job = await _job(client, headers, unconfirmed.json()["job_id"])
    assert job["status"] == "FAILED"
    assert all("confirmation_required" in r["error"] for r in job["per_device_results"])
    assert 10 in lab.devices["192.0.2.1"].vlans

    dry = await client.post("/api/v1/jobs", json={**payload, "dry_run": True}, headers=headers)
    await _wait(app)
    dry_job = await _job(client, headers, dry.json()["job_id"])
    assert dry_job["status"] == "SUCCESS"
    assert dry_job["output"]["confirmation_required"] is True
    token = dry_job["confirmation_token"]
    assert token
    other = await auth_headers(Role.NETWORK_ADMIN)
    assert (await _job(client, other, dry.json()["job_id"]))["confirmation_token"] is None

    run = await client.post(
        "/api/v1/jobs",
        json={**payload, "confirm": True, "confirmation_token": token, "reason": "decommission"},
        headers=headers,
    )
    assert run.status_code == 202, run.text
    await _wait(app)
    assert (await _job(client, headers, run.json()["job_id"]))["status"] == "SUCCESS"
    assert 10 not in lab.devices["192.0.2.1"].vlans
    replay = await client.post(
        "/api/v1/jobs",
        json={**payload, "confirm": True, "confirmation_token": token, "reason": "again"},
        headers=headers,
    )
    assert replay.status_code == 409


async def test_idempotency_key(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.OPERATOR)
    admin = await auth_headers(Role.NETWORK_ADMIN)
    lab.add("192.0.2.1")
    ids = await _devices(client, admin, ["192.0.2.1"])
    payload = {"type": "collect.vlans", "target_devices": ids}
    h = {**headers, "Idempotency-Key": "abc-123"}
    first = await client.post("/api/v1/jobs", json=payload, headers=h)
    second = await client.post("/api/v1/jobs", json=payload, headers=h)
    assert first.json()["job_id"] == second.json()["job_id"]
    conflict = await client.post("/api/v1/jobs", json={**payload, "type": "collect.mac"}, headers=h)
    assert conflict.status_code == 409
    await _wait(app)


async def test_cancel_skips_remaining(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    for i in range(1, 6):
        lab.add(f"192.0.2.{i}")
    lab.delay = 0.3
    ids = await _devices(client, headers, [f"192.0.2.{i}" for i in range(1, 6)])
    resp = await client.post(
        "/api/v1/jobs",
        json={"type": "collect.mac", "target_devices": ids, "concurrency": 1},
        headers=headers,
    )
    job_id = resp.json()["job_id"]
    await asyncio.sleep(0.5)
    cancel = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
    assert cancel.status_code == 200, cancel.text
    await _wait(app)
    job = await _job(client, headers, job_id)
    assert job["status"] == "CANCELLED"
    assert "SKIPPED" in [r["status"] for r in job["per_device_results"]]
    again = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
    assert again.status_code == 409


async def test_rbac_and_visibility(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    admin = await auth_headers(Role.NETWORK_ADMIN)
    lab.add("192.0.2.1")
    ids = await _devices(client, admin, ["192.0.2.1"])
    reader = await auth_headers(Role.READ_ONLY)
    payload = {"type": "collect.mac", "target_devices": ids}
    assert (await client.post("/api/v1/jobs", json=payload, headers=reader)).status_code == 403
    op1 = await auth_headers(Role.OPERATOR)
    op2 = await auth_headers(Role.OPERATOR)
    resp = await client.post("/api/v1/jobs", json=payload, headers=op1)
    await _wait(app)
    job_id = resp.json()["job_id"]
    assert (await client.get(f"/api/v1/jobs/{job_id}", headers=op2)).status_code == 404
    assert (await client.get(f"/api/v1/jobs/{job_id}", headers=admin)).status_code == 200
    listing = await client.get("/api/v1/jobs", headers=op2)
    assert listing.json()["total"] == 0
    bad = await client.post("/api/v1/jobs", json={**payload, "type": "nope"}, headers=op1)
    assert bad.status_code == 422
    params = await client.post(
        "/api/v1/jobs",
        json={**payload, "type": "vlan.create", "params": {"vlans": "x"}},
        headers=op1,
    )
    assert params.status_code == 422
    types = await client.get("/api/v1/jobs/types", headers=op1)
    assert "vlan.create" in [t["name"] for t in types.json()]


async def test_websocket_job_stream(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    lab.add("192.0.2.1")
    lab.add("192.0.2.2")
    lab.delay = 0.2
    ids = await _devices(client, headers, ["192.0.2.1", "192.0.2.2"])
    resp = await client.post(
        "/api/v1/jobs",
        json={"type": "collect.mac", "target_devices": ids, "concurrency": 1},
        headers=headers,
    )
    job_id = resp.json()["job_id"]
    ticket = (await client.post("/api/v1/auth/ws-ticket", headers=headers)).json()["ticket"]
    messages: list[dict[str, Any]] = []
    async with AsyncClient(
        transport=ASGIWebSocketTransport(app), base_url="http://testserver"
    ) as ws_client:
        async with aconnect_ws(f"/api/v1/ws/jobs/{job_id}?ticket={ticket}", ws_client) as ws:
            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
                except Exception:
                    break
                messages.append(msg)
                if msg.get("type") == "status" and msg.get("progress") == 100:
                    break
    await _wait(app)
    assert messages[0]["type"] == "snapshot"
    assert any(m["type"] == "device" for m in messages) or messages[0]["status"] in {
        "SUCCESS",
        "PARTIAL",
    }


async def test_websocket_rejects_bad_ticket(app: Any) -> None:
    async with AsyncClient(
        transport=ASGIWebSocketTransport(app), base_url="http://testserver"
    ) as ws_client:
        with pytest.raises(Exception) as err:  # noqa: PT011 - httpx-ws raises on close
            async with aconnect_ws("/api/v1/ws/alerts?ticket=wst_bogus", ws_client) as ws:
                await ws.receive_json()
    found: list[BaseException] = [err.value]
    while found and isinstance(found[-1], BaseExceptionGroup):
        found.append(found[-1].exceptions[0])
    assert getattr(found[-1], "code", None) == 4401, repr(found[-1])
