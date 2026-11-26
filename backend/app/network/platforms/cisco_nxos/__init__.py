"""Cisco NX-OS (Nexus) platform adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.platforms import CommitModel, Platform, RollbackTimer
from app.network.platforms.base import PlatformAdapter, Q
from app.network.platforms.cisco_ios import config_text
from app.network.platforms.cisco_nxos import (
    builders_acl,
    builders_l2,
    builders_nac,
    builders_routing,
    parsers,
)
from app.network.platforms.cisco_nxos.rules import NxosClassifier, NxosLockoutGuard

if TYPE_CHECKING:
    from app.network.ports import ConnectionParams, DeviceDriver

COMMANDS: dict[Q, str] = {
    Q.VERSION: "show version",
    Q.RUNNING_CONFIG: "show running-config",
    Q.SAVED_CONFIG: "show startup-config",
    Q.VLANS: "show vlan brief",
    Q.INTERFACES_BRIEF: "show interface status",
    Q.INTERFACES: "show interface",
    Q.INTERFACE: "show interface {name}",
    Q.INTERFACE_CONFIG: "show running-config interface {name}",
    Q.MAC_TABLE: "show mac address-table",
    Q.MAC_AGING: "show mac address-table aging-time",
    Q.ARP: "show ip arp",
    Q.DHCP_BINDINGS: "show ip dhcp snooping binding",
    Q.ROUTES: "show ip route",
    Q.STATIC_ROUTE_CONFIG: "show running-config | include ^ip route",
    Q.OSPF_NEIGHBORS: "show ip ospf neighbors",
    Q.BGP_PEERS: "show ip bgp summary",
    Q.STP_GLOBAL: "show spanning-tree",
    Q.STP_BRIEF: "show spanning-tree",
    Q.CPU: "show system resources",
    Q.MEMORY: "show system resources",
    Q.ACLS: "show ip access-lists",
    Q.ACL: "show ip access-lists {identifier}",
}

PROBES: dict[str, str] = {
    "static": "show ip route static",
    "ospf": "show ip ospf",
    "bgp": "show ip bgp summary",
    "rip": "show ip rip",
    "vrrp": "show vrrp",
    "route_policy": "show route-map",
}


def _ssh(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.cisco_nxos import CiscoNxosDriver

    return CiscoNxosDriver(params)


def _snmp(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_snmp import CiscoSnmpDriver

    return CiscoSnmpDriver(params)


CISCO_NXOS = PlatformAdapter(
    platform=Platform.CISCO_NXOS,
    commit_model=CommitModel.IMMEDIATE,
    rollback_timer=RollbackTimer.NONE,
    commands=COMMANDS,
    parsers=parsers,
    l2=builders_l2,
    acl=builders_acl,
    routing=builders_routing,
    nac=builders_nac,
    config=config_text,
    classifier=NxosClassifier(),
    lockout=NxosLockoutGuard(),
    drivers={"ssh": _ssh, "snmp": _snmp},
    routing_probes=PROBES,
    notes=(
        "Generated for NX-OS 9.3 / 10.x (Nexus 9000); verify on a lab device first.",
        "NX-OS has no CLI rollback timer: lockout overrides are refused on this platform.",
    ),
)
