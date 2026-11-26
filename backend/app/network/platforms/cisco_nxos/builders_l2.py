"""L2 builders for NX-OS: IOS syntax with NX-OS differences.

Differences handled here: edge ports are 'spanning-tree port type edge', there is no plain
PVST mode (rapid-pvst / mst only), STP VLAN priority covers 1-3967 (3968-4094 are reserved),
and DHCP snooping needs 'feature dhcp' first.
"""

from __future__ import annotations

from app.domain.interfaces import normalize_interface
from app.domain.vlans import to_cisco_ranges
from app.network.builders.plan import ConfigPlan
from app.network.platforms.cisco_ios.builders_l2 import (
    ALL_VLANS,
    create_vlans,
    delete_vlans,
    rename_vlan,
    set_admin_state,
    set_description,
    set_port_mode,
    set_speed_duplex,
    static_mac,
    trunk_vlans,
)

__all__ = [
    "ALL_VLANS",
    "create_vlans",
    "delete_vlans",
    "dhcp_snooping",
    "rename_vlan",
    "set_admin_state",
    "set_description",
    "set_port_mode",
    "set_speed_duplex",
    "static_mac",
    "stp_edge_port",
    "stp_settings",
    "trunk_vlans",
]

_MODES = {"rstp": "rapid-pvst", "mstp": "mst"}


def stp_settings(mode: str | None, priority: int | None, instance: int = 0) -> ConfigPlan:
    lines: list[str] = []
    if mode:
        if mode not in _MODES:
            raise ValueError("NX-OS supports rstp (rapid-pvst) or mstp (mst)")
        lines.append(f"spanning-tree mode {_MODES[mode]}")
    if priority is not None:
        if priority % 4096 or not 0 <= priority <= 61440:
            raise ValueError("priority must be a multiple of 4096 in 0-61440")
        lines.append(
            f"spanning-tree mst {instance} priority {priority}"
            if mode == "mstp"
            else f"spanning-tree vlan 1-3967 priority {priority}"
        )
    return ConfigPlan("stp.settings", tuple(lines), summary="stp")


def stp_edge_port(interface: str, enabled: bool) -> ConfigPlan:
    name = normalize_interface(interface)
    line = "spanning-tree port type edge" if enabled else "no spanning-tree port type edge"
    return ConfigPlan("stp.edge_port", (f"interface {name}", line, "exit"))


def dhcp_snooping(vlans: list[int], trusted: list[str]) -> ConfigPlan:
    lines = ["feature dhcp", "ip dhcp snooping"]
    if vlans:
        lines.append(f"ip dhcp snooping vlan {to_cisco_ranges(vlans)}")
    for iface in trusted:
        lines += [f"interface {normalize_interface(iface)}", "ip dhcp snooping trust", "exit"]
    return ConfigPlan("dhcp.snooping", tuple(lines), summary="dhcp snooping")
