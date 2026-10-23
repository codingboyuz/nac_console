"""VLAN ranges and MAC normalization."""

from __future__ import annotations

import pytest

from app.domain.mac import (
    InvalidMac,
    is_locally_administered,
    is_multicast,
    normalize_mac,
    oui,
    to_huawei,
)
from app.domain.vlans import InvalidVlan, parse_vlan_list, to_huawei_ranges


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("10", [10]),
        ("10,20-22", [10, 20, 21, 22]),
        ("10 20 to 22", [10, 20, 21, 22]),
        ("30 - 31, 30", [30, 31]),
        ("4094", [4094]),
    ],
)
def test_parse_vlan_list(spec: str, expected: list[int]) -> None:
    assert parse_vlan_list(spec) == expected


@pytest.mark.parametrize("spec", ["0", "4095", "20-10", "abc", "", "10-", "1-5000"])
def test_invalid_vlans(spec: str) -> None:
    with pytest.raises(InvalidVlan):
        parse_vlan_list(spec)


def test_reserved_vlan_rejected_when_requested() -> None:
    with pytest.raises(InvalidVlan, match="reserved"):
        parse_vlan_list("1-5", allow_reserved=False)
    assert parse_vlan_list("1 x 5", strict=False) == [1, 5]


def test_to_huawei_ranges() -> None:
    assert to_huawei_ranges([10, 11, 12, 20, 22, 23]) == "10 to 12 20 22 to 23"
    with pytest.raises(InvalidVlan):
        to_huawei_ranges([])


@pytest.mark.parametrize(
    "raw",
    ["00e0-fc12-3456", "00:E0:FC:12:34:56", "00-e0-fc-12-34-56", "00e0.fc12.3456", "00e0fc123456"],
)
def test_normalize_mac(raw: str) -> None:
    assert normalize_mac(raw) == "00:e0:fc:12:34:56"


def test_mac_helpers() -> None:
    assert to_huawei("00:e0:fc:12:34:56") == "00e0-fc12-3456"
    assert oui("00e0-fc12-3456") == "00:E0:FC"
    assert is_multicast("01:00:5e:00:00:01")
    assert is_locally_administered("02:00:00:00:00:01")
    for bad in ("", "00e0-fc12-345", "zz:e0:fc:12:34:56"):
        with pytest.raises(InvalidMac):
            normalize_mac(bad)
