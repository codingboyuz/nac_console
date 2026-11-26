"""TP-Link JetStream / Omada managed switches over CLI (T1600G, T2600G, SG3xxx)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.platforms import CommitModel, Platform, RollbackTimer
from app.network.platforms.base import PlatformAdapter, Q
from app.network.platforms.tplink_jetstream import (
    builders_acl,
    builders_l2,
    builders_nac,
    builders_routing,
    config_text,
    parsers,
)
from app.network.platforms.tplink_jetstream.naming import tp_iface
from app.network.platforms.tplink_jetstream.rules import TplinkClassifier, TplinkLockoutGuard

if TYPE_CHECKING:
    from app.network.ports import ConnectionParams, DeviceDriver

COMMANDS: dict[Q, str] = {
    Q.VERSION: "show system-info",
    Q.RUNNING_CONFIG: "show running-config",
    Q.SAVED_CONFIG: "show startup-config",
    Q.VLANS: "show vlan",
    Q.INTERFACES_BRIEF: "show interface status",
    Q.INTERFACES: "show interface status",
    Q.INTERFACE: "show interface counters {name}",
    Q.INTERFACE_CONFIG: "show running-config interface {name}",
    Q.MAC_TABLE: "show mac address-table",
    Q.MAC_AGING: "show mac address-table aging-time",
    Q.ARP: "show arp",
    Q.DHCP_BINDINGS: "show ip source binding",
    Q.ROUTES: "show ip route",
    Q.STATIC_ROUTE_CONFIG: "show running-config | include ip route",
    Q.OSPF_NEIGHBORS: "show ip ospf neighbor",
    Q.STP_GLOBAL: "show spanning-tree bridge",
    Q.STP_BRIEF: "show spanning-tree interface",
    Q.CPU: "show cpu-utilization",
    Q.MEMORY: "show memory-utilization",
    Q.ACLS: "show running-config | include access-list",
    Q.ACL: "show running-config | include access-list",
}

PROBES: dict[str, str] = {"static": "show ip route", "ospf": "show ip ospf neighbor"}


def _ssh(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.tplink_jetstream import TplinkJetstreamDriver

    return TplinkJetstreamDriver(params)


def _telnet(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.tplink_jetstream import TplinkJetstreamTelnetDriver

    return TplinkJetstreamTelnetDriver(params)


def _snmp(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_snmp import TplinkSnmpDriver

    return TplinkSnmpDriver(params)


TPLINK_JETSTREAM = PlatformAdapter(
    platform=Platform.TPLINK_JETSTREAM,
    commit_model=CommitModel.IMMEDIATE,
    rollback_timer=RollbackTimer.SCHEDULED_RELOAD,
    commands=COMMANDS,
    parsers=parsers,
    l2=builders_l2,
    acl=builders_acl,
    routing=builders_routing,
    nac=builders_nac,
    config=config_text,
    classifier=TplinkClassifier(),
    lockout=TplinkLockoutGuard(),
    drivers={"ssh": _ssh, "telnet": _telnet, "snmp": _snmp},
    routing_probes=PROBES,
    notes=(
        "Generated from TP-Link CLI reference guides (2019/2022/2025); verify on a lab device.",
        "Parsers are built on synthetic fixtures: capture real output before relying on them.",
        "Rollback timer = 'reboot-schedule in N' (reboots into the saved configuration).",
    ),
    interface_cli=tp_iface,
)
