from __future__ import annotations

import pytest

from app.network.parsers.lldp import LldpNeighbor, parse_lldp_neighbors_brief
from tests.unit.parsers._helpers import CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (
            S5735,
            [
                LldpNeighbor("GigabitEthernet0/0/1", "AP-Floor1-01", "GigabitEthernet0/0/0", 104),
                LldpNeighbor("GigabitEthernet0/0/23", "SW-Core-02", "GigabitEthernet0/0/23", 98),
                LldpNeighbor("GigabitEthernet0/0/24", "SW-Core-02", "GigabitEthernet0/0/24", 98),
                LldpNeighbor("XGigabitEthernet0/0/1", "CE-01", "10GE1/0/48", 115),
            ],
        ),
        (
            CE6881,
            [
                LldpNeighbor("10GE1/0/1", "server-rack01-n01", "eth0", 109),
                LldpNeighbor("10GE1/0/2", "storage-a", "5489-98ab-cdef", 95),
                LldpNeighbor("10GE1/0/48", "SW-01", "XGigabitEthernet0/0/1", 112),
                LldpNeighbor("40GE1/0/1", "CE-02", "40GE1/0/1", 101),
                LldpNeighbor("40GE1/0/2", "CE-02", "40GE1/0/2", 101),
            ],
        ),
    ],
)
def test_parse_lldp_brief_golden(generation: str, expected: list[LldpNeighbor]) -> None:
    assert (
        parse_lldp_neighbors_brief(load(generation, "display_lldp_neighbor_brief.txt")) == expected
    )


def test_parse_lldp_device_name_with_spaces_uses_columns() -> None:
    text = (
        "Local Intf       Neighbor Dev             Neighbor Intf             Exptime(s)\n"
        "GE0/0/2          Lab Printer 01           eth0                      90\n"
        "GE0/0/3          host-only                                          90\n"
    )
    assert parse_lldp_neighbors_brief(text) == [
        LldpNeighbor("GigabitEthernet0/0/2", "Lab Printer 01", "eth0", 90)
    ]
