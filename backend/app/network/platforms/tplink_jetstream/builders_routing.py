"""Routing plans for TP-Link: static routes everywhere; OSPF only on L3 models (2025 guide).

BGP, RIP, VRRP and route policies are refused: the official guides mark them as available on
certain models only, without enough syntax detail to generate them safely.
"""

from __future__ import annotations

import ipaddress
from typing import Literal

from app.network.builders.cli_routing import PrefixEntry, RoutingValidationError, StaticRoute
from app.network.builders.plan import ConfigPlan
from app.network.platforms.tplink_jetstream.naming import tp_iface

_UNSUPPORTED = "is not generated for TP-Link switches"


def _ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise RoutingValidationError(f"invalid IP address {value!r}") from exc


def render_static(route: StaticRoute) -> str:
    net = ipaddress.ip_network(route.prefix, strict=False)
    if net.version != 4 or not route.next_hop:
        raise RoutingValidationError("TP-Link static routes are IPv4 with a next hop")
    if route.vpn_instance:
        raise RoutingValidationError(f"VRF routes {_UNSUPPORTED}")
    line = f"ip route {net.network_address} {net.netmask} {_ip(route.next_hop)}"
    if route.preference is not None:
        line += f" {int(route.preference)}"
    return line


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
    area_id = _ip(area) if "." in area else str(ipaddress.IPv4Address(int(area)))
    body = ["ip routing", f"router ospf {int(process)}"]
    if router_id:
        body.append(f"router-id {_ip(router_id)}")
    for cidr in networks:
        net = ipaddress.ip_network(cidr, strict=False)
        body.append(f"network {net.network_address} {net.netmask} area {area_id}")
    for iface in silent_interfaces or []:
        body.append(f"passive-interface {tp_iface(iface)}")
    body.append("exit")
    return ConfigPlan(
        "routing.ospf",
        tuple(body),
        (f"no router ospf {int(process)}",),
        summary=f"OSPF {process}",
        notes=("OSPF exists only on TP-Link L3 models (Omada CLI guide 2025).",),
    )


def bgp(
    asn: int, router_id: str | None, peers: list[tuple[str, int]], networks: list[str]
) -> ConfigPlan:
    raise RoutingValidationError(f"BGP {_UNSUPPORTED}")


def rip(process: int, version: Literal[1, 2], networks: list[str]) -> ConfigPlan:
    raise RoutingValidationError(f"RIP {_UNSUPPORTED}")


def vrrp(
    interface: str, vrid: int, virtual_ip: str, priority: int | None, preempt_delay: int | None
) -> ConfigPlan:
    raise RoutingValidationError(f"VRRP {_UNSUPPORTED}")


def route_policy(
    name: str,
    node: int,
    action: Literal["permit", "deny"],
    prefix_list: str | None,
    prefixes: list[PrefixEntry],
    apply_cost: int | None = None,
) -> ConfigPlan:
    raise RoutingValidationError(f"route policies {_UNSUPPORTED}")
