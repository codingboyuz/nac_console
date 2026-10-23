from __future__ import annotations

import pytest

from app.network.parsers.transceivers import TransceiverInfo, parse_transceivers
from tests.unit.parsers._helpers import CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (
            S5735,
            [
                TransceiverInfo(
                    "XGigabitEthernet0/0/1",
                    "10GBASE_SR_SFP",
                    850,
                    "HUAWEI",
                    -2.31,
                    -2.45,
                    35.0,
                    6.53,
                    3.31,
                ),
                TransceiverInfo(
                    "XGigabitEthernet0/0/2",
                    "10GBASE_LR_SFP",
                    1310,
                    "FAKEOPTICS",
                    -40.0,
                    -1.02,
                    41.25,
                    31.2,
                    3.28,
                ),
            ],
        ),
        (
            CE6881,
            [
                TransceiverInfo(
                    "10GE1/0/1", "10GBASE_SR_SFP", 850, "HUAWEI", -1.85, -2.10, 38.5, 6.12, 3.29
                ),
                TransceiverInfo(
                    "40GE1/0/1", "40GBASE_SR4_QSFP+", 850, "HUAWEI", -0.95, -1.20, 41.0, 7.40, 3.31
                ),
            ],
        ),
    ],
)
def test_parse_transceivers_golden(generation: str, expected: list[TransceiverInfo]) -> None:
    assert parse_transceivers(load(generation, "display_transceiver_verbose.txt")) == expected


def test_parse_transceivers_missing_values() -> None:
    text = (
        "Stray Key :ignored before any block\n"
        " GE0/0/9 transceiver information:\n"
        "  Transceiver Type                      :1000_BASE_T_SFP\n"
        "  Wavelength(nm)                        :N/A\n"
        "  Vendor Name                           :\n"
    )
    assert parse_transceivers(text) == [
        TransceiverInfo(
            "GigabitEthernet0/0/9", "1000_BASE_T_SFP", None, None, None, None, None, None, None
        )
    ]
