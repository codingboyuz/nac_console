from __future__ import annotations

import pytest

from app.network.parsers.dhcp import DhcpBindingEntry, parse_dhcp_snooping_bindings
from tests.unit.parsers._helpers import CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (
            S5735,
            [
                DhcpBindingEntry(
                    "10.10.10.50",
                    "00:e0:fc:12:34:01",
                    10,
                    "GigabitEthernet0/0/3",
                    "2026.10.24-09:15",
                    None,
                ),
                DhcpBindingEntry(
                    "10.10.10.51",
                    "54:89:98:11:22:10",
                    10,
                    "GigabitEthernet0/0/4",
                    "2026.10.24-11:42",
                    None,
                ),
            ],
        ),
        (
            CE6881,
            [
                DhcpBindingEntry(
                    "10.10.10.61", "54:89:98:12:34:61", 10, "10GE1/0/1", "2026.10.24-08:00", None
                ),
                DhcpBindingEntry(
                    "10.20.20.62", "54:89:98:12:34:62", 20, "10GE1/0/2", "2026.10.25-02:30", None
                ),
                DhcpBindingEntry(
                    "10.40.0.9", "54:89:98:12:34:90", None, "Eth-Trunk1", "2026.10.24-12:00", "vsi1"
                ),
            ],
        ),
    ],
)
def test_parse_dhcp_bindings_golden(generation: str, expected: list[DhcpBindingEntry]) -> None:
    text = load(generation, "display_dhcp_snooping_user_bind_all.txt")
    assert parse_dhcp_snooping_bindings(text) == expected


def test_parse_dhcp_bindings_without_lease() -> None:
    text = "10.1.1.2   00e0-fc12-3456  --  /--  /--    GE0/0/1\n"
    assert parse_dhcp_snooping_bindings(text) == [
        DhcpBindingEntry("10.1.1.2", "00:e0:fc:12:34:56", None, "GigabitEthernet0/0/1", None, None)
    ]
