"""Routing plans for NX-OS (static, OSPF, BGP, RIP, VRRP, route-map / prefix-list).

NX-OS enables protocols with 'feature <name>' and attaches interfaces to OSPF/RIP with
'ip router ospf N area A' / 'ip router rip TAG' in interface mode; it has no 'network'
statements. The shared builder signature passes networks, so OSPF/RIP here use the
'silent_interfaces' list as the interfaces to enable and refuse plain network lists.
"""

from __future__ import annotations

import ipaddress
from typing import Literal

from app.domain.commands.sanitize import quote_free_text
from app.domain.interfaces import normalize_interface
from app.network.builders.cli_routing import PrefixEntry, RoutingValidationError, StaticRoute
from app.network.builders.plan import ConfigPlan, Verifier
from app.network.platforms.cisco_ios.builders_routing import route_policy as _ios_route_policy
from app.network.ports import DeviceDriver

NETWORKS_UNSUPPORTED = (
    "NX-OS has no 'network' statements: OSPF/RIP are enabled per interface. Pass the "
    "interfaces to enable instead of networks."
)


def _ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise RoutingValidationError(f"invalid IP address {value!r}") from exc


def _route_present(prefix: str) -> Verifier:
    def verify(driver: DeviceDriver) -> bool:
        from app.network.platforms.cisco_nxos.parsers import parse_routing_table

        table = parse_routing_table(driver.run_show("show ip route static").output)
        return any(r.prefix == prefix for r in table)

    return verify


def render_static(route: StaticRoute) -> str:
    net = ipaddress.ip_network(route.prefix, strict=False)
    if net.version != 4:
        raise RoutingValidationError("only IPv4 static routes are supported here")
    if not route.next_hop and not route.interface:
        raise RoutingValidationError("a route needs a next hop and/or an outgoing interface")
    parts = ["ip route", str(net)]
    if route.interface:
        parts.append(normalize_interface(route.interface))
    if route.next_hop:
        parts.append(_ip(route.next_hop))
    if route.description:
        parts.append(f"name {quote_free_text(route.description, max_length=35).replace(' ', '_')}")
    if route.preference is not None:
        if not 1 <= route.preference <= 255:
            raise RoutingValidationError("preference must be 1-255")
        parts.append(str(route.preference))
    if route.vpn_instance:
        parts.append(f"vrf {quote_free_text(route.vpn_instance, max_length=31)}")
    return " ".join(parts)


def static_route(
    route: StaticRoute, existing: set[str], *, remove: bool = False, verify: bool = True
) -> ConfigPlan:
    line = render_static(route)
    if (line in existing) != remove:
        return ConfigPlan("route.static", (), summary="no change")
    apply, undo = (f"no {line}", line) if remove else (line, f"no {line}")
    return ConfigPlan(
        "route.static.remove" if remove else "route.static",
        (apply,),
        (undo,),
        _route_present(route.normalized_prefix) if verify and not remove else None,
        summary=f"{'remove' if remove else 'add'} static {route.normalized_prefix}",
    )


def ospf(
    process: int,
    router_id: str | None,
    area: str,
    networks: list[str],
    *,
    silent_interfaces: list[str] | None = None,
) -> ConfigPlan:
    if not 1 <= process <= 65535:
        raise RoutingValidationError("OSPF process 1-65535")
    if networks and not silent_interfaces:
        raise RoutingValidationError(NETWORKS_UNSUPPORTED)
    area_id = _ip(area) if "." in area else str(int(area))
    body = ["feature ospf", f"router ospf {process}"]
    if router_id:
        body.append(f"router-id {_ip(router_id)}")
    body.append("exit")
    for iface in silent_interfaces or []:
        body += [
            f"interface {normalize_interface(iface)}",
            f"ip router ospf {process} area {area_id}",
            "exit",
        ]
    return ConfigPlan(
        "routing.ospf", tuple(body), (f"no router ospf {process}",), summary=f"OSPF {process}"
    )


def bgp(
    asn: int, router_id: str | None, peers: list[tuple[str, int]], networks: list[str]
) -> ConfigPlan:
    if not 1 <= asn <= 4294967295:
        raise RoutingValidationError("invalid AS number")
    body = ["feature bgp", f"router bgp {asn}"]
    if router_id:
        body.append(f"router-id {_ip(router_id)}")
    body.append("address-family ipv4 unicast")
    for cidr in networks:
        body.append(f"network {ipaddress.ip_network(cidr, strict=False)}")
    body.append("exit")
    for peer, remote_as in peers:
        body += [
            f"neighbor {_ip(peer)}",
            f"remote-as {int(remote_as)}",
            "address-family ipv4 unicast",
            "exit",
            "exit",
        ]
    body.append("exit")
    rollback = [f"router bgp {asn}", *[f"no neighbor {_ip(p)}" for p, _ in peers], "exit"]
    return ConfigPlan("routing.bgp", tuple(body), tuple(rollback), summary=f"BGP AS{asn}")


def rip(process: int, version: Literal[1, 2], networks: list[str]) -> ConfigPlan:
    raise RoutingValidationError(
        "NX-OS RIP is enabled per interface ('ip router rip TAG'); " + NETWORKS_UNSUPPORTED
    )


def vrrp(
    interface: str, vrid: int, virtual_ip: str, priority: int | None, preempt_delay: int | None
) -> ConfigPlan:
    if not 1 <= vrid <= 255:
        raise RoutingValidationError("VRID 1-255")
    name = normalize_interface(interface)
    body = ["feature vrrp", f"interface {name}", f"vrrp {vrid}", f"address {_ip(virtual_ip)}"]
    if priority is not None:
        if not 1 <= priority <= 254:
            raise RoutingValidationError("VRRP priority 1-254")
        body.append(f"priority {priority}")
    if preempt_delay is not None:
        body.append("preempt")
    body += ["no shutdown", "exit", "exit"]
    return ConfigPlan(
        "routing.vrrp",
        tuple(body),
        (f"interface {name}", f"no vrrp {vrid}", "exit"),
        summary=f"VRRP {vrid} on {name}",
        notes=(
            ("NX-OS VRRP has no configurable preempt delay; 'preempt' is enabled instead.",)
            if preempt_delay is not None
            else ()
        ),
    )


def route_policy(
    name: str,
    node: int,
    action: Literal["permit", "deny"],
    prefix_list: str | None,
    prefixes: list[PrefixEntry],
    apply_cost: int | None = None,
) -> ConfigPlan:
    return _ios_route_policy(name, node, action, prefix_list, prefixes, apply_cost)
