"""Parsers for ``display cpu-usage`` and ``display memory-usage``."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.network.parsers.common import clean_output, to_bytes, to_float

_NUM = r"(?P<v>\d+(?:\.\d+)?)\s*%"
_CPU_USAGE_RE = re.compile(rf"^\s*CPU Usage\s*:\s*{_NUM}", re.IGNORECASE | re.MULTILINE)
_CPU_RATE_RE = re.compile(
    rf"^\s*System (?:cpu use rate is|CPU Using Percentage)\s*:\s*{_NUM}",
    re.IGNORECASE | re.MULTILINE,
)
_FIVE_SEC_RE = re.compile(rf"five seconds\s*:\s*{_NUM}", re.IGNORECASE)
_ONE_MIN_RE = re.compile(rf"one minute\s*:\s*{_NUM}", re.IGNORECASE)
_FIVE_MIN_RE = re.compile(rf"five minutes\s*:\s*{_NUM}", re.IGNORECASE)

_SIZE = r"(?P<v>\d+(?:\.\d+)?)\s*(?P<u>[KMG]?(?:bytes?|B))?"
_MEM_TOTAL_RE = re.compile(rf"System Total Memory(?:\s+Is)?\s*:\s*{_SIZE}", re.IGNORECASE)
_MEM_USED_RE = re.compile(rf"Total Memory Used(?:\s+Is)?\s*:\s*{_SIZE}", re.IGNORECASE)
_MEM_FREE_RE = re.compile(rf"(?:Total\s+)?Free Memory(?:\s+Is)?\s*:\s*{_SIZE}", re.IGNORECASE)
_MEM_PCT_RE = re.compile(
    rf"Memory Using Percent(?:age)?(?:\s+Is)?\s*:\s*{_NUM}", re.IGNORECASE | re.MULTILINE
)


@dataclass(frozen=True, slots=True)
class CpuUsage:
    """CPU utilization percentages."""

    current_percent: float | None
    five_seconds: float | None
    one_minute: float | None
    five_minutes: float | None


@dataclass(frozen=True, slots=True)
class MemoryUsage:
    """Memory usage in bytes plus the used percentage."""

    total_bytes: int | None
    used_bytes: int | None
    free_bytes: int | None
    used_percent: float | None


def _pct(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    return to_float(match.group("v")) if match else None


def _size(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    return to_bytes(match.group("v"), match.group("u")) if match else None


def parse_cpu_usage(text: str | None) -> CpuUsage:
    """Parse ``display cpu-usage``; current = five-second value, else ``CPU Usage``."""
    cleaned = clean_output(text)
    five_sec = _pct(_FIVE_SEC_RE, cleaned)
    usage = _pct(_CPU_USAGE_RE, cleaned)
    if usage is None:
        usage = _pct(_CPU_RATE_RE, cleaned)
    return CpuUsage(
        current_percent=five_sec if five_sec is not None else usage,
        five_seconds=five_sec,
        one_minute=_pct(_ONE_MIN_RE, cleaned),
        five_minutes=_pct(_FIVE_MIN_RE, cleaned),
    )


def parse_memory_usage(text: str | None) -> MemoryUsage:
    """Parse ``display memory-usage``; derives free/used/percent when possible."""
    cleaned = clean_output(text)
    total = _size(_MEM_TOTAL_RE, cleaned)
    used = _size(_MEM_USED_RE, cleaned)
    free = _size(_MEM_FREE_RE, cleaned)
    percent = _pct(_MEM_PCT_RE, cleaned)
    if total is not None:
        if used is None and free is not None:
            used = total - free
        if free is None and used is not None:
            free = total - used
        if used is None and percent is not None:
            used = int(total * percent / 100)
            free = total - used
        if percent is None and used is not None and total > 0:
            percent = round(used * 100 / total, 2)
    return MemoryUsage(total_bytes=total, used_bytes=used, free_bytes=free, used_percent=percent)
