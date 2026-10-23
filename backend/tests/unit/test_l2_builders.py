"""L2 builders (idempotent diffs), interface config parser and anomaly heuristics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.exceptions import UnsafeCommand
from app.domain import l2_analysis as a
from app.domain.vlans import InvalidVlan
from app.network.builders import cli_l2
from app.network.parsers.interface_config import InterfaceConfig, parse_interface_config

CFG = """#
interface GigabitEthernet0/0/3
 description printer
 port link-type access
 port default vlan 10
 stp edged-port enable
#
return"""


def test_parse_interface_config() -> None:
    cfg = parse_interface_config(CFG)
    assert cfg.name == "GigabitEthernet0/0/3"
    assert cfg.link_type == "access"
    assert cfg.default_vlan == 10
    assert cfg.description == "printer"
    assert cfg.stp_edged
    trunk = parse_interface_config(
        "interface XGigabitEthernet0/0/1\n port link-type trunk\n port trunk allow-pass vlan 10 20 to 22\n shutdown"
    )
    assert trunk.trunk_allowed == {10, 20, 21, 22}
    assert trunk.shutdown


def test_create_vlans_idempotent() -> None:
    plan = cli_l2.create_vlans({10: "Users", 30: None, 31: "New"}, {10: "Users"})
    assert plan.lines == ("vlan batch 30 to 31", "vlan 31", "name New", "quit")
    assert plan.rollback == ("undo vlan batch 30 to 31",)
    assert cli_l2.create_vlans({10: "Users"}, {10: "Users"}).noop
    with pytest.raises(InvalidVlan):
        cli_l2.create_vlans({1: None}, {})
    with pytest.raises(UnsafeCommand):
        cli_l2.create_vlans({40: "x\nreboot"}, {})


def test_delete_vlans() -> None:
    assert cli_l2.delete_vlans([99], {10: None}).noop
    single = cli_l2.delete_vlans([10], {10: "Users", 20: None})
    assert single.lines == ("undo vlan 10",)
    assert single.rollback == ("vlan batch 10", "vlan 10", "name Users", "quit")
    many = cli_l2.delete_vlans([10, 20], {10: None, 20: None})
    assert many.lines == ("undo vlan batch 10 20",)


def test_port_mode_change_resets_existing_vlan_config() -> None:
    current = parse_interface_config(CFG)
    plan = cli_l2.set_port_mode("GE0/0/3", "trunk", current, trunk_vlans=[10, 20])
    assert plan.lines == (
        "interface GigabitEthernet0/0/3",
        "undo port default vlan",
        "port link-type trunk",
        "port trunk allow-pass vlan 10 20",
        "quit",
    )
    assert plan.rollback[-2] == "port default vlan 10"
    same = cli_l2.set_port_mode("GE0/0/3", "access", current, access_vlan=10)
    assert same.noop


def test_trunk_vlans_diff() -> None:
    current = InterfaceConfig(link_type="trunk", trunk_allowed={10, 20})
    plan = cli_l2.trunk_vlans("XGE0/0/1", [20, 30], [10, 99], current)
    assert plan.lines == (
        "interface XGigabitEthernet0/0/1",
        "port trunk allow-pass vlan 30",
        "undo port trunk allow-pass vlan 10",
        "quit",
    )


def test_admin_state_and_description() -> None:
    up = InterfaceConfig(shutdown=False)
    assert cli_l2.set_admin_state("GE0/0/1", True, up).noop
    down = cli_l2.set_admin_state("GE0/0/1", False, up)
    assert down.lines[1] == "shutdown"
    assert down.rollback[1] == "undo shutdown"
    desc = cli_l2.set_description("GE0/0/1", "Uplink to core", InterfaceConfig())
    assert desc.lines[1] == "description Uplink to core"
    with pytest.raises(UnsafeCommand):
        cli_l2.set_description("GE0/0/1", "x; reboot", InterfaceConfig())


def test_static_mac_and_stp() -> None:
    plan = cli_l2.static_mac("00:e0:fc:12:34:56", 10, "GE0/0/1")
    assert plan.lines == ("mac-address static 00e0-fc12-3456 GigabitEthernet0/0/1 vlan 10",)
    hole = cli_l2.static_mac("00:e0:fc:12:34:56", 10, None, blackhole=True, remove=True)
    assert hole.lines == ("undo mac-address blackhole 00e0-fc12-3456 vlan 10",)
    with pytest.raises(ValueError, match="multiple of 4096"):
        cli_l2.stp_settings(None, 1000)
    assert cli_l2.stp_settings("mstp", 4096).lines == (
        "stp mode mstp",
        "stp instance 0 priority 4096",
    )
    assert cli_l2.stp_settings(None, 8192).lines == ("stp priority 8192",)


NOW = datetime(2026, 10, 23, tzinfo=UTC)


def test_mac_flapping() -> None:
    moves = [
        a.MoveObservation("aa:aa:aa:aa:aa:aa", NOW + timedelta(minutes=i), "GE0/0/1", "GE0/0/2")
        for i in range(3)
    ]
    slow = [
        a.MoveObservation("bb:bb:bb:bb:bb:bb", NOW + timedelta(hours=i), "GE0/0/1", "GE0/0/2")
        for i in range(3)
    ]
    findings = a.mac_flapping(moves + slow)
    assert [f.key for f in findings] == ["aa:aa:aa:aa:aa:aa"]


@pytest.mark.parametrize(
    ("arp", "binds", "kinds"),
    [
        (
            [a.ArpObservation("d1", "10.0.0.1", "m1"), a.ArpObservation("d2", "10.0.0.1", "m2")],
            [],
            {"duplicate_ip"},
        ),
        (
            [
                a.ArpObservation("d1", "10.0.0.1", "m1", "interface"),
                a.ArpObservation("d2", "10.0.0.1", "m2"),
            ],
            [],
            set(),
        ),
        (
            [a.ArpObservation("d1", "10.0.0.9", "m9")],
            [a.BindingObservation("d1", "10.0.0.9", "m1")],
            {"binding_arp_mismatch"},
        ),
        (
            [],
            [
                a.BindingObservation("d1", "10.0.0.7", "m1"),
                a.BindingObservation("d2", "10.0.0.7", "m2"),
            ],
            {"dhcp_duplicate_ip"},
        ),
    ],
)
def test_ip_anomalies(
    arp: list[a.ArpObservation], binds: list[a.BindingObservation], kinds: set[str]
) -> None:
    found = a.duplicate_ips(arp) + a.binding_mismatches(binds, arp) + a.duplicate_bindings(binds)
    assert {f.kind for f in found} == kinds


def test_arp_many_ips_and_unknown_macs() -> None:
    arp = [a.ArpObservation("d", f"10.0.1.{i}", "pp") for i in range(20)]
    assert a.arp_anomalies(arp)[0].kind == "arp_many_ips"
    assert a.arp_anomalies(arp, router_macs=frozenset({"pp"})) == []
    obs = [
        a.MacObservation("d", "m1", 10, "GE0/0/1"),
        a.MacObservation("d", "m2", 10, "XGE0/0/1", False),
    ]
    assert [f.key for f in a.unknown_macs(obs, known={"m3"})] == ["m1"]
    assert [f.key for f in a.untrusted_dhcp_ports(["GE0/0/5", "XGE0/0/1"], ["XGE0/0/1"])] == [
        "GE0/0/5"
    ]
