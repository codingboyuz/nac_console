"""ACL and routing request/response schemas (strict validation)."""

from __future__ import annotations

import ipaddress
import uuid
from typing import Literal

from pydantic import Field, IPvAnyAddress, field_validator, model_validator

from app.domain.acl import AclKind, AclRuleSpec, PortMatch
from app.network.builders.cli_routing import PrefixEntry, StaticRoute
from app.schemas.common import ApiModel
from app.schemas.l2 import ChangeRequest, ChangeResponse

_NAME = r"^[A-Za-z][A-Za-z0-9_-]{0,31}$"


def _cidr(value: str | None) -> str | None:
    if value is None or value == "any":
        return None
    return str(ipaddress.ip_network(value, strict=False))


class PortIn(ApiModel):
    op: Literal["eq", "gt", "lt", "range"]
    start: int = Field(ge=0, le=65535)
    end: int | None = Field(default=None, ge=0, le=65535)

    def to_domain(self) -> PortMatch:
        return PortMatch(self.op, self.start, self.end)


class AclRuleIn(ApiModel):
    rule_id: int = Field(default=0, ge=0, le=4294967294, description="0 = next free id (step 5)")
    action: Literal["permit", "deny"]
    protocol: Literal["ip", "ipv6", "tcp", "udp", "icmp", "icmpv6", "gre", "ospf"] | None = None
    source: str | None = Field(default=None, examples=["10.0.0.0/24"])
    destination: str | None = None
    source_port: PortIn | None = None
    destination_port: PortIn | None = None
    source_mac: str | None = None
    destination_mac: str | None = None
    vlan: int | None = Field(default=None, ge=1, le=4094)
    log: bool = False

    _cidrs = field_validator("source", "destination")(_cidr)

    def to_domain(self) -> AclRuleSpec:
        return AclRuleSpec(
            rule_id=self.rule_id,
            action=self.action,
            protocol=self.protocol,
            source=self.source,
            destination=self.destination,
            source_port=self.source_port.to_domain() if self.source_port else None,
            destination_port=self.destination_port.to_domain() if self.destination_port else None,
            source_mac=self.source_mac,
            destination_mac=self.destination_mac,
            vlan=self.vlan,
            log=self.log,
        )


class AclDeployRequest(ChangeRequest):
    kind: AclKind
    number: int | None = Field(default=None, ge=2000, le=4999)
    name: str | None = Field(default=None, pattern=_NAME)
    ipv6: bool = False
    description: str | None = Field(default=None, max_length=127)
    rules: list[AclRuleIn] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def _ident(self) -> AclDeployRequest:
        if (self.number is None) == (self.name is None):
            raise ValueError("give exactly one of number or name")
        ids = [r.rule_id for r in self.rules if r.rule_id]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule ids")
        return self


class AclRuleRequest(ChangeRequest):
    rule: AclRuleIn


class AclBindRequest(ChangeRequest):
    direction: Literal["inbound", "outbound"] = "inbound"
    interface: str | None = Field(default=None, description="None = global (S-series only)")


class AclRuleOut(ApiModel):
    rule_id: int
    action: str
    protocol: str | None
    source: str | None
    destination: str | None
    source_mac: str | None
    destination_mac: str | None
    vlan: int | None
    log: bool


class AclOut(ApiModel):
    id: uuid.UUID
    identifier: str
    number: int | None
    name: str | None
    kind: AclKind
    ipv6: bool
    description: str | None
    applied_to: list[dict[str, str | None]]
    rules: list[AclRuleOut]


class LiveAclOut(ApiModel):
    kind: str
    number: int | None
    name: str | None
    rules: dict[int, str]
    matches: dict[int, int]


class StaticRouteRequest(ChangeRequest):
    prefix: str = Field(examples=["10.20.0.0/16", "0.0.0.0/0"])
    next_hop: IPvAnyAddress | None = None
    interface: str | None = None
    preference: int | None = Field(default=None, ge=1, le=255)
    description: str | None = Field(default=None, max_length=35)
    vpn_instance: str | None = Field(default=None, pattern=_NAME)
    remove: bool = False
    verify: bool = Field(default=True, description="Require the route to be active afterwards")

    _p = field_validator("prefix")(lambda v: str(ipaddress.ip_network(v, strict=False)))

    def to_domain(self) -> StaticRoute:
        return StaticRoute(
            self.prefix,
            str(self.next_hop) if self.next_hop else None,
            self.interface,
            self.preference,
            self.description,
            self.vpn_instance,
        )


class OspfRequest(ChangeRequest):
    process: int = Field(default=1, ge=1, le=65535)
    router_id: IPvAnyAddress | None = None
    area: str = Field(default="0.0.0.0", pattern=r"^(\d+|\d+\.\d+\.\d+\.\d+)$")  # noqa: S104
    networks: list[str] = Field(min_length=1, max_length=256)
    silent_interfaces: list[str] = Field(default_factory=list)


class BgpPeerIn(ApiModel):
    address: IPvAnyAddress
    remote_as: int = Field(ge=1, le=4294967295)


class BgpRequest(ChangeRequest):
    asn: int = Field(ge=1, le=4294967295)
    router_id: IPvAnyAddress | None = None
    peers: list[BgpPeerIn] = Field(default_factory=list, max_length=256)
    networks: list[str] = Field(default_factory=list, max_length=1024)


class RipRequest(ChangeRequest):
    process: int = Field(default=1, ge=1, le=65535)
    version: Literal[1, 2] = 2
    networks: list[str] = Field(min_length=1)


class VrrpRequest(ChangeRequest):
    interface: str
    vrid: int = Field(ge=1, le=255)
    virtual_ip: IPvAnyAddress
    priority: int | None = Field(default=None, ge=1, le=254)
    preempt_delay: int | None = Field(default=None, ge=0, le=3600)


class PrefixIn(ApiModel):
    index: int = Field(ge=1, le=4294967295)
    action: Literal["permit", "deny"]
    prefix: str
    greater_equal: int | None = Field(default=None, ge=0, le=32)
    less_equal: int | None = Field(default=None, ge=0, le=32)

    def to_domain(self) -> PrefixEntry:
        return PrefixEntry(
            self.index, self.action, self.prefix, self.greater_equal, self.less_equal
        )


class RoutePolicyRequest(ChangeRequest):
    name: str = Field(pattern=_NAME)
    node: int = Field(ge=0, le=65535)
    action: Literal["permit", "deny"] = "permit"
    prefix_list: str | None = Field(default=None, pattern=_NAME)
    prefixes: list[PrefixIn] = Field(default_factory=list)
    apply_cost: int | None = Field(default=None, ge=0, le=4294967295)


class RouteOut(ApiModel):
    prefix: str
    protocol: str
    preference: int
    cost: int
    flags: str
    nexthop: str
    interface: str | None


class RoutingChangeResponse(ChangeResponse):
    routes_added: list[str]
    routes_removed: list[str]
