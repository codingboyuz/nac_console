from __future__ import annotations

import pytest

from app.network.parsers.routing import RouteEntry, parse_routing_table
from tests.unit.parsers._helpers import CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "count", "golden"),
    [
        (
            S5735,
            10,
            {
                0: RouteEntry("0.0.0.0/0", "Static", 60, 0, "RD", "192.0.2.254", "Vlanif100"),
                3: RouteEntry("10.30.0.0/16", "OSPF", 10, 2, "D", "192.0.2.11", "Vlanif100"),
                4: RouteEntry("10.30.0.0/16", "OSPF", 10, 2, "D", "192.0.2.12", "Vlanif100"),
                5: RouteEntry("10.40.0.0/24", "O_ASE", 150, 1, "D", "192.0.2.11", "Vlanif100"),
                6: RouteEntry("127.0.0.0/8", "Direct", 0, 0, "D", "127.0.0.1", "InLoopBack0"),
                9: RouteEntry("198.51.100.1/32", "Direct", 0, 0, "D", "127.0.0.1", "LoopBack0"),
            },
        ),
        (
            CE6881,
            12,
            {
                0: RouteEntry("0.0.0.0/0", "Static", 60, 0, "RD", "10.10.10.254", "Vlanif10"),
                1: RouteEntry("0.0.0.0/0", "Static", 60, 0, "RD", "10.20.20.254", "Vlanif20"),
                5: RouteEntry("10.50.0.0/16", "OSPF", 10, 3, "D", "10.10.10.2", "Vlanif10"),
                6: RouteEntry("10.50.0.0/16", "OSPF", 10, 3, "D", "10.10.10.3", "Vlanif10"),
                7: RouteEntry("10.50.0.0/16", "OSPF", 10, 3, "D", "10.20.20.2", "Vlanif20"),
                8: RouteEntry("10.60.0.0/24", "IBGP", 255, 0, "RD", "198.51.100.21", "Vlanif10"),
            },
        ),
    ],
)
def test_parse_routing_table_golden(
    generation: str, count: int, golden: dict[int, RouteEntry]
) -> None:
    routes = parse_routing_table(load(generation, "display_ip_routing_table.txt"))
    assert len(routes) == count
    for index, expected in golden.items():
        assert routes[index] == expected


def test_routing_continuation_without_prefix_is_skipped() -> None:
    text = (
        "                    OSPF    10   2           D   192.0.2.12      Vlanif100\n"
        "      10.0.0.0/8    Static  60   0               192.0.2.1       NULL0\n"
    )
    assert parse_routing_table(text) == [
        RouteEntry("10.0.0.0/8", "Static", 60, 0, "", "192.0.2.1", "NULL0")
    ]
