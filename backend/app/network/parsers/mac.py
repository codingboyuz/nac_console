"""Parsers for ``display mac-address`` and ``display mac-address aging-time``."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.network.parsers.common import (
    MAC_PATTERN,
    clean_lines,
    clean_output,
    none_if_dash,
    safe_interface,
    safe_mac,
    to_int,
)

_ROW_RE = re.compile(
    rf"^\s*(?P<mac>{MAC_PATTERN})\s+(?P<vlan>\S+)\s+(?P<port>\S+)\s+(?P<type>[A-Za-z][\w-]*)"
    r"(?:\s+(?P<age>\S+))?\s*$"
)
_AGING_RE = re.compile(r"Aging[\s-]*time\s*(?:is)?\s*:?\s*(?P<sec>\d+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MacEntry:
    """One MAC address table entry."""

    mac: str
    vlan: int | None
    interface: str | None
    entry_type: str
    vsi: str | None


def parse_mac_table(text: str | None) -> list[MacEntry]:
    """Parse ``display mac-address`` rows from S-series and CE outputs."""
    entries: list[MacEntry] = []
    for line in clean_lines(text):
        match = _ROW_RE.match(line)
        if not match:
            continue
        mac = safe_mac(match.group("mac"))
        if mac is None:
            continue
        vlan_cell = match.group("vlan").split("/")
        vlan = to_int(vlan_cell[0])
        vsi = none_if_dash(vlan_cell[1]) if len(vlan_cell) > 1 else None
        if vlan is None and vsi is None:
            vsi = none_if_dash(vlan_cell[0])
        entries.append(
            MacEntry(
                mac=mac,
                vlan=vlan,
                interface=safe_interface(match.group("port")),
                entry_type=match.group("type").lower(),
                vsi=vsi,
            )
        )
    return entries


def parse_mac_aging(text: str | None) -> int | None:
    """Return the MAC aging time in seconds from ``display mac-address aging-time``."""
    match = _AGING_RE.search(clean_output(text))
    return int(match.group("sec")) if match else None
