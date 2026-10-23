from __future__ import annotations

import pytest

from app.network.parsers.mac import MacEntry, parse_mac_aging, parse_mac_table
from tests.unit.parsers._helpers import CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (
            S5735,
            [
                MacEntry("00:e0:fc:12:34:56", 10, "GigabitEthernet0/0/1", "dynamic", None),
                MacEntry("54:89:98:11:22:01", 10, "GigabitEthernet0/0/4", "dynamic", None),
                MacEntry("54:89:98:11:22:02", 20, "GigabitEthernet0/0/9", "dynamic", None),
                MacEntry("00:e0:fc:12:34:60", 100, "Eth-Trunk1", "dynamic", None),
                MacEntry("54:89:98:11:22:03", 10, "GigabitEthernet0/0/4", "static", None),
                MacEntry("54:89:98:11:de:ad", 20, None, "blackhole", None),
                MacEntry("54:89:98:11:22:04", 10, "GigabitEthernet0/0/1", "sticky", None),
            ],
        ),
        (
            CE6881,
            [
                MacEntry("54:89:98:12:34:21", 10, "10GE1/0/1", "dynamic", None),
                MacEntry("54:89:98:12:34:22", 20, "10GE1/0/2", "dynamic", None),
                MacEntry("00:e0:fc:12:34:56", 10, "Eth-Trunk1", "dynamic", None),
                MacEntry("54:89:98:12:34:25", 30, "10GE1/0/8", "static", None),
                MacEntry("54:89:98:12:34:26", 30, None, "blackhole", None),
                MacEntry("54:89:98:12:34:27", None, "Nve1", "dynamic", None),
            ],
        ),
    ],
)
def test_parse_mac_table_golden(generation: str, expected: list[MacEntry]) -> None:
    assert parse_mac_table(load(generation, "display_mac_address.txt")) == expected


def test_parse_mac_table_vsi_and_bare_vlan() -> None:
    text = (
        "00e0-fc12-3456 -/vsi-blue/-  Tunnel0/0/1  dynamic\n"
        "00e0-fc12-3457 30            GE0/0/2      security\n"
        "00e0-fc12-3458 vsi-red       -            static\n"
    )
    assert parse_mac_table(text) == [
        MacEntry("00:e0:fc:12:34:56", None, "Tunnel0/0/1", "dynamic", "vsi-blue"),
        MacEntry("00:e0:fc:12:34:57", 30, "GigabitEthernet0/0/2", "security", None),
        MacEntry("00:e0:fc:12:34:58", None, None, "static", "vsi-red"),
    ]


@pytest.mark.parametrize(("generation", "seconds"), [(S5735, 300), (CE6881, 600)])
def test_parse_mac_aging(generation: str, seconds: int) -> None:
    assert parse_mac_aging(load(generation, "display_mac_address_aging_time.txt")) == seconds


def test_parse_mac_aging_missing() -> None:
    assert parse_mac_aging("") is None
    assert parse_mac_aging(load("errors", "unrecognized_command.txt")) is None
