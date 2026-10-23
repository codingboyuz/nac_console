"""ACL domain/builders, routing builders and parsers."""

from __future__ import annotations

import pytest

from app.domain.acl import AclKind, AclRuleSpec, AclValidationError, PortMatch, wildcard
from app.domain.commands.categories import CommandCategory
from app.domain.devices import DeviceFamily
from app.network.builders import cli_acl, cli_routing
from app.network.builders.cli_acl import AclDefinition
from app.network.parsers import RouteEntry
from app.network.parsers.acl import ParsedAcl, management_acls, parse_acls
from app.network.parsers.routing_protocols import (
    parse_bgp_peers,
    parse_ospf_peers,
    parse_static_route_lines,
)
from app.services.routing_service import route_diff

ADV = AclKind.ADVANCED


def test_wildcard() -> None:
    assert wildcard("10.0.0.0/24") == "10.0.0.0 0.0.0.255"
    assert wildcard("192.0.2.7/32") == "192.0.2.7 0.0.0.0"
    assert wildcard("2001:db8::/32") == "2001:db8::/32"


@pytest.mark.parametrize(
    ("rule", "kind", "text"),
    [
        (
            AclRuleSpec(5, "permit", source="10.0.0.0/24"),
            AclKind.BASIC,
            "rule 5 permit source 10.0.0.0 0.0.0.255",
        ),
        (
            AclRuleSpec(
                10,
                "permit",
                "tcp",
                "10.0.0.0/8",
                "192.0.2.10/32",
                destination_port=PortMatch("eq", 22),
                log=True,
            ),
            ADV,
            "rule 10 permit tcp source 10.0.0.0 0.255.255.255 destination 192.0.2.10 0.0.0.0 destination-port eq 22 logging",
        ),
        (
            AclRuleSpec(15, "deny", "udp", destination_port=PortMatch("range", 1000, 2000)),
            ADV,
            "rule 15 deny udp destination-port range 1000 2000",
        ),
        (
            AclRuleSpec(5, "deny", source_mac="00:e0:fc:12:34:56", vlan=10),
            AclKind.LAYER2,
            "rule 5 deny source-mac 00e0-fc12-3456 vlan-id 10",
        ),
    ],
)
def test_rule_render(rule: AclRuleSpec, kind: AclKind, text: str) -> None:
    assert rule.render(kind) == text


@pytest.mark.parametrize(
    ("rule", "kind"),
    [
        (AclRuleSpec(5, "permit", destination="10.0.0.0/8"), AclKind.BASIC),
        (AclRuleSpec(5, "permit", source="10.0.0.0/8"), ADV),  # no protocol
        (AclRuleSpec(5, "permit", "icmp", destination_port=PortMatch("eq", 22)), ADV),
        (AclRuleSpec(5, "permit", source="10.0.0.0/8"), AclKind.LAYER2),
        (AclRuleSpec(5, "permit", source="not-a-net"), AclKind.BASIC),
    ],
)
def test_rule_validation(rule: AclRuleSpec, kind: AclKind) -> None:
    with pytest.raises((AclValidationError, ValueError)):
        rule.render(kind)
    with pytest.raises(AclValidationError):
        PortMatch("range", 10, 5)


def test_plan_acl_diff_and_escalation() -> None:
    current = ParsedAcl(
        "advanced",
        3000,
        None,
        {5: "rule 5 permit ip source 10.0.0.0 0.0.0.255", 10: "rule 10 deny ip"},
    )
    desired = AclDefinition(
        ADV,
        3000,
        rules=(AclRuleSpec(5, "permit", "ip", "10.0.0.0/24"), AclRuleSpec(20, "permit", "tcp")),
    )
    plan = cli_acl.plan_acl(desired, current)
    assert plan.lines == ("acl number 3000", "rule 20 permit tcp", "undo rule 10", "quit")
    assert plan.rollback == ("acl number 3000", "undo rule 20", "rule 10 deny ip", "quit")
    assert plan.min_category is CommandCategory.WARNING
    only_deny = AclDefinition(ADV, 3000, rules=(AclRuleSpec(10, "deny", "ip"),))
    plan = cli_acl.plan_acl(only_deny, current)
    assert plan.min_category is CommandCategory.DANGEROUS
    assert "last permit" in plan.min_category_reason
    vty = cli_acl.plan_acl(
        AclDefinition(AclKind.BASIC, 2000, rules=(AclRuleSpec(5, "permit", source="10.0.0.0/24"),)),
        None,
        frozenset({"2000"}),
    )
    assert vty.min_category is CommandCategory.DANGEROUS
    assert cli_acl.plan_acl(
        AclDefinition(
            ADV,
            3000,
            rules=(AclRuleSpec(5, "permit", "ip", "10.0.0.0/24"), AclRuleSpec(10, "deny", "ip")),
        ),
        current,
    ).noop


