from __future__ import annotations

import pytest

from app.network.parsers.cpu_mem import (
    CpuUsage,
    MemoryUsage,
    parse_cpu_usage,
    parse_memory_usage,
)
from tests.unit.parsers._helpers import CE6881, S5735, load


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (S5735, CpuUsage(12.0, 12.0, 10.0, 9.0)),
        (CE6881, CpuUsage(8.0, 8.0, 7.0, 7.0)),
    ],
)
def test_parse_cpu_usage_golden(generation: str, expected: CpuUsage) -> None:
    assert parse_cpu_usage(load(generation, "display_cpu_usage.txt")) == expected


@pytest.mark.parametrize(
    ("text", "current"),
    [
        (" CPU Usage            : 23% Max: 45%\n", 23.0),
        ("System cpu use rate is : 4%\n", 4.0),
        ("System CPU Using Percentage :     6%\nMax CPU Usage : 90%\n", 6.0),
    ],
)
def test_parse_cpu_usage_fallbacks(text: str, current: float) -> None:
    usage = parse_cpu_usage(text)
    assert usage.current_percent == current
    assert usage.five_seconds is None


@pytest.mark.parametrize(
    ("generation", "expected"),
    [
        (S5735, MemoryUsage(1022361600, 487026688, 1022361600 - 487026688, 47.0)),
        (
            CE6881,
            MemoryUsage(4044012 * 1024, 1828856 * 1024, (4044012 - 1828856) * 1024, 45.0),
        ),
    ],
)
def test_parse_memory_usage_golden(generation: str, expected: MemoryUsage) -> None:
    assert parse_memory_usage(load(generation, "display_memory_usage.txt")) == expected


def test_parse_memory_usage_derives_missing_values() -> None:
    from_free = parse_memory_usage(
        "System Total Memory: 1000 bytes\nTotal Free Memory: 250 bytes\n"
    )
    assert from_free == MemoryUsage(1000, 750, 250, 75.0)
    from_pct = parse_memory_usage("System Total Memory: 2000 bytes\nMemory Using Percent: 10%\n")
    assert from_pct == MemoryUsage(2000, 200, 1800, 10.0)
    only_used = parse_memory_usage("Total Memory Used: 5 Kbytes\n")
    assert only_used == MemoryUsage(None, 5120, None, None)
