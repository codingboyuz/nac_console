"""Phase 17: TP-Link JetStream adapter (fixtures are synthetic - see their README)."""

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
from app.network.platforms.tplink_jetstream import builders_acl as acl
from app.network.platforms.tplink_jetstream import builders_l2 as l2
from app.network.platforms.tplink_jetstream import builders_nac as nac
from app.network.platforms.tplink_jetstream import builders_routing as routing
from app.network.platforms.tplink_jetstream import config_text
from app.network.platforms.tplink_jetstream import parsers as p
from app.network.platforms.tplink_jetstream.naming import tp_iface
from app.network.platforms.tplink_jetstream.rules import TplinkClassifier, TplinkLockoutGuard

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "tplink" / "t2600g_v3"
S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_adapter_and_interface_spelling() -> None:
    adapter = adapter_for(Platform.TPLINK_JETSTREAM)
    assert adapter.rollback_timer is RollbackTimer.SCHEDULED_RELOAD
    assert adapter.command(Q.INTERFACE_CONFIG, name="GigabitEthernet1/0/1") == (
        "show running-config interface gigabitEthernet 1/0/1"
    )
    assert tp_iface("Te1/0/25") == "ten-gigabitEthernet 1/0/25"
    assert tp_iface("Po2") == "port-channel 2"
    assert not adapter.supports(Q.BGP_PEERS)


def test_parsers_on_synthetic_fixtures() -> None:
    info = p.parse_version(fx("show_system-info.txt"))
    assert (info.model, info.vrp_version, info.uptime_seconds) == ("T2600G-28TS", "3.0.0", 268066)
    vlans = {v.vlan_id: v for v in p.parse_vlans(fx("show_vlan.txt"))}
    assert len(vlans[1].untagged_ports) == 23
    brief = {b.name: b for b in p.parse_interface_brief(fx("show_interface_status.txt"))}
    assert brief["GigabitEthernet1/0/4"].admin_down
    detail = {d.name: d for d in p.parse_interface_detail(fx("show_interface_status.txt"))}
    assert (
        detail["GigabitEthernet1/0/1"].speed_mbps,
        detail["GigabitEthernet1/0/1"].description,
    ) == (
        1000,
        "PC-101",
    )
    counters = p.parse_interface_detail(fx("show_interface_counters_gi1-0-1.txt"))[0]
    assert (counters.crc_errors, counters.input_bytes) == (2, 987654321)
    cfg = p.parse_interface_config(fx("show_running-config_interface_gi1-0-25.txt"))
    assert (cfg.link_type, cfg.trunk_allowed, cfg.trunk_pvid) == ("trunk", {10, 20, 100}, 100)
    access = p.parse_interface_config(
        "interface gigabitEthernet 1/0/1\n  switchport general allowed vlan 10 untagged\n"
        "  switchport pvid 10\n"
    )
    assert (access.link_type, access.default_vlan) == ("access", 10)
    macs = p.parse_mac_table(fx("show_mac_address-table.txt"))
    assert macs[-1].entry_type == "static"
    assert [a.vlan for a in p.parse_arp(fx("show_arp.txt"))] == [100, 10, 100]
    assert len(p.parse_dhcp_snooping_bindings(fx("show_ip_source_binding.txt"))) == 2
    assert {r.interface for r in p.parse_routing_table(fx("show_ip_route.txt"))} == {
        "Vlan100",
        "Vlan10",
    }
    assert (
        p.parse_stp_global(fx("show_spanning-tree_bridge.txt")).root_port == "GigabitEthernet1/0/25"
    )
    assert [s.role for s in p.parse_stp_brief(fx("show_spanning-tree_interface.txt"))] == [
        "DESI",
        "DISA",
        "ROOT",
        "ALTE",
    ]
    assert p.parse_cpu_usage(fx("show_cpu-utilization.txt")).one_minute == 8.0
    assert p.parse_memory_usage(fx("show_memory-utilization.txt")).used_percent == 63.0
    acls = p.parse_acls(fx("show_running-config.txt"))
    assert acls[0].name == "MGMT-ONLY"
    assert acls[0].rules[5] == "rule 5 deny logging disable"
    assert p.management_acls(fx("show_running-config.txt")) == frozenset({"500", "MGMT-ONLY"})


