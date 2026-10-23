from __future__ import annotations

import pytest

from app.network.parsers.interfaces import (
    InterfaceBrief,
    InterfaceDetail,
    IpInterfaceBrief,
    parse_interface_brief,
    parse_interface_detail,
    parse_ip_interface_brief,
)
from tests.unit.parsers._helpers import CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "count", "expected_rows"),
    [
        (
            S5735,
            15,
            [
                InterfaceBrief("Eth-Trunk1", "up", "up", 0.01, 0.02, 0, 0, False),
                InterfaceBrief("GigabitEthernet0/0/1", "up", "up", 1.25, 0.87, 0, 0, False),
                InterfaceBrief("GigabitEthernet0/0/3", "down", "down", 0.0, 0.0, 0, 0, True),
                InterfaceBrief("GigabitEthernet0/0/4", "up", "up", 0.05, 0.11, 152, 0, False),
                InterfaceBrief("GigabitEthernet0/0/5", "up", "up", 0.0, 0.0, 0, 0, False),
                InterfaceBrief("XGigabitEthernet0/0/2", "down", "down", 0.0, 0.0, 0, 0, False),
                InterfaceBrief("NULL0", "up", "up", 0.0, 0.0, 0, 0, False),
                InterfaceBrief("Vlanif1", "up", "down", None, None, 0, 0, False),
            ],
        ),
        (
            CE6881,
            12,
            [
                InterfaceBrief("10GE1/0/1", "up", "up", 12.04, 8.51, 0, 0, False),
                InterfaceBrief("10GE1/0/2", "up", "up", 0.35, 0.21, 37, 0, False),
                InterfaceBrief("10GE1/0/3", "down", "down", 0.0, 0.0, 0, 0, True),
                InterfaceBrief("40GE1/0/1", "up", "up", 21.1, 19.87, 0, 0, False),
                InterfaceBrief("Eth-Trunk1", "up", "up", 21.02, 19.63, 0, 0, False),
                InterfaceBrief("MEth0/0/0", "up", "up", 0.01, 0.01, 0, 0, False),
                InterfaceBrief("Vlanif20", "up", "up", None, None, 0, 0, False),
            ],
        ),
    ],
)
def test_interface_brief_golden(
    generation: str, count: int, expected_rows: list[InterfaceBrief]
) -> None:
    rows = parse_interface_brief(load(generation, "display_interface_brief.txt"))
    assert len(rows) == count
    by_name = {row.name: row for row in rows}
    assert len(by_name) == count
    for expected in expected_rows:
        assert by_name[expected.name] == expected


def test_interface_brief_abbreviated_names_are_canonical() -> None:
    text = "GE0/0/1   up   up   1%   2%   0   0\nXGE0/0/2  ^down  down\nWlan-Radio0/0/0 up up\n"
    rows = parse_interface_brief(text)
    assert [r.name for r in rows] == [
        "GigabitEthernet0/0/1",
        "XGigabitEthernet0/0/2",
        "Wlan-Radio0/0/0",
    ]
    assert rows[1].in_util is None
    assert rows[1].phy == "down"
    assert rows[1].admin_down is False


S_DETAIL = {
    "GigabitEthernet0/0/1": InterfaceDetail(
        name="GigabitEthernet0/0/1",
        admin_up=True,
        oper_up=True,
        description="To-AP-Floor1",
        speed_mbps=1000,
        duplex="full",
        mtu=None,
        mac="00:e0:fc:12:34:56",
        pvid=10,
        link_type="switch",
        input_packets=1234567,
        input_bytes=987654321,
        output_packets=2345678,
        output_bytes=1876543210,
        crc_errors=0,
        input_errors=0,
        output_errors=0,
        input_discards=0,
        output_discards=3,
        input_rate_bps=12345,
        output_rate_bps=23456,
    ),
    "GigabitEthernet0/0/4": InterfaceDetail(
        name="GigabitEthernet0/0/4",
        admin_up=True,
        oper_up=True,
        description="Printer-2F",
        speed_mbps=100,
        duplex="half",
        mtu=None,
        mac="00:e0:fc:12:34:59",
        pvid=10,
        link_type="switch",
        input_packets=88120,
        input_bytes=10234567,
        output_packets=99231,
        output_bytes=20345678,
        crc_errors=152,
        input_errors=152,
        output_errors=1,
        input_discards=2,
        output_discards=0,
        input_rate_bps=4410,
        output_rate_bps=9120,
    ),
}
CE_DETAIL = {
    "10GE1/0/1": InterfaceDetail(
        name="10GE1/0/1",
        admin_up=True,
        oper_up=True,
        description="To-Server-Rack01",
        speed_mbps=10000,
        duplex="full",
        mtu=None,
        mac="54:89:98:12:34:01",
        pvid=10,
        link_type="switch",
        input_packets=123456789,
        input_bytes=98765432109,
        output_packets=112345678,
        output_bytes=87654321098,
        crc_errors=0,
        input_errors=0,
        output_errors=0,
        input_discards=0,
        output_discards=0,
        input_rate_bps=2341720,
        output_rate_bps=1820544,
    ),
    "10GE1/0/2": InterfaceDetail(
        name="10GE1/0/2",
        admin_up=True,
        oper_up=True,
        description="To-Storage-A",
        speed_mbps=10000,
        duplex="full",
        mtu=None,
        mac="54:89:98:12:34:02",
        pvid=1,
        link_type="switch",
        input_packets=5678901,
        input_bytes=4567890123,
        output_packets=4567890,
        output_bytes=3456789012,
        crc_errors=37,
        input_errors=37,
        output_errors=0,
        input_discards=5,
        output_discards=0,
        input_rate_bps=35120,
        output_rate_bps=21002,
    ),
}


