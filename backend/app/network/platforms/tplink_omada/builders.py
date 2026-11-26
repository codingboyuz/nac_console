"""Change plans for Omada-managed switches, written in the Omada change language (dsl.py).

Ports carry one native (untagged) VLAN plus a tagged set, applied as a per-port profile
override. Features the controller manages site-wide (ACLs, routing, 802.1X, STP, DHCP
snooping, static MACs, speed) are refused: configure them in the controller.
"""

from __future__ import annotations

from typing import Literal, NoReturn

from app.core.exceptions import UnsupportedCapability
from app.domain.commands.sanitize import quote_free_text
from app.domain.devices import DeviceFamily
from app.domain.vlans import to_cisco_ranges, validate_vlan_id
from app.network.builders.cli_acl import AclDefinition, Direction
from app.network.builders.cli_nac import NacProvisionSpec
from app.network.builders.cli_routing import PrefixEntry, StaticRoute
from app.network.builders.plan import ConfigPlan, Verifier
from app.network.parsers.acl import ParsedAcl
from app.network.parsers.interface_config import InterfaceConfig
from app.network.platforms.contracts import PortMode
from app.network.platforms.tplink_omada.dsl import port_number
from app.network.ports import DeviceDriver


def _refuse(feature: str) -> NoReturn:
    raise UnsupportedCapability(
        f"{feature} is managed site-wide in the Omada controller and is not generated here"
    )


def _name(text: str) -> str:
    return quote_free_text(text, max_length=32)


def _verifier(present: set[int], absent: set[int]) -> Verifier:
    def verify(driver: DeviceDriver) -> bool:
        from app.network.platforms.tplink_omada.parsers import parse_vlans

        have = {v.vlan_id for v in parse_vlans(driver.run_show("GET networks").output)}
        return present <= have and not (absent & have)

    return verify


# --- VLANs -----------------------------------------------------------------------------------
def create_vlans(wanted: dict[int, str | None], existing: dict[int, str | None]) -> ConfigPlan:
    lines: list[str] = []
    undo: list[str] = []
    for vid in sorted(wanted):
        validate_vlan_id(vid, allow_reserved=False)
        name = _name(wanted[vid] or f"VLAN{vid:04d}")
        if vid not in existing:
            lines.append(f"vlan create {vid} name {name}")
            undo.append(f"vlan delete {vid}")
        elif wanted[vid] and existing[vid] != wanted[vid]:
            lines.append(f"vlan rename {vid} name {name}")
            undo.append(f"vlan rename {vid} name {_name(existing[vid] or f'VLAN{vid:04d}')}")
    return ConfigPlan(
        "vlan.create",
        tuple(lines),
        tuple(undo),
        _verifier(set(wanted), set()),
        summary=f"create {sum(v not in existing for v in wanted)} VLAN(s), ensure {len(wanted)}",
    )


def delete_vlans(vlans: list[int], existing: dict[int, str | None]) -> ConfigPlan:
    targets = sorted(v for v in set(vlans) if v in existing)
    for vid in targets:
        validate_vlan_id(vid, allow_reserved=False)
    if not targets:
        return ConfigPlan("vlan.delete", (), summary="nothing to delete")
    return ConfigPlan(
        "vlan.delete",
        tuple(f"vlan delete {vid}" for vid in targets),
        tuple(f"vlan create {v} name {_name(existing[v] or f'VLAN{v:04d}')}" for v in targets),
        _verifier(set(), set(targets)),
        summary=f"delete VLAN(s) {to_cisco_ranges(targets)}",
        notes=("Ports keep their profile; re-created networks get a new controller id.",),
    )


def rename_vlan(vlan_id: int, name: str) -> ConfigPlan:
    validate_vlan_id(vlan_id)
    return ConfigPlan(
        "vlan.rename", (f"vlan rename {vlan_id} name {_name(name)}",), summary="rename VLAN"
    )


# --- ports -----------------------------------------------------------------------------------
def _tagged(vlans: set[int]) -> str:
    return to_cisco_ranges(sorted(vlans)) if vlans else "none"


def _port_lines(
    port: int, native: int | None, tagged: set[int], current: InterfaceConfig
) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    undo: list[str] = []
    if native is not None and native != current.hybrid_pvid:
        lines.append(f"port {port} native {validate_vlan_id(native)}")
        if current.hybrid_pvid is not None:
            undo.append(f"port {port} native {current.hybrid_pvid}")
    if tagged != set(current.hybrid_tagged):
        for vid in tagged:
            validate_vlan_id(vid)
        lines.append(f"port {port} tagged {_tagged(tagged)}")
        undo.append(f"port {port} tagged {_tagged(set(current.hybrid_tagged))}")
    return lines, undo