def test_acl_header_validation() -> None:
    with pytest.raises(AclValidationError):
        AclDefinition(AclKind.BASIC, 3000).header()
    assert AclDefinition(ADV, None, "WEB").header() == "acl name WEB advance"
    assert AclDefinition(ADV, 3001, ipv6=True).header() == "acl ipv6 number 3001"
    assert cli_acl.next_rule_id({5, 10}) == 15
    assert cli_acl.next_rule_id(set()) == 5


def test_apply_acl_syntax_by_family() -> None:
    s = cli_acl.apply_acl(DeviceFamily.S, "3000", "inbound", interface="GE0/0/1")
    assert s.lines[1] == "traffic-filter inbound acl 3000"
    ce = cli_acl.apply_acl(DeviceFamily.CE, "3000", "inbound", interface="10GE1/0/1")
    assert ce.lines[1] == "traffic-filter acl 3000 inbound"
    glob = cli_acl.apply_acl(DeviceFamily.S, "3000", "inbound", interface=None)
    assert glob.min_category is CommandCategory.DANGEROUS
    with pytest.raises(AclValidationError):
        cli_acl.apply_acl(DeviceFamily.CE, "3000", "inbound", interface=None)


def test_parse_acls_and_vty() -> None:
    text = (
        "Advanced ACL 3000, 2 rules\nAcl's step is 5\n rule 5 permit tcp destination-port eq 22 (4 matches)\n"
        " rule 10 deny ip\nBasic ACL 2000, 1 rule\n rule 5 permit source 10.0.0.0 0.0.0.255"
    )
    acls = parse_acls(text)
    assert [a.number for a in acls] == [3000, 2000]
    assert acls[0].rules[5] == "rule 5 permit tcp destination-port eq 22"
    assert acls[0].matches[5] == 4
    cfg = "#\nuser-interface vty 0 4\n acl 2000 inbound\n authentication-mode aaa\n#\ninterface GE0/0/1\n acl 9 inbound"
    assert management_acls(cfg) == frozenset({"2000"})


def test_static_route_builder() -> None:
    default = cli_routing.StaticRoute("0.0.0.0/0", "192.0.2.1", preference=70)
    assert default.render() == "ip route-static 0.0.0.0 0.0.0.0 192.0.2.1 preference 70"
    plan = cli_routing.static_route(default, set())
    assert plan.lines == ("ip route-static 0.0.0.0 0.0.0.0 192.0.2.1 preference 70",)
    assert plan.verify is not None
    assert cli_routing.static_route(default, {default.render()}).noop
    removal = cli_routing.static_route(default, {default.render()}, remove=True)
    assert removal.lines[0].startswith("undo ip route-static")
    with pytest.raises(cli_routing.RoutingValidationError):
        cli_routing.StaticRoute("10.0.0.0/8").render()


def test_protocol_builders() -> None:
    ospf = cli_routing.ospf(1, "1.1.1.1", "0", ["10.0.0.0/24"])
    assert ospf.lines == (
        "ospf 1 router-id 1.1.1.1",
        "area 0",
        "network 10.0.0.0 0.0.0.255",
        "quit",
        "quit",
    )
    bgp = cli_routing.bgp(65001, None, [("192.0.2.2", 65002)], ["10.0.0.0/16"])
    assert "peer 192.0.2.2 as-number 65002" in bgp.lines
    assert "network 10.0.0.0 16" in bgp.lines
    vrrp = cli_routing.vrrp("Vlanif10", 1, "10.0.10.1", 120, 20)
    assert vrrp.lines[1] == "vrrp vrid 1 virtual-ip 10.0.10.1"
    with pytest.raises(cli_routing.RoutingValidationError):
        cli_routing.vrrp("Vlanif10", 300, "10.0.10.1", None, None)
    rp = cli_routing.route_policy(
        "RP",
        10,
        "permit",
        "PL",
        [cli_routing.PrefixEntry(10, "permit", "10.0.0.0/8", less_equal=24)],
    )
    assert rp.lines[0] == "ip ip-prefix PL index 10 permit 10.0.0.0 8 less-equal 24"


def test_routing_parsers_and_diff() -> None:
    peers = parse_ospf_peers(
        " Area Id    Interface    Neighbor id   State\n 0.0.0.0    Vlanif10     192.0.2.2     Full"
    )
    assert peers[0].is_full
    bgp = parse_bgp_peers(
        "  Peer   V   AS  MsgRcvd  MsgSent  OutQ  Up/Down  State  PrefRcv\n"
        "  192.0.2.2  4  65002  120  118  0  01:02:03  Established  5"
    )
    assert bgp[0].established
    assert bgp[0].remote_as == 65002
    assert parse_static_route_lines("ip route-static 0.0.0.0 0.0.0.0 192.0.2.1\n#") == {
        "ip route-static 0.0.0.0 0.0.0.0 192.0.2.1"
    }
    a = RouteEntry("10.0.0.0/24", "Static", 60, 0, "RD", "192.0.2.1", None)
    b = RouteEntry("10.0.1.0/24", "Static", 60, 0, "RD", "192.0.2.1", None)
    diff = route_diff([a], [a, b])
    assert diff.added == ["10.0.1.0/24 Static via 192.0.2.1"]
    assert diff.removed == []
