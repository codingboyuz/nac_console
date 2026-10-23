"""Parser for ``display dhcp snooping user-bind all``."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.network.parsers.common import (
    IPV4_PATTERN,
    MAC_PATTERN,
    clean_lines,
    none_if_dash,
    safe_interface,
    safe_mac,
    to_int,
)

_ROW_RE = re.compile(
    rf"^\s*(?P<ip>{IPV4_PATTERN})\s+(?P<mac>{MAC_PATTERN})\s+"
    r"(?P<outer>[^\s/]+)\s*/\s*(?P<inner>[^\s/]+)\s*/\s*(?P<pevlan>[^\s/]+)"
    r"(?:\s+(?P<intf>\S+))?(?:\s+(?P<lease>\S+))?\s*$"
)


@dataclass(frozen=True, slots=True)
class DhcpBindingEntry:
    """One DHCP snooping binding."""

    ip: str
    mac: str
    vlan: int | None
    interface: str | None
    lease_expire: str | None
    vsi: str | None


def parse_dhcp_snooping_bindings(text: str | None) -> list[DhcpBindingEntry]:
    """Parse binding rows; the ``print count``/``total count`` trailer is ignored."""
    entries: list[DhcpBindingEntry] = []
    for line in clean_lines(text):
        match = _ROW_RE.match(line)
        if not match:
            continue
        mac = safe_mac(match.group("mac"))
        if mac is None:
            continue
        outer = match.group("outer")
        vlan = to_int(outer)
        vsi = None if vlan is not None else none_if_dash(outer)
        entries.append(
            DhcpBindingEntry(
                ip=match.group("ip"),
                mac=mac,
                vlan=vlan,
                interface=safe_interface(match.group("intf")),
                lease_expire=none_if_dash(match.group("lease")),
                vsi=vsi,
            )
        )
    return entries