def set_port_mode(
    interface: str,
    mode: PortMode,
    current: InterfaceConfig,
    *,
    access_vlan: int | None = None,
    trunk_vlans: list[int] | None = None,
    pvid: int | None = None,
    tagged: list[int] | None = None,
    untagged: list[int] | None = None,
) -> ConfigPlan:
    port = port_number(interface)
    native: int | None
    want: set[int]
    if mode == "access":
        if access_vlan is None:
            raise ValueError("access mode needs access_vlan")
        native, want = access_vlan, set()
    elif mode == "trunk":
        native, want = pvid or current.hybrid_pvid or 1, set(trunk_vlans or [])
    else:
        extra = sorted(set(untagged or []) - ({pvid} if pvid else set()))
        if len(extra) > 1 or (extra and pvid):
            raise UnsupportedCapability("Omada ports carry one untagged (native) VLAN")
        native, want = pvid or (extra[0] if extra else current.hybrid_pvid), set(tagged or [])
    lines, undo = _port_lines(port, native, want - ({native} if native else set()), current)
    return ConfigPlan("interface.port_mode", tuple(lines), tuple(undo), summary=f"port {port}")


def trunk_vlans(
    interface: str, add: list[int], remove: list[int], current: InterfaceConfig
) -> ConfigPlan:
    port = port_number(interface)
    want = (set(current.hybrid_tagged) | set(add)) - set(remove)
    lines, undo = _port_lines(port, None, want, current)
    return ConfigPlan("interface.trunk_vlans", tuple(lines), tuple(undo), summary=f"port {port}")


def set_admin_state(interface: str, enabled: bool, current: InterfaceConfig) -> ConfigPlan:
    port = port_number(interface)
    if enabled != current.shutdown:
        return ConfigPlan("interface.admin_state", (), summary="no change")
    line, undo = (f"port {port} enable", f"port {port} disable")
    if not enabled:
        line, undo = undo, line
    return ConfigPlan("interface.admin_state", (line,), (undo,), summary=line)


def set_description(
    interface: str, description: str | None, current: InterfaceConfig
) -> ConfigPlan:
    port = port_number(interface)
    if (description or None) == current.description:
        return ConfigPlan("interface.description", (), summary="no change")
    line = f"port {port} name {_name(description) if description else 'none'}"
    undo = f"port {port} name {_name(current.description) if current.description else 'none'}"
    return ConfigPlan("interface.description", (line,), (undo,), summary=line)


def set_speed_duplex(
    interface: str, speed: int | None, duplex: str | None, auto: bool
) -> ConfigPlan:
    _refuse("Port speed/duplex")


def static_mac(
    mac: str, vlan: int, interface: str | None, *, remove: bool = False, blackhole: bool = False
) -> ConfigPlan:
    _refuse("Static MAC")


def stp_settings(mode: str | None, priority: int | None, instance: int = 0) -> ConfigPlan:
    _refuse("Spanning tree")


def stp_edge_port(interface: str, enabled: bool) -> ConfigPlan:
    _refuse("Spanning tree")


def dhcp_snooping(vlans: list[int], trusted: list[str]) -> ConfigPlan:
    _refuse("DHCP snooping")


# --- ACL / routing / NAC: controller-managed -------------------------------------------------
def plan_acl(
    desired: AclDefinition, current: ParsedAcl | None, mgmt_acls: frozenset[str] = frozenset()
) -> ConfigPlan:
    _refuse("ACL")


def delete_acl(definition: AclDefinition, mgmt_acls: frozenset[str]) -> ConfigPlan:
    _refuse("ACL")


def apply_acl(
    family: DeviceFamily,
    acl: str,
    direction: Direction,
    *,
    interface: str | None,
    remove: bool = False,
    ipv6: bool = False,
    mgmt_acls: frozenset[str] = frozenset(),
) -> ConfigPlan:
    _refuse("ACL")


def static_route(
    route: StaticRoute, existing: set[str], *, remove: bool = False, verify: bool = True
) -> ConfigPlan:
    _refuse("Routing")


def ospf(
    process: int,
    router_id: str | None,
    area: str,
    networks: list[str],
    *,
    silent_interfaces: list[str] | None = None,
) -> ConfigPlan:
    _refuse("Routing")


def bgp(
    asn: int, router_id: str | None, peers: list[tuple[str, int]], networks: list[str]
) -> ConfigPlan:
    _refuse("Routing")


def rip(process: int, version: Literal[1, 2], networks: list[str]) -> ConfigPlan:
    _refuse("Routing")


def vrrp(
    interface: str, vrid: int, virtual_ip: str, priority: int | None, preempt_delay: int | None
) -> ConfigPlan:
    _refuse("Routing")


def route_policy(
    name: str,
    node: int,
    action: Literal["permit", "deny"],
    prefix_list: str | None,
    prefixes: list[PrefixEntry],
    apply_cost: int | None = None,
) -> ConfigPlan:
    _refuse("Routing")


def provision(family: DeviceFamily, spec: NacProvisionSpec) -> ConfigPlan:
    _refuse("802.1X / RADIUS")


def related_config(running: str) -> str:
    return ""
