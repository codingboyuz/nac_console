"""ACL domain model: Huawei numbering, strictly validated rules, CLI rendering.

Numbering (VRP): basic 2000-2999, advanced 3000-3999, layer-2 4000-4999; IPv6 basic/advanced use
the same ranges under ``acl ipv6``. Rendering is VRP-VERSION-DEPENDENT (S V200R and CE V8 share
the rule syntax used here).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from app.domain.mac import normalize_mac, to_huawei


class AclKind(StrEnum):
    BASIC = "basic"
    ADVANCED = "advanced"
    LAYER2 = "layer2"

    @property
    def number_range(self) -> range:
        return {
            "basic": range(2000, 3000),
            "advanced": range(3000, 4000),
            "layer2": range(4000, 5000),
        }[self.value]

    @property
    def named_keyword(self) -> str:
        return {"basic": "basic", "advanced": "advance", "layer2": "link"}[self.value]


Action = Literal["permit", "deny"]
Protocol = Literal["ip", "ipv6", "tcp", "udp", "icmp", "icmpv6", "gre", "ospf"]
_PORT_PROTOCOLS = {"tcp", "udp"}


class AclValidationError(ValueError):
    pass


def kind_for_number(number: int) -> AclKind:
    for kind in AclKind:
        if number in kind.number_range:
            return kind
    raise AclValidationError("ACL number must be 2000-4999")


def wildcard(cidr: str) -> str:
    """'10.0.0.0/24' -> '10.0.0.0 0.0.0.255'; IPv6 keeps prefix notation."""
    net = ipaddress.ip_network(cidr, strict=False)
    if net.version == 6:
        return str(net)
    return f"{net.network_address} {net.hostmask}"


@dataclass(frozen=True, slots=True)
class PortMatch:
    op: Literal["eq", "gt", "lt", "range"]
    start: int
    end: int | None = None

    def __post_init__(self) -> None:
        for port in (self.start, self.end):
            if port is not None and not 0 <= port <= 65535:
                raise AclValidationError("ports must be 0-65535")
        if self.op == "range" and (self.end is None or self.end < self.start):
            raise AclValidationError("range needs start <= end")

    def render(self) -> str:
        return f"range {self.start} {self.end}" if self.op == "range" else f"{self.op} {self.start}"


@dataclass(frozen=True, slots=True)
class AclRuleSpec:
    rule_id: int
    action: Action
    protocol: Protocol | None = None
    source: str | None = None  # CIDR or None for any
    destination: str | None = None
    source_port: PortMatch | None = None
    destination_port: PortMatch | None = None
    source_mac: str | None = None
    destination_mac: str | None = None
    vlan: int | None = None
    log: bool = False

    @property
    def is_deny_any(self) -> bool:
        return self.action == "deny" and not any(
            (
                self.source,
                self.destination,
                self.source_port,
                self.destination_port,
                self.source_mac,
                self.destination_mac,
                self.vlan,
            )
        )

    def validate(self, kind: AclKind) -> None:
        if not 0 <= self.rule_id <= 4294967294:
            raise AclValidationError("rule id out of range")
        for net in (self.source, self.destination):
            if net is not None:
                ipaddress.ip_network(net, strict=False)
        if kind is AclKind.BASIC and (
            self.destination or self.protocol or self.source_port or self.destination_port
        ):
            raise AclValidationError("basic ACLs only match on source")
        if kind is AclKind.ADVANCED and not self.protocol:
            raise AclValidationError("advanced rules need a protocol")
        if (self.source_port or self.destination_port) and self.protocol not in _PORT_PROTOCOLS:
            raise AclValidationError("ports require protocol tcp or udp")
        if kind is AclKind.LAYER2:
            if self.source or self.destination or self.protocol:
                raise AclValidationError("layer-2 ACLs match on MAC/VLAN only")
            for mac in (self.source_mac, self.destination_mac):
                if mac is not None:
                    normalize_mac(mac)
        elif self.source_mac or self.destination_mac:
            raise AclValidationError("MAC matching needs a layer-2 ACL")

    def render(self, kind: AclKind) -> str:
        self.validate(kind)
        parts = [f"rule {self.rule_id} {self.action}"]
        if kind is AclKind.ADVANCED and self.protocol:
            parts.append(self.protocol)
        if kind is AclKind.LAYER2:
            if self.source_mac:
                parts.append(f"source-mac {to_huawei(self.source_mac)}")
            if self.destination_mac:
                parts.append(f"destination-mac {to_huawei(self.destination_mac)}")
            if self.vlan is not None:
                parts.append(f"vlan-id {self.vlan}")
        else:
            if self.source:
                parts.append(f"source {wildcard(self.source)}")
            if self.destination:
                parts.append(f"destination {wildcard(self.destination)}")
            if self.source_port:
                parts.append(f"source-port {self.source_port.render()}")
            if self.destination_port:
                parts.append(f"destination-port {self.destination_port.render()}")
        if self.log:
            parts.append("logging")
        return " ".join(parts)


def acl_header(number: int | None, name: str | None, kind: AclKind, ipv6: bool) -> str:
    """'acl number 3000' / 'acl ipv6 number 3000' / 'acl name WEB advance'."""
    family = "acl ipv6" if ipv6 else "acl"
    if name:
        return f"{family} name {name} {kind.named_keyword}"
    if number is None or number not in kind.number_range:
        raise AclValidationError(
            f"{kind.value} ACL numbers are {kind.number_range.start}-{kind.number_range.stop - 1}"
        )
    return f"{family} number {number}"
