"""Counter wrap handling and NETCONF ModelRegistry selection."""

from __future__ import annotations

import pytest

from app.domain.metrics import MAX32, MAX64, InterfaceCounters, counter_delta, interface_rates
from app.network.netconf_models import ModelRegistry


@pytest.mark.parametrize(
    ("prev", "curr", "bits", "expected"),
    [
        (100, 250, 32, 150),
        (MAX32 - 10, 5, 32, 15),  # single 32-bit wrap
        (MAX64 - 1, 9, 64, 10),  # single 64-bit wrap
        (MAX32 - 10, MAX32 - 20, 32, None),  # huge "wrap" -> counter reset
        (None, 5, 32, None),
        (5, None, 64, None),
    ],
)
def test_counter_delta(prev: int | None, curr: int | None, bits: int, expected: int | None) -> None:
    assert counter_delta(prev, curr, bits=bits) == expected


def test_interface_rates_bits_per_second() -> None:
    prev = InterfaceCounters(1_000, 2_000, 0, 0, 0, 0, 64)
    curr = InterfaceCounters(126_001_000, 2_000, 30, 0, 0, 0, 64)
    rates = interface_rates(prev, curr, 1.0)
    assert rates.in_bps == 126_000_000 * 8
    assert rates.out_bps == 0
    assert rates.in_errors_ps == 30
    assert interface_rates(prev, curr, 0).in_bps is None


def test_mixed_counter_widths_use_32_bit_wrap() -> None:
    prev = InterfaceCounters(MAX32 - 1, 0, 0, 0, 0, 0, 32)
    curr = InterfaceCounters(1, 0, 0, 0, 0, 0, 64)
    assert interface_rates(prev, curr, 1.0).in_bps == 16


CE_CAPS = [
    "urn:ietf:params:netconf:base:1.1",
    "urn:ietf:params:netconf:capability:candidate:1.0",
    "urn:ietf:params:netconf:capability:confirmed-commit:1.1",
    "urn:ietf:params:netconf:capability:validate:1.1",
    "urn:huawei:yang:huawei-vlan?module=huawei-vlan&revision=2019-04-24",
    "urn:ietf:params:xml:ns:yang:ietf-interfaces?module=ietf-interfaces&revision=2014-05-08",
]


def test_registry_ce() -> None:
    reg = ModelRegistry.from_capabilities(CE_CAPS)
    assert reg.edit_target == "candidate"
    assert reg.features["confirmed_commit"]
    assert reg.module_for("vlan").name == "huawei-vlan"  # type: ignore[union-attr]
    assert reg.module_for("interfaces").name == "ietf-interfaces"  # type: ignore[union-attr]
    assert reg.module_for("acl") is None
    assert reg.supports("vlan")
    assert not reg.supports("acl")
    data = reg.to_dict()
    assert data["modules"]["huawei-vlan"]["rev"] == "2019-04-24"


def test_registry_running_only() -> None:
    reg = ModelRegistry.from_capabilities(
        [
            "urn:ietf:params:netconf:base:1.0",
            "urn:ietf:params:netconf:capability:writable-running:1.0",
        ]
    )
    assert reg.edit_target == "running"
    assert reg.features["writable_running"]
    assert not reg.features["candidate"]
