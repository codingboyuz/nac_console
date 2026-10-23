from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.network import parsers as p
from app.network.parsers.common import (
    clean_output,
    find_cli_error,
    none_if_dash,
    safe_interface,
    safe_mac,
    strip_state,
    to_bytes,
    to_float,
    to_int,
)
from tests.unit.parsers._helpers import ERROR_FIXTURES, S5735, load

LIST_PARSERS: list[Callable[[str], list[Any]]] = [
    p.parse_interface_brief,
    p.parse_interface_detail,
    p.parse_ip_interface_brief,
    p.parse_vlans,
    p.parse_mac_table,
    p.parse_arp,
    p.parse_routing_table,
    p.parse_stp_brief,
    p.parse_dhcp_snooping_bindings,
    p.parse_transceivers,
    p.parse_lldp_neighbors_brief,
]
OBJECT_PARSERS: list[Callable[[str], Any]] = [
    p.parse_version,
    p.parse_stp_global,
    p.parse_cpu_usage,
    p.parse_memory_usage,
]
GARBAGE = [
    "",
    "\n\n   \n",
    "<SW-01>\n[~CE-01]\n",
    "lorem ipsum dolor sit amet\n12 34 56\n:::\n----\n" * 3,
    "GigabitEthernet0/0/1 current state\nInput: x packets\n\x00\x07\x1b[5m",
    "Interface PHY Protocol\nfoo bar baz qux\nVID Status Property\n99999 enable x\n",
    "Local Intf  Neighbor Dev  Neighbor Intf  Exptime\nnot-an-intf dev intf 10\n",
]


def _non_empty_fields(obj: Any) -> list[str]:
    return [
        name
        for name in obj.__dataclass_fields__
        if name != "family" and getattr(obj, name) is not None
    ]


@pytest.mark.parametrize("parser", LIST_PARSERS)
@pytest.mark.parametrize("text", GARBAGE)
def test_list_parsers_tolerate_garbage(parser: Callable[[str], list[Any]], text: str) -> None:
    assert parser(text) == []


@pytest.mark.parametrize("parser", LIST_PARSERS)
@pytest.mark.parametrize("fixture", ERROR_FIXTURES)
def test_error_fixtures_yield_no_rows(parser: Callable[[str], list[Any]], fixture: str) -> None:
    assert parser(load("errors", fixture)) == []


@pytest.mark.parametrize("parser", OBJECT_PARSERS)
@pytest.mark.parametrize("text", ["", *ERROR_FIXTURES])
def test_object_parsers_return_empty_objects(parser: Callable[[str], Any], text: str) -> None:
    raw = load("errors", text) if text.endswith(".txt") else text
    assert _non_empty_fields(parser(raw)) == []


@pytest.mark.parametrize("parser", LIST_PARSERS + OBJECT_PARSERS)
def test_parsers_accept_none(parser: Callable[[Any], Any]) -> None:
    parser(None)


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("unrecognized_command.txt", "Unrecognized command found at '^' position."),
        ("wrong_parameter.txt", "Wrong parameter found at '^' position."),
        ("incomplete_command.txt", "Incomplete command found at '^' position."),
        ("too_many_parameters.txt", "Too many parameters found at '^' position."),
        ("vlan_does_not_exist.txt", "The VLAN does not exist."),
        ("ce_commit_failed.txt", "The configuration failed to be committed."),
    ],
)
def test_find_cli_error(fixture: str, message: str) -> None:
    assert find_cli_error(load("errors", fixture)) == message


def test_find_cli_error_none_for_normal_output() -> None:
    assert find_cli_error(load(S5735, "display_version.txt")) is None


def test_clean_output_strips_prompt_echo_and_paging() -> None:
    raw = (
        "<SW-01>display mac-address\r\n"
        "line one\r\n"
        "  ---- More ----\x1b[42D                                          \x1b[42D"
        "   indented two\r\n"
        "\x1b[1mbold\x1b[0m\r\n"
        "[~CE-01]\r\n"
        "[*CE-01-vlan10]\r\n"
    )
    assert clean_output(raw) == "line one\n   indented two\nbold"
    assert clean_output(None) == ""
    assert clean_output("") == ""


def test_paged_fixture_parses_identically() -> None:
    paged = load(S5735, "display_mac_address_paged.txt")
    assert "---- More ----" in paged
    assert "\x1b[42D" in paged
    clean = load(S5735, "display_mac_address.txt")
    assert p.parse_mac_table(paged) == p.parse_mac_table(clean)
    assert len(p.parse_mac_table(paged)) == 7


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12", 12), ("-3", -3), ("1,234", 1234), ("--", None), (None, None), ("1.5", None)],
)
def test_to_int(value: str | None, expected: int | None) -> None:
    assert to_int(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.25%", 1.25), ("0%", 0.0), ("-2.31", -2.31), ("--", None), (None, None), ("x", None)],
)
def test_to_float(value: str | None, expected: float | None) -> None:
    assert to_float(value) == expected


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("10", "bytes", 10),
        ("4", "Kbytes", 4096),
        ("2", "MB", 2 * 1024**2),
        ("1", "G", 1024**3),
        ("7", None, 7),
        ("1", "parsecs", None),
        ("abc", "bytes", None),
    ],
)
def test_to_bytes(value: str, unit: str | None, expected: int | None) -> None:
    assert to_bytes(value, unit) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("GE0/0/1", "GigabitEthernet0/0/1"),
        ("XGE0/0/1", "XGigabitEthernet0/0/1"),
        ("10GE1/0/1", "10GE1/0/1"),
        ("Eth-Trunk1", "Eth-Trunk1"),
        ("MEth0/0/1", "MEth0/0/1"),
        ("Vlanif100", "Vlanif100"),
        ("NULL0", "NULL0"),
        ("LoopBack0", "LoopBack0"),
        (" Wlan-Radio0/0/0 ", "Wlan-Radio0/0/0"),
        ("InLoopBack0", "InLoopBack0"),
        ("-", None),
        ("", None),
        (None, None),
    ],
)
def test_safe_interface(name: str | None, expected: str | None) -> None:
    assert safe_interface(name) == expected


def test_safe_mac_and_helpers() -> None:
    assert safe_mac("00e0-fc12-3456") == "00:e0:fc:12:34:56"
    assert safe_mac("zz") is None
    assert safe_mac(None) is None
    assert strip_state("*down") == "down"
    assert strip_state("(l)up") == "up"
    assert strip_state("up(s)") == "up"
    assert strip_state("Unknown") == "unknown"
    assert none_if_dash("--") is None
    assert none_if_dash(None) is None
    assert none_if_dash(" MGMT ") == "MGMT"
