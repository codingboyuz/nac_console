"""Configuration backups. ``content`` is sanitized (secrets masked) for display/diff; the exact
original is only kept encrypted (vault, AAD bound to the backup id) for restores."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UtcDateTime, UuidPkMixin, str_enum, utcnow


class BackupTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    PRE_CHANGE = "pre-change"
    POST_CHANGE = "post-change"


class BackupSource(StrEnum):
    CURRENT = "current"
    SAVED = "saved"


class Backup(UuidPkMixin, Base):
    __tablename__ = "backups"
    __table_args__ = (
        Index("ix_backups_device_created", "device_id", "created_at"),
        Index("ix_backups_device_hash", "device_id", "sha256"),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    trigger: Mapped[BackupTrigger] = mapped_column(str_enum(BackupTrigger, "backup_trigger"))
    source: Mapped[BackupSource] = mapped_column(
        str_enum(BackupSource, "backup_source"), default=BackupSource.CURRENT
    )
    status: Mapped[str] = mapped_column(String(16), default="SUCCESS")
    sha256: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    family: Mapped[str | None] = mapped_column(String(16))
    # Platform the configuration was taken from (NULL on pre-0009 rows: Huawei).
    platform: Mapped[str | None] = mapped_column(String(32))
    vrp_version: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str | None] = mapped_column(Text)
    content_ciphertext: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str | None] = mapped_column(String(32))
    known_good: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    change_operation: Mapped[str | None] = mapped_column(String(64))
