from __future__ import annotations

import pytest

from app.network.parsers.arp import ArpEntry, normalize_arp_type, parse_arp
from tests.unit.parsers._helpers import CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (
            S5735,
            [
                ArpEntry(
                    "10.10.10.1", "00:e0:fc:12:34:00", None, "interface", None, "Vlanif10", None
                ),
                ArpEntry(
                    "10.10.10.25",
                    "54:89:98:11:22:01",
                    18,
                    "dynamic",
                    10,
                    "GigabitEthernet0/0/4",
                    None,
                ),
                ArpEntry(
                    "10.10.10.26",
                    "00:e0:fc:12:34:56",
                    20,
                    "dynamic",
                    10,
                    "GigabitEthernet0/0/1",
                    None,
                ),
                ArpEntry("192.0.2.254", "00:e0:fc:00:00:01", 9, "dynamic", 100, "Eth-Trunk1", None),
                ArpEntry(
                    "192.0.2.2", "00:e0:fc:12:34:01", None, "interface", None, "Vlanif100", None
                ),
                ArpEntry(
                    "10.10.10.99",
                    "54:89:98:11:22:99",
                    None,
                    "static",
                    10,
                    "GigabitEthernet0/0/4",
                    None,
                ),
            ],
        ),
        (
            CE6881,
            [
                ArpEntry(
                    "10.10.10.1", "54:89:98:12:34:00", None, "interface", None, "Vlanif10", None
                ),
                ArpEntry("10.10.10.21", "54:89:98:12:34:21", 17, "dynamic", 10, "10GE1/0/1", None),
                ArpEntry("10.20.20.22", "54:89:98:12:34:22", 19, "dynamic", 20, "10GE1/0/2", None),
                ArpEntry("10.10.10.30", "00:e0:fc:12:34:56", 5, "dynamic", 10, "Eth-Trunk1", None),
                ArpEntry("10.30.30.5", "54:89:98:12:34:25", None, "static", 30, "10GE1/0/8", None),
                ArpEntry(
                    "192.0.2.1", "54:89:98:12:34:ff", 11, "dynamic", None, "MEth0/0/0", "MGMT"
                ),
            ],
        ),
    ],
)
def test_parse_arp_golden(generation: str, expected: list[ArpEntry]) -> None:
    assert parse_arp(load(generation, "display_arp.txt")) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("I -", "interface"),
        ("I", "interface"),
        ("D-0", "dynamic"),
        ("D", "dynamic"),
        ("S--", "static"),
        ("S", "static"),
        ("Remote", "remote"),
        ("O", "o"),
    ],
)
def test_normalize_arp_type(raw: str, expected: str) -> None:
    assert normalize_arp_type(raw) == expected


def test_parse_arp_skips_incomplete_and_orphan_continuations() -> None:
    text = (
        "                                          10/-\n"
        "10.1.1.5        Incomplete      1         D-0         GE0/0/1\n"
        "                                          10/-\n"
        "Total:1   Dynamic:1   Static:0   Interface:0\n"
    )
    assert parse_arp(text) == []
