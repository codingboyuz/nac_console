"""Phase 11: internal RADIUS API, decisions + audit, fail modes, accounting, policies, CoA over
UDP (fake NAS), RADIUS servers, provisioning job, reference server, no-arbitrary-CLI check."""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.rbac import Role
from app.models.audit import AuditLog
from app.services.coa_service import DICTIONARY
from tests.conftest import make_settings
from tests.fakes import FakeLab, install

TOKEN_HEADERS = {"X-Internal-Token": "test-internal-token-" + "y" * 32}


def _rest(**attrs: str) -> dict[str, Any]:
    """FreeRADIUS rlm_rest JSON body shape."""
    return {k: {"type": "string", "value": [v]} for k, v in attrs.items()}


async def test_internal_api_requires_token_and_network(client: AsyncClient, app: Any) -> None:
    body = _rest(**{"User-Name": "001122334455", "Calling-Station-Id": "00-11-22-33-44-55"})
    assert (await client.post("/internal/v1/radius/authorize", json=body)).status_code == 401
    wrong = await client.post(
        "/internal/v1/radius/authorize", json=body, headers={"X-Internal-Token": "nope"}
    )
    assert wrong.status_code == 401
    outsider = AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.9", 4000)), base_url="http://t"
    )
    async with outsider:
        assert (
            await outsider.post("/internal/v1/radius/authorize", json=body, headers=TOKEN_HEADERS)
        ).status_code == 403
    import base64

    basic = {
        "Authorization": "Basic "
        + base64.b64encode(f"freeradius:{TOKEN_HEADERS['X-Internal-Token']}".encode()).decode()
    }
    assert (
        await client.post("/internal/v1/radius/authorize", json=body, headers=basic)
    ).status_code == 200


async def test_mab_decisions_endpoint_inventory_and_audit(
    client: AsyncClient, app: Any, auth_headers: Any
) -> None:
    admin = await auth_headers(Role.NETWORK_ADMIN)
    dev = await client.post(
        "/api/v1/devices", json={"hostname": "SW-NAS", "mgmt_ip": "192.0.2.77"}, headers=admin
    )
    body = _rest(
        **{
            "User-Name": "00e0fc123456",
            "Calling-Station-Id": "00e0-fc12-3456",
            "NAS-IP-Address": "192.0.2.77",
            "NAS-Port-Id": "slot=0;subslot=0;port=5;vlanid=1",
        }
    )
    first = await client.post("/internal/v1/radius/authorize", json=body, headers=TOKEN_HEADERS)
    assert first.status_code == 200
    assert first.json()["reply:Tunnel-Private-Group-Id"] == "999"
    assert first.json()["policy"] == "default-quarantine"
    endpoint = (await client.get("/api/v1/nac/endpoints/00:e0:fc:12:34:56", headers=admin)).json()
    assert endpoint["status"] == "QUARANTINED"
    assert endpoint["vendor"] == "Huawei"
    assert endpoint["switch_id"] == dev.json()["id"]

    reg = await client.put(
        "/api/v1/nac/endpoints/00e0.fc12.3456", json={"registered": True}, headers=admin
    )
    assert reg.status_code == 200
    second = await client.post("/internal/v1/radius/authorize", json=body, headers=TOKEN_HEADERS)
    assert second.json()["reply:Tunnel-Private-Group-Id"] == "10"

    blocked = await client.post("/api/v1/nac/endpoints/00:e0:fc:12:34:56/block", headers=admin)
    assert blocked.status_code == 200
    third = await client.post("/internal/v1/radius/authorize", json=body, headers=TOKEN_HEADERS)
    assert third.status_code == 401
    async with app.state.db.session() as s:
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.operation == "nac.decision")))
            .scalars()
            .all()
        )
    assert [r.extra["policy"] for r in rows] == [
        "default-quarantine",
        "registered-employee",
        "blocked",
    ]
    assert rows[1].extra["policy_id"]


