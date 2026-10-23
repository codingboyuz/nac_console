"""Schedule definitions (pure): what runs periodically, how often, as which service account."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScheduleEntry(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_.-]{1,64}$")
    job_type: str
    interval_seconds: int = Field(ge=30, le=7 * 86400)
    params: dict[str, Any] = Field(default_factory=dict)
    device_filter: dict[str, str] = Field(default_factory=dict)  # e.g. {"site": "hq"}
    enabled: bool = True
    run_as: str = Field(default="svc-scheduler", description="service account username")


DEFAULT_SCHEDULES: list[ScheduleEntry] = [
    ScheduleEntry(
        name="backup-daily",
        job_type="backup.run",
        interval_seconds=86400,
        params={"trigger": "scheduled"},
    ),
    ScheduleEntry(
        name="cpu-30s", job_type="poll.metrics", interval_seconds=30, params={"scope": "cpu"}
    ),
    ScheduleEntry(
        name="interfaces-60s",
        job_type="poll.metrics",
        interval_seconds=60,
        params={"scope": "interfaces"},
    ),
    ScheduleEntry(name="mac-15m", job_type="collect.mac", interval_seconds=900),
    ScheduleEntry(name="arp-15m", job_type="collect.arp", interval_seconds=900),
]
