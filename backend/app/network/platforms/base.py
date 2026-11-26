"""PlatformAdapter: everything vendor-specific behind one object per platform.

Services never hard-code CLI text. They ask the adapter for a logical query's command and the
matching parser (all parsers return the shared value objects in ``app.network.parsers``), and
for typed builders that return ``ConfigPlan`` objects in the platform's CLI dialect.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from app.core.exceptions import UnsupportedCapability
from app.domain.platforms import CommitModel, Platform, RollbackTimer, Vendor

if TYPE_CHECKING:
    from app.domain.commands.classifier import CommandClassifier
    from app.domain.commands.lockout import LockoutGuard
    from app.network.platforms.contracts import (
        AclBuilders,
        ConfigText,
        L2Builders,
        NacBuilders,
        Parsers,
        RoutingBuilders,
    )
    from app.network.ports import ConnectionParams, DeviceDriver


class Q(StrEnum):
    """Logical read queries. Commands may take ``name`` / ``identifier`` format arguments."""

    VERSION = "version"
    RUNNING_CONFIG = "running_config"
    SAVED_CONFIG = "saved_config"
    VLANS = "vlans"
    INTERFACES_BRIEF = "interfaces_brief"
    INTERFACES = "interfaces"
    INTERFACE = "interface"  # {name}
    INTERFACE_CONFIG = "interface_config"  # {name}
    MAC_TABLE = "mac_table"
    MAC_AGING = "mac_aging"
    ARP = "arp"
    DHCP_BINDINGS = "dhcp_bindings"
    ROUTES = "routes"
    STATIC_ROUTE_CONFIG = "static_route_config"
    OSPF_NEIGHBORS = "ospf_neighbors"
    BGP_PEERS = "bgp_peers"
    STP_GLOBAL = "stp_global"
    STP_BRIEF = "stp_brief"
    CPU = "cpu"
    MEMORY = "memory"
    ACLS = "acls"
    ACL = "acl"  # {identifier}


@dataclass(frozen=True, slots=True)
class PlatformAdapter:
    """Immutable bundle of a platform's dialect. One shared instance per platform."""

    platform: Platform
    commit_model: CommitModel
    rollback_timer: RollbackTimer
    commands: Mapping[Q, str]
    parsers: Parsers
    # Builder namespaces (modules or objects implementing the contracts).
    l2: L2Builders
    acl: AclBuilders
    routing: RoutingBuilders
    nac: NacBuilders
    config: ConfigText
    classifier: CommandClassifier
    lockout: LockoutGuard
    # Driver constructors per transport ("ssh", "telnet", "netconf", "snmp", "api").
    drivers: Mapping[str, Callable[[ConnectionParams], DeviceDriver]]
    # Probe commands for routing feature detection ("Unrecognized" == unsupported).
    routing_probes: Mapping[str, str] = field(default_factory=dict)
    # Caveats shown with generated configuration (e.g. version-dependent syntax).
    notes: tuple[str, ...] = ()
    # Canonical interface name -> CLI spelling (TP-Link: 'gigabitEthernet 1/0/1').
    interface_cli: Callable[[str], str] | None = None

    @property
    def vendor(self) -> Vendor:
        return self.platform.vendor

    @property
    def uses_commit(self) -> bool:
        return self.commit_model is CommitModel.TWO_STAGE

    @property
    def supports_rollback_timer(self) -> bool:
        return self.rollback_timer is not RollbackTimer.NONE

    def command(self, query: Q, **kwargs: str) -> str:
        template = self.commands.get(query)
        if template is None:
            raise UnsupportedCapability(
                f"{self.platform.label} does not support '{query.value}' over the CLI"
            )
        if "name" in kwargs and self.interface_cli is not None:
            kwargs = {**kwargs, "name": self.interface_cli(kwargs["name"])}
        return template.format(**kwargs) if kwargs else template

    def supports(self, query: Q) -> bool:
        return query in self.commands
