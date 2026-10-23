"""Coverage for read-side L2 features (ARP/DHCP/STP/interfaces) and alert channel transports."""

from __future__ import annotations

import json
import smtplib
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.rbac import Role
from app.services.notifications import (
    ChannelError,
    EmailChannel,
    Notification,
    TelegramChannel,
    WebhookChannel,
    sign,
)
from tests.conftest import make_settings
from tests.fakes import FakeLab, install


@pytest.fixture
def lab(app: Any) -> FakeLab:
    fake = FakeLab()
    install(app, fake)
    emu = fake.add("192.0.2.1")
    emu.outputs["display interface GigabitEthernet0/0/1"] = emu.outputs["display interface"]
    emu.interfaces["GigabitEthernet0/0/5"] = ["dhcp snooping trusted"]
    return fake


async def _device(client: AsyncClient, headers: dict[str, str]) -> str:
    cred = await client.post(
        "/api/v1/credentials",
        json={"name": "c", "kind": "cli", "username": "u", "password": "Fake-Passw0rd!"},
        headers=headers,
    )
    resp = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "SW-1",
            "mgmt_ip": "192.0.2.1",
            "family": "S",
            "credential_id": cred.json()["id"],
            "uplink_interfaces": ["XGE0/0/1"],
        },
        headers=headers,
    )
    return str(resp.json()["id"])


async def test_arp_dhcp_collection_search_and_audits(
    client: AsyncClient, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers)
    counts = (await client.post(f"/api/v1/arp/collect/{dev}", headers=headers)).json()
    assert counts["arp"] >= 3
    assert counts["dhcp_bindings"] >= 1
    arp = (await client.get("/api/v1/arp", headers=headers)).json()
    first = arp[0]
    by_ip = (await client.get(f"/api/v1/arp?ip={first['ip']}", headers=headers)).json()
    assert by_ip[0]["mac"] == first["mac"]
    by_mac = (await client.get(f"/api/v1/arp?mac={first['mac']}", headers=headers)).json()
    assert by_mac
    bindings = (await client.get("/api/v1/dhcp/bindings", headers=headers)).json()
    assert bindings
    assert (await client.get(f"/api/v1/dhcp/bindings?device_id={dev}", headers=headers)).json()
    anomalies = await client.get("/api/v1/anomalies", headers=headers)
    assert anomalies.status_code == 200
    audit = (await client.get(f"/api/v1/dhcp/trust-audit/{dev}", headers=headers)).json()
    assert [f["key"] for f in audit] == ["GigabitEthernet0/0/5"]
    snooping = await client.put(
        f"/api/v1/dhcp/snooping/{dev}",
        json={"vlans": "10,20", "trusted_interfaces": ["XGE0/0/1"], "dry_run": True},
        headers=headers,
    )
    assert snooping.status_code == 200, snooping.text
    assert "dhcp snooping enable vlan 10 20" in snooping.json()["lines"]


async def test_stp_status_and_settings(
    client: AsyncClient, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers)
    status = (await client.get(f"/api/v1/devices/{dev}/stp", headers=headers)).json()
    assert status["mode"]
    assert status["ports"]
    cfg = await client.put(
        f"/api/v1/devices/{dev}/stp",
        json={"mode": "rstp", "priority": 8192, "dry_run": True},
        headers=headers,
    )
    assert cfg.json()["lines"] == ["stp mode rstp", "stp priority 8192"]
    edge = await client.put(
        f"/api/v1/devices/{dev}/stp/edge-ports/GE0%2F0%2F3", json={"enabled": True}, headers=headers
    )
    assert edge.status_code == 200, edge.text
    assert "stp edged-port enable" in lab.devices["192.0.2.1"].interfaces["GigabitEthernet0/0/3"]
    bad = await client.put(f"/api/v1/devices/{dev}/stp", json={"priority": 1000}, headers=headers)
    assert bad.status_code == 422


