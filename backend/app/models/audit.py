"""Append-only audit log (UPDATE/DELETE blocked by a PostgreSQL trigger)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JsonType, UtcDateTime, UuidPkMixin, utcnow


class AuditLog(UuidPkMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_user_ts", "user_id", "timestamp"),
        Index("ix_audit_logs_device_ts", "device_id", "timestamp"),
    )

    timestamp: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    username: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str | None] = mapped_column(String(32))
    source_ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    operation: Mapped[str] = mapped_column(String(96))
    command_category: Mapped[str | None] = mapped_column(String(16))
    request_id: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean)
    result_summary: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
