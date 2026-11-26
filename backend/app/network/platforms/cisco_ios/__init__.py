"""Cisco IOS / IOS-XE (Catalyst) platform adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.platforms import CommitModel, Platform, RollbackTimer
from app.network.platforms.base import PlatformAdapter, Q
from app.network.platforms.cisco_ios import (
    builders_acl,
    builders_l2,
    builders_nac,
    builders_routing,
    config_text,
    parsers,
)
from app.network.platforms.cisco_ios.lockout import CiscoLockoutGuard
from app.network.platforms.cisco_ios.rules import CiscoIosClassifier

if TYPE_CHECKING:
    from app.network.ports import ConnectionParams, DeviceDriver

COMMANDS: dict[Q, str] = {
    Q.VERSION: "show version",
    Q.RUNNING_CONFIG: "show running-config",
    Q.SAVED_CONFIG: "show startup-config",
    Q.VLANS: "show vlan brief",
    Q.INTERFACES_BRIEF: "show interfaces status",
    Q.INTERFACES: "show interfaces",
    Q.INTERFACE: "show interfaces {name}",
    Q.INTERFACE_CONFIG: "show running-config interface {name}",
    Q.MAC_TABLE: "show mac address-table",
    Q.MAC_AGING: "show mac address-table aging-time",
    Q.ARP: "show ip arp",
    Q.DHCP_BINDINGS: "show ip dhcp snooping binding",
    Q.ROUTES: "show ip route",
    Q.STATIC_ROUTE_CONFIG: "show running-config | include ^ip route",
    Q.OSPF_NEIGHBORS: "show ip ospf neighbor",
    Q.BGP_PEERS: "show ip bgp summary",
    Q.STP_GLOBAL: "show spanning-tree",
    Q.STP_BRIEF: "show spanning-tree",
    Q.CPU: "show processes cpu | include CPU utilization",
    Q.MEMORY: "show processes memory | include Processor Pool",
    Q.ACLS: "show ip access-lists",
    Q.ACL: "show ip access-lists {identifier}",
}

PROBES: dict[str, str] = {
    "static": "show ip route static",
    "ospf": "show ip ospf",
    "bgp": "show ip bgp summary",
    "rip": "show ip rip database",
    "vrrp": "show vrrp brief",
    "route_policy": "show route-map",
}


def _ssh(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.cisco_ios import CiscoIosDriver

    return CiscoIosDriver(params)


def _telnet(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.cisco_ios import CiscoIosTelnetDriver

    return CiscoIosTelnetDriver(params)


def _snmp(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_snmp import CiscoSnmpDriver

    return CiscoSnmpDriver(params)


CISCO_IOS = PlatformAdapter(
    platform=Platform.CISCO_IOS,
    commit_model=CommitModel.IMMEDIATE,
    rollback_timer=RollbackTimer.COMMIT_TRIAL,
    commands=COMMANDS,
    parsers=parsers,
    l2=builders_l2,
    acl=builders_acl,
    routing=builders_routing,
    nac=builders_nac,
    config=config_text,
    classifier=CiscoIosClassifier(),
    lockout=CiscoLockoutGuard(),
    drivers={"ssh": _ssh, "telnet": _telnet, "snmp": _snmp},
    routing_probes=PROBES,
    notes=(
        "Generated for IOS 15.2 / IOS-XE 16-17; verify on a lab device first.",
        "Rollback timers need the configuration archive ('archive' + 'path') on the switch.",
    ),
)
