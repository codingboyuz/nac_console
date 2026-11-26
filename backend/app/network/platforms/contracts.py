"""Typed contracts every platform implements (parsers + builder namespaces).

Parsers return the shared value objects from ``app.network.parsers`` so services stay
vendor-neutral. Builders return ``ConfigPlan`` objects in the platform's CLI dialect; their
signatures mirror the original Huawei builders so services call every vendor the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from app.domain.devices import DeviceFamily
    from app.network.builders.cli_acl import AclDefinition, Direction
    from app.network.builders.cli_nac import NacProvisionSpec
    from app.network.builders.cli_routing import PrefixEntry, StaticRoute
    from app.network.builders.plan import ConfigPlan
    from app.network.parsers import (
        ArpEntry,
        CpuUsage,
        DhcpBindingEntry,
        InterfaceBrief,
        InterfaceDetail,
        MacEntry,
        MemoryUsage,
        RouteEntry,
        StpGlobal,
        StpPortBrief,
        VersionInfo,
        VlanInfo,
    )
    from app.network.parsers.acl import ParsedAcl
    from app.network.parsers.interface_config import InterfaceConfig
    from app.network.parsers.routing_protocols import BgpPeer, OspfNeighbor

PortMode = Literal["access", "trunk", "hybrid"]


class Parsers(Protocol):
    def parse_version(self, text: str | None) -> VersionInfo: ...
    def parse_vlans(self, text: str | None) -> list[VlanInfo]: ...
    def parse_interface_brief(self, text: str | None) -> list[InterfaceBrief]: ...
    def parse_interface_detail(self, text: str | None) -> list[InterfaceDetail]: ...
    def parse_interface_config(self, text: str | None) -> InterfaceConfig: ...
    def parse_mac_table(self, text: str | None) -> list[MacEntry]: ...
    def parse_mac_aging(self, text: str | None) -> int | None: ...
    def parse_arp(self, text: str | None) -> list[ArpEntry]: ...
    def parse_dhcp_snooping_bindings(self, text: str | None) -> list[DhcpBindingEntry]: ...
    def parse_routing_table(self, text: str | None) -> list[RouteEntry]: ...
    def parse_static_route_lines(self, text: str | None) -> set[str]: ...
    def parse_ospf_peers(self, text: str | None) -> list[OspfNeighbor]: ...
    def parse_bgp_peers(self, text: str | None) -> list[BgpPeer]: ...
    def parse_stp_global(self, text: str | None) -> StpGlobal: ...
    def parse_stp_brief(self, text: str | None) -> list[StpPortBrief]: ...
    def parse_cpu_usage(self, text: str | None) -> CpuUsage: ...
    def parse_memory_usage(self, text: str | None) -> MemoryUsage: ...
    def parse_acls(self, text: str | None) -> list[ParsedAcl]: ...
    def management_acls(self, running_config: str | None) -> frozenset[str]: ...


class L2Builders(Protocol):
    def create_vlans(
        self, wanted: dict[int, str | None], existing: dict[int, str | None]
    ) -> ConfigPlan: ...
    def delete_vlans(self, vlans: list[int], existing: dict[int, str | None]) -> ConfigPlan: ...
    def rename_vlan(self, vlan_id: int, name: str) -> ConfigPlan: ...
    def set_port_mode(
        self,
        interface: str,
        mode: PortMode,
        current: InterfaceConfig,
        *,
        access_vlan: int | None = None,
        trunk_vlans: list[int] | None = None,
        pvid: int | None = None,
        tagged: list[int] | None = None,
        untagged: list[int] | None = None,
    ) -> ConfigPlan: ...
    def trunk_vlans(
        self, interface: str, add: list[int], remove: list[int], current: InterfaceConfig
    ) -> ConfigPlan: ...
    def set_admin_state(
        self, interface: str, enabled: bool, current: InterfaceConfig
    ) -> ConfigPlan: ...
    def set_description(
        self, interface: str, description: str | None, current: InterfaceConfig
    ) -> ConfigPlan: ...
    def set_speed_duplex(
        self, interface: str, speed: int | None, duplex: str | None, auto: bool
    ) -> ConfigPlan: ...
    def static_mac(
        self,
        mac: str,
        vlan: int,
        interface: str | None,
        *,
        remove: bool = False,
        blackhole: bool = False,
    ) -> ConfigPlan: ...
    def stp_settings(
        self, mode: str | None, priority: int | None, instance: int = 0
    ) -> ConfigPlan: ...
    def stp_edge_port(self, interface: str, enabled: bool) -> ConfigPlan: ...
    def dhcp_snooping(self, vlans: list[int], trusted: list[str]) -> ConfigPlan: ...


class AclBuilders(Protocol):
    def plan_acl(
        self,
        desired: AclDefinition,
        current: ParsedAcl | None,
        mgmt_acls: frozenset[str] = ...,
    ) -> ConfigPlan: ...
    def delete_acl(self, definition: AclDefinition, mgmt_acls: frozenset[str]) -> ConfigPlan: ...
    def apply_acl(
        self,
        family: DeviceFamily,
        acl: str,
        direction: Direction,
        *,
        interface: str | None,
        remove: bool = False,
        ipv6: bool = False,
        mgmt_acls: frozenset[str] = ...,
    ) -> ConfigPlan: ...


class RoutingBuilders(Protocol):
    def static_route(
        self, route: StaticRoute, existing: set[str], *, remove: bool = False, verify: bool = True
    ) -> ConfigPlan: ...
    def ospf(
        self,
        process: int,
        router_id: str | None,
        area: str,
        networks: list[str],
        *,
        silent_interfaces: list[str] | None = None,
    ) -> ConfigPlan: ...
    def bgp(
        self,
        asn: int,
        router_id: str | None,
        peers: list[tuple[str, int]],
        networks: list[str],
    ) -> ConfigPlan: ...
    def rip(self, process: int, version: Literal[1, 2], networks: list[str]) -> ConfigPlan: ...
    def vrrp(
        self,
        interface: str,
        vrid: int,
        virtual_ip: str,
        priority: int | None,
        preempt_delay: int | None,
    ) -> ConfigPlan: ...
    def route_policy(
        self,
        name: str,
        node: int,
        action: Literal["permit", "deny"],
        prefix_list: str | None,
        prefixes: list[PrefixEntry],
        apply_cost: int | None = None,
    ) -> ConfigPlan: ...


class RestorePlanLike(Protocol):
    @property
    def lines(self) -> tuple[str, ...]: ...
    @property
    def notes(self) -> tuple[str, ...]: ...


class ConfigText(Protocol):
    """Configuration text handling in the platform's dialect (comments, volatile headers)."""

    def normalize(self, text: str) -> str: ...
    def config_hash(self, text: str) -> str: ...
    def sanitize(self, text: str) -> str: ...
    def unified_diff(
        self, old: str, new: str, *, old_name: str = "a", new_name: str = "b", context: int = 3
    ) -> str: ...
    def restore_plan(self, running: str, backup: str) -> RestorePlanLike: ...


class NacBuilders(Protocol):
    def provision(self, family: DeviceFamily, spec: NacProvisionSpec) -> ConfigPlan: ...
    def related_config(self, running: str) -> str: ...
