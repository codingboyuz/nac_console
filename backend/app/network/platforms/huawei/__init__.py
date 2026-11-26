"""Huawei VRP adapters (S / AR on VRP V5, CloudEngine on VRP V8) over the existing modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain import config_text
from app.domain.commands.classifier import CommandClassifier
from app.domain.commands.lockout import LockoutGuard
from app.domain.platforms import CommitModel, Platform, RollbackTimer
from app.network import parsers as _parsers
from app.network.builders import cli_acl, cli_l2, cli_nac, cli_routing
from app.network.parsers import acl as _acl
from app.network.parsers import interface_config as _iface_cfg
from app.network.parsers import routing_protocols as _rp
from app.network.platforms.base import PlatformAdapter, Q

if TYPE_CHECKING:
    from app.network.ports import ConnectionParams, DeviceDriver


class _HuaweiParsers:
    """The VRP parsers under the adapter's parser names (same functions, one namespace)."""

    parse_version = staticmethod(_parsers.parse_version)
    parse_vlans = staticmethod(_parsers.parse_vlans)
    parse_interface_brief = staticmethod(_parsers.parse_interface_brief)
    parse_interface_detail = staticmethod(_parsers.parse_interface_detail)
    parse_interface_config = staticmethod(_iface_cfg.parse_interface_config)
    parse_mac_table = staticmethod(_parsers.parse_mac_table)
    parse_mac_aging = staticmethod(_parsers.parse_mac_aging)
    parse_arp = staticmethod(_parsers.parse_arp)
    parse_dhcp_snooping_bindings = staticmethod(_parsers.parse_dhcp_snooping_bindings)
    parse_routing_table = staticmethod(_parsers.parse_routing_table)
    parse_static_route_lines = staticmethod(_rp.parse_static_route_lines)
    parse_ospf_peers = staticmethod(_rp.parse_ospf_peers)
    parse_bgp_peers = staticmethod(_rp.parse_bgp_peers)
    parse_stp_global = staticmethod(_parsers.parse_stp_global)
    parse_stp_brief = staticmethod(_parsers.parse_stp_brief)
    parse_cpu_usage = staticmethod(_parsers.parse_cpu_usage)
    parse_memory_usage = staticmethod(_parsers.parse_memory_usage)
    parse_acls = staticmethod(_acl.parse_acls)
    management_acls = staticmethod(_acl.management_acls)


COMMANDS: dict[Q, str] = {
    Q.VERSION: "display version",
    Q.RUNNING_CONFIG: "display current-configuration",
    Q.SAVED_CONFIG: "display saved-configuration",
    Q.VLANS: "display vlan",
    Q.INTERFACES_BRIEF: "display interface brief",
    Q.INTERFACES: "display interface",
    Q.INTERFACE: "display interface {name}",
    Q.INTERFACE_CONFIG: "display current-configuration interface {name}",
    Q.MAC_TABLE: "display mac-address",
    Q.MAC_AGING: "display mac-address aging-time",
    Q.ARP: "display arp",
    Q.DHCP_BINDINGS: "display dhcp snooping user-bind all",
    Q.ROUTES: "display ip routing-table",
    Q.STATIC_ROUTE_CONFIG: "display current-configuration | include ip route-static",
    Q.OSPF_NEIGHBORS: "display ospf peer brief",
    Q.BGP_PEERS: "display bgp peer",
    Q.STP_GLOBAL: "display stp",
    Q.STP_BRIEF: "display stp brief",
    Q.CPU: "display cpu-usage",
    Q.MEMORY: "display memory-usage",
    Q.ACLS: "display acl all",
    Q.ACL: "display acl {identifier}",
}

PROBES: dict[str, str] = {
    "static": "display ip routing-table protocol static",
    "ospf": "display ospf brief",
    "bgp": "display bgp peer",
    "rip": "display rip",
    "vrrp": "display vrrp brief",
    "route_policy": "display route-policy",
}


def _ssh(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_ssh import HuaweiCliDriver

    return HuaweiCliDriver(params)


def _telnet(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_telnet import HuaweiTelnetDriver

    return HuaweiTelnetDriver(params)


def _netconf(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_netconf import HuaweiNetconfDriver

    return HuaweiNetconfDriver(params)


def _snmp(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_snmp import HuaweiSnmpDriver

    return HuaweiSnmpDriver(params)


DRIVERS = {"ssh": _ssh, "telnet": _telnet, "netconf": _netconf, "snmp": _snmp}
_NOTES = ("Generated commands are VRP-VERSION-DEPENDENT; verify on a lab device first.",)


def _adapter(platform: Platform, commit: CommitModel, timer: RollbackTimer) -> PlatformAdapter:
    return PlatformAdapter(
        platform=platform,
        commit_model=commit,
        rollback_timer=timer,
        commands=COMMANDS,
        parsers=_HuaweiParsers(),
        l2=cli_l2,
        acl=cli_acl,
        routing=cli_routing,
        nac=cli_nac,
        config=config_text,
        classifier=CommandClassifier(),
        lockout=LockoutGuard(),
        drivers=DRIVERS,
        routing_probes=PROBES,
        notes=_NOTES,
    )


HUAWEI_VRP = _adapter(Platform.HUAWEI_VRP, CommitModel.IMMEDIATE, RollbackTimer.SCHEDULED_RELOAD)
HUAWEI_VRPV8 = _adapter(Platform.HUAWEI_VRPV8, CommitModel.TWO_STAGE, RollbackTimer.COMMIT_TRIAL)
