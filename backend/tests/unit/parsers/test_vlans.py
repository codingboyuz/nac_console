from __future__ import annotations

import pytest

from app.network.parsers.vlans import VlanInfo, parse_vlans
from tests.unit.parsers._helpers import CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (
            S5735,
            [
                VlanInfo(
                    1,
                    "VLAN 0001",
                    "enable",
                    "common",
                    ("Eth-Trunk1",),
                    tuple(f"GigabitEthernet0/0/{n}" for n in (2, 3, 5, 6, 7, 8)),
                ),
                VlanInfo(
                    10,
                    "Users-Floor1",
                    "enable",
                    "common",
                    ("XGigabitEthernet0/0/1", "Eth-Trunk1"),
                    ("GigabitEthernet0/0/1", "GigabitEthernet0/0/4"),
                ),
                VlanInfo(
                    20,
                    "Voice",
                    "enable",
                    "common",
                    ("XGigabitEthernet0/0/1", "Eth-Trunk1"),
                    ("GigabitEthernet0/0/9", "GigabitEthernet0/0/10"),
                ),
                VlanInfo(
                    100,
                    "Management",
                    "enable",
                    "common",
                    ("XGigabitEthernet0/0/1", "Eth-Trunk1"),
                    (),
                ),
                VlanInfo(200, "VLAN 0200", "disable", "common", (), ()),
            ],
        ),
        (
            CE6881,
            [
                VlanInfo(
                    1, "VLAN 0001", "enable", "common", (), ("10GE1/0/3", "10GE1/0/4", "10GE1/0/5")
                ),
                VlanInfo(
                    10,
                    "Servers",
                    "enable",
                    "common",
                    ("40GE1/0/1", "40GE1/0/2", "Eth-Trunk1"),
                    ("10GE1/0/1",),
                ),
                VlanInfo(20, "Storage", "enable", "common", ("Eth-Trunk1",), ("10GE1/0/2",)),
                VlanInfo(
                    30,
                    "Tenant A Backend",
                    "enable",
                    "common",
                    (
                        "10GE1/0/6",
                        "10GE1/0/7",
                        "10GE1/0/8",
                        "10GE1/0/9",
                        "10GE1/0/10",
                        "Eth-Trunk1",
                    ),
                    (),
                ),
                VlanInfo(4000, None, "enable", "common", (), ()),
            ],
        ),
    ],
)
def test_parse_vlans_golden(generation: str, expected: list[VlanInfo]) -> None:
    assert parse_vlans(load(generation, "display_vlan.txt")) == expected


def test_parse_vlans_props_without_description_header() -> None:
    text = (
        "VID  Type    Ports\n"
        "*5   common  UT:GE0/0/1(U)\n"
        "VID  Status  Property MAC-LRN Statistics\n"
        "5    enable  default  enable  disable    Lab VLAN\n"
        "6    disable default  enable  disable\n"
        "9999 enable  default  enable  disable    bogus\n"
    )
    rows = parse_vlans(text)
    assert rows == [
        VlanInfo(5, "Lab VLAN", "enable", "common", (), ("GigabitEthernet0/0/1",)),
        VlanInfo(6, None, "disable", None, (), ()),
    ]


def test_parse_vlans_ignores_rows_before_any_header() -> None:
    assert parse_vlans("10   common  UT:GE0/0/1(U)\n") == []
