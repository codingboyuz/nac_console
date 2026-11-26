"""Routing plans in Cisco IOS / IOS-XE syntax (static, OSPF, BGP, RIP, VRRP, route-map)."""

from __future__ import annotations

import ipaddress
from typing import Literal

from app.domain.commands.sanitize import quote_free_text
from app.domain.interfaces import normalize_interface
from app.network.builders.cli_routing import (
    PrefixEntry,
    RoutingValidationError,
    StaticRoute,
)
from app.network.builders.plan import ConfigPlan, Verifier
from app.network.ports import DeviceDriver


def _ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise RoutingValidationError(f"invalid IP address {value!r}") from exc


def _route_present(prefix: str) -> Verifier:
    def verify(driver: DeviceDriver) -> bool:
        from app.network.platforms.cisco_ios.parsers import parse_routing_table

        table = parse_routing_table(driver.run_show("show ip route static").output)
        return any(r.prefix == prefix for r in table)

    return verify


def render_static(route: StaticRoute) -> str:
    net = ipaddress.ip_network(route.prefix, strict=False)
    if net.version != 4:
        raise RoutingValidationError("only IPv4 static routes are supported here")
    if not route.next_hop and not route.interface:
        raise RoutingValidationError("a route needs a next hop and/or an outgoing interface")
    parts = ["ip route"]
    if route.vpn_instance:
        parts.append(f"vrf {quote_free_text(route.vpn_instance, max_length=31)}")
    parts += [str(net.network_address), str(net.netmask)]
    if route.interface:
        parts.append(normalize_interface(route.interface))
    if route.next_hop:
        parts.append(_ip(route.next_hop))
    if route.preference is not None:
        if not 1 <= route.preference <= 255:
            raise RoutingValidationError("administrative distance must be 1-255")
        parts.append(str(route.preference))
    if route.description:
        parts.append(f"name {quote_free_text(route.description, max_length=35).replace(' ', '_')}")
    return " ".join(parts)


def static_route(
    route: StaticRoute, existing: set[str], *, remove: bool = False, verify: bool = True
) -> ConfigPlan:
    line = render_static(route)
    if (line in existing) != remove:
        return ConfigPlan("route.static", (), summary="no change")
    apply, undo = (f"no {line}", line) if remove else (line, f"no {line}")
    checker = _route_present(route.normalized_prefix) if verify and not remove else None
    return ConfigPlan(
        "route.static.remove" if remove else "route.static",
        (apply,),
        (undo,),
        checker,
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
    area_id = _ip(area) if "." in area else str(int(area))
    body = [f"router ospf {process}"]
    if router_id:
        body.append(f"router-id {_ip(router_id)}")
    for iface in silent_interfaces or []:
        body.append(f"passive-interface {normalize_interface(iface)}")
    for cidr in networks:
        net = ipaddress.ip_network(cidr, strict=False)
        body.append(f"network {net.network_address} {net.hostmask} area {area_id}")
    body.append("exit")
    return ConfigPlan(
        "routing.ospf", tuple(body), (f"no router ospf {process}",), summary=f"OSPF {process}"
    )


def bgp(
    asn: int, router_id: str | None, peers: list[tuple[str, int]], networks: list[str]
) -> ConfigPlan:
    if not 1 <= asn <= 4294967295:
        raise RoutingValidationError("invalid AS number")
    body = [f"router bgp {asn}"]
    if router_id:
        body.append(f"bgp router-id {_ip(router_id)}")
    body.append("bgp log-neighbor-changes")
    for peer, remote_as in peers:
        body.append(f"neighbor {_ip(peer)} remote-as {int(remote_as)}")
    body.append("address-family ipv4 unicast")
    for peer, _ in peers:
        body.append(f"neighbor {_ip(peer)} activate")
    for cidr in networks:
        net = ipaddress.ip_network(cidr, strict=False)
        body.append(f"network {net.network_address} mask {net.netmask}")
    body += ["exit-address-family", "exit"]
    rollback = [f"router bgp {asn}", *[f"no neighbor {_ip(p)}" for p, _ in peers], "exit"]
    return ConfigPlan("routing.bgp", tuple(body), tuple(rollback), summary=f"BGP AS{asn}")


def rip(process: int, version: Literal[1, 2], networks: list[str]) -> ConfigPlan:
    # IOS has a single RIP process; 'process' is accepted for API symmetry and ignored.
    body = ["router rip", f"version {version}", "no auto-summary"]
    for cidr in networks:
        body.append(f"network {ipaddress.ip_network(cidr, strict=False).network_address}")
    body.append("exit")
    return ConfigPlan("routing.rip", tuple(body), ("no router rip",), summary="RIP")


def vrrp(
    interface: str, vrid: int, virtual_ip: str, priority: int | None, preempt_delay: int | None
) -> ConfigPlan:
    """Legacy IOS VRRP syntax ('vrrp N ip X'); IOS-XE 16.x+ 'fhrp version vrrp v3' differs."""
    if not 1 <= vrid <= 255:
        raise RoutingValidationError("VRID 1-255")
    name = normalize_interface(interface)
    body = [f"interface {name}", f"vrrp {vrid} ip {_ip(virtual_ip)}"]
    if priority is not None:
        if not 1 <= priority <= 254:
            raise RoutingValidationError("VRRP priority 1-254")
        body.append(f"vrrp {vrid} priority {priority}")
    if preempt_delay is not None:
        body.append(f"vrrp {vrid} preempt delay minimum {int(preempt_delay)}")
    body.append("exit")
    return ConfigPlan(
        "routing.vrrp",
        tuple(body),
        (f"interface {name}", f"no vrrp {vrid}", "exit"),
        summary=f"VRRP {vrid} on {name}",
        notes=("IOS-XE with 'fhrp version vrrp v3' uses 'vrrp N address-family ipv4'.",),
    )


def route_policy(
    name: str,
    node: int,
    action: Literal["permit", "deny"],
    prefix_list: str | None,
    prefixes: list[PrefixEntry],
    apply_cost: int | None = None,
) -> ConfigPlan:
    policy = quote_free_text(name, max_length=40)
    body: list[str] = []
    if prefix_list:
        plist = quote_free_text(prefix_list, max_length=63)
        for entry in prefixes:
            net = ipaddress.ip_network(entry.prefix, strict=False)
            line = f"ip prefix-list {plist} seq {entry.index} {entry.action} {net}"
            if entry.greater_equal is not None:
                line += f" ge {entry.greater_equal}"
            if entry.less_equal is not None:
                line += f" le {entry.less_equal}"
            body.append(line)
    body.append(f"route-map {policy} {action} {node}")
    if prefix_list:
        body.append(f"match ip address prefix-list {prefix_list}")
    if apply_cost is not None:
        body.append(f"set metric {int(apply_cost)}")
    body.append("exit")
    return ConfigPlan(
        "routing.route_policy",
        tuple(body),
        (f"no route-map {policy} {action} {node}",),
        summary=f"route-map {policy} {node}",
    )
