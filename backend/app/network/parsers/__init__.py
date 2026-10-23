"""Pure text parsers for Huawei VRP ``display`` command output (S-series, CE, AR)."""

from __future__ import annotations

from app.network.parsers.arp import ArpEntry, parse_arp
from app.network.parsers.common import clean_output, find_cli_error
from app.network.parsers.cpu_mem import CpuUsage, MemoryUsage, parse_cpu_usage, parse_memory_usage
from app.network.parsers.dhcp import DhcpBindingEntry, parse_dhcp_snooping_bindings
from app.network.parsers.interfaces import (
    InterfaceBrief,
    InterfaceDetail,
    IpInterfaceBrief,
    parse_interface_brief,
    parse_interface_detail,
    parse_ip_interface_brief,
)
from app.network.parsers.lldp import LldpNeighbor, parse_lldp_neighbors_brief
from app.network.parsers.mac import MacEntry, parse_mac_aging, parse_mac_table
from app.network.parsers.routing import RouteEntry, parse_routing_table
from app.network.parsers.stp import StpGlobal, StpPortBrief, parse_stp_brief, parse_stp_global
from app.network.parsers.transceivers import TransceiverInfo, parse_transceivers
from app.network.parsers.version import VersionInfo, parse_version
from app.network.parsers.vlans import VlanInfo, parse_vlans

__all__ = [
    "ArpEntry",
    "CpuUsage",
    "DhcpBindingEntry",
    "InterfaceBrief",
    "InterfaceDetail",
    "IpInterfaceBrief",
    "LldpNeighbor",
    "MacEntry",
    "MemoryUsage",
    "RouteEntry",
    "StpGlobal",
    "StpPortBrief",
    "TransceiverInfo",
    "VersionInfo",
    "VlanInfo",
    "clean_output",
    "find_cli_error",
    "parse_arp",
    "parse_cpu_usage",
    "parse_dhcp_snooping_bindings",
    "parse_interface_brief",
    "parse_interface_detail",
    "parse_ip_interface_brief",
    "parse_lldp_neighbors_brief",
    "parse_mac_aging",
    "parse_mac_table",
    "parse_memory_usage",
    "parse_routing_table",
    "parse_stp_brief",
    "parse_stp_global",
    "parse_transceivers",
    "parse_version",
    "parse_vlans",
]