async def test_fail_closed_and_site_fail_open(
    client: AsyncClient, app: Any, auth_headers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken() -> list[Any]:
        raise ConnectionError("db down")

    monkeypatch.setattr(app.state.services.nac.policies, "active", broken)
    body = _rest(**{"User-Name": "a1b2c3d4e5f6", "Calling-Station-Id": "a1-b2-c3-d4-e5-f6"})
    resp = await client.post("/internal/v1/radius/authorize", json=body, headers=TOKEN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["reply:Tunnel-Private-Group-Id"] == "999"
    assert resp.json()["policy"] == "fail-closed"

    admin = await auth_headers(Role.ADMIN)
    await client.post(
        "/api/v1/devices",
        json={"hostname": "SW-LAB", "mgmt_ip": "192.0.2.78", "site": "lab"},
        headers=admin,
    )
    from app.models.job import Setting

    async with app.state.db.session() as s:
        s.add(Setting(key="nac", value={"site_fail_modes": {"lab": "open"}}))
        await s.commit()
    lab_body = _rest(
        **{
            "User-Name": "a1b2c3d4e5f7",
            "Calling-Station-Id": "a1-b2-c3-d4-e5-f7",
            "NAS-IP-Address": "192.0.2.78",
        }
    )
    resp = await client.post("/internal/v1/radius/authorize", json=lab_body, headers=TOKEN_HEADERS)
    assert resp.json()["policy"] == "fail-open"
    assert "reply:Tunnel-Private-Group-Id" not in resp.json()


async def test_accounting_lifecycle(client: AsyncClient, auth_headers: Any) -> None:
    base = {
        "Acct-Session-Id": "sess-1",
        "Calling-Station-Id": "00-11-22-33-44-66",
        "NAS-IP-Address": "192.0.2.77",
        "User-Name": "alice",
    }
    assert (
        await client.post(
            "/internal/v1/radius/accounting",
            json=_rest(**base, **{"Acct-Status-Type": "Start"}),
            headers=TOKEN_HEADERS,
        )
    ).status_code == 204
    await client.post(
        "/internal/v1/radius/accounting",
        json=_rest(
            **base,
            **{
                "Acct-Status-Type": "Interim-Update",
                "Acct-Input-Octets": "1000",
                "Framed-IP-Address": "192.0.2.150",
            },
        ),
        headers=TOKEN_HEADERS,
    )
    admin = await auth_headers(Role.NETWORK_ADMIN)
    active = (await client.get("/api/v1/nac/sessions", headers=admin)).json()
    assert active[0]["input_octets"] == 1000
    assert active[0]["framed_ip"] == "192.0.2.150"
    await client.post(
        "/internal/v1/radius/accounting",
        json=_rest(**base, **{"Acct-Status-Type": "Stop", "Acct-Terminate-Cause": "User-Request"}),
        headers=TOKEN_HEADERS,
    )
    assert (await client.get("/api/v1/nac/sessions", headers=admin)).json() == []
    stopped = (await client.get("/api/v1/nac/sessions?active=false", headers=admin)).json()
    assert stopped[0]["status"] == "STOPPED"


async def test_authentication_failures_raise_alert(
    client: AsyncClient, app: Any, auth_headers: Any
) -> None:
    admin = await auth_headers(Role.NETWORK_ADMIN)
    await client.put(
        "/api/v1/nac/endpoints/00:11:22:33:44:77", json={"status": "BLOCKED"}, headers=admin
    )
    body = _rest(**{"User-Name": "001122334477", "Calling-Station-Id": "00-11-22-33-44-77"})
    for _ in range(5):
        assert (
            await client.post("/internal/v1/radius/authorize", json=body, headers=TOKEN_HEADERS)
        ).status_code == 401
    await app.state.services.alerts.drain()
    alerts = (await client.get("/api/v1/alerts?state=FIRING", headers=admin)).json()["items"]
    assert [a["alert_type"] for a in alerts] == ["authentication_failures"]


async def test_policy_crud_versions_simulate(client: AsyncClient, auth_headers: Any) -> None:
    admin = await auth_headers(Role.NETWORK_ADMIN)
    operator = await auth_headers(Role.OPERATOR)
    policy = {
        "name": "voip",
        "priority": 50,
        "condition": {"type": "oui_in", "ouis": ["00:04:F2"]},
        "action": {"type": "assign_vlan", "vlan": 30},
    }
    assert (
        await client.post("/api/v1/nac/policies", json=policy, headers=operator)
    ).status_code == 403
    created = await client.post("/api/v1/nac/policies", json=policy, headers=admin)
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    updated = await client.put(
        f"/api/v1/nac/policies/{pid}",
        json={**policy, "action": {"type": "assign_vlan", "vlan": 31}},
        headers=admin,
    )
    assert updated.json()["version"] == 2
    versions = (await client.get(f"/api/v1/nac/policies/{pid}/versions", headers=admin)).json()
    assert [v["snapshot"]["action"]["vlan"] for v in versions] == [30, 31]
    sim = await client.post(
        "/api/v1/nac/policies/simulate", json={"mac": "00:04:f2:01:02:03"}, headers=operator
    )
    assert sim.json()["vlan"] == 31
    assert sim.json()["policy_name"] == "voip"
    bad = await client.post(
        "/api/v1/nac/policies",
        json={**policy, "name": "evil", "condition": {"type": "python", "src": "1"}},
        headers=admin,
    )
    assert bad.status_code == 422
    all_policies = (await client.get("/api/v1/nac/policies", headers=admin)).json()
    default = next(p for p in all_policies if p["is_default"])
    assert (
        await client.delete(f"/api/v1/nac/policies/{default['id']}", headers=admin)
    ).status_code == 409
    weaken = await client.put(
        f"/api/v1/nac/policies/{default['id']}",
        json={
            "name": default["name"],
            "priority": default["priority"],
            "condition": {"type": "always"},
            "action": {"type": "accept"},
        },
        headers=admin,
    )
    assert weaken.status_code == 409


class FakeNas:
    """UDP CoA responder (the switch side of RFC 5176)."""

    def __init__(self, secret: bytes, *, answer: bool = True) -> None:
        from pyrad.dictionary import Dictionary

        self.secret, self.answer = secret, answer
        self.dictionary = Dictionary(str(DICTIONARY))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.received: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        from pyrad import packet

        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            pkt = packet.CoAPacket(packet=data, secret=self.secret, dict=self.dictionary)
            self.received.append({"code": pkt.code, "session": pkt.get("Acct-Session-Id")})
            if self.answer:
                reply = pkt.CreateReply()
                reply.code = (
                    packet.DisconnectACK if pkt.code == packet.DisconnectRequest else packet.CoAACK
                )
                self.sock.sendto(reply.ReplyPacket(), addr)

    def __enter__(self) -> FakeNas:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()


@pytest.fixture
def nas() -> Iterator[FakeNas]:
    with FakeNas(b"Lab-Radius-Secret1") as fake:
        yield fake


async def test_radius_servers_and_coa_disconnect(
    client: AsyncClient, app: Any, auth_headers: Any, nas: FakeNas
) -> None:
    admin = await auth_headers(Role.NETWORK_ADMIN)
    created = await client.post(
        "/api/v1/radius/servers",
        json={
            "name": "fr1",
            "host": "192.0.2.50",
            "shared_secret": "Lab-Radius-Secret1",
            "coa_port": nas.port,
            "timeout": 1,
            "retries": 0,
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    assert "Lab-Radius-Secret1" not in created.text
    listing = await client.get("/api/v1/radius/servers", headers=admin)
    assert "Lab-Radius-Secret1" not in listing.text
    await client.post(
        "/internal/v1/radius/accounting",
        json=_rest(
            **{
                "Acct-Session-Id": "coa-1",
                "Calling-Station-Id": "00-11-22-33-44-88",
                "NAS-IP-Address": "127.0.0.1",
                "Acct-Status-Type": "Start",
            }
        ),
        headers=TOKEN_HEADERS,
    )
    session = (await client.get("/api/v1/nac/sessions", headers=admin)).json()[0]
    resp = await client.post(f"/api/v1/nac/sessions/{session['id']}/disconnect", headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["acknowledged"] is True
    assert nas.received[0]["session"] == ["coa-1"]
    reauth = await client.post(
        f"/api/v1/nac/sessions/{session['id']}/reauthenticate", headers=admin
    )
    assert reauth.json()["detail"] == "ACK"
    nas.answer = False
    timeout = await client.post(f"/api/v1/nac/sessions/{session['id']}/disconnect", headers=admin)
    assert timeout.status_code == 504
    async with app.state.db.session() as s:
        ops = [
            r.operation
            for r in (await s.execute(select(AuditLog))).scalars().all()
            if r.operation.startswith("nac.coa")
        ]
    assert ops == ["nac.coa.disconnect", "nac.coa.reauthenticate", "nac.coa.disconnect"]


async def test_provisioning_preview_and_guarded_job(
    client: AsyncClient, app: Any, auth_headers: Any
) -> None:
    lab = FakeLab()
    install(app, lab)
    emu = lab.add("192.0.2.1")
    admin = await auth_headers(Role.NETWORK_ADMIN)
    cred = await client.post(
        "/api/v1/credentials",
        json={"name": "c", "kind": "cli", "username": "u", "password": "Fake-Passw0rd!"},
        headers=admin,
    )
    dev = (
        await client.post(
            "/api/v1/devices",
            json={
                "hostname": "SW-1",
                "mgmt_ip": "192.0.2.1",
                "family": "S",
                "credential_id": cred.json()["id"],
            },
            headers=admin,
        )
    ).json()["id"]
    server = (
        await client.post(
            "/api/v1/radius/servers",
            json={"name": "fr1", "host": "192.0.2.50", "shared_secret": "Lab-Radius-Secret1"},
            headers=admin,
        )
    ).json()
    preview = await client.post(
        "/api/v1/nac/provisioning/preview",
        json={"device_id": dev, "radius_server_id": server["id"], "interfaces": ["GE0/0/3"]},
        headers=admin,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert "radius-server template nac" in body["lines"]
    assert "Lab-Radius-Secret1" not in preview.text
    params = {"radius_server_id": server["id"], "interfaces": ["GE0/0/3"]}
    dry = await client.post(
        "/api/v1/jobs",
        json={"type": "nac.provision", "target_devices": [dev], "params": params, "dry_run": True},
        headers=admin,
    )
    await app.state.services.inline_dispatcher.drain()
    dry_job = (await client.get(dry.headers["location"], headers=admin)).json()
    assert dry_job["output"]["confirmation_required"] is True
    run = await client.post(
        "/api/v1/jobs",
        json={
            "type": "nac.provision",
            "target_devices": [dev],
            "params": params,
            "confirm": True,
            "confirmation_token": dry_job["confirmation_token"],
            "reason": "enable NAC on floor 1",
        },
        headers=admin,
    )
    await app.state.services.inline_dispatcher.drain()
    job = (await client.get(run.headers["location"], headers=admin)).json()
    assert job["status"] == "SUCCESS", job["per_device_results"]
    assert "radius-server template nac" in emu.received
    assert "authentication-profile nac-auth" in emu.interfaces["GigabitEthernet0/0/3"]
    assert all("Lab-Radius-Secret1" not in str(r["output"]) for r in job["per_device_results"])


async def test_reference_server_mab(app: Any) -> None:
    from pyrad import packet
    from pyrad.dictionary import Dictionary

    from app.nac.reference_server import ReferenceServerDisabled, serve
    from app.nac.udp import exchange

    with pytest.raises(ReferenceServerDisabled):
        await serve(make_settings(), app.state.services.nac, "x")
    settings = make_settings(radius_reference_server_enabled=True)
    transports = await serve(
        settings, app.state.services.nac, "Ref-Secret-1", auth_port=0, acct_port=0
    )
    port = transports[0].get_extra_info("sockname")[1]
    try:
        dictionary = Dictionary(str(DICTIONARY))

        def ask(mac: str, password: str) -> Any:
            req = packet.AuthPacket(
                code=packet.AccessRequest, secret=b"Ref-Secret-1", dict=dictionary
            )
            req["User-Name"] = mac
            req["User-Password"] = req.PwCrypt(password)
            req["Calling-Station-Id"] = mac
            return exchange("127.0.0.1", port, req, timeout=2, retries=1)

        reply = await asyncio.to_thread(ask, "00e0fc0000aa", "00e0fc0000aa")
        assert reply.code == packet.AccessAccept
        assert reply["Tunnel-Private-Group-Id"] == ["999"]
        assert (await asyncio.to_thread(ask, "00e0fc0000ab", "wrong")).code == packet.AccessReject
    finally:
        for transport in transports:
            transport.close()


def test_no_arbitrary_command_path_in_openapi(app: Any) -> None:
    """Free-form CLI is only accepted by execute-command (classified, gated); nothing else."""
    schema = app.openapi()
    suspicious = {"command", "commands", "cli", "lines", "config", "script"}
    offenders = []
    for name, component in schema["components"]["schemas"].items():
        props = set(component.get("properties", {}))
        hits = props & suspicious
        if hits and not name.endswith(("Out", "Response")):
            offenders.append((name, sorted(hits)))
    assert offenders == [("ExecuteCommandRequest", ["command"])], offenders
    # Job params are typed per job type; only device.show takes a command and it is SAFE-only.
    from app.services.job_types import REGISTRY

    with_command = [n for n, t in REGISTRY.items() if set(t.params.model_fields) & suspicious]
    assert with_command == ["device.show"]
    for path in schema["paths"]:
        last = path.rstrip("/").rsplit("/", 1)[-1]
        assert last not in {"shell", "exec", "raw", "cli", "run", "config-push"}, path
