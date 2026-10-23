"""Parse ``display current-configuration interface <name>`` into the port's L2 settings.

Used for desired-state comparison so builders only emit the diff. VRP-VERSION-DEPENDENT:
CE prints ``port link-type`` only when it differs from the default (access on CE, hybrid on
some S models), so a missing line means "platform default" (None).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.vlans import parse_vlan_list
from app.network.parsers.common import clean_lines

_TRUNK_ALLOW = re.compile(r"^port trunk allow-pass vlan (.+)$")
_HYBRID = re.compile(r"^port hybrid (tagged|untagged) vlan (.+)$")


@dataclass(slots=True)
class InterfaceConfig:
    name: str | None = None
    link_type: str | None = None
    default_vlan: int | None = None
    trunk_pvid: int | None = None
    hybrid_pvid: int | None = None
    trunk_allowed: set[int] = field(default_factory=set)
    hybrid_tagged: set[int] = field(default_factory=set)
    hybrid_untagged: set[int] = field(default_factory=set)
    description: str | None = None
    shutdown: bool = False
    stp_edged: bool = False
    dhcp_trusted: bool = False
    lines: list[str] = field(default_factory=list)


def parse_interface_config(text: str | None) -> InterfaceConfig:  # noqa: PLR0912 - flat dispatch
    cfg = InterfaceConfig()
    for raw in clean_lines(text):
        line = " ".join(raw.split())
        if line.startswith("#") or not line:
            continue
        if line.startswith("interface "):
            cfg.name = line.split(" ", 1)[1]
            continue
        cfg.lines.append(line)
        if line.startswith("port link-type "):
            cfg.link_type = line.rsplit(" ", 1)[1]
        elif line.startswith("port default vlan "):
            cfg.default_vlan = int(line.rsplit(" ", 1)[1])
        elif line.startswith("port trunk pvid vlan "):
            cfg.trunk_pvid = int(line.rsplit(" ", 1)[1])
        elif line.startswith("port hybrid pvid vlan "):
            cfg.hybrid_pvid = int(line.rsplit(" ", 1)[1])
        elif match := _TRUNK_ALLOW.match(line):
            cfg.trunk_allowed |= set(parse_vlan_list(match.group(1), strict=False))
        elif match := _HYBRID.match(line):
            target = cfg.hybrid_tagged if match.group(1) == "tagged" else cfg.hybrid_untagged
            target |= set(parse_vlan_list(match.group(2), strict=False))
        elif line.startswith("description "):
            cfg.description = line.split(" ", 1)[1]
        elif line == "shutdown":
            cfg.shutdown = True
        elif line in {"stp edged-port enable", "stp edged-port"}:
            cfg.stp_edged = True
        elif line == "dhcp snooping trusted":
            cfg.dhcp_trusted = True
    return cfg
