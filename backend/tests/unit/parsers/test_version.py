from __future__ import annotations

import pytest

from app.domain.devices import DeviceFamily
from app.network.parsers.version import (
    VersionInfo,
    detect_family,
    parse_version,
    uptime_to_seconds,
)
from tests.unit.parsers._helpers import AR6120, CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (
            S5735,
            VersionInfo(
                family=DeviceFamily.S,
                vrp_version="5.170",
                software_version="V200R019C10SPC500",
                model="S5735-L48T4X-A1",
                uptime="12 weeks, 3 days, 4 hours, 25 minutes",
                uptime_seconds=12 * 604800 + 3 * 86400 + 4 * 3600 + 25 * 60,
            ),
        ),
        (
            CE6881,
            VersionInfo(
                family=DeviceFamily.CE,
                vrp_version="8.180",
                software_version="V200R005C10SPC800",
                model="CE6881-48S6CQ",
                uptime="45 days, 6 hours, 12 minutes",
                uptime_seconds=45 * 86400 + 6 * 3600 + 12 * 60,
            ),
        ),
        (
            AR6120,
            VersionInfo(
                family=DeviceFamily.AR,
                vrp_version="5.170",
                software_version="V300R021C10SPC100",
                model="AR6120",
                uptime="0 week, 2 days, 1 hour, 7 minutes",
                uptime_seconds=2 * 86400 + 3600 + 7 * 60,
            ),
        ),
    ],
)
def test_parse_version_golden(generation: str, expected: VersionInfo) -> None:
    assert parse_version(load(generation, "display_version.txt")) == expected


def test_parse_version_falls_back_to_parenthesized_model() -> None:
    text = "VRP (R) software, Version 5.170 (S5720 V200R011C10SPC600)\n"
    info = parse_version(text)
    assert info.model == "S5720"
    assert info.family is DeviceFamily.S
    assert info.software_version == "V200R011C10SPC600"
    assert info.uptime is None
    assert info.uptime_seconds is None


def test_parse_version_vrp8_unknown_model_is_ce() -> None:
    info = parse_version("VRP (R) software, Version 8.191 (V300R001C00)\n")
    assert info.family is DeviceFamily.CE
    assert info.model is None
    assert info.software_version == "V300R001C00"


def test_parse_version_empty() -> None:
    assert parse_version("") == VersionInfo(DeviceFamily.UNKNOWN, None, None, None, None, None)


@pytest.mark.parametrize(
    ("model", "vrp", "family"),
    [
        ("CE12808", None, DeviceFamily.CE),
        ("S5735-L48T4X-A1", None, DeviceFamily.S),
        ("AR6120", None, DeviceFamily.AR),
        ("SR-1", None, DeviceFamily.UNKNOWN),
        (None, "8.180", DeviceFamily.CE),
        (None, "5.170", DeviceFamily.UNKNOWN),
        ("NE40E", None, DeviceFamily.UNKNOWN),
    ],
)
def test_detect_family(model: str | None, vrp: str | None, family: DeviceFamily) -> None:
    assert detect_family(model, vrp) is family


def test_uptime_to_seconds_edge_cases() -> None:
    assert uptime_to_seconds(None) is None
    assert uptime_to_seconds("forever") is None
    assert uptime_to_seconds("1 day, 30 seconds") == 86430
