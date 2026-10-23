"""Deployed ACL definitions per device and collected routing tables."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.acl import AclKind
from app.models.base import (
    Base,
    JsonType,
    TimestampMixin,
    UtcDateTime,
    UuidPkMixin,
    str_enum,
    utcnow,
)


class Acl(UuidPkMixin, TimestampMixin, Base):
    """Last successfully deployed definition (desired state) of one ACL on one device."""

    __tablename__ = "acls"
    __table_args__ = (
        UniqueConstraint("device_id", "identifier", name="uq_acls_device_identifier"),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    identifier: Mapped[str] = mapped_column(String(64))  # number or name
    number: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[AclKind] = mapped_column(str_enum(AclKind, "acl_kind"))
    ipv6: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(String(127))
    # [{"interface": "GigabitEthernet0/0/1" | None, "direction": "inbound"}]
    applied_to: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)

    rules: Mapped[list[AclRule]] = relationship(
        back_populates="acl",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="AclRule.rule_id",
    )


class AclRule(UuidPkMixin, Base):
    __tablename__ = "acl_rules"
    __table_args__ = (UniqueConstraint("acl_id", "rule_id", name="uq_acl_rules_acl_rule"),)

    acl_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("acls.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(8))
    protocol: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(64))
    destination: Mapped[str | None] = mapped_column(String(64))
    source_port: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    destination_port: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    source_mac: Mapped[str | None] = mapped_column(String(17))
    destination_mac: Mapped[str | None] = mapped_column(String(17))
    vlan: Mapped[int | None] = mapped_column(Integer)
    log: Mapped[bool] = mapped_column(Boolean, default=False)

    acl: Mapped[Acl] = relationship(back_populates="rules", lazy="raise")


class Route(UuidPkMixin, Base):
    """Collected routing table rows (snapshot per device; replaced on each collection)."""

    __tablename__ = "routes"
    __table_args__ = (Index("ix_routes_device_prefix", "device_id", "prefix"),)

    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    prefix: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(16))
    preference: Mapped[int] = mapped_column(Integer)
    cost: Mapped[int] = mapped_column(Integer)
    flags: Mapped[str] = mapped_column(String(8))
    nexthop: Mapped[str] = mapped_column(String(64))
    interface: Mapped[str | None] = mapped_column(String(64))
    collected_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
