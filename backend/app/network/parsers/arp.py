"""Parser for ``display arp`` (S-series two-line rows and CE ``TYPE/VLAN/CEVLAN`` rows)."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

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
    rf"^\s*(?P<ip>{IPV4_PATTERN})\s+(?P<mac>{MAC_PATTERN}|Incomplete)\s+"
    r"(?:(?P<exp>\d+)\s+)?(?P<type>I\s+-(?=\s)|[A-Za-z][\w/\-]*)"
    r"(?:\s+(?P<intf>\S+))?(?:\s+(?P<vpn>\S+))?\s*$",
    re.IGNORECASE,
)
_VLAN_CONT_RE = re.compile(r"^\s{10,}(?P<vlan>\d{1,4}|-)(?:/\S*)?\s*$")
_TYPES = {"I": "interface", "D": "dynamic", "S": "static"}


@dataclass(frozen=True, slots=True)
class ArpEntry:
    """One ARP table entry."""

    ip: str
    mac: str
    expire_minutes: int | None
    entry_type: str
    vlan: int | None
    interface: str | None
    vpn: str | None


def normalize_arp_type(raw: str) -> str:
    """``I``/``I -`` -> interface, ``D-0``/``D`` -> dynamic, ``S-``/``S`` -> static."""
    value = raw.strip()
    head = value[:1].upper()
    if head in _TYPES and (len(value) == 1 or not value[1].isalpha()):
        return _TYPES[head]
    return value.lower()


def parse_arp(text: str | None) -> list[ArpEntry]:
    """Parse ``display arp``; VLAN continuation lines are attached to the previous entry."""
    entries: list[ArpEntry] = []
    last_was_entry = False
    for line in clean_lines(text):
        match = _ROW_RE.match(line)
        mac = safe_mac(match.group("mac")) if match else None
        if match and mac is not None:
            type_cell = match.group("type").split("/")
            vlan = to_int(type_cell[1]) if len(type_cell) > 1 else None
            entries.append(
                ArpEntry(
                    ip=match.group("ip"),
                    mac=mac,
                    expire_minutes=to_int(match.group("exp")),
                    entry_type=normalize_arp_type(type_cell[0]),
                    vlan=vlan,
                    interface=safe_interface(match.group("intf")),
                    vpn=none_if_dash(match.group("vpn")),
                )
            )
            last_was_entry = True
            continue
        cont = _VLAN_CONT_RE.match(line)
        if cont and last_was_entry and entries[-1].vlan is None:
            entries[-1] = replace(entries[-1], vlan=to_int(cont.group("vlan")))
        last_was_entry = False
    return entries
