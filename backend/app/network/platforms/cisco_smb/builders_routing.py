"""Routing plans for Cisco SMB: static routes and RIP (CBS350); no OSPF/BGP/route-maps.

VRRP exists only on SG550X firmware ('vrrp N ip X' under 'interface vlan N').
"""

from __future__ import annotations

import ipaddress
from typing import Literal

from app.domain.interfaces import normalize_interface
from app.network.builders.cli_routing import PrefixEntry, RoutingValidationError, StaticRoute
from app.network.builders.plan import ConfigPlan

_UNSUPPORTED = "is not available on Cisco Small Business switches"


def _ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise RoutingValidationError(f"invalid IP address {value!r}") from exc


def render_static(route: StaticRoute) -> str:
    net = ipaddress.ip_network(route.prefix, strict=False)
    if net.version != 4:
        raise RoutingValidationError("only IPv4 static routes are supported here")
    if not route.next_hop:
        raise RoutingValidationError("Cisco SMB static routes need a next hop")
    if route.vpn_instance:
        raise RoutingValidationError(f"VRF routes {_UNSUPPORTED}")
    line = f"ip route {net.network_address} {net.netmask} {_ip(route.next_hop)}"
    if route.preference is not None:
        if not 1 <= route.preference <= 255:
            raise RoutingValidationError("metric must be 1-255")
        line += f" metric {route.preference}"
    return line


def static_route(
    route: StaticRoute, existing: set[str], *, remove: bool = False, verify: bool = True
) -> ConfigPlan:
    line = render_static(route)
    if (line in existing) != remove:
        return ConfigPlan("route.static", (), summary="no change")
    net = ipaddress.ip_network(route.prefix, strict=False)
    no_line = f"no ip route {net.network_address} {net.netmask} {route.next_hop}"
    apply, undo = (no_line, line) if remove else (line, no_line)
    return ConfigPlan(
        "route.static.remove" if remove else "route.static",
        (apply,),
        (undo,),
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
    raise RoutingValidationError(f"OSPF {_UNSUPPORTED}")


def bgp(
    asn: int, router_id: str | None, peers: list[tuple[str, int]], networks: list[str]
) -> ConfigPlan:
    raise RoutingValidationError(f"BGP {_UNSUPPORTED}")


def rip(process: int, version: Literal[1, 2], networks: list[str]) -> ConfigPlan:
    body = ["router rip", f"version {version}"]
    for cidr in networks:
        body.append(f"network {ipaddress.ip_network(cidr, strict=False).network_address}")
    body.append("exit")
    return ConfigPlan(
        "routing.rip",
        tuple(body),
        ("no router rip",),
        summary="RIP",
        notes=("RIP is available on CBS350 firmware 3.x; not on SG350 2.5.",),
    )


def vrrp(
    interface: str, vrid: int, virtual_ip: str, priority: int | None, preempt_delay: int | None
) -> ConfigPlan:
    if not 1 <= vrid <= 255:
        raise RoutingValidationError("VRID 1-255")
    name = normalize_interface(interface)
    body = [f"interface {name}", f"vrrp {vrid} ip {_ip(virtual_ip)}"]
    if priority is not None:
        body.append(f"vrrp {vrid} priority {priority}")
    if preempt_delay is not None:
        body.append(f"vrrp {vrid} preempt")
    body += [f"no vrrp {vrid} shutdown", "exit"]
    return ConfigPlan(
        "routing.vrrp",
        tuple(body),
        (f"interface {name}", f"no vrrp {vrid}", "exit"),
        summary=f"VRRP {vrid} on {name}",
        notes=("VRRP is available on SG550X / CBS350 L3 firmware only.",),
    )


def route_policy(
    name: str,
    node: int,
    action: Literal["permit", "deny"],
    prefix_list: str | None,
    prefixes: list[PrefixEntry],
    apply_cost: int | None = None,
) -> ConfigPlan:
    raise RoutingValidationError(f"route-maps {_UNSUPPORTED}")