def test_general_port_model() -> None:
    access = l2.set_port_mode(
        "Gi1/0/5", "access", InterfaceConfig(hybrid_untagged={1}, hybrid_pvid=1), access_vlan=300
    )
    assert access.lines == (
        "interface gigabitEthernet 1/0/5",
        "no switchport general allowed vlan 1",
        "switchport general allowed vlan 300 untagged",
        "switchport pvid 300",
        "exit",
    )
    trunk = l2.set_port_mode("Gi1/0/25", "trunk", InterfaceConfig(), trunk_vlans=[10, 20])
    assert "switchport general allowed vlan 10,20 tagged" in trunk.lines
    remove = l2.trunk_vlans("Gi1/0/25", [], [20], InterfaceConfig(hybrid_tagged={10, 20}))
    assert remove.lines[1] == "no switchport general allowed vlan 20"
    assert l2.static_mac("00e0.fc12.3456", 10, None, blackhole=True).lines == (
        "mac address-table filtering 00:e0:fc:12:34:56 vid 10",
    )
    assert (
        l2.stp_edge_port("Gi1/0/1", True).lines[1] == "spanning-tree common-config portfast enable"
    )
    assert l2.create_vlans({30: "Lab"}, {}).lines == ("vlan 30", "name Lab", "exit")


def test_acl_rules_and_binding() -> None:
    desired = AclDefinition(
        AclKind.ADVANCED,
        number=600,
        name="USERS",
        rules=(
            AclRuleSpec(1, "permit", "tcp", "10.10.10.0/24", None, None, PortMatch("eq", 80)),
            AclRuleSpec(5, "deny", "ip"),
        ),
    )
    plan = acl.plan_acl(desired, None)
    assert plan.lines[0] == 'access-list create 600 name "USERS"'
    assert plan.lines[1] == (
        "access-list ip 600 rule 1 permit logging disable sip 10.10.10.0 sip-mask 255.255.255.0 "
        "protocol 6 d-port 80 d-port-mask 65535"
    )
    current = p.parse_acls(
        'access-list create 600 name "USERS"\n' + "\n".join(plan.lines[1:]) + "\n"
    )[0]
    assert acl.plan_acl(desired, current).noop  # idempotent against its own running-config lines
    with pytest.raises(AclValidationError, match="500-999"):
        acl.plan_acl(AclDefinition(AclKind.ADVANCED, number=3000, rules=()), None)
    bind = acl.apply_acl(DeviceFamily.UNKNOWN, "600", "inbound", interface="Gi1/0/1")
    assert bind.lines == ("access-list bind 600 interface gigabitEthernet 1/0/1",)


def test_routing_nac_and_config_text() -> None:
    assert routing.static_route(StaticRoute("10.40.0.0/16", next_hop="192.0.2.1"), set()).lines == (
        "ip route 10.40.0.0 255.255.0.0 192.0.2.1",
    )
    assert (
        "network 10.10.10.0 255.255.255.0 area 0.0.0.0"
        in routing.ospf(1, None, "0", ["10.10.10.0/24"]).lines
    )
    with pytest.raises(RoutingValidationError):
        routing.bgp(65001, None, [], [])
    plan = nac.provision(
        DeviceFamily.UNKNOWN,
        NacProvisionSpec(
            radius_host="192.0.2.50",
            shared_secret="S3c",
            controller_ip="10.0.0.5",
            interfaces=("Gi1/0/1",),
        ),
    )
    assert "dot1x vlan-assignment" in plan.lines
    assert "dot1x mab" in plan.lines
    running = fx("show_running-config.txt")
    assert "#" not in config_text.normalize(running).split("\n")
    sanitized = config_text.sanitize(running)
    for secret in ("$1$J7K8", "0822455D0A16", "S3cretRO"):
        assert secret not in sanitized
    restore = config_text.restore_plan(running, running.replace('"PC-101"', '"OLD"'))
    assert 'description "OLD"' in restore.lines


def test_classifier_and_lockout() -> None:
    c = TplinkClassifier()
    assert c.classify_exec("show system-info").category is S
    assert c.classify_exec("reboot").category is D
    assert c.classify_exec("copy running-config startup-config").category is W
    ctx_dev = DeviceContext(mgmt_vlan=100, uplink_interfaces=frozenset({"GigabitEthernet1/0/25"}))
    assert (
        c.classify_config(
            ["interface gigabitEthernet 1/0/25", "no switchport general allowed vlan 10", "exit"],
            ctx_dev,
        ).category
        is D
    )
    assert c.classify_config(["reboot-schedule in 5"]).category is W
    ctx = LockoutContext(ctx_dev, ("10.0.0.5",))
    guard = TplinkLockoutGuard()
    assert guard.check(
        ["interface gigabitEthernet 1/0/25", "no switchport general allowed vlan 100", "exit"], ctx
    )
    deny = [
        'access-list create 700 name "X"',
        "access-list ip 700 rule 1 deny logging disable sip 10.0.0.5 sip-mask 255.255.255.255",
        "access-list bind 700 interface vlan 100",
    ]
    assert guard.check(deny, ctx)
    other = [deny[0], deny[1].replace("10.0.0.5", "172.16.0.1"), deny[2]]
    assert guard.check(other, ctx) == []
    assert guard.check(["no vlan 100"], ctx)
