"""Job API schemas and WebSocket message contracts (documented as JSON Schema in API.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr

from app.domain.jobs import DeviceResultStatus, JobStatus
from app.schemas.common import ApiModel


class JobCreate(ApiModel):
    type: str = Field(examples=["vlan.create"], max_length=64)
    target_devices: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    params: dict[str, Any] = Field(
        default_factory=dict, examples=[{"vlans": [{"id": 100, "name": "Lab"}]}]
    )
    name: str | None = Field(default=None, max_length=128)
    dry_run: bool = False
    confirm: bool = False
    confirmation_token: SecretStr | None = None
    reason: str | None = Field(default=None, max_length=500)
    concurrency: int = Field(default=5, ge=1, le=20)
    stop_on_failure: int | None = Field(
        default=None, ge=1, description="stop after N device failures"
    )


class JobDeviceResultOut(ApiModel):
    device_id: uuid.UUID
    status: DeviceResultStatus
    output: dict[str, Any]
    error: str | None
    attempts: int
    started_at: datetime | None
    completed_at: datetime | None


class JobOut(ApiModel):
    id: uuid.UUID
    name: str
    type: str
    status: JobStatus
    progress: int
    created_by: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    target_devices: list[str]
    params: dict[str, Any]
    output: dict[str, Any]
    error: str | None
    retry_count: int
    dry_run: bool
    confirmed: bool
    cancel_requested: bool
    queue: str


class JobDetailOut(JobOut):
    per_device_results: list[JobDeviceResultOut] = Field(default_factory=list)
    confirmation_token: str | None = Field(
        default=None, description="Only for the owner of a dry-run job with DANGEROUS changes"
    )


class JobTypeOut(BaseModel):
    name: str
    permission: str
    queue: str
    params_schema: dict[str, Any]
    changes_config: bool


# --- WebSocket message contracts ---------------------------------------------------------
class WsJobStatus(BaseModel):
    type: Literal["status", "progress", "device", "snapshot"]
    job_id: str
    status: str | None = None
    progress: int | None = None
    device_id: str | None = None
    error: str | None = None


class WsMetric(BaseModel):
    type: Literal["metric"]
    device_id: str
    metric: str
    value: float | None
    labels: dict[str, str] = Field(default_factory=dict)
    timestamp: str


class WsAlert(BaseModel):
    type: Literal["alert"]
    alert_id: str
    rule: str
    severity: str
    state: str
    message: str
    device_id: str | None = None
    timestamp: str
