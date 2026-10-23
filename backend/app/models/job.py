"""Jobs, per-device results and key/value platform settings (incl. schedules)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.jobs import DeviceResultStatus, JobStatus
from app.models.base import Base, JsonType, UtcDateTime, UuidPkMixin, str_enum, utcnow


class Job(UuidPkMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("ix_jobs_created_by", "created_by"),
        UniqueConstraint("created_by", "idempotency_key", name="uq_jobs_idempotency"),
    )

    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "job_status"), default=JobStatus.PENDING
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    target_devices: Mapped[list[str]] = mapped_column(JsonType, default=list)
    params: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    concurrency: Mapped[int] = mapped_column(Integer, default=5)
    stop_on_failure: Mapped[int | None] = mapped_column(Integer)
    queue: Mapped[str] = mapped_column(String(16), default="default")
    task_id: Mapped[str | None] = mapped_column(String(64))

    results: Mapped[list[JobDeviceResult]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="raise"
    )


class JobDeviceResult(UuidPkMixin, Base):
    __tablename__ = "job_device_results"
    __table_args__ = (
        UniqueConstraint("job_id", "device_id", name="uq_job_device_results_job_device"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    status: Mapped[DeviceResultStatus] = mapped_column(
        str_enum(DeviceResultStatus, "job_device_status"), default=DeviceResultStatus.PENDING
    )
    output: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]

    job: Mapped[Job] = relationship(back_populates="results", lazy="raise")


class Setting(Base):
    """Platform settings editable from the settings API (e.g. 'schedules')."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, onupdate=utcnow)
