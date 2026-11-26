"""Phase 16: Cisco Small Business adapter - parsers, builders, classifier, lockout, config."""

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
from app.network.parsers.interface_config import InterfaceConfig
from app.network.platforms import Q, adapter_for
from app.network.platforms.cisco_smb import builders_acl as acl
from app.network.platforms.cisco_smb import builders_l2 as l2
from app.network.platforms.cisco_smb import builders_nac as nac
from app.network.platforms.cisco_smb import builders_routing as routing
from app.network.platforms.cisco_smb import config_text
from app.network.platforms.cisco_smb import parsers as p
from app.network.platforms.cisco_smb.rules import SmbClassifier, SmbLockoutGuard

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "cisco" / "cbs350_3x"
S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_adapter_capabilities() -> None:
    adapter = adapter_for(Platform.CISCO_SMB)
    assert adapter.rollback_timer is RollbackTimer.NONE
    assert not adapter.supports(Q.OSPF_NEIGHBORS)
    assert not adapter.supports(Q.MEMORY)
    assert adapter.command(Q.VLANS) == "show vlan"


def test_version_vlans_interfaces() -> None:
    info = p.parse_version(fx("show_version.txt") + "\n" + fx("show_system.txt"))
    assert (info.vrp_version, info.model) == ("3.3.0.16", "CBS350-48P-4X")
    assert info.uptime_seconds == 3 * 86400 + 2 * 3600 + 27 * 60 + 46
    vlans = {v.vlan_id: v for v in p.parse_vlans(fx("show_vlan.txt"))}
    assert len(vlans[1].untagged_ports) == 47  # gi1/0/5-48 + te1/0/2-4 (continuation line)
    assert vlans[10].tagged_ports == ("TenGigabitEthernet1/0/1",)
    assert vlans[10].untagged_ports == ("GigabitEthernet1/0/1", "GigabitEthernet1/0/2")
    brief = {b.name: b for b in p.parse_interface_brief(fx("show_interfaces_status.txt"))}
    assert brief["GigabitEthernet1/0/4"].admin_down  # '*' suspended
    assert brief["Port-channel1"].phy == "up"
    counters = p.parse_interface_detail(fx("show_interfaces_counters_gi1-0-1.txt"))[0]
    assert (counters.crc_errors, counters.input_errors, counters.input_bytes) == (2, 3, 987654321)


def test_interface_config_tables_routes_stp() -> None:
    cfg = p.parse_interface_config(fx("show_running-config_interface_te1-0-1.txt"))
    assert (cfg.link_type, cfg.trunk_pvid, cfg.trunk_allowed) == ("trunk", 100, {10, 20, 100, 999})
    general = p.parse_interface_config(
        "interface gi1/0/9\nswitchport mode general\nswitchport general pvid 30\nexit"
    )
    assert (general.link_type, general.hybrid_pvid) == ("hybrid", 30)
    assert len(p.parse_mac_table(fx("show_mac_address-table.txt"))) == 5  # 'self' dropped
    assert p.parse_mac_aging(fx("show_mac_address-table.txt")) == 300
    assert [a.vlan for a in p.parse_arp(fx("show_arp.txt"))] == [100, 10, 100]
    assert p.parse_dhcp_snooping_bindings(fx("show_ip_dhcp_snooping_binding.txt"))[0].mac == (
        "00:e0:fc:12:34:56"
    )
    routes = p.parse_routing_table(fx("show_ip_route.txt"))
    assert [(r.protocol, r.interface) for r in routes] == [
        ("Static", "Vlan100"),
        ("Static", "Vlan100"),
        ("Direct", "Vlan100"),
    ]
    stp = p.parse_stp_global(fx("show_spanning-tree.txt"))
    assert (stp.mode, stp.root_port, stp.tc_count) == ("rstp", "TenGigabitEthernet1/0/1", 12)
    roles = [x.role for x in p.parse_stp_brief(fx("show_spanning-tree.txt"))]
    assert roles == ["DESI", "DISA", "ROOT", "ALTE"]
    assert p.parse_cpu_usage(fx("show_cpu_utilization.txt")).one_minute == 3.0


def test_acls_and_management() -> None:
    acls = {a.name: a for a in p.parse_acls(fx("show_access-lists.txt"))}
    assert acls["USERS-IN"].rules[20] == "permit tcp 10.10.10.0 0.0.0.255 any any 80"
    assert p.management_acls(fx("show_running-config.txt")) == frozenset({"MGMT"})


def test_vlan_builders_use_vlan_database() -> None:
    plan = l2.create_vlans({30: "Lab", 10: "Users"}, {10: "Users"})
    assert plan.lines == (
        "vlan database",
        "vlan 30",
        "exit",
        "interface vlan 30",
        "name Lab",
        "exit",
    )
    assert l2.delete_vlans([10], {10: "Users"}).lines == ("vlan database", "no vlan 10", "exit")


