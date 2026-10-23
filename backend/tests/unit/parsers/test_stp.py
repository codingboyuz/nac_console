from __future__ import annotations

import pytest

from app.network.parsers.stp import StpGlobal, StpPortBrief, parse_stp_brief, parse_stp_global
from tests.unit.parsers._helpers import CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (
            S5735,
            [
                StpPortBrief(0, "GigabitEthernet0/0/1", "DESI", "FORWARDING", "NONE"),
                StpPortBrief(0, "GigabitEthernet0/0/4", "DESI", "FORWARDING", "BPDU"),
                StpPortBrief(0, "XGigabitEthernet0/0/1", "DESI", "FORWARDING", "NONE"),
                StpPortBrief(0, "XGigabitEthernet0/0/2", "ALTE", "DISCARDING", "NONE"),
                StpPortBrief(0, "Eth-Trunk1", "ROOT", "FORWARDING", "NONE"),
            ],
        ),
        (
            CE6881,
            [
                StpPortBrief(0, "10GE1/0/1", "DESI", "FORWARDING", "NONE"),
                StpPortBrief(0, "10GE1/0/2", "DESI", "FORWARDING", "NONE"),
                StpPortBrief(0, "10GE1/0/4", "DESI", "DISCARDING", "NONE"),
                StpPortBrief(0, "Eth-Trunk1", "ROOT", "FORWARDING", "NONE"),
                StpPortBrief(1, "Eth-Trunk1", "MAST", "FORWARDING", "NONE"),
                StpPortBrief(1, "10GE1/0/8", "DESI", "FORWARDING", "ROOT"),
            ],
        ),
    ],
)
def test_parse_stp_brief_golden(generation: str, expected: list[StpPortBrief]) -> None:
    assert parse_stp_brief(load(generation, "display_stp_brief.txt")) == expected


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (
            S5735,
            StpGlobal(
                mode="MSTP",
                bridge_id="32768.00e0-fc12-3400",
                root_id="4096.00e0-fc00-0001",
                root_path_cost=20000,
                root_port="128.24",
                tc_count=12,
                time_since_last_tc="0 days 1h:2m:3s",
                bpdu_protection=False,
            ),
        ),
        (
            CE6881,
            StpGlobal(
                mode="MSTP",
                bridge_id="32768.5489-9812-3400",
                root_id="0.5489-9800-0001",
                root_path_cost=1000,
                root_port="128.53",
                tc_count=4,
                time_since_last_tc="3 days 4h:5m:6s",
                bpdu_protection=True,
            ),
        ),
    ],
)
def test_parse_stp_global_golden(generation: str, expected: StpGlobal) -> None:
    assert parse_stp_global(load(generation, "display_stp.txt")) == expected


def test_parse_stp_global_without_section_header() -> None:
    text = (
        "Protocol Status     :Enabled\n"
        "Mode RSTP\n"
        "CIST Bridge         :4096 .00e0-fc12-0001\n"
        "BPDU-Protection     :Unknown\n"
        "TC or TCN received  :n/a\n"
        "----[Port1(GigabitEthernet0/0/1)][FORWARDING]----\n"
        "CIST Root/ERPC      :1.0000-0000-0001 / 5\n"
    )
    info = parse_stp_global(text)
    assert info.mode == "RSTP"
    assert info.bridge_id == "4096.00e0-fc12-0001"
    assert info.root_id is None
    assert info.bpdu_protection is None
    assert info.tc_count is None


def test_parse_stp_global_mode_key_value() -> None:
    assert parse_stp_global("Mode   :STP compatible\n").mode == "STP"
