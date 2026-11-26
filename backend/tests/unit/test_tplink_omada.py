"""Phase 18: Omada Controller platform (change language, plans, guards, driver vs mock)."""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from app.core.exceptions import CommandRejected, DeviceAuthFailed, UnsupportedCapability
from app.domain.commands.categories import CommandCategory
from app.domain.commands.classifier import DeviceContext
from app.domain.commands.lockout import LockoutContext
from app.domain.devices import DeviceProtocol
from app.domain.platforms import CommitModel, Platform, RollbackTimer
from app.network.drivers.omada_api import OmadaApiDriver
from app.network.parsers.interface_config import InterfaceConfig
from app.network.platforms import Q, adapter_for
from app.network.platforms.tplink_omada import builders as b
from app.network.platforms.tplink_omada import config_text
from app.network.platforms.tplink_omada import parsers as p
from app.network.platforms.tplink_omada.dsl import PortOp, VlanOp, parse_line
from app.network.platforms.tplink_omada.rules import OmadaClassifier, OmadaLockoutGuard
from app.network.ports import ConnectionParams
from tests.e2e.omada_mock import OMADAC, OmadaState

S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS


def _driver(state: OmadaState, secret: str | None = None) -> OmadaApiDriver:
    params = ConnectionParams(
        device_id="d1",
        host=state.switch_ip,
        port=443,
        protocol=DeviceProtocol.API,
        platform=Platform.TPLINK_OMADA,
        username=state.client_id,
        password=SecretStr(secret or state.client_secret),
        api={"controller_url": "https://omada.test:8043", "omadac_id": OMADAC, "site": "HQ"},
    )
    return OmadaApiDriver(params)


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> OmadaState:
    st = OmadaState()
    monkeypatch.setattr(OmadaApiDriver, "transport", st.transport())
    return st


def test_adapter_shape() -> None:
    adapter = adapter_for(Platform.TPLINK_OMADA)
    assert adapter.commit_model is CommitModel.API
    assert adapter.rollback_timer is RollbackTimer.NONE
    assert list(adapter.drivers) == ["api"]
    assert adapter.command(Q.INTERFACE, name="GigabitEthernet1/0/7") == "GET port 7"
    assert not adapter.supports(Q.ROUTES)


def test_change_language() -> None:
    assert parse_line("vlan create 300 name LAB") == VlanOp("create", 300, "LAB")
    assert parse_line("vlan delete 300") == VlanOp("delete", 300)
    assert parse_line("port 5 tagged 10-12,20").tagged() == [10, 11, 12, 20]  # type: ignore[union-attr]
    assert parse_line("port 5 disable") == PortOp(5, "disable")
    for bad in ("port 5 native", "port 5 disable now", "vlan 5", "display current"):
        with pytest.raises(ValueError, match="not an Omada change line"):
            parse_line(bad)


def test_plans() -> None:
    assert b.create_vlans({300: "LAB", 10: "Staff"}, {10: "Users"}).lines == (
        "vlan rename 10 name Staff",
        "vlan create 300 name LAB",
    )
    current = InterfaceConfig(hybrid_pvid=1, hybrid_untagged={1})
    plan = b.set_port_mode("Gi1/0/5", "access", current, access_vlan=300)
    assert plan.lines == ("port 5 native 300",)
    assert plan.rollback == ("port 5 native 1",)
    trunk = b.trunk_vlans("Gi1/0/25", [20], [10], InterfaceConfig(hybrid_tagged={10, 100}))
    assert trunk.lines == ("port 25 tagged 20,100",)
    assert b.set_admin_state("Gi1/0/5", False, InterfaceConfig()).lines == ("port 5 disable",)
    with pytest.raises(UnsupportedCapability, match="Omada controller"):
        b.static_route(None, set())  # type: ignore[arg-type]
    with pytest.raises(UnsupportedCapability, match="one untagged"):
        b.set_port_mode("Gi1/0/5", "hybrid", current, untagged=[10, 20])


