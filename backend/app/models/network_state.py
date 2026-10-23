"""Collected L2/L3 state per device: interfaces, VLANs, MAC/ARP tables, DHCP bindings."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, InetType, UtcDateTime, UuidPkMixin, utcnow


def _device_fk() -> ForeignKey:
    return ForeignKey("devices.id", ondelete="CASCADE")


class Interface(UuidPkMixin, Base):
    __tablename__ = "interfaces"
    __table_args__ = (UniqueConstraint("device_id", "name", name="uq_interfaces_device_name"),)

    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, _device_fk(), index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(255))
    admin_up: Mapped[bool | None] = mapped_column(Boolean)
    oper_up: Mapped[bool | None] = mapped_column(Boolean)
    speed_mbps: Mapped[int | None] = mapped_column(Integer)
    duplex: Mapped[str | None] = mapped_column(String(16))
    link_type: Mapped[str | None] = mapped_column(String(16))
    pvid: Mapped[int | None] = mapped_column(Integer)
    mac: Mapped[str | None] = mapped_column(String(17))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    crc_errors: Mapped[int | None] = mapped_column(Integer)
    input_errors: Mapped[int | None] = mapped_column(Integer)
    output_errors: Mapped[int | None] = mapped_column(Integer)
    is_uplink: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, onupdate=utcnow)


class Vlan(UuidPkMixin, Base):
    __tablename__ = "vlans"
    __table_args__ = (
        UniqueConstraint("device_id", "vlan_id", name="uq_vlans_device_vlan"),
        Index("ix_vlans_vlan_id", "vlan_id"),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, _device_fk(), index=True)
    vlan_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(16))
    tagged_ports: Mapped[str | None] = mapped_column(String(4000))
    untagged_ports: Mapped[str | None] = mapped_column(String(4000))
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, onupdate=utcnow)


class MacEntry(UuidPkMixin, Base):
    """Current MAC table row; ``first_seen``/``last_seen`` give history across collections."""

    __tablename__ = "mac_entries"
    __table_args__ = (
        UniqueConstraint("device_id", "mac", "vlan", name="uq_mac_entries_device_mac_vlan"),
        Index("ix_mac_entries_mac", "mac"),
        Index("ix_mac_entries_device_seen", "device_id", "last_seen"),
        Index("ix_mac_entries_vlan", "vlan"),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, _device_fk())
    mac: Mapped[str] = mapped_column(String(17))
    vlan: Mapped[int | None] = mapped_column(Integer)
    interface: Mapped[str | None] = mapped_column(String(64))
    entry_type: Mapped[str] = mapped_column(String(16))
    is_edge: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    moves: Mapped[int] = mapped_column(Integer, default=0)


class MacMove(UuidPkMixin, Base):
    """A MAC seen on a different port than in the previous collection (flapping evidence)."""

    __tablename__ = "mac_moves"
    __table_args__ = (Index("ix_mac_moves_mac_ts", "mac", "timestamp"),)

    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, _device_fk(), index=True)
    mac: Mapped[str] = mapped_column(String(17))
    vlan: Mapped[int | None] = mapped_column(Integer)
    from_interface: Mapped[str | None] = mapped_column(String(64))
    to_interface: Mapped[str | None] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)


class ArpEntry(UuidPkMixin, Base):
    __tablename__ = "arp_entries"
    __table_args__ = (
        UniqueConstraint("device_id", "ip", "vpn", name="uq_arp_entries_device_ip_vpn"),
        Index("ix_arp_entries_mac", "mac"),
        Index("ix_arp_entries_ip", "ip"),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, _device_fk(), index=True)
    ip: Mapped[str] = mapped_column(InetType())
    mac: Mapped[str] = mapped_column(String(17))
    vlan: Mapped[int | None] = mapped_column(Integer)
    interface: Mapped[str | None] = mapped_column(String(64))
    entry_type: Mapped[str] = mapped_column(String(16))
    vpn: Mapped[str] = mapped_column(String(64), default="")
    first_seen: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)


class DhcpBinding(UuidPkMixin, Base):
    __tablename__ = "dhcp_bindings"
    __table_args__ = (
        UniqueConstraint("device_id", "ip", name="uq_dhcp_bindings_device_ip"),
        Index("ix_dhcp_bindings_mac", "mac"),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, _device_fk(), index=True)
    ip: Mapped[str] = mapped_column(InetType())
    mac: Mapped[str] = mapped_column(String(17))
    vlan: Mapped[int | None] = mapped_column(Integer)
    interface: Mapped[str | None] = mapped_column(String(64))
    lease_expire: Mapped[str | None] = mapped_column(String(32))
    last_seen: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
