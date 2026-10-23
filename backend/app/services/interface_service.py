"""Interface features: inventory, live detail, admin state, description, speed/duplex."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.database import Database
from app.domain.identity import Principal, RequestContext
from app.domain.interfaces import normalize_interface
from app.models.network_state import Interface
from app.network.builders import cli_l2
from app.network.parsers import InterfaceDetail, parse_interface_detail
from app.repositories.uow import UnitOfWork
from app.services.change_service import ChangeOptions, ChangeResult, ChangeService
from app.services.collection_service import CollectionService
from app.services.device_service import DeviceService
from app.services.vlan_service import L2Base


class InterfaceService(L2Base):
    def __init__(
        self,
        db: Database,
        devices: DeviceService,
        collection: CollectionService,
        changes: ChangeService,
    ) -> None:
        super().__init__(devices, collection, changes)
        self._db = db

    async def refresh(self, principal: Principal, device_id: uuid.UUID) -> list[Interface]:
        device = await self._device(principal, device_id)
        return await self._collection.collect_interfaces(device, principal)

    async def cached(self, principal: Principal, device_id: uuid.UUID) -> list[Interface]:
        await self._device(principal, device_id)
        async with UnitOfWork(self._db) as uow:
            rows = await uow.session.scalars(
                select(Interface).where(Interface.device_id == device_id).order_by(Interface.name)
            )
            return list(rows.all())

    async def detail(
        self, principal: Principal, device_id: uuid.UUID, interface: str
    ) -> InterfaceDetail | None:
        device = await self._device(principal, device_id)
        name = normalize_interface(interface)
        out = await self._collection.show(device, f"display interface {name}", principal)
        found = parse_interface_detail(out)
        return found[0] if found else None

    async def set_admin_state(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        interface: str,
        enabled: bool,
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        current = await self.interface_config(principal, device, interface)
        plan = cli_l2.set_admin_state(interface, enabled, current)
        return await self._apply(principal, ctx, device, plan, options)

    async def set_description(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        interface: str,
        description: str | None,
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        current = await self.interface_config(principal, device, interface)
        plan = cli_l2.set_description(interface, description, current)
        return await self._apply(principal, ctx, device, plan, options)

    async def set_speed_duplex(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        interface: str,
        speed: int | None,
        duplex: str | None,
        auto: bool,
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        plan = cli_l2.set_speed_duplex(interface, speed, duplex, auto)
        return await self._apply(principal, ctx, device, plan, options)
