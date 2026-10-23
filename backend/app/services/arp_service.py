"""ARP and DHCP snooping: lookups, collection and anomaly analysis across devices."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.database import Database
from app.domain import l2_analysis as analysis
from app.domain.commands.classifier import canonical_interface
from app.domain.identity import Principal, RequestContext
from app.domain.mac import normalize_mac
from app.models.device import Device
from app.models.network_state import ArpEntry, DhcpBinding, MacEntry, MacMove
from app.network.builders import cli_l2
from app.repositories.devices import DeviceRepository
from app.repositories.uow import UnitOfWork
from app.services.change_service import ChangeOptions, ChangeResult, ChangeService
from app.services.collection_service import CollectionService
from app.services.device_service import DeviceService
from app.services.vlan_service import L2Base


class ArpService(L2Base):
    def __init__(
        self,
        db: Database,
        devices: DeviceService,
        collection: CollectionService,
        changes: ChangeService,
    ) -> None:
        super().__init__(devices, collection, changes)
        self._db = db

    async def _scoped_devices(self, principal: Principal) -> dict[uuid.UUID, Device]:
        async with UnitOfWork(self._db) as uow:
            rows = await uow.session.scalars(
                uow.repo(DeviceRepository).scoped_query(principal.group_scope)
            )
            return {d.id: d for d in rows.all()}

    async def collect(self, principal: Principal, device_id: uuid.UUID) -> dict[str, int]:
        device = await self._device(principal, device_id)
        return {
            "arp": await self._collection.collect_arp(device, principal),
            "dhcp_bindings": await self._collection.collect_dhcp(device, principal),
        }

    async def search(
        self,
        principal: Principal,
        *,
        ip: str | None = None,
        mac: str | None = None,
        limit: int = 200,
    ) -> list[ArpEntry]:
        """IP -> MAC and MAC -> IP lookups over collected ARP tables."""
        devices = await self._scoped_devices(principal)
        stmt = select(ArpEntry).where(ArpEntry.device_id.in_(devices))
        if ip:
            stmt = stmt.where(ArpEntry.ip == ip)
        if mac:
            stmt = stmt.where(ArpEntry.mac == normalize_mac(mac))
        async with UnitOfWork(self._db) as uow:
            return list((await uow.session.scalars(stmt.order_by(ArpEntry.ip).limit(limit))).all())

    async def bindings(
        self, principal: Principal, device_id: uuid.UUID | None = None
    ) -> list[DhcpBinding]:
        devices = await self._scoped_devices(principal)
        ids = [device_id] if device_id and device_id in devices else list(devices)
        async with UnitOfWork(self._db) as uow:
            rows = await uow.session.scalars(
                select(DhcpBinding).where(DhcpBinding.device_id.in_(ids))
            )
            return list(rows.all())

    async def anomalies(
        self, principal: Principal, *, known_macs: set[str] | None = None
    ) -> list[analysis.Finding]:
        devices = await self._scoped_devices(principal)
        ids = list(devices)
        since = datetime.now(UTC) - timedelta(hours=1)
        async with UnitOfWork(self._db) as uow:
            s = uow.session
            arp = [
                analysis.ArpObservation(str(r.device_id), r.ip, r.mac, r.entry_type)
                for r in await s.scalars(select(ArpEntry).where(ArpEntry.device_id.in_(ids)))
            ]
            binds = [
                analysis.BindingObservation(str(r.device_id), r.ip, r.mac, r.interface)
                for r in await s.scalars(select(DhcpBinding).where(DhcpBinding.device_id.in_(ids)))
            ]
            moves = [
                analysis.MoveObservation(r.mac, r.timestamp, r.from_interface, r.to_interface)
                for r in await s.scalars(
                    select(MacMove).where(MacMove.device_id.in_(ids), MacMove.timestamp >= since)
                )
            ]
            macs = [
                analysis.MacObservation(str(r.device_id), r.mac, r.vlan, r.interface, r.is_edge)
                for r in await s.scalars(select(MacEntry).where(MacEntry.device_id.in_(ids)))
            ]
        findings = (
            analysis.duplicate_ips(arp)
            + analysis.arp_anomalies(arp)
            + analysis.binding_mismatches(binds, arp)
            + analysis.duplicate_bindings(binds)
            + analysis.mac_flapping(moves)
        )
        if known_macs is not None:
            findings += analysis.unknown_macs(macs, known_macs)
        return findings

    async def dhcp_trust_audit(
        self, principal: Principal, device_id: uuid.UUID
    ) -> list[analysis.Finding]:
        """Rogue-DHCP exposure: 'dhcp snooping trusted' configured on non-uplink ports."""
        device = await self._device(principal, device_id)
        out = await self._collection.show(device, "display current-configuration", principal)
        trusted: list[str] = []
        current: str | None = None
        for line in out.splitlines():
            text = line.strip()
            if text.startswith("interface "):
                current = canonical_interface(text.split(" ", 1)[1])
            elif text == "dhcp snooping trusted" and current:
                trusted.append(current)
        return analysis.untrusted_dhcp_ports(trusted, device.uplink_interfaces or [])

    async def configure_snooping(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        vlans: list[int],
        trusted: list[str],
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        return await self._apply(
            principal, ctx, device, cli_l2.dhcp_snooping(vlans, trusted), options
        )
