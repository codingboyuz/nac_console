"""Routing: capability detection, table snapshots, guarded protocol changes with verification."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete

from app.core.database import Database
from app.core.exceptions import CommandRejected, UnsupportedCapability, ValidationFailed
from app.domain.identity import Principal, RequestContext
from app.models.acl import Route
from app.models.device import Device
from app.network.builders import cli_routing
from app.network.builders.cli_routing import RoutingValidationError
from app.network.builders.plan import ConfigPlan
from app.network.parsers import RouteEntry, parse_routing_table
from app.network.parsers.routing_protocols import (
    BgpPeer,
    OspfNeighbor,
    parse_bgp_peers,
    parse_ospf_peers,
    parse_static_route_lines,
)
from app.network.ports import DeviceDriver
from app.repositories.uow import UnitOfWork
from app.services.change_service import ChangeOptions, ChangeResult, ChangeService
from app.services.collection_service import CollectionService
from app.services.device_service import DeviceService
from app.services.vlan_service import L2Base

# feature -> probe command (Unrecognized command => feature absent on this platform/licence)
PROBES: dict[str, str] = {
    "static": "display ip routing-table protocol static",
    "ospf": "display ospf brief",
    "bgp": "display bgp peer",
    "rip": "display rip",
    "vrrp": "display vrrp brief",
    "route_policy": "display route-policy",
}


@dataclass(frozen=True, slots=True)
class RouteDiff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RoutingChangeResult:
    change: ChangeResult
    diff: RouteDiff


def route_diff(before: list[RouteEntry], after: list[RouteEntry]) -> RouteDiff:
    def keys(rows: list[RouteEntry]) -> set[str]:
        return {f"{r.prefix} {r.protocol} via {r.nexthop}" for r in rows}

    b, a = keys(before), keys(after)
    return RouteDiff(sorted(a - b), sorted(b - a))


class RoutingService(L2Base):
    def __init__(
        self,
        db: Database,
        devices: DeviceService,
        collection: CollectionService,
        changes: ChangeService,
    ) -> None:
        super().__init__(devices, collection, changes)
        self._db = db

    async def capabilities(
        self, principal: Principal, device_id: uuid.UUID, *, refresh: bool = False
    ) -> dict[str, bool]:
        device = await self._device(principal, device_id)
        cached: dict[str, bool] | None = (device.capabilities or {}).get("routing")
        if cached and not refresh:
            return cached

        def probe(driver: DeviceDriver) -> dict[str, bool]:
            result: dict[str, bool] = {}
            for feature, command in PROBES.items():
                try:
                    driver.run_show(command)
                    result[feature] = True
                except CommandRejected:
                    result[feature] = False
            return result

        caps = await self._collection.run(device, probe, principal)
        await self._devices.update_facts(
            device.id, capabilities={**(device.capabilities or {}), "routing": caps}
        )
        return caps

    async def _require(self, principal: Principal, device: Device, feature: str) -> None:
        caps = await self.capabilities(principal, device.id)
        if not caps.get(feature):
            raise UnsupportedCapability(
                f"{feature} is not supported by {device.hostname} "
                f"({device.model or device.family.value}); "
                "detected from device probes"
            )

    async def table(self, principal: Principal, device_id: uuid.UUID) -> list[RouteEntry]:
        device = await self._device(principal, device_id)
        rows = parse_routing_table(
            await self._collection.show(device, "display ip routing-table", principal)
        )
        async with UnitOfWork(self._db) as uow:
            await uow.session.execute(delete(Route).where(Route.device_id == device.id))
            uow.session.add_all(
                Route(
                    device_id=device.id,
                    prefix=r.prefix,
                    protocol=r.protocol,
                    preference=r.preference,
                    cost=r.cost,
                    flags=r.flags,
                    nexthop=r.nexthop,
                    interface=r.interface,
                )
                for r in rows
            )
            await uow.commit()
        return rows

    async def ospf_neighbors(
        self, principal: Principal, device_id: uuid.UUID
    ) -> list[OspfNeighbor]:
        device = await self._device(principal, device_id)
        await self._require(principal, device, "ospf")
        return parse_ospf_peers(
            await self._collection.show(device, "display ospf peer brief", principal)
        )

    async def bgp_peers(self, principal: Principal, device_id: uuid.UUID) -> list[BgpPeer]:
        device = await self._device(principal, device_id)
        await self._require(principal, device, "bgp")
        return parse_bgp_peers(await self._collection.show(device, "display bgp peer", principal))

    async def _guarded(
        self,
        principal: Principal,
        ctx: RequestContext,
        device: Device,
        feature: str,
        build: Callable[[], ConfigPlan],
        options: ChangeOptions,
    ) -> RoutingChangeResult:
        await self._require(principal, device, feature)
        try:
            plan = build()
        except (RoutingValidationError, ValueError) as exc:
            raise ValidationFailed(str(exc)) from exc
        before = [] if options.dry_run else await self.table(principal, device.id)
        change = await self._apply(principal, ctx, device, plan, options)
        if options.dry_run or change.status != "applied":
            return RoutingChangeResult(change, RouteDiff())
        after = await self.table(principal, device.id)
        return RoutingChangeResult(change, route_diff(before, after))

    async def static_route(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        route: cli_routing.StaticRoute,
        *,
        remove: bool,
        verify: bool,
        options: ChangeOptions,
    ) -> RoutingChangeResult:
        device = await self._device(principal, device_id)
        existing = parse_static_route_lines(
            await self._collection.show(
                device, "display current-configuration | include ip route-static", principal
            )
        )
        return await self._guarded(
            principal,
            ctx,
            device,
            "static",
            lambda: cli_routing.static_route(route, existing, remove=remove, verify=verify),
            options,
        )

    async def protocol(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        feature: str,
        params: dict[str, Any],
        options: ChangeOptions,
    ) -> RoutingChangeResult:
        """OSPF / BGP / RIP / VRRP / route-policy via typed builders."""
        builders: dict[str, Callable[[], ConfigPlan]] = {
            "ospf": lambda: cli_routing.ospf(**params),
            "bgp": lambda: cli_routing.bgp(**params),
            "rip": lambda: cli_routing.rip(**params),
            "vrrp": lambda: cli_routing.vrrp(**params),
            "route_policy": lambda: cli_routing.route_policy(**params),
        }
        if feature not in builders:
            raise ValidationFailed(f"unknown routing feature {feature}")
        device = await self._device(principal, device_id)
        return await self._guarded(principal, ctx, device, feature, builders[feature], options)
