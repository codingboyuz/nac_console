"""NAC: endpoints, versioned policies, sessions (accounting) and RADIUS server definitions."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
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

from app.models.base import (
    Base,
    InetType,
    JsonType,
    TimestampMixin,
    UtcDateTime,
    UuidPkMixin,
    str_enum,
    utcnow,
)


class EndpointStatus(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    UNKNOWN = "UNKNOWN"
    QUARANTINED = "QUARANTINED"
    BLOCKED = "BLOCKED"
    PENDING = "PENDING"


class EndpointSource(StrEnum):
    DOT1X = "dot1x"
    MAB = "mab"
    MANUAL = "manual"


class Endpoint(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "endpoints"
    __table_args__ = (
        Index("ix_endpoints_status", "status"),
        Index("ix_endpoints_switch", "switch_id"),
    )

    mac: Mapped[str] = mapped_column(String(17), unique=True)  # canonical aa:bb:cc:dd:ee:ff
    ip: Mapped[str | None] = mapped_column(InetType())
    hostname: Mapped[str | None] = mapped_column(String(128))
    device_type: Mapped[str | None] = mapped_column(String(64))
    os: Mapped[str | None] = mapped_column(String(64))
    vendor: Mapped[str | None] = mapped_column(String(64))
    user: Mapped[str | None] = mapped_column(String(128))
    switch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="SET NULL")
    )
    interface: Mapped[str | None] = mapped_column(String(128))
    vlan: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[EndpointStatus] = mapped_column(
        str_enum(EndpointStatus, "endpoint_status"), default=EndpointStatus.UNKNOWN
    )
    authorization_state: Mapped[str | None] = mapped_column(String(64))  # last decision summary
    profile_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[EndpointSource] = mapped_column(
        str_enum(EndpointSource, "endpoint_source"), default=EndpointSource.MAB
    )
    registered: Mapped[bool] = mapped_column(Boolean, default=False)
    endpoint_group: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(255))
    first_seen: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    profile_reasons: Mapped[list[str]] = mapped_column(JsonType, default=list)


class NacPolicy(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "nac_policies"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    priority: Mapped[int] = mapped_column(BigInteger, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    condition: Mapped[dict[str, Any]] = mapped_column(JsonType)
    action: Mapped[dict[str, Any]] = mapped_column(JsonType)
    description: Mapped[str | None] = mapped_column(String(255))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class NacPolicyVersion(UuidPkMixin, Base):
    __tablename__ = "nac_policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_nac_policy_versions_pv"),)

    policy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("nac_policies.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)


class NacSession(UuidPkMixin, Base):
    __tablename__ = "nac_sessions"
    __table_args__ = (
        UniqueConstraint("acct_session_id", "nas_ip", name="uq_nac_sessions_acct"),
        Index("ix_nac_sessions_mac", "mac"),
        Index("ix_nac_sessions_status", "status"),
    )

    acct_session_id: Mapped[str] = mapped_column(String(128))
    mac: Mapped[str] = mapped_column(String(17))
    username: Mapped[str | None] = mapped_column(String(128))
    nas_ip: Mapped[str] = mapped_column(InetType())
    nas_port_id: Mapped[str | None] = mapped_column(String(128))
    framed_ip: Mapped[str | None] = mapped_column(InetType())
    vlan: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")  # ACTIVE|STOPPED
    policy_name: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    stopped_at: Mapped[datetime | None]
    input_octets: Mapped[int] = mapped_column(BigInteger, default=0)
    output_octets: Mapped[int] = mapped_column(BigInteger, default=0)
    terminate_cause: Mapped[str | None] = mapped_column(String(64))


class RadiusServer(UuidPkMixin, TimestampMixin, Base):
    """RADIUS server the switches use (FreeRADIUS). The shared secret is vault-encrypted."""

    __tablename__ = "radius_servers"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    host: Mapped[str] = mapped_column(InetType())
    auth_port: Mapped[int] = mapped_column(Integer, default=1812)
    acct_port: Mapped[int] = mapped_column(Integer, default=1813)
    coa_port: Mapped[int] = mapped_column(Integer, default=3799)
    shared_secret_ciphertext: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(32))
    timeout: Mapped[int] = mapped_column(Integer, default=5)
    retries: Mapped[int] = mapped_column(Integer, default=3)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
