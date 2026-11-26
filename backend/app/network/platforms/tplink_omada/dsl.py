"""Controller-neutral change language for Omada-managed switches.

Plans, audit records and the classifier see these readable lines; only the Omada driver turns
them into Open API calls. That keeps every (partly unverified) REST path in one module.

    vlan create <vid> name <text>      vlan rename <vid> name <text>      vlan delete <vid>
    port <n> enable|disable            port <n> name <text>|none
    port <n> native <vid>              port <n> tagged <vlan-ranges>|none
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.interfaces import parse_interface
from app.domain.vlans import parse_vlan_list

VLAN = re.compile(r"^vlan (create|rename) (\d{1,4}) name (.+)$|^vlan (delete) (\d{1,4})$")
PORT = re.compile(r"^port (\d{1,3}) (enable|disable|name|native|tagged)(?: (.+))?$")


@dataclass(frozen=True, slots=True)
class VlanOp:
    action: str  # create | rename | delete
    vlan: int
    name: str | None = None


@dataclass(frozen=True, slots=True)
class PortOp:
    port: int
    field: str  # enable | disable | name | native | tagged
    value: str | None = None

    def tagged(self) -> list[int]:
        if not self.value or self.value == "none":
            return []
        return parse_vlan_list(self.value, strict=True)


def parse_line(line: str) -> VlanOp | PortOp:
    text = " ".join(line.split())
    if match := VLAN.match(text):
        if match.group(4):
            return VlanOp("delete", int(match.group(5)))
        return VlanOp(match.group(1), int(match.group(2)), match.group(3))
    if (match := PORT.match(text)) and (match.group(3) is not None) == (
        match.group(2) in {"name", "native", "tagged"}
    ):
        return PortOp(int(match.group(1)), match.group(2), match.group(3))
    raise ValueError(f"not an Omada change line: {line!r}")


def port_number(interface: str) -> int:
    """'GigabitEthernet1/0/5' (or any accepted spelling, or a bare number) -> 5."""
    if interface.strip().isdigit():
        return int(interface)
    return int(parse_interface(interface).number.rsplit("/", 1)[-1])


def port_name(port: int) -> str:
    """Omada numbers ports per switch; they are shown as unit-1 gigabit ports."""
    return f"GigabitEthernet1/0/{port}"
