"""Job lifecycle enums and status aggregation (pure)."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def finished(self) -> bool:
        return self in {JobStatus.SUCCESS, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELLED}


class DeviceResultStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # stop-on-failure threshold reached or job cancelled


class JobQueue(StrEnum):
    CONFIG = "config"
    POLLING = "polling"
    DISCOVERY = "discovery"
    ALERTS = "alerts"
    DEFAULT = "default"


def aggregate_status(results: Iterable[DeviceResultStatus], *, cancelled: bool) -> JobStatus:
    """SUCCESS if all succeeded; FAILED if none did; PARTIAL otherwise; CANCELLED wins."""
    items = list(results)
    if cancelled:
        return JobStatus.CANCELLED
    ok = sum(1 for r in items if r is DeviceResultStatus.SUCCESS)
    if items and ok == len(items):
        return JobStatus.SUCCESS
    if ok == 0:
        return JobStatus.FAILED
    return JobStatus.PARTIAL
