"""Routing plans: static/default routes, OSPF, BGP, RIP, VRRP, route-policy / ip-prefix.

All commands are VRP-VERSION-DEPENDENT; spellings follow S V200R019 and CE V8 (identical for
the subset used here).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Literal

from app.domain.commands.sanitize import quote_free_text
from app.domain.interfaces import normalize_interface
from app.network.builders.plan import ConfigPlan, Verifier
from app.network.parsers import parse_routing_table
from app.network.ports import DeviceDriver


class RoutingValidationError(ValueError):
    pass


def _ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise RoutingValidationError(f"invalid IP address {value!r}") from exc


def _route_present(prefix: str, protocol: str) -> Verifier:
    def verify(driver: DeviceDriver) -> bool:
        table = parse_routing_table(driver.run_show("display ip routing-table").output)
        return any(r.prefix == prefix and r.protocol.lower().startswith(protocol) for r in table)

    return verify


@dataclass(frozen=True, slots=True)
class StaticRoute:
    prefix: str  # CIDR
    next_hop: str | None = None
    interface: str | None = None
    preference: int | None = None
    description: str | None = None
    vpn_instance: str | None = None

    def render(self) -> str:
        net = ipaddress.ip_network(self.prefix, strict=False)
        if net.version != 4:
            raise RoutingValidationError("only IPv4 static routes are supported here")
        if not self.next_hop and not self.interface:
            raise RoutingValidationError("a route needs a next hop and/or an outgoing interface")
        parts = ["ip route-static"]
        if self.vpn_instance:
            parts.append(f"vpn-instance {quote_free_text(self.vpn_instance, max_length=31)}")
        parts += [str(net.network_address), str(net.netmask)]
        if self.interface:
            parts.append(normalize_interface(self.interface))
        if self.next_hop:
            parts.append(_ip(self.next_hop))
        if self.preference is not None:
            if not 1 <= self.preference <= 255:
                raise RoutingValidationError("preference must be 1-255")
            parts.append(f"preference {self.preference}")
        if self.description:
            parts.append(f"description {quote_free_text(self.description, max_length=35)}")
        return " ".join(parts)

    @property
    def normalized_prefix(self) -> str:
        return str(ipaddress.ip_network(self.prefix, strict=False))


def static_route(
    route: StaticRoute, existing: set[str], *, remove: bool = False, verify: bool = True
) -> ConfigPlan:
    line = route.render()
    if (line in existing) != remove:
        return ConfigPlan("route.static", (), summary="no change")
    apply, undo = (f"undo {line}", line) if remove else (line, f"undo {line}")
    checker = _route_present(route.normalized_prefix, "static") if verify and not remove else None
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
    header = f"ospf {process}" + (f" router-id {_ip(router_id)}" if router_id else "")
    body = [header]
    for iface in silent_interfaces or []:
        body.append(f"silent-interface {normalize_interface(iface)}")
    body.append(f"area {_ip(area) if '.' in area else int(area)}")
    for cidr in networks:
        net = ipaddress.ip_network(cidr, strict=False)
        body.append(f"network {net.network_address} {net.hostmask}")
    body += ["quit", "quit"]
    return ConfigPlan(
        "routing.ospf", tuple(body), (f"undo ospf {process}",), summary=f"OSPF {process}"
    )


def bgp(
    asn: int,
    router_id: str | None,
    peers: list[tuple[str, int]],
    networks: list[str],
) -> ConfigPlan:
    if not 1 <= asn <= 4294967295:
        raise RoutingValidationError("invalid AS number")
    body = [f"bgp {asn}"]
    if router_id:
        body.append(f"router-id {_ip(router_id)}")
    for peer, remote_as in peers:
        body.append(f"peer {_ip(peer)} as-number {int(remote_as)}")
    body.append("ipv4-family unicast")
    for peer, _ in peers:
        body.append(f"peer {_ip(peer)} enable")
    for cidr in networks:
        net = ipaddress.ip_network(cidr, strict=False)
        body.append(f"network {net.network_address} {net.prefixlen}")
    body += ["quit", "quit"]
    rollback = [f"bgp {asn}", *[f"undo peer {_ip(p)}" for p, _ in peers], "quit"]
    return ConfigPlan("routing.bgp", tuple(body), tuple(rollback), summary=f"BGP AS{asn}")


def rip(process: int, version: Literal[1, 2], networks: list[str]) -> ConfigPlan:
    body = [f"rip {process}", f"version {version}"]
    for cidr in networks:
        # RIP 'network' takes the classful network address.
        body.append(f"network {ipaddress.ip_network(cidr, strict=False).network_address}")
    body.append("quit")
    return ConfigPlan(
        "routing.rip", tuple(body), (f"undo rip {process}",), summary=f"RIP {process}"
    )


def vrrp(
    interface: str, vrid: int, virtual_ip: str, priority: int | None, preempt_delay: int | None
) -> ConfigPlan:
    if not 1 <= vrid <= 255:
        raise RoutingValidationError("VRID 1-255")
    name = normalize_interface(interface)
    body = [f"interface {name}", f"vrrp vrid {vrid} virtual-ip {_ip(virtual_ip)}"]
    if priority is not None:
        if not 1 <= priority <= 254:
            raise RoutingValidationError("VRRP priority 1-254")
        body.append(f"vrrp vrid {vrid} priority {priority}")
    if preempt_delay is not None:
        body.append(f"vrrp vrid {vrid} preempt-mode timer delay {int(preempt_delay)}")
    body.append("quit")
    rollback = (f"interface {name}", f"undo vrrp vrid {vrid}", "quit")
    return ConfigPlan("routing.vrrp", tuple(body), rollback, summary=f"VRRP {vrid} on {name}")


@dataclass(frozen=True, slots=True)
class PrefixEntry:
    index: int
    action: Literal["permit", "deny"]
    prefix: str
    greater_equal: int | None = None
    less_equal: int | None = None


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
        plist = quote_free_text(prefix_list, max_length=169)
        for entry in prefixes:
            net = ipaddress.ip_network(entry.prefix, strict=False)
            line = (
                f"ip ip-prefix {plist} index {entry.index} {entry.action} "
                f"{net.network_address} {net.prefixlen}"
            )
            if entry.greater_equal is not None:
                line += f" greater-equal {entry.greater_equal}"
            if entry.less_equal is not None:
                line += f" less-equal {entry.less_equal}"
            body.append(line)
    body.append(f"route-policy {policy} {action} node {node}")
    if prefix_list:
        body.append(f"if-match ip-prefix {prefix_list}")
    if apply_cost is not None:
        body.append(f"apply cost {int(apply_cost)}")
    body.append("quit")
    return ConfigPlan(
        "routing.route_policy",
        tuple(body),
        (f"undo route-policy {policy} node {node}",),
        summary=f"route-policy {policy} node {node}",
    )
