"""Phase 15: Cisco NX-OS adapter - parsers, builders, classifier, lockout."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.acl import AclKind, AclRuleSpec, AclValidationError, PortMatch
from app.domain.commands.categories import CommandCategory
from app.domain.commands.classifier import DeviceContext
from app.domain.commands.lockout import LockoutContext
from app.domain.devices import DeviceFamily
from app.domain.platforms import Platform, RollbackTimer
from app.network.builders.cli_acl import AclDefinition
from app.network.builders.cli_nac import NacProvisionSpec
from app.network.builders.cli_routing import RoutingValidationError, StaticRoute
from app.network.platforms import Q, adapter_for
from app.network.platforms.cisco_nxos import builders_acl as acl
from app.network.platforms.cisco_nxos import builders_l2 as l2
from app.network.platforms.cisco_nxos import builders_nac as nac
from app.network.platforms.cisco_nxos import builders_routing as routing
from app.network.platforms.cisco_nxos import parsers as p
from app.network.platforms.cisco_nxos.rules import NxosClassifier, NxosLockoutGuard

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "cisco" / "n9k_nxos93"
S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_adapter_has_no_rollback_timer() -> None:
    adapter = adapter_for(Platform.CISCO_NXOS)
    assert adapter.rollback_timer is RollbackTimer.NONE
    assert not adapter.supports_rollback_timer
    assert adapter.command(Q.CPU) == "show system resources"
    assert "telnet" not in adapter.drivers
    for query in Q:
        assert adapter.supports(query), query


def test_version_and_interfaces() -> None:
    info = p.parse_version(fx("show_version.txt"))
    assert (info.vrp_version, info.model) == ("9.3(10)", "C93180YC-EX")
    assert info.uptime_seconds == 120 * 86400 + 3 * 3600 + 14 * 60 + 2
    detail = {d.name: d for d in p.parse_interface_detail(fx("show_interface.txt"))}
    eth = detail["Ethernet1/1"]
    assert (eth.speed_mbps, eth.duplex, eth.crc_errors, eth.input_errors) == (10000, "full", 2, 3)
    assert (eth.input_discards, eth.output_discards, eth.link_type) == (4, 1, "access")
    assert eth.input_bytes == 98765432
    assert not detail["Ethernet1/4"].admin_up
    assert not detail["Ethernet1/2"].oper_up
    assert detail["Ethernet1/49"].speed_mbps == 100000
    names = [b.name for b in p.parse_interface_brief(fx("show_interface_status.txt"))]
    assert names[0] == "mgmt0"
    assert "Port-channel10" in names
    assert not any(n.startswith("-") for n in names)


def test_vlans_config_mac_arp_routes() -> None:
    assert {v.vlan_id for v in p.parse_vlans(fx("show_vlan_brief.txt"))} == {1, 10, 100}
    cfg = p.parse_interface_config(fx("show_running-config_interface_Ethernet1-49.txt"))
    assert (cfg.link_type, cfg.trunk_pvid, cfg.trunk_allowed) == ("trunk", 100, {10, 100})
    macs = p.parse_mac_table(fx("show_mac_address-table.txt"))
    assert [m.interface for m in macs] == ["Ethernet1/1", "Port-channel10", "Ethernet1/49", None]
    arp = p.parse_arp(fx("show_ip_arp.txt"))
    assert [a.ip for a in arp] == ["192.0.2.1", "10.10.10.21"]
    routes = p.parse_routing_table(fx("show_ip_route.txt"))
    assert [r.nexthop for r in routes if r.prefix == "10.20.0.0/16"] == ["192.0.2.2", "192.0.2.3"]
    assert next(r for r in routes if r.protocol == "BGP").preference == 20
    direct = next(r for r in routes if r.prefix == "10.10.10.0/24")
    assert (direct.protocol, direct.interface) == ("Direct", "Vlan10")


def test_resources_acls_dhcp() -> None:
    assert p.parse_cpu_usage(fx("show_system_resources.txt")).current_percent == 7.3
    assert p.parse_memory_usage(fx("show_system_resources.txt")).used_percent == 33.1
    acls = {a.name: a for a in p.parse_acls(fx("show_ip_access-lists.txt"))}
    assert acls["USERS-IN"].rules[10] == "10 permit tcp 10.10.10.0/24 any eq 80"
    assert acls["USERS-IN"].matches[10] == 120
    assert p.parse_dhcp_snooping_bindings(fx("show_ip_dhcp_snooping_binding.txt"))[0].vlan == 10
    assert p.management_acls(fx("show_running-config.txt")) == frozenset({"MGMT-ONLY"})


def test_l2_differences() -> None:
    assert l2.stp_edge_port("Eth1/1", True).lines[1] == "spanning-tree port type edge"
    assert l2.stp_settings("rstp", 4096).lines == (
        "spanning-tree mode rapid-pvst",
        "spanning-tree vlan 1-3967 priority 4096",
    )
    with pytest.raises(ValueError, match="rapid-pvst"):
        l2.stp_settings("stp", None)
    assert l2.dhcp_snooping([10], []).lines[0] == "feature dhcp"
    assert l2.create_vlans({30: "Lab"}, {}).lines == ("vlan 30", "name Lab", "exit")


def test_acl_cidr_and_port_vs_routed_apply() -> None:
    desired = AclDefinition(
        AclKind.ADVANCED,
        name="USERS-IN",
        rules=(AclRuleSpec(10, "permit", "tcp", "10.10.10.0/24", None, None, PortMatch("eq", 80)),),
    )
    current = next(a for a in p.parse_acls(fx("show_ip_access-lists.txt")) if a.name == "USERS-IN")
    plan = acl.plan_acl(desired, current)
    assert plan.lines[0] == "ip access-list USERS-IN"
    assert "10 permit tcp 10.10.10.0/24 any eq 80" not in plan.lines  # unchanged
    assert {"no 20", "no 30", "no 40"} <= set(plan.lines)
    port = acl.apply_acl(DeviceFamily.UNKNOWN, "USERS-IN", "inbound", interface="Eth1/1")
    assert port.lines[1] == "ip port access-group USERS-IN in"
    routed = acl.apply_acl(DeviceFamily.UNKNOWN, "USERS-IN", "outbound", interface="Vlan10")
    assert routed.lines[1] == "ip access-group USERS-IN out"
    with pytest.raises(AclValidationError, match="ingress"):
        acl.apply_acl(DeviceFamily.UNKNOWN, "X", "outbound", interface="Eth1/1")


def test_routing_feature_and_per_interface_ospf() -> None:
    assert routing.static_route(StaticRoute("10.40.0.0/16", next_hop="192.0.2.1"), set()).lines == (
        "ip route 10.40.0.0/16 192.0.2.1",
    )
    with pytest.raises(RoutingValidationError, match="network"):
        routing.ospf(1, None, "0", ["10.0.0.0/8"])
    ospf = routing.ospf(1, "10.255.0.1", "0", [], silent_interfaces=["Vlan10"])
    assert ospf.lines[:2] == ("feature ospf", "router ospf 1")
    assert "ip router ospf 1 area 0" in ospf.lines
    bgp = routing.bgp(65001, None, [("192.0.2.5", 65010)], ["203.0.113.0/24"])
    assert "network 203.0.113.0/24" in bgp.lines
    assert "remote-as 65010" in bgp.lines
    with pytest.raises(RoutingValidationError):
        routing.rip(1, 2, ["10.0.0.0/8"])
    vrrp = routing.vrrp("Vlan10", 10, "10.10.10.254", 120, 5)
    assert vrrp.lines[:4] == ("feature vrrp", "interface Vlan10", "vrrp 10", "address 10.10.10.254")
    assert vrrp.notes


def test_nac_template() -> None:
    plan = nac.provision(
        DeviceFamily.UNKNOWN,
        NacProvisionSpec(
            radius_host="192.0.2.50",
            shared_secret="S3c",
            controller_ip="10.0.0.5",
            interfaces=("Eth1/1",),
        ),
    )
    assert plan.lines[0] == "feature dot1x"
    assert "dot1x mac-auth-bypass" in plan.lines
    assert plan.min_category is D


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("show system resources", S),
        ("show interface status", S),
        ("copy running-config startup-config", W),
        ("reload", D),
        ("checkpoint before-change", D),
    ],
)
def test_exec_classification(command: str, category: CommandCategory) -> None:
    assert NxosClassifier().classify_exec(command).category is category


def test_config_classification() -> None:
    c = NxosClassifier()
    assert c.classify_config(["feature ospf"]).category is W
    assert c.classify_config(["no feature ospf"]).category is D
    assert c.classify_config(["interface Eth1/5", "ip router ospf 1 area 0", "exit"]).category is W


def test_lockout_port_acl_on_uplink() -> None:
    dev = DeviceContext(mgmt_vlan=100, uplink_interfaces=frozenset({"Ethernet1/49"}))
    ctx = LockoutContext(dev, ("10.0.0.5",))
    lines = [
        "ip access-list BLOCK",
        "10 deny ip 10.0.0.5/32 any",
        "exit",
        "interface Ethernet1/49",
        "ip port access-group BLOCK in",
        "exit",
    ]
    findings = NxosLockoutGuard().check(lines, ctx)
    assert findings
    assert NxosLockoutGuard().check(["interface Ethernet1/49", "shutdown"], ctx)


def test_lockout_cidr_deny_not_covering_controller_is_safe() -> None:
    dev = DeviceContext(mgmt_vlan=100, uplink_interfaces=frozenset({"Ethernet1/49"}))
    ctx = LockoutContext(dev, ("10.0.0.5",))
    lines = [
        "ip access-list BLOCK",
        "10 deny ip 172.16.0.0/12 any",
        "exit",
        "interface Ethernet1/49",
        "ip port access-group BLOCK in",
        "exit",
    ]
    assert NxosLockoutGuard().check(lines, ctx) == []
