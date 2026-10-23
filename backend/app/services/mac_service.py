"""MAC table search/locate, static and blackhole entries, aging info.

MAC blackholing is a convenience only - it is NOT a security control (MACs are trivially
spoofed). Use 802.1X / MAB through the NAC engine for access control.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.database import Database
from app.domain.identity import Principal, RequestContext
from app.domain.mac import normalize_mac
from app.models.network_state import MacEntry
from app.network.builders import cli_l2
from app.network.parsers import parse_mac_aging
from app.repositories.devices import DeviceRepository
from app.repositories.uow import UnitOfWork
from app.services.change_service import ChangeOptions, ChangeResult, ChangeService
from app.services.collection_service import CollectionService
from app.services.device_service import DeviceService
from app.services.vlan_service import L2Base

MAC_BLOCK_NOTICE = (
    "MAC blackholing is a convenience, not a security control: MAC addresses can be spoofed. "
    "Use 802.1X (or MAB with profiling) via the NAC policies for access control."
)


@dataclass(frozen=True, slots=True)
class MacLocation:
    mac: str
    device_id: uuid.UUID
    hostname: str
    interface: str | None
    vlan: int | None
    is_edge: bool
    last_seen: str


class MacService(L2Base):
    def __init__(
        self,
        db: Database,
        devices: DeviceService,
        collection: CollectionService,
        changes: ChangeService,
    ) -> None:
        super().__init__(devices, collection, changes)
        self._db = db

    async def search(
        self,
        principal: Principal,
        *,
        mac: str | None = None,
        vlan: int | None = None,
        interface: str | None = None,
        device_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[MacEntry]:
        stmt = select(MacEntry)
        if mac:
            stmt = stmt.where(MacEntry.mac == normalize_mac(mac))
        if vlan is not None:
            stmt = stmt.where(MacEntry.vlan == vlan)
        if interface:
            stmt = stmt.where(MacEntry.interface == interface)
        if device_id:
            stmt = stmt.where(MacEntry.device_id == device_id)
        async with UnitOfWork(self._db) as uow:
            rows = list(
                (
                    await uow.session.scalars(stmt.order_by(MacEntry.last_seen.desc()).limit(limit))
                ).all()
            )
            devices = {
                d.id: d for d in await uow.repo(DeviceRepository).many([r.device_id for r in rows])
            }
        return [
            r
            for r in rows
            if r.device_id in devices and principal.in_scope(devices[r.device_id].group_id)
        ]

    async def locate(self, principal: Principal, mac: str) -> list[MacLocation]:
        """Where is this MAC plugged in? Edge-port sightings first (uplinks only transit)."""
        rows = await self.search(principal, mac=mac)
        async with UnitOfWork(self._db) as uow:
            devices = {
                d.id: d for d in await uow.repo(DeviceRepository).many([r.device_id for r in rows])
            }
        locations = [
            MacLocation(
                r.mac,
                r.device_id,
                devices[r.device_id].hostname,
                r.interface,
                r.vlan,
                r.is_edge,
                r.last_seen.isoformat(),
            )
            for r in rows
        ]
        return sorted(locations, key=lambda loc: (not loc.is_edge, loc.hostname))

    async def collect(self, principal: Principal, device_id: uuid.UUID) -> int:
        device = await self._device(principal, device_id)
        return await self._collection.collect_mac(device, principal)

    async def aging(self, principal: Principal, device_id: uuid.UUID) -> int | None:
        device = await self._device(principal, device_id)
        return parse_mac_aging(
            await self._collection.show(device, "display mac-address aging-time", principal)
        )

    async def static(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        mac: str,
        vlan: int,
        interface: str | None,
        *,
        remove: bool,
        blackhole: bool,
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        plan = cli_l2.static_mac(
            normalize_mac(mac), vlan, interface, remove=remove, blackhole=blackhole
        )
        return await self._apply(principal, ctx, device, plan, options)