def test_port_modes_including_general() -> None:
    current = InterfaceConfig(link_type="trunk", trunk_allowed={10, 20})
    trunk = l2.set_port_mode("te1/0/1", "trunk", current, trunk_vlans=[20, 30])
    assert "switchport trunk allowed vlan remove 10" in trunk.lines
    assert "switchport trunk allowed vlan add 30" in trunk.lines
    hybrid = l2.set_port_mode("gi1/0/9", "hybrid", InterfaceConfig(), pvid=30, tagged=[40])
    assert hybrid.lines[1:4] == (
        "switchport mode general",
        "switchport general pvid 30",
        "switchport general allowed vlan add 40 tagged",
    )
    with pytest.raises(ValueError, match="drop"):
        l2.static_mac("00:e0:fc:12:34:56", 10, None, blackhole=True)
    assert l2.static_mac("00e0.fc12.3456", 10, "gi1/0/1").lines == (
        "mac address-table static 00:e0:fc:12:34:56 vlan 10 interface GigabitEthernet1/0/1",
    )
    assert l2.stp_settings("rstp", 4096).lines == (
        "spanning-tree mode rstp",
        "spanning-tree priority 4096",
    )


def test_acl_ace_priority_diff_and_binding() -> None:
    desired = AclDefinition(
        AclKind.ADVANCED,
        name="USERS-IN",
        rules=(
            AclRuleSpec(20, "permit", "tcp", "10.10.10.0/24", None, None, PortMatch("eq", 80)),
            AclRuleSpec(30, "deny", "ip", None, "192.0.2.10/32"),
        ),
    )
    current = next(a for a in p.parse_acls(fx("show_access-lists.txt")) if a.name == "USERS-IN")
    plan = acl.plan_acl(desired, current)
    assert "permit tcp 10.10.10.0 0.0.0.255 any any 80 ace-priority 20" not in plan.lines
    assert "no permit udp any any any 53" in plan.lines
    assert "deny ip any 192.0.2.10 0.0.0.0 ace-priority 30" in plan.lines
    bind = acl.apply_acl(DeviceFamily.UNKNOWN, "USERS-IN", "inbound", interface="gi1/0/1")
    assert bind.lines[1] == "service-acl input USERS-IN"
    with pytest.raises(AclValidationError):
        acl.plan_acl(AclDefinition(AclKind.LAYER2, name="M", rules=()), None)


def test_routing_and_nac() -> None:
    assert routing.static_route(StaticRoute("10.40.0.0/16", next_hop="192.0.2.1"), set()).lines == (
        "ip route 10.40.0.0 255.255.0.0 192.0.2.1",
    )
    for build in (lambda: routing.ospf(1, None, "0", []), lambda: routing.bgp(1, None, [], [])):
        with pytest.raises(RoutingValidationError):
            build()
    plan = nac.provision(
        DeviceFamily.UNKNOWN,
        NacProvisionSpec(
            radius_host="192.0.2.50",
            shared_secret="S3c",
            controller_ip="10.0.0.5",
            interfaces=("gi1/0/1",),
            guest_vlan=50,
        ),
    )
    assert "dot1x authentication 802.1x mac" in plan.lines
    assert "dot1x guest-vlan enable" in plan.lines
    assert any("CoA" in n for n in plan.notes)
    assert "radius-server host 192.0.2.50" in nac.related_config(fx("show_running-config.txt"))


def test_classifier_and_lockout() -> None:
    c = SmbClassifier()
    assert c.classify_exec("show system").category is S
    assert c.classify_exec("show cpu utilization").category is S
    assert c.classify_exec("write memory").category is W
    assert c.classify_config(["management access-class MGMT"]).category is D
    assert c.classify_config(["vlan database", "vlan 30", "exit"]).category is W
    dev = DeviceContext(mgmt_vlan=100, uplink_interfaces=frozenset({"TenGigabitEthernet1/0/1"}))
    ctx = LockoutContext(dev, ("10.0.0.5",))
    lines = [
        "ip access-list extended BLOCK",
        "deny ip 10.0.0.5 0.0.0.0 any ace-priority 10",
        "exit",
        "interface te1/0/1",
        "service-acl input BLOCK",
        "exit",
    ]
    assert SmbLockoutGuard().check(lines, ctx)
    assert SmbLockoutGuard().check(["interface te1/0/1", "shutdown", "exit"], ctx)
    assert SmbLockoutGuard().check(["interface gi1/0/5", "shutdown", "exit"], ctx) == []


def test_config_text_header_and_restore() -> None:
    running = fx("show_running-config.txt")
    normalized = config_text.normalize(running)
    assert "config-file-header" not in normalized
    assert "ssd-control" not in normalized
    other_header = running.replace("v3.3.0.16 / RCBS3.3_950_377_134", "v3.3.0.17 / X")
    assert config_text.config_hash(running) == config_text.config_hash(other_header)
    sanitized = config_text.sanitize(running)
    assert "S3cretRO" not in sanitized
    assert "MyRadKey1" not in sanitized
    assert "$15$" not in sanitized
    backup = running.replace(" description PC-101\n", " description OLD\n")
    plan = config_text.restore_plan(running, backup)
    assert plan.lines == (
        "interface GigabitEthernet1/0/1",
        "no description PC-101",
        "description OLD",
        "exit",
    )