@pytest.mark.parametrize(
    ("generation", "names", "golden", "admin_down", "vlanif_mtu"),
    [
        (
            S5735,
            ["GigabitEthernet0/0/1", "GigabitEthernet0/0/3", "GigabitEthernet0/0/4", "Vlanif10"],
            S_DETAIL,
            "GigabitEthernet0/0/3",
            1500,
        ),
        (CE6881, ["10GE1/0/1", "10GE1/0/2", "10GE1/0/3", "Vlanif10"], CE_DETAIL, "10GE1/0/3", 9000),
    ],
)
def test_interface_detail_golden(
    generation: str,
    names: list[str],
    golden: dict[str, InterfaceDetail],
    admin_down: str,
    vlanif_mtu: int,
) -> None:
    rows = parse_interface_detail(load(generation, "display_interface.txt"))
    assert [r.name for r in rows] == names
    by_name = {r.name: r for r in rows}
    for name, expected in golden.items():
        assert by_name[name] == expected
    down = by_name[admin_down]
    assert down.admin_up is False
    assert down.oper_up is False
    vlanif = by_name["Vlanif10"]
    assert vlanif.mtu == vlanif_mtu
    assert vlanif.link_type == "route"
    assert vlanif.pvid is None
    assert vlanif.input_packets is None


def test_interface_detail_ce_empty_description_and_speed() -> None:
    rows = parse_interface_detail(load(CE6881, "display_interface.txt"))
    assert rows[2].description is None
    assert rows[2].speed_mbps == 10000
    s_rows = parse_interface_detail(load(S5735, "display_interface.txt"))
    assert s_rows[1].speed_mbps is None  # "Speed :  AUTO"
    assert s_rows[1].duplex == "auto"


def test_interface_detail_explicit_link_type_and_protocol_down() -> None:
    text = (
        "Eth-Trunk1 current state : UP\n"
        "Line protocol current state : DOWN\n"
        "Port link-type: trunk\n"
        "Switch Port, PVID :    1\n"
    )
    (row,) = parse_interface_detail(text)
    assert row.link_type == "trunk"
    assert row.admin_up is True
    assert row.oper_up is False
    assert row.pvid == 1


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (
            S5735,
            [
                IpInterfaceBrief("LoopBack0", "198.51.100.1", 32, "up", "up", None),
                IpInterfaceBrief("MEth0/0/1", "10.255.0.253", 24, "down", "down", None),
                IpInterfaceBrief("NULL0", None, None, "up", "up", None),
                IpInterfaceBrief("Vlanif1", None, None, "up", "down", None),
                IpInterfaceBrief("Vlanif10", "10.10.10.1", 24, "up", "up", None),
                IpInterfaceBrief("Vlanif100", "192.0.2.2", 24, "up", "up", None),
                IpInterfaceBrief("Vlanif200", "10.20.0.1", 24, "down", "down", None),
            ],
        ),
        (
            CE6881,
            [
                IpInterfaceBrief("LoopBack0", "198.51.100.11", 32, "up", "up", None),
                IpInterfaceBrief("MEth0/0/0", "192.0.2.100", 24, "up", "up", "MGMT"),
                IpInterfaceBrief("NULL0", None, None, "up", "up", None),
                IpInterfaceBrief("Vlanif10", "10.10.10.1", 24, "up", "up", None),
                IpInterfaceBrief("Vlanif20", "10.20.20.1", 24, "up", "up", "TENANT-A"),
                IpInterfaceBrief("Vlanif30", None, None, "down", "down", None),
            ],
        ),
    ],
)
def test_ip_interface_brief_golden(generation: str, expected: list[IpInterfaceBrief]) -> None:
    assert parse_ip_interface_brief(load(generation, "display_ip_interface_brief.txt")) == expected


def test_ip_interface_brief_without_prefix() -> None:
    (row,) = parse_ip_interface_brief("Vlanif5   10.5.5.1   up   up\n")
    assert row.ip == "10.5.5.1"
    assert row.prefix_len is None