def test_classifier_and_lockout() -> None:
    c = OmadaClassifier()
    assert c.classify_exec("GET clients").category is S
    assert c.classify_exec("port 5 disable").category is D  # no changes from the console
    ctx = DeviceContext(mgmt_vlan=100, uplink_interfaces=frozenset({"GigabitEthernet1/0/25"}))
    assert c.classify_config(["port 5 disable"], ctx).category is W
    assert c.classify_config(["port 25 disable"], ctx).category is D
    assert c.classify_config(["vlan delete 100"], ctx).category is D
    guard = OmadaLockoutGuard()
    lctx = LockoutContext(ctx)
    assert guard.check(["port 25 tagged 10"], lctx)
    assert guard.check(["port 25 tagged 10,100"], lctx) == []
    assert guard.check(["port 25 disable"], lctx)
    assert guard.check(["vlan delete 100"], lctx)
    assert guard.check(["port 5 disable"], lctx) == []


def test_parsers_and_config_text(state: OmadaState) -> None:
    with _driver(state) as drv:
        ports = drv.run_show("GET ports").output
        brief = {x.name: x for x in p.parse_interface_brief(ports)}
        assert brief["GigabitEthernet1/0/4"].admin_down
        detail = {d.name: d for d in p.parse_interface_detail(ports)}
        assert detail["GigabitEthernet1/0/1"].speed_mbps == 1000
        cfg = p.parse_interface_config(drv.run_show("GET port 25").output)
        assert (cfg.link_type, cfg.trunk_allowed, cfg.trunk_pvid) == ("trunk", {10, 100}, 100)
        info = p.parse_version(drv.run_show("GET switch").output)
        assert (info.model, info.uptime_seconds) == ("SG3428", 268066)
        assert p.parse_cpu_usage(drv.run_show("GET switch").output).one_minute == 9.0
        assert [v.vlan_id for v in p.parse_vlans(drv.run_show("GET networks").output)] == [
            1,
            10,
            100,
        ]
        macs = p.parse_mac_table(drv.run_show("GET clients").output)
        assert [(m.mac, m.interface) for m in macs] == [
            ("00:e0:fc:12:34:56", "GigabitEthernet1/0/1")
        ]  # clients of other switches are filtered out
        snapshot = drv.run_show("GET config").output
    data = json.loads(snapshot)
    assert {
        "port": 25,
        "name": "UPLINK",
        "disable": False,
        "nativeVlan": 100,
        "taggedVlans": [10, 100],
    } in data["ports"]
    changed = json.loads(snapshot)
    changed["ports"][4]["nativeVlan"] = 10
    changed["networks"].append({"vlan": 300, "name": "LAB"})
    restore = config_text.restore_plan(json.dumps(changed), snapshot)
    assert restore.lines == ("port 5 native 1",)
    assert restore.notes == ("VLAN 300 exists only on the controller; not removed automatically",)
    assert config_text.config_hash(snapshot) == config_text.config_hash(
        json.dumps(json.loads(snapshot), indent=4)
    )


def test_driver_writes_and_errors(state: OmadaState) -> None:
    with _driver(state) as drv:
        drv.push_config(["vlan create 300 name LAB", "port 5 native 300", "port 5 tagged 10"])
        assert state.port(5)["nativeNetworkId"] == state.networks[-1]["id"]
        assert state.port(5)["tagNetworkIds"] == ["net-10"]
        assert state.port(5)["profileOverrideEnable"] is True
        with pytest.raises(CommandRejected, match="no LAN network"):
            drv.push_config(["port 6 native 999"])
        with pytest.raises(CommandRejected):
            drv.push_config(["display version"])
        with pytest.raises(UnsupportedCapability):
            drv.run_show("show version")
    with pytest.raises(DeviceAuthFailed):
        _driver(state, secret="wrong").connect()
