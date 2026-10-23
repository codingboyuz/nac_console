"""Device inventory, groups, encrypted credentials and pinned SSH host keys."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.devices import (
    CredentialKind,
    DeviceFamily,
    DeviceProtocol,
    DeviceStatus,
    HostKeyStatus,
)
from app.models.base import Base, InetType, JsonType, TimestampMixin, UuidPkMixin, str_enum


class Credential(UuidPkMixin, TimestampMixin, Base):
    """Secrets are only in ``secret_ciphertext`` (AES-256-GCM, AAD bound to the row id)."""

    __tablename__ = "credentials"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[CredentialKind] = mapped_column(str_enum(CredentialKind, "credential_kind"))
    username: Mapped[str | None] = mapped_column(String(128))
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    # Non-secret metadata, e.g. SNMPv3 auth/priv protocol names, which fields are set.
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class DeviceGroup(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "device_groups"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    site: Mapped[str | None] = mapped_column(String(128))


class Device(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (
        Index("ix_devices_status", "status"),
        Index("ix_devices_group_id", "group_id"),
    )

    hostname: Mapped[str] = mapped_column(String(128), index=True)
    mgmt_ip: Mapped[str] = mapped_column(InetType(), unique=True)
    vendor: Mapped[str] = mapped_column(String(32), default="Huawei")
    model: Mapped[str | None] = mapped_column(String(64))
    family: Mapped[DeviceFamily] = mapped_column(
        str_enum(DeviceFamily, "device_family"), default=DeviceFamily.UNKNOWN
    )
    vrp_version: Mapped[str | None] = mapped_column(String(64))
    protocol: Mapped[DeviceProtocol] = mapped_column(str_enum(DeviceProtocol, "device_protocol"))
    port: Mapped[int] = mapped_column(Integer)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("credentials.id", ondelete="RESTRICT")
    )
    snmp_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("credentials.id", ondelete="RESTRICT")
    )
    requires_super: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[DeviceStatus] = mapped_column(
        str_enum(DeviceStatus, "device_status"), default=DeviceStatus.UNKNOWN
    )
    last_seen: Mapped[datetime | None]
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("device_groups.id", ondelete="SET NULL")
    )
    tags: Mapped[list[str]] = mapped_column(JsonType, default=list)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    allow_insecure_protocol: Mapped[bool] = mapped_column(Boolean, default=False)
    # Self-lockout guard inputs.
    mgmt_interface: Mapped[str | None] = mapped_column(String(64))
    mgmt_vlan: Mapped[int | None] = mapped_column(Integer)
    uplink_interfaces: Mapped[list[str]] = mapped_column(JsonType, default=list)
    site: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(255))

    group: Mapped[DeviceGroup | None] = relationship(lazy="raise")


class SshHostKey(UuidPkMixin, TimestampMixin, Base):
    """Trust-on-first-use host key store. Mismatches create PENDING keys for approval."""

    __tablename__ = "ssh_host_keys"
    __table_args__ = (UniqueConstraint("device_id", "fingerprint", name="uq_ssh_host_keys_fp"),)

    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    key_type: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(128))  # SHA256:<base64>
    public_key: Mapped[str] = mapped_column(Text)  # base64 of the wire-format key
    status: Mapped[HostKeyStatus] = mapped_column(str_enum(HostKeyStatus, "host_key_status"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None]
