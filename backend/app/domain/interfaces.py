"""Huawei interface name normalization - the single place that knows naming variants.

S-series (VRP V200R): GigabitEthernet0/0/1, XGigabitEthernet0/0/1, Ethernet0/0/1, MEth0/0/1.
CE-series (VRP V8):  10GE1/0/1, 25GE1/0/1, 40GE1/0/1, 100GE1/0/1, GE1/0/1, MEth0/0/0.
Logical:             Eth-Trunk1, Vlanif100, LoopBack0, NULL0, Tunnel0/0/1, Nve1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Canonical name keyed by lower-case accepted spellings (abbreviations as shown in
# 'display interface brief' and as accepted by the VRP CLI).
_ALIASES: dict[str, str] = {
    "gigabitethernet": "GigabitEthernet",
    "ge": "GigabitEthernet",
    "gi": "GigabitEthernet",
    "xgigabitethernet": "XGigabitEthernet",
    "xge": "XGigabitEthernet",
    "10ge": "10GE",
    "25ge": "25GE",
    "40ge": "40GE",
    "50ge": "50GE",
    "100ge": "100GE",
    "200ge": "200GE",
    "400ge": "400GE",
    "ethernet": "Ethernet",
    "eth": "Ethernet",
    "meth": "MEth",
    "eth-trunk": "Eth-Trunk",
    "ethtrunk": "Eth-Trunk",
    "vlanif": "Vlanif",
    "loopback": "LoopBack",
    "null": "NULL",
    "tunnel": "Tunnel",
    "nve": "Nve",
}

_PHYSICAL = {
    "GigabitEthernet",
    "XGigabitEthernet",
    "10GE",
    "25GE",
    "40GE",
    "50GE",
    "100GE",
    "200GE",
    "400GE",
    "Ethernet",
}
_NAME_RE = re.compile(
    r"^\s*(?P<type>[A-Za-z][A-Za-z-]*|\d{2,3}[Gg][Ee])\s*(?P<num>\d+(?:/\d+){0,3}(?:\.\d+)?)\s*$"
)


class InvalidInterfaceName(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InterfaceName:
    kind: str
    number: str

    @property
    def canonical(self) -> str:
        return f"{self.kind}{self.number}"

    @property
    def is_physical(self) -> bool:
        return self.kind in _PHYSICAL

    @property
    def is_management(self) -> bool:
        return self.kind == "MEth"

    @property
    def is_lag(self) -> bool:
        return self.kind == "Eth-Trunk"

    @property
    def vlanif_id(self) -> int | None:
        return int(self.number) if self.kind == "Vlanif" else None


def parse_interface(name: str) -> InterfaceName:
    """Parse any accepted spelling into a canonical InterfaceName."""
    match = _NAME_RE.match(name or "")
    if not match:
        raise InvalidInterfaceName(f"invalid interface name: {name!r}")
    kind = _ALIASES.get(match.group("type").lower())
    if kind is None:
        raise InvalidInterfaceName(f"unknown interface type: {match.group('type')!r}")
    number = match.group("num")
    if kind == "Vlanif" and not (number.isdigit() and 1 <= int(number) <= 4094):
        raise InvalidInterfaceName("Vlanif number must be 1-4094")
    if kind in _PHYSICAL | {"MEth", "Tunnel"} and "/" not in number:
        raise InvalidInterfaceName(f"{kind} requires slot/port numbering")
    return InterfaceName(kind, number)


def normalize_interface(name: str) -> str:
    return parse_interface(name).canonical


def same_interface(a: str, b: str) -> bool:
    try:
        return normalize_interface(a) == normalize_interface(b)
    except InvalidInterfaceName:
        return False
