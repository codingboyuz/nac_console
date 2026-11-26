"""TP-Link CLI interface spelling: 'gigabitEthernet 1/0/1', 'ten-gigabitEthernet 1/0/25'."""

from __future__ import annotations

from app.domain.interfaces import parse_interface

_CLI = {
    "FastEthernet": "fastEthernet",
    "GigabitEthernet": "gigabitEthernet",
    "TwoGigabitEthernet": "two-gigabitEthernet",
    "TenGigabitEthernet": "ten-gigabitEthernet",
    "TwentyFiveGigE": "twentyFive-gigabitEthernet",
    "HundredGigE": "hundred-gigabitEthernet",
    "Port-channel": "port-channel",
    "Vlan": "vlan",
    "LoopBack": "loopback",
}


def tp_iface(name: str) -> str:
    """Canonical (or any accepted spelling) -> TP-Link CLI form with a space before the number."""
    parsed = parse_interface(name)
    keyword = _CLI.get(parsed.kind)
    if keyword is None:
        raise ValueError(f"{parsed.kind} interfaces are not addressable on TP-Link switches")
    return f"{keyword} {parsed.number}"