async def test_interfaces_inventory_detail_and_changes(
    client: AsyncClient, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers)
    reader = await auth_headers(Role.READ_ONLY)
    assert (
        await client.get(f"/api/v1/devices/{dev}/interfaces?refresh=true", headers=reader)
    ).status_code == 403
    rows = (
        await client.get(f"/api/v1/devices/{dev}/interfaces?refresh=true", headers=headers)
    ).json()
    assert any(r["is_uplink"] for r in rows)
    cached = (await client.get(f"/api/v1/devices/{dev}/interfaces", headers=reader)).json()
    assert len(cached) == len(rows)
    detail = await client.get(f"/api/v1/devices/{dev}/interfaces/GE0%2F0%2F1", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"]
    desc = await client.put(
        f"/api/v1/devices/{dev}/interfaces/GE0%2F0%2F7/description",
        json={"description": "printer-floor2"},
        headers=headers,
    )
    assert desc.json()["status"] == "applied"
    speed = await client.put(
        f"/api/v1/devices/{dev}/interfaces/GE0%2F0%2F7/speed",
        json={"auto": False, "speed": 100, "duplex": "full", "dry_run": True},
        headers=headers,
    )
    assert speed.json()["lines"][1:4] == ["undo negotiation auto", "speed 100", "duplex full"]
    shut = await client.put(
        f"/api/v1/devices/{dev}/interfaces/GE0%2F0%2F7/admin-state",
        json={"enabled": False},
        headers=headers,
    )
    assert shut.json()["status"] == "applied"
    trunk = await client.post(
        f"/api/v1/devices/{dev}/interfaces/XGE0%2F0%2F1/trunk-vlans",
        json={"add": "30", "dry_run": True},
        headers=headers,
    )
    assert trunk.status_code == 200


async def test_user_password_flows_and_credentials(
    client: AsyncClient, app: Any, auth_headers: Any, user_factory: Any, login: Any
) -> None:
    admin_headers = await auth_headers(Role.ADMIN)
    user = await user_factory(Role.OPERATOR)
    tokens = await login(user.username)
    own = {"Authorization": f"Bearer {tokens['access_token']}"}
    wrong = await client.post(
        f"/api/v1/users/{user.id}/password",
        json={"current_password": "nope", "new_password": "N3w!Passw0rd-x"},
        headers=own,
    )
    assert wrong.status_code == 401
    ok = await client.post(
        f"/api/v1/users/{user.id}/password",
        json={"current_password": "Str0ng!Passw0rd", "new_password": "N3w!Passw0rd-x"},
        headers=own,
    )
    assert ok.status_code == 204
    other = await user_factory(Role.OPERATOR)
    assert (
        await client.post(
            f"/api/v1/users/{other.id}/password",
            json={"new_password": "N3w!Passw0rd-x"},
            headers=(await auth_headers(Role.NETWORK_ADMIN)),
        )
    ).status_code == 403
    reset = await client.post(
        f"/api/v1/users/{other.id}/password",
        json={"new_password": "R3set!Passw0rd"},
        headers=admin_headers,
    )
    assert reset.status_code == 204
    assert (
        await client.delete(f"/api/v1/users/{other.id}", headers=admin_headers)
    ).status_code == 204
    users = (await client.get("/api/v1/users?is_active=false", headers=admin_headers)).json()
    assert other.username in [u["username"] for u in users["items"]]

    cred = await client.post(
        "/api/v1/credentials",
        json={
            "name": "rot",
            "kind": "snmp_v3",
            "username": "mon",
            "auth_protocol": "SHA",
            "auth_key": "authkey-123",
            "priv_protocol": "AES128",
            "priv_key": "privkey-123",
        },
        headers=admin_headers,
    )
    assert cred.status_code == 201, cred.text
    upd = await client.patch(
        f"/api/v1/credentials/{cred.json()['id']}",
        json={"auth_key": "authkey-456", "description": "rotated"},
        headers=admin_headers,
    )
    assert upd.json()["description"] == "rotated"
    resolved = await app.state.services.credentials.resolve(
        __import__("uuid").UUID(cred.json()["id"])
    )
    assert resolved.auth_key.get_secret_value() == "authkey-456"  # type: ignore[union-attr]
    assert (
        await client.delete(f"/api/v1/credentials/{cred.json()['id']}", headers=admin_headers)
    ).status_code == 204


async def test_radius_server_secret_update(app: Any) -> None:
    svc = app.state.services.radius
    row = await svc.create(
        name="r1",
        host="192.0.2.60",
        shared_secret=SecretStr("Old-Secret-1"),
        auth_port=1812,
        acct_port=1813,
        coa_port=3799,
        request_timeout=5,
        retries=3,
        enabled=True,
    )
    await svc.update_secret(row.id, SecretStr("New-Secret-2"))
    assert svc.secret(await svc.get(row.id)) == "New-Secret-2"
    await svc.delete(row.id)
    with pytest.raises(Exception):  # noqa: B017,PT011 - NotFound
        await svc.get(row.id)


NOTE = Notification(
    "interface_down", "Huawei SW-01 GigabitEthernet0/0/12 is DOWN", "critical", {"alert_id": "a1"}
)


async def test_webhook_channel_signs_requests() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = make_settings(webhook_signing_secret="hook-secret-123")
    channel = WebhookChannel(settings, client)
    await channel.send(NOTE, {"url": "https://hooks.example.test/nac"})
    req = seen[0]
    body = req.content
    assert req.headers["X-NAC-Signature"] == sign(
        "hook-secret-123", req.headers["X-NAC-Timestamp"], body
    )
    assert json.loads(body)["text"].endswith("is DOWN")
    with pytest.raises(ChannelError, match="https"):
        await channel.send(NOTE, {"url": "http://plain.example.test"})
    failing = WebhookChannel(
        settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    with pytest.raises(ChannelError, match="500"):
        await failing.send(NOTE, {"url": "https://hooks.example.test/nac"})


async def test_telegram_channel_hides_token() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return (
            httpx.Response(200, json={"ok": True})
            if b"-100" in request.content
            else httpx.Response(400)
        )

    settings = make_settings(telegram_bot_token="123456:FAKE-BOT-TOKEN")
    channel = TelegramChannel(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await channel.send(NOTE, {"chat_id": "-100123"})
    assert "/bot123456:FAKE-BOT-TOKEN/sendMessage" in urls[0]
    with pytest.raises(ChannelError) as err:
        await channel.send(NOTE, {"chat_id": "42"})
    assert "FAKE-BOT-TOKEN" not in str(err.value)
    with pytest.raises(ChannelError, match="not configured"):
        await TelegramChannel(make_settings()).send(NOTE, {"chat_id": "1"})


async def test_email_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            self.host = host

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def starttls(self, context: Any) -> None:
            sent.append("tls")

        def login(self, user: str, password: str) -> None:
            sent.append(("login", user))

        def send_message(self, msg: Any) -> None:
            sent.append(msg["Subject"])

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    settings = make_settings(
        smtp_host="smtp.example.test",
        smtp_from="nac@example.test",
        smtp_username="nac",
        smtp_password="smtp-pass",
    )
    await EmailChannel(settings).send(NOTE, {"to": ["noc@example.test"]})
    assert sent == ["tls", ("login", "nac"), "[CRITICAL] interface_down"]
    with pytest.raises(ChannelError, match="recipients"):
        await EmailChannel(settings).send(NOTE, {})
    with pytest.raises(ChannelError, match="not configured"):
        await EmailChannel(make_settings()).send(NOTE, {"to": ["a@example.test"]})
