"""Metric samples (monthly partitions on PostgreSQL), alert rules/alerts/deliveries,
maintenance windows and discovery proposals."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, InetType, JsonType, UtcDateTime, UuidPkMixin, utcnow


class MetricSample(Base):
    """Time series. PK includes ``ts`` because PostgreSQL partitions by RANGE (ts)."""

    __tablename__ = "metric_samples"
    __table_args__ = (
        Index("ix_metric_samples_device_ts", "device_id", "ts"),
        Index("ix_metric_samples_metric_ts", "metric", "ts"),
        {"postgresql_partition_by": "RANGE (ts)"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    ts: Mapped[datetime] = mapped_column(UtcDateTime(), primary_key=True, default=utcnow)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    metric: Mapped[str] = mapped_column(String(32))
    interface: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float)


class AlertRule(UuidPkMixin, Base):
    __tablename__ = "alert_rules"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    alert_type: Mapped[str] = mapped_column(String(32))  # high_cpu, interface_down, ...
    metric: Mapped[str | None] = mapped_column(String(32))
    operator: Mapped[str] = mapped_column(String(4), default="gt")
    threshold: Mapped[float | None] = mapped_column(Float)
    clear_threshold: Mapped[float | None] = mapped_column(Float)  # hysteresis
    for_seconds: Mapped[int] = mapped_column(Integer, default=0)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scope: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)  # device_id/group_id/site
    channels: Mapped[list[str]] = mapped_column(JsonType, default=list)
    flap_window_seconds: Mapped[int] = mapped_column(Integer, default=600)
    flap_max_transitions: Mapped[int] = mapped_column(Integer, default=4)


class Alert(UuidPkMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_state", "state"),
        Index("ix_alerts_dedupe", "dedupe_key", "state"),
        Index("ix_alerts_device", "device_id"),
    )

    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("alert_rules.id", ondelete="SET NULL")
    )
    alert_type: Mapped[str] = mapped_column(String(32))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    interface: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="FIRING")  # FIRING|ACKNOWLEDGED|RESOLVED
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Float)
    first_seen: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    acknowledged_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]


class AlertDelivery(UuidPkMixin, Base):
    __tablename__ = "alert_deliveries"

    alert_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("alerts.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING|SENT|RETRY|DEAD
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    delivered_at: Mapped[datetime | None]
    event: Mapped[str] = mapped_column(String(16), default="firing")  # firing|resolved


class MaintenanceWindow(UuidPkMixin, Base):
    __tablename__ = "maintenance_windows"

    scope: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class DiscoveryRun(UuidPkMixin, Base):
    __tablename__ = "discovery_runs"

    cidr: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    completed_at: Mapped[datetime | None]
    hosts_scanned: Mapped[int] = mapped_column(Integer, default=0)
    hosts_found: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    snmp_credential_ids: Mapped[list[str]] = mapped_column(JsonType, default=list)


class DiscoveredDevice(UuidPkMixin, Base):
    """A proposal only: an operator must explicitly adopt it into the inventory."""

    __tablename__ = "discovered_devices"
    __table_args__ = (UniqueConstraint("run_id", "ip", name="uq_discovered_devices_run_ip"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("discovery_runs.id", ondelete="CASCADE"), index=True
    )
    ip: Mapped[str] = mapped_column(InetType())
    reachable: Mapped[bool] = mapped_column(Boolean, default=False)
    sys_name: Mapped[str | None] = mapped_column(String(128))
    sys_descr: Mapped[str | None] = mapped_column(Text)
    sys_object_id: Mapped[str | None] = mapped_column(String(128))
    family: Mapped[str | None] = mapped_column(String(16))
    ssh_banner: Mapped[str | None] = mapped_column(String(255))
    is_huawei: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="PROPOSED")  # PROPOSED|ADOPTED|IGNORED
    adopted_device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
