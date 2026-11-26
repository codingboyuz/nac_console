"""Cisco Small Business (CBS350 / SG350 / SG550X) platform adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.platforms import CommitModel, Platform, RollbackTimer
from app.network.platforms.base import PlatformAdapter, Q
from app.network.platforms.cisco_smb import (
    builders_acl,
    builders_l2,
    builders_nac,
    builders_routing,
    config_text,
    parsers,
)
from app.network.platforms.cisco_smb.rules import SmbClassifier, SmbLockoutGuard

if TYPE_CHECKING:
    from app.network.ports import ConnectionParams, DeviceDriver

# No OSPF / BGP and no memory command on these switches: those queries are not offered.
COMMANDS: dict[Q, str] = {
    Q.VERSION: "show version",
    Q.RUNNING_CONFIG: "show running-config",
    Q.SAVED_CONFIG: "show startup-config",
    Q.VLANS: "show vlan",
    Q.INTERFACES_BRIEF: "show interfaces status",
    Q.INTERFACES: "show interfaces status",
    Q.INTERFACE: "show interfaces counters {name}",
    Q.INTERFACE_CONFIG: "show running-config interface {name}",
    Q.MAC_TABLE: "show mac address-table",
    Q.MAC_AGING: "show mac address-table",
    Q.ARP: "show arp",
    Q.DHCP_BINDINGS: "show ip dhcp snooping binding",
    Q.ROUTES: "show ip route",
    Q.STATIC_ROUTE_CONFIG: "show running-config | include ip route",
    Q.STP_GLOBAL: "show spanning-tree",
    Q.STP_BRIEF: "show spanning-tree",
    Q.CPU: "show cpu utilization",
    Q.ACLS: "show access-lists",
    Q.ACL: "show access-lists {identifier}",
}

PROBES: dict[str, str] = {
    "static": "show ip route static",
    "rip": "show ip rip peer",
    "vrrp": "show vrrp brief",
}


def _ssh(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.cisco_smb import CiscoSmbDriver

    return CiscoSmbDriver(params)


def _telnet(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.cisco_smb import CiscoSmbTelnetDriver

    return CiscoSmbTelnetDriver(params)


def _snmp(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_snmp import CiscoSnmpDriver

    return CiscoSnmpDriver(params)


CISCO_SMB = PlatformAdapter(
    platform=Platform.CISCO_SMB,
    commit_model=CommitModel.IMMEDIATE,
    rollback_timer=RollbackTimer.NONE,
    commands=COMMANDS,
    parsers=parsers,
    l2=builders_l2,
    acl=builders_acl,
    routing=builders_routing,
    nac=builders_nac,
    config=config_text,
    classifier=SmbClassifier(),
    lockout=SmbLockoutGuard(),
    drivers={"ssh": _ssh, "telnet": _telnet, "snmp": _snmp},
    routing_probes=PROBES,
    notes=(
        "Generated for CBS350 3.x / SG350 2.5 per the CLI guides; verify on a lab device first.",
        "No rollback timer and no RADIUS CoA on Cisco SMB; SG300 (1.x) is not supported.",
    ),
)
