"""Phase 14: Cisco IOS / IOS-XE adapter - parsers, classifier, lockout, builders, config text."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import UnsafeCommand
from app.domain.acl import AclKind, AclRuleSpec, AclValidationError, PortMatch
from app.domain.commands.categories import CommandCategory
from app.domain.commands.classifier import DeviceContext
from app.domain.commands.lockout import LockoutContext
from app.domain.platforms import CommitModel, Platform, RollbackTimer
from app.network.builders.cli_acl import AclDefinition
from app.network.builders.cli_nac import NacProvisionSpec
from app.network.builders.cli_routing import PrefixEntry, StaticRoute
from app.network.parsers.interface_config import InterfaceConfig
from app.network.platforms import Q, adapter_for
from app.network.platforms.cisco_ios import builders_acl as acl
from app.network.platforms.cisco_ios import builders_l2 as l2
from app.network.platforms.cisco_ios import builders_nac as nac
from app.network.platforms.cisco_ios import builders_routing as routing
from app.network.platforms.cisco_ios import config_text
from app.network.platforms.cisco_ios import parsers as p
from app.network.platforms.cisco_ios.lockout import CiscoLockoutGuard
from app.network.platforms.cisco_ios.rules import CiscoIosClassifier

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "cisco" / "c9300_iosxe17"
S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# --- adapter --------------------------------------------------------------------------------
def test_adapter_registered_with_ios_semantics() -> None:
    adapter = adapter_for(Platform.CISCO_IOS)
    assert adapter.commit_model is CommitModel.IMMEDIATE
    assert adapter.rollback_timer is RollbackTimer.COMMIT_TRIAL
    assert adapter.command(Q.VLANS) == "show vlan brief"
    assert set(adapter.drivers) == {"ssh", "telnet", "snmp"}
    for query in Q:
        assert adapter.supports(query), query


# --- parsers --------------------------------------------------------------------------------
def test_version() -> None:
    info = p.parse_version(fx("show_version.txt"))
    assert info.model == "C9300-48P"
    assert info.vrp_version == "17.09.04a"
    assert info.uptime_seconds == 12 * 604800 + 3 * 86400 + 4 * 3600 + 25 * 60


def test_version_classic_ios() -> None:
    text = (
        "Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E8, "
        "RELEASE SOFTWARE (fc1)\nSW-2960 uptime is 1 year, 2 weeks, 1 hour, 5 minutes\n"
        "cisco WS-C2960X-48FPD-L (APM86XXX) processor (revision C0) with 524288K bytes\n"
    )
    info = p.parse_version(text)
    assert info.vrp_version == "15.2(7)E8"
    assert info.model == "WS-C2960X-48FPD-L"
    assert info.uptime_seconds == 31536000 + 2 * 604800 + 3600 + 300


def test_vlans_skip_reserved_and_join_continuation() -> None:
    vlans = {v.vlan_id: v for v in p.parse_vlans(fx("show_vlan_brief.txt"))}
    assert set(vlans) == {1, 10, 20, 100, 999}
    assert len(vlans[1].untagged_ports) == 6
    assert vlans[10].name == "Users-Floor1"


def test_interface_status_and_detail() -> None:
    brief = {b.name: b for b in p.parse_interface_brief(fx("show_interfaces_status.txt"))}
    assert brief["GigabitEthernet1/0/1"].phy == "up"
    assert brief["GigabitEthernet1/0/4"].admin_down
    assert brief["Port-channel1"].phy == "up"
    detail = {d.name: d for d in p.parse_interface_detail(fx("show_interfaces.txt"))}
    gi = detail["GigabitEthernet1/0/1"]
    assert (gi.speed_mbps, gi.duplex, gi.crc_errors, gi.input_errors) == (1000, "full", 2, 3)
    assert gi.output_discards == 12
    assert detail["TenGigabitEthernet1/1/1"].speed_mbps == 10000
    assert not detail["GigabitEthernet1/0/4"].admin_up


def test_interface_config_trunk_mapping() -> None:
    cfg = p.parse_interface_config(fx("show_running-config_interface_TenGigabitEthernet1-1-1.txt"))
    assert cfg.link_type == "trunk"
    assert cfg.trunk_pvid == 100
    assert cfg.trunk_allowed == {10, 20, 100, 999}
    assert cfg.dhcp_trusted
    allowed_all = p.parse_interface_config("interface Gi1/0/9\n switchport mode trunk\nend")
    assert len(allowed_all.trunk_allowed) == 4094


def test_mac_arp_dhcp() -> None:
    macs = p.parse_mac_table(fx("show_mac_address-table.txt"))
    assert len(macs) == 6  # CPU entries dropped
    assert any(m.entry_type == "blackhole" and m.interface is None for m in macs)
    arp = p.parse_arp(fx("show_ip_arp.txt"))
    assert [a.ip for a in arp] == ["192.0.2.1", "192.0.2.10", "10.10.10.21"]  # incomplete dropped
    assert arp[1].entry_type == "static"
    assert arp[0].vlan == 100
    bindings = p.parse_dhcp_snooping_bindings(fx("show_ip_dhcp_snooping_binding.txt"))
    assert bindings[0].interface == "GigabitEthernet1/0/1"
    assert p.parse_mac_aging("Global Aging Time:   300\n") == 300


def test_routing_table_with_ecmp_and_bgp() -> None:
    routes = p.parse_routing_table(fx("show_ip_route.txt"))
    ospf = [r for r in routes if r.prefix == "10.20.0.0/16"]
    assert [r.nexthop for r in ospf] == ["192.0.2.2", "192.0.2.3"]
    assert all(r.interface == "Vlan100" for r in ospf)
    bgp = next(r for r in routes if r.protocol == "BGP")
    assert (bgp.nexthop, bgp.interface, bgp.preference) == ("192.0.2.5", None, 20)
    assert next(r for r in routes if r.prefix == "0.0.0.0/0").flags == "D*"


def test_ospf_bgp_stp_cpu_mem() -> None:
    assert [n.state for n in p.parse_ospf_peers(fx("show_ip_ospf_neighbor.txt"))] == [
        "Full",
        "Full",
        "2way",
    ]
    peers = p.parse_bgp_peers(fx("show_ip_bgp_summary.txt"))
    assert [b.state for b in peers] == ["Established", "Idle", "Active"]
    stp = p.parse_stp_global(fx("show_spanning-tree.txt"))
    assert stp.mode == "rstp"
    assert stp.root_path_cost == 0
    ports = p.parse_stp_brief(fx("show_spanning-tree.txt"))
    assert any(x.role == "ALTE" and x.state == "DISCARDING" for x in ports)
    assert p.parse_cpu_usage(fx("show_processes_cpu_sorted.txt")).current_percent == 7.0
    assert p.parse_memory_usage(fx("show_processes_memory.txt")).used_percent == 29.3


def test_acls_canonical_and_management() -> None:
    acls = {a.name: a for a in p.parse_acls(fx("show_ip_access-lists.txt"))}
    assert acls["MGMT-ONLY"].rules[10] == "10 permit 192.0.2.0 0.0.0.255"
    assert acls["USERS-IN"].rules[10] == "10 permit tcp 10.10.10.0 0.0.0.255 any eq 80"
    assert acls["MGMT-ONLY"].matches[10] == 1234
    assert p.management_acls(fx("show_running-config.txt")) == frozenset({"MGMT-ONLY"})


def test_prompt_and_paging_lines_are_dropped() -> None:
    text = "ACC-SW-01#show ip arp\n --More-- \x1b[2KInternet 1.1.1.1 1 aabb.cc00.0001 ARPA Vlan1\n"
    text += "ACC-SW-01#"
    assert [a.ip for a in p.parse_arp(text)] == ["1.1.1.1"]


# --- classifier -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("show version", S),
        ("sh ip int br", S),
        ("show running-config | include ip route", S),
        ("show run", S),
        ("reload", D),
        ("write erase", D),
        ("copy running-config startup-config", W),
        ("copy tftp: running-config", D),
        ("configure replace flash:old.cfg", D),
        ("ping 10.0.0.1", W),
        ("terminal length 0", S),
        ("debug ip packet", D),
        ("vlan 10", D),  # configuration outside configuration mode
        ("unknown-command", D),
    ],
)
def test_exec_classification(command: str, category: CommandCategory) -> None:
    assert CiscoIosClassifier().classify_exec(command).category is category


def test_config_block_categories_and_escalation() -> None:
    ctx = DeviceContext(
        mgmt_interface="Vlan100",
        mgmt_vlan=100,
        uplink_interfaces=frozenset({"TenGigabitEthernet1/1/1"}),
    )
    c = CiscoIosClassifier()
    assert c.classify_config(["interface Gi1/0/5", "shutdown", "exit"], ctx).category is W
    up = c.classify_config(["interface Te1/1/1", "shutdown", "exit"], ctx)
    assert up.category is D
    assert "protected" in up.reason
    assert c.classify_config(["no vlan 100"], ctx).category is D
    assert c.classify_config(["no vlan 200"], ctx).category is W
    assert c.classify_config(["username x privilege 15 secret y"], ctx).category is D
    assert c.classify_config(["ip access-list extended A", "10 deny ip any any"], ctx).category is D
    assert c.classify_config(["interface range Gi1/0/1 - 24", "shutdown"], ctx).category is D


def test_pipe_only_after_show() -> None:
    with pytest.raises(UnsafeCommand):
        CiscoIosClassifier().classify_exec("configure terminal | include x")


# --- lockout --------------------------------------------------------------------------------
def _lctx(**kw: object) -> LockoutContext:
    dev = DeviceContext(
        mgmt_interface="Vlan100",
        mgmt_vlan=100,
        uplink_interfaces=frozenset({"TenGigabitEthernet1/1/1"}),
    )
    return LockoutContext(dev, ("10.0.0.5",), **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "lines",
    [
        ["interface TenGigabitEthernet1/1/1", "shutdown", "exit"],
        ["interface Vlan100", "no ip address", "exit"],
        ["interface Te1/1/1", "switchport trunk allowed vlan remove 100", "exit"],
        ["interface Te1/1/1", "switchport trunk allowed vlan 10,20", "exit"],
        ["interface Te1/1/1", "switchport mode access", "exit"],
        ["no vlan 100"],
        ["no interface Vlan100"],
        [
            "ip access-list standard VTY",
            "10 deny host 10.0.0.5",
            "exit",
            "line vty 0 4",
            "access-class VTY in",
            "exit",
        ],
        ["access-list 10 deny any", "line vty 0 15", "access-class 10 in"],
    ],
)
def test_lockout_findings(lines: list[str]) -> None:
    assert CiscoLockoutGuard().check(lines, _lctx())


@pytest.mark.parametrize(
    "lines",
    [
        ["interface Gi1/0/5", "shutdown", "exit"],
        ["interface Te1/1/1", "switchport trunk allowed vlan add 300", "exit"],
        ["interface Te1/1/1", "switchport trunk allowed vlan 10,100", "exit"],
        [
            "ip access-list standard VTY",
            "10 deny host 10.9.9.9",
            "10 permit any",
            "exit",
            "line vty 0 4",
            "access-class VTY in",
        ],
        ["ip access-list extended USERS", "10 deny ip any any"],  # not bound to the mgmt plane
    ],
)
def test_lockout_safe(lines: list[str]) -> None:
    assert CiscoLockoutGuard().check(lines, _lctx()) == []


def test_lockout_uses_known_mgmt_acls() -> None:
    lines = ["ip access-list extended MGMT", "10 deny tcp host 10.0.0.5 any eq 22"]
    assert CiscoLockoutGuard().check(lines, _lctx(mgmt_acls=frozenset({"MGMT"})))


# --- L2 builders ----------------------------------------------------------------------------
def test_vlan_create_delete_rename() -> None:
    plan = l2.create_vlans({30: "Lab", 10: "Users-Floor1"}, {10: "Users-Floor1"})
    assert plan.lines == ("vlan 30", "name Lab", "exit")
    assert plan.rollback == ("no vlan 30",)
    delete = l2.delete_vlans([10, 11], {10: "Users"})
    assert delete.lines == ("no vlan 10",)
    assert delete.rollback == ("vlan 10", "name Users", "exit")
    assert l2.delete_vlans([77], {}).noop
    assert l2.rename_vlan(10, "X").lines == ("vlan 10", "name X", "exit")


def test_port_mode_access_and_trunk() -> None:
    access = l2.set_port_mode("Gi1/0/5", "access", InterfaceConfig(), access_vlan=10)
    assert access.lines == (
        "interface GigabitEthernet1/0/5",
        "switchport mode access",
        "switchport access vlan 10",
        "exit",
    )
    trunk = l2.set_port_mode(
        "Te1/1/2", "trunk", InterfaceConfig(), trunk_vlans=[10, 11, 12], pvid=100
    )
    assert "switchport trunk allowed vlan 10-12" in trunk.lines
    assert "switchport trunk native vlan 100" in trunk.lines
    with pytest.raises(ValueError, match="hybrid"):
        l2.set_port_mode("Gi1/0/1", "hybrid", InterfaceConfig())


def test_trunk_add_remove_against_all_vlans() -> None:
    current = InterfaceConfig(link_type="trunk", trunk_allowed=set(l2.ALL_VLANS))
    plan = l2.trunk_vlans("Te1/1/1", [20], [30, 31], current)
    assert plan.lines == (
        "interface TenGigabitEthernet1/1/1",
        "switchport trunk allowed vlan remove 30-31",
        "exit",
    )


def test_admin_description_speed_mac_stp_dhcp() -> None:
    assert l2.set_admin_state("Gi1/0/1", False, InterfaceConfig()).lines[1] == "shutdown"
    assert l2.set_admin_state("Gi1/0/1", True, InterfaceConfig()).noop
    assert l2.set_description("Gi1/0/1", None, InterfaceConfig(description="x")).lines[1] == (
        "no description"
    )
    assert l2.set_speed_duplex("Gi1/0/1", 100, "full", False).lines[1:3] == (
        "speed 100",
        "duplex full",
    )
    assert l2.static_mac("00:e0:fc:12:34:56", 10, "Gi1/0/1").lines == (
        "mac address-table static 00e0.fc12.3456 vlan 10 interface GigabitEthernet1/0/1",
    )
    assert (
        l2.static_mac("00e0-fc12-3456", 10, None, blackhole=True, remove=True)
        .lines[0]
        .startswith("no mac address-table static")
    )
    assert l2.stp_settings("rstp", 4096).lines == (
        "spanning-tree mode rapid-pvst",
        "spanning-tree vlan 1-4094 priority 4096",
    )
    assert l2.stp_settings("mstp", 8192, 1).lines[-1] == "spanning-tree mst 1 priority 8192"
    assert l2.dhcp_snooping([10, 20], ["Te1/1/1"]).lines == (
        "ip dhcp snooping",
        "ip dhcp snooping vlan 10,20",
        "interface TenGigabitEthernet1/1/1",
        "ip dhcp snooping trust",
        "exit",
    )


# --- ACL builders ---------------------------------------------------------------------------
def _rules() -> tuple[AclRuleSpec, ...]:
    return (
        AclRuleSpec(10, "permit", "tcp", "10.10.10.0/24", None, None, PortMatch("eq", 80)),
        AclRuleSpec(20, "deny", "ip", None, "192.0.2.10/32", log=True),
    )


def test_acl_render_and_idempotent_diff() -> None:
    desired = AclDefinition(AclKind.ADVANCED, name="USERS-IN", rules=_rules())
    plan = acl.plan_acl(desired, None)
    assert plan.lines == (
        "ip access-list extended USERS-IN",
        "10 permit tcp 10.10.10.0 0.0.0.255 any eq 80",
        "20 deny ip any host 192.0.2.10 log",
        "exit",
    )
    current = next(a for a in p.parse_acls(fx("show_ip_access-lists.txt")) if a.name == "USERS-IN")
    diff = acl.plan_acl(desired, current)
    # rule 10 equal after canonicalisation ('eq www' == 'eq 80'); 30/40 removed, 20 replaced.
    assert "10 permit tcp 10.10.10.0 0.0.0.255 any eq 80" not in diff.lines
    assert "no 30" in diff.lines
    assert "no 40" in diff.lines


def test_acl_named_only_and_interface_apply() -> None:
    with pytest.raises(AclValidationError):
        acl.plan_acl(AclDefinition(AclKind.ADVANCED, number=3000, rules=_rules()), None)
    from app.domain.devices import DeviceFamily

    plan = acl.apply_acl(DeviceFamily.UNKNOWN, "USERS-IN", "inbound", interface="Gi1/0/1")
    assert plan.lines == ("interface GigabitEthernet1/0/1", "ip access-group USERS-IN in", "exit")
    with pytest.raises(AclValidationError):
        acl.apply_acl(DeviceFamily.UNKNOWN, "X", "inbound", interface=None)


def test_acl_removing_last_permit_is_dangerous() -> None:
    current = next(a for a in p.parse_acls(fx("show_ip_access-lists.txt")) if a.name == "USERS-IN")
    deny_only = AclDefinition(
        AclKind.ADVANCED, name="USERS-IN", rules=(AclRuleSpec(10, "deny", "ip"),)
    )
    assert acl.plan_acl(deny_only, current).min_category is D


# --- routing / NAC ---------------------------------------------------------------------------
def test_routing_builders() -> None:
    route = StaticRoute("10.40.0.0/16", next_hop="192.0.2.1", preference=5)
    assert routing.static_route(route, set()).lines == (
        "ip route 10.40.0.0 255.255.0.0 192.0.2.1 5",
    )
    assert routing.static_route(route, {"ip route 10.40.0.0 255.255.0.0 192.0.2.1 5"}).noop
    ospf = routing.ospf(1, "10.255.0.1", "0", ["10.10.10.0/24"], silent_interfaces=["Vlan10"])
    assert "network 10.10.10.0 0.0.0.255 area 0" in ospf.lines
    assert "passive-interface Vlan10" in ospf.lines
    bgp = routing.bgp(65001, None, [("192.0.2.5", 65010)], ["203.0.113.0/24"])
    assert "neighbor 192.0.2.5 remote-as 65010" in bgp.lines
    assert "network 203.0.113.0 mask 255.255.255.0" in bgp.lines
    policy = routing.route_policy(
        "EXPORT", 10, "permit", "PL", [PrefixEntry(5, "permit", "10.0.0.0/8", less_equal=24)]
    )
    assert policy.lines[0] == "ip prefix-list PL seq 5 permit 10.0.0.0/8 le 24"


def test_nac_provisioning_plan() -> None:
    from app.domain.devices import DeviceFamily

    spec = NacProvisionSpec(
        radius_host="192.0.2.50",
        shared_secret="S3cret!",
        controller_ip="10.0.0.5",
        interfaces=("Gi1/0/1",),
        guest_vlan=50,
    )
    plan = nac.provision(DeviceFamily.UNKNOWN, spec)
    assert plan.min_category is D
    assert "dot1x system-auth-control" in plan.lines
    assert "authentication order dot1x mab" in plan.lines
    assert "authentication event no-response action authorize vlan 50" in plan.lines
    assert "client 10.0.0.5 server-key S3cret!" in plan.lines
    assert plan.notes
    with pytest.raises(ValueError, match="secret"):
        nac.provision(
            DeviceFamily.UNKNOWN,
            NacProvisionSpec(radius_host="1.1.1.1", shared_secret="a b", controller_ip="2.2.2.2"),
        )
    related = nac.related_config(fx("show_running-config.txt"))
    assert "radius server NAC" in related
    assert "aaa new-model" in related


# --- config text ----------------------------------------------------------------------------
def test_config_hash_ignores_volatile_header() -> None:
    a = fx("show_running-config.txt")
    b = a.replace("10:44:12 UTC Mon Nov 24 2026", "11:00:00 UTC Tue Nov 25 2026")
    b = b.replace("Current configuration : 2456 bytes", "Current configuration : 2457 bytes")
    assert config_text.config_hash(a) == config_text.config_hash(b)
    assert "$9$" not in config_text.sanitize(a)
    assert "S3cretRO" not in config_text.sanitize(a)
    assert "0822455D0A16544541" not in config_text.sanitize(a)


def test_restore_plan_merges_with_no() -> None:
    running = fx("show_running-config.txt")
    backup = running.replace(" description PC-101\n", " description OLD\n").replace(
        "ip route 10.30.0.0 255.255.0.0 192.0.2.1\n", ""
    )
    plan = config_text.restore_plan(running, backup)
    assert "interface GigabitEthernet1/0/1" in plan.lines
    assert "description OLD" in plan.lines
    assert "no description PC-101" in plan.lines
    assert "no ip route 10.30.0.0 255.255.0.0 192.0.2.1" in plan.lines
    assert not any(line.startswith("hostname") for line in plan.lines)
