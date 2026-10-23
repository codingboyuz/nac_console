"""Phase 10: polling (CLI + SNMP simulator), storage/history, alert lifecycle, deliveries,
event alerts, maintenance windows, discovery proposals/adoption, settings, partitions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.rbac import Role
from app.models.monitoring import Alert, AlertDelivery, MetricSample
from app.services.notifications import ChannelError, Notification
from tests.e2e.snmp_simulator import SnmpSimulator, huawei_switch_mib
from tests.fakes import FakeLab, install


class RecordingChannel:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.sent: list[Notification] = []

    async def send(self, note: Notification, destination: dict[str, Any]) -> None:
        if self.fail:
            raise ChannelError("receiver down")
        self.sent.append(note)


@pytest.fixture
def lab(app: Any) -> FakeLab:
    fake = FakeLab()
    install(app, fake)
    fake.add("192.0.2.1")
    return fake


@pytest.fixture
def channels(app: Any) -> dict[str, RecordingChannel]:
    recorders = {
        "webhook": RecordingChannel("webhook"),
        "telegram": RecordingChannel("telegram"),
        "email": RecordingChannel("email", fail=True),
    }
    app.state.services.alerts.channels = recorders
    return recorders


async def _device(client: AsyncClient, headers: dict[str, str], **extra: Any) -> str:
    cred = await client.post(
        "/api/v1/credentials",
        json={"name": "c", "kind": "cli", "username": "u", "password": "Fake-Passw0rd!"},
        headers=headers,
    )
    resp = await client.post(
        "/api/v1/devices",
        json={
            "hostname": "SW-01",
            "mgmt_ip": "192.0.2.1",
            "family": "S",
            "credential_id": cred.json()["id"],
            **extra,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def test_cli_poll_history_latest(
    client: AsyncClient, app: Any, auth_headers: Any, lab: FakeLab
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers)
    samples = (await client.post(f"/api/v1/devices/{dev}/poll", headers=headers)).json()
    metrics = {s["metric"] for s in samples}
    assert {"cpu", "memory", "if_oper"} <= metrics
    hist = (await client.get(f"/api/v1/devices/{dev}/metrics?metric=cpu", headers=headers)).json()
    assert len(hist) == 1
    latest = (await client.get(f"/api/v1/devices/{dev}/metrics/latest", headers=headers)).json()
    assert "cpu" in latest


async def test_snmp_poll_rates_and_interface_down_alert(
    client: AsyncClient, app: Any, auth_headers: Any, channels: dict[str, RecordingChannel]
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    with SnmpSimulator(huawei_switch_mib(0)) as sim:
        snmp = await client.post(
            "/api/v1/credentials",
            json={"name": "snmp", "kind": "snmp_v2c", "community": sim.community},
            headers=headers,
        )
        cli = await client.post(
            "/api/v1/credentials",
            json={"name": "cli", "kind": "cli", "username": "u", "password": "Fake-Passw0rd!"},
            headers=headers,
        )
        resp = await client.post(
            "/api/v1/devices",
            json={
                "hostname": "SW-SNMP",
                "mgmt_ip": "127.0.0.1",
                "credential_id": cli.json()["id"],
                "snmp_credential_id": snmp.json()["id"],
                "snmp_port": sim.port,
            },
            headers=headers,
        )
        dev = resp.json()["id"]
        first = (await client.post(f"/api/v1/devices/{dev}/poll", headers=headers)).json()
        assert {"cpu", "memory", "temperature", "if_oper"} <= {s["metric"] for s in first}
        sim.load(huawei_switch_mib(1))
        second = (await client.post(f"/api/v1/devices/{dev}/poll", headers=headers)).json()
        assert any(s["metric"] == "if_in_bps" and s["value"] for s in second)
    await app.state.services.alerts.drain()
    alerts = (await client.get("/api/v1/alerts?state=FIRING", headers=headers)).json()["items"]
    down = [a for a in alerts if a["alert_type"] == "interface_down"]
    assert down
    assert down[0]["message"] == "Huawei SW-SNMP GigabitEthernet0/0/2 is DOWN"
    assert channels["webhook"].sent
    assert channels["webhook"].sent[0].text.endswith("is DOWN")
    deliveries = (
        await client.get(f"/api/v1/alerts/{down[0]['id']}/deliveries", headers=headers)
    ).json()
    email = next(d for d in deliveries if d["channel"] == "email")
    assert email["status"] == "DEAD"
    assert email["attempts"] == 5
    assert email["last_error"] == "receiver down"


async def test_threshold_dedupe_ack_resolve_and_maintenance(
    client: AsyncClient,
    app: Any,
    auth_headers: Any,
    lab: FakeLab,
    channels: dict[str, RecordingChannel],
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev_id = await _device(client, headers, site="hq")
    rule = await client.put(
        "/api/v1/alert-rules",
        json={
            "name": "high-cpu",
            "alert_type": "high_cpu",
            "metric": "cpu",
            "threshold": 80,
            "clear_threshold": 60,
            "for_seconds": 0,
            "channels": ["webhook"],
        },
        headers=headers,
    )
    assert rule.status_code == 200, rule.text
    svc = app.state.services
    device = await svc.devices.get(await _principal(app, headers), uuid.UUID(dev_id))
    await svc.alerts.process_sample(device, "cpu", 95.0)
    await svc.alerts.process_sample(device, "cpu", 97.0)  # still firing: deduplicated
    async with svc.db.session() as s:
        rows = (
            (await s.execute(select(Alert).where(Alert.alert_type == "high_cpu"))).scalars().all()
        )
    assert len(rows) == 1
    alert_id = rows[0].id
    ack = await client.post(f"/api/v1/alerts/{alert_id}/ack", headers=headers)
    assert ack.json()["state"] == "ACKNOWLEDGED"
    await svc.alerts.process_sample(device, "cpu", 70.0)  # hysteresis: not yet cleared
    async with svc.db.session() as s:
        assert (await s.get(Alert, alert_id)).state == "ACKNOWLEDGED"  # type: ignore[union-attr]
    await svc.alerts.process_sample(device, "cpu", 50.0)
    async with svc.db.session() as s:
        assert (await s.get(Alert, alert_id)).state == "RESOLVED"  # type: ignore[union-attr]

    now = datetime.now(UTC)
    win = await client.post(
        "/api/v1/maintenance-windows",
        json={
            "scope": {"site": "hq"},
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
            "reason": "upgrade",
        },
        headers=headers,
    )
    assert win.status_code == 201
    channels["webhook"].sent.clear()
    await svc.alerts.process_sample(device, "cpu", 99.0)
    await svc.alerts.drain()
    async with svc.db.session() as s:
        newest = (
            (
                await s.execute(
                    select(Alert).where(Alert.alert_type == "high_cpu", Alert.state == "FIRING")
                )
            )
            .scalars()
            .one()
        )
    assert newest.suppressed is True
    assert channels["webhook"].sent == []


async def _principal(app: Any, headers: dict[str, str]) -> Any:
    principal, _ = await app.state.services.auth.authenticate(headers["Authorization"].split()[1])
    return principal


async def test_device_offline_event_alert(
    client: AsyncClient,
    app: Any,
    auth_headers: Any,
    lab: FakeLab,
    channels: dict[str, RecordingChannel],
) -> None:
    headers = await auth_headers(Role.NETWORK_ADMIN)
    dev = await _device(client, headers)
    assert (
        await client.post(f"/api/v1/devices/{dev}/test-connection", headers=headers)
    ).status_code == 200
    lab.down.add("192.0.2.1")
    assert (
        await client.post(f"/api/v1/devices/{dev}/test-connection", headers=headers)
    ).status_code == 504
    firing = (await client.get("/api/v1/alerts?state=FIRING", headers=headers)).json()["items"]
    assert [a["alert_type"] for a in firing] == ["device_offline"]
    lab.down.clear()
    await client.post(f"/api/v1/devices/{dev}/test-connection", headers=headers)
    assert (await client.get("/api/v1/alerts?state=FIRING", headers=headers)).json()["total"] == 0
    await app.state.services.alerts.drain()


async def test_discovery_proposals_and_adoption(
    client: AsyncClient, app: Any, auth_headers: Any
) -> None:
    class FakeProbes:
        async def ping(self, ip: str) -> bool:
            return ip in {"192.0.2.10", "192.0.2.11"}

        async def snmp_system(self, ip: str, secret: Any) -> dict[str, Any] | None:
            return (
                {
                    "sys_name": "SW-NEW",
                    "sys_descr": "HUAWEI S5735-L48T4X-A1 VRP 5.170",
                    "sys_object_id": "1.3.6.1.4.1.2011.2.23.629",
                }
                if ip == "192.0.2.10"
                else None
            )

        async def ssh_banner(self, ip: str, port: int = 22) -> str | None:
            return "SSH-2.0-HUAWEI-1.5"

    app.state.services.discovery.probes = FakeProbes()
    headers = await auth_headers(Role.NETWORK_ADMIN)
    snmp = await client.post(
        "/api/v1/credentials",
        json={"name": "snmp", "kind": "snmp_v2c", "community": "lab-community"},
        headers=headers,
    )
    too_big = await client.post("/api/v1/discovery", json={"cidr": "10.0.0.0/16"}, headers=headers)
    assert too_big.status_code == 422
    outside = await client.post(
        "/api/v1/discovery", json={"cidr": "198.51.100.0/28"}, headers=headers
    )
    assert outside.status_code == 422
    run = await client.post(
        "/api/v1/discovery",
        json={"cidr": "192.0.2.8/29", "snmp_credential_ids": [snmp.json()["id"]]},
        headers=headers,
    )
    assert run.status_code == 202, run.text
    await app.state.services.discovery.drain()
    result = (await client.get(run.json()["location"], headers=headers)).json()
    assert result["status"] == "SUCCESS"
    assert result["hosts_scanned"] == 6
    by_ip = {d["ip"]: d for d in result["devices"]}
    assert set(by_ip) == {"192.0.2.10", "192.0.2.11"}
    assert by_ip["192.0.2.10"]["family"] == "S"
    assert by_ip["192.0.2.10"]["is_huawei"] is True
    devices_before = (await client.get("/api/v1/devices", headers=headers)).json()["total"]
    assert devices_before == 0  # discovery never auto-onboards
    proposal = by_ip["192.0.2.10"]["id"]
    adopt = await client.post(
        f"/api/v1/discovery/devices/{proposal}/adopt",
        json={"hostname": "SW-NEW", "mgmt_ip": "192.0.2.10", "family": "S"},
        headers=headers,
    )
    assert adopt.status_code == 200, adopt.text
    again = await client.post(
        f"/api/v1/discovery/devices/{proposal}/adopt",
        json={"hostname": "SW-NEW", "mgmt_ip": "192.0.2.10"},
        headers=headers,
    )
    assert again.status_code == 409
    operator = await auth_headers(Role.OPERATOR)
    assert (
        await client.post(
            f"/api/v1/discovery/devices/{by_ip['192.0.2.11']['id']}/adopt",
            json={"hostname": "X", "mgmt_ip": "192.0.2.11"},
            headers=operator,
        )
    ).status_code == 403


async def test_settings_api(client: AsyncClient, auth_headers: Any) -> None:
    admin = await auth_headers(Role.ADMIN)
    operator = await auth_headers(Role.OPERATOR)
    body = {
        "webhook": {"url": "https://hooks.example.test/nac"},
        "telegram": {"chat_id": "-1001234567"},
    }
    assert (
        await client.put("/api/v1/settings/alert_channels", json=body, headers=operator)
    ).status_code == 403
    resp = await client.put("/api/v1/settings/alert_channels", json=body, headers=admin)
    assert resp.status_code == 200, resp.text
    assert (await client.get("/api/v1/settings/alert_channels", headers=operator)).json()[
        "telegram"
    ]["chat_id"] == "-1001234567"
    bad = await client.put(
        "/api/v1/settings/alert_channels", json={"telegram": {"chat_id": "x;y"}}, headers=admin
    )
    assert bad.status_code == 422
    assert (await client.get("/api/v1/settings/nope", headers=admin)).status_code == 404
    schedules = (await client.get("/api/v1/settings/schedules", headers=admin)).json()
    assert any(e["job_type"] == "poll.metrics" for e in schedules["entries"])


async def test_partition_maintenance_and_retention(app: Any) -> None:
    svc = app.state.services
    async with svc.db.session() as s:
        s.add(
            MetricSample(
                ts=datetime.now(UTC) - timedelta(days=400),
                device_id=uuid.uuid4(),
                metric="cpu",
                value=1,
            )
        )
        s.add(MetricSample(ts=datetime.now(UTC), device_id=uuid.uuid4(), metric="cpu", value=2))
        await s.commit()
    report = await svc.monitoring.maintain()
    async with svc.db.session() as s:
        values = sorted(v for (v,) in (await s.execute(select(MetricSample.value))).all())
        if s.bind.dialect.name == "postgresql":
            parts = (
                (
                    await s.execute(
                        text(
                            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid "
                            "JOIN pg_class p ON p.oid=i.inhparent WHERE p.relname='metric_samples'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(report["created"]) == 3
            assert set(report["created"]) <= set(parts)
    assert values == [2.0]


async def test_alert_delivery_rows_recorded(
    app: Any, channels: dict[str, RecordingChannel]
) -> None:
    svc = app.state.services
    alert = await svc.alerts.open(
        "authentication_failures", None, "5 failed logins", key_suffix="admin"
    )
    await svc.alerts.drain()
    async with svc.db.session() as s:
        rows = (
            (await s.execute(select(AlertDelivery).where(AlertDelivery.alert_id == alert.id)))
            .scalars()
            .all()
        )
    assert {r.channel: r.status for r in rows} == {
        "webhook": "SENT",
        "telegram": "SENT",
        "email": "DEAD",
    }
