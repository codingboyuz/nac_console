"""Routing endpoints: capabilities, tables, neighbors and guarded protocol changes."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import Ctx, Services, require
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.schemas.acl_routing import (
    BgpRequest,
    OspfRequest,
    RipRequest,
    RouteOut,
    RoutePolicyRequest,
    RoutingChangeResponse,
    StaticRouteRequest,
    VrrpRequest,
)
from app.schemas.common import ERROR_RESPONSES
from app.schemas.l2 import ChangeRequest, ChangeResponse
from app.services.routing_service import RoutingChangeResult

router = APIRouter(
    prefix="/devices/{device_id}/routing", tags=["routing"], responses=ERROR_RESPONSES
)
Show = Annotated[Principal, Depends(require(Permission.DEVICE_SHOW))]
Configure = Annotated[Principal, Depends(require(Permission.DEVICE_CONFIGURE))]


def _out(result: RoutingChangeResult) -> RoutingChangeResponse:
    base = ChangeResponse.of(result.change).model_dump()
    return RoutingChangeResponse(
        **base, routes_added=result.diff.added, routes_removed=result.diff.removed
    )


@router.get("/capabilities", summary="Detected routing features (probe-based, cached)")
async def capabilities(
    device_id: uuid.UUID, services: Services, actor: Show, refresh: bool = False
) -> dict[str, bool]:
    return await services.routing.capabilities(actor, device_id, refresh=refresh)


@router.get("/table", response_model=list[RouteOut], summary="display ip routing-table")
async def table(device_id: uuid.UUID, services: Services, actor: Show) -> list[RouteOut]:
    return [
        RouteOut.model_validate(r, from_attributes=True)
        for r in await services.routing.table(actor, device_id)
    ]


@router.get("/ospf/neighbors")
async def ospf_neighbors(
    device_id: uuid.UUID, services: Services, actor: Show
) -> list[dict[str, Any]]:
    return [
        asdict(n) | {"full": n.is_full}
        for n in await services.routing.ospf_neighbors(actor, device_id)
    ]


@router.get("/bgp/peers")
async def bgp_peers(device_id: uuid.UUID, services: Services, actor: Show) -> list[dict[str, Any]]:
    return [
        asdict(p) | {"established": p.established}
        for p in await services.routing.bgp_peers(actor, device_id)
    ]


@router.post(
    "/static", response_model=RoutingChangeResponse, summary="Add/remove a static or default route"
)
async def static(
    device_id: uuid.UUID, body: StaticRouteRequest, services: Services, ctx: Ctx, actor: Configure
) -> RoutingChangeResponse:
    result = await services.routing.static_route(
        actor,
        ctx,
        device_id,
        body.to_domain(),
        remove=body.remove,
        verify=body.verify,
        options=body.options(),
    )
    return _out(result)


async def _protocol(
    services: Services,
    actor: Principal,
    ctx: Any,
    device_id: uuid.UUID,
    feature: str,
    params: dict[str, Any],
    body: ChangeRequest,
) -> RoutingChangeResponse:
    return _out(
        await services.routing.protocol(actor, ctx, device_id, feature, params, body.options())
    )


@router.post("/ospf", response_model=RoutingChangeResponse)
async def ospf(
    device_id: uuid.UUID, body: OspfRequest, services: Services, ctx: Ctx, actor: Configure
) -> RoutingChangeResponse:
    params = {
        "process": body.process,
        "router_id": str(body.router_id) if body.router_id else None,
        "area": body.area,
        "networks": body.networks,
        "silent_interfaces": body.silent_interfaces,
    }
    return await _protocol(services, actor, ctx, device_id, "ospf", params, body)


@router.post("/bgp", response_model=RoutingChangeResponse)
async def bgp(
    device_id: uuid.UUID, body: BgpRequest, services: Services, ctx: Ctx, actor: Configure
) -> RoutingChangeResponse:
    params = {
        "asn": body.asn,
        "router_id": str(body.router_id) if body.router_id else None,
        "peers": [(str(p.address), p.remote_as) for p in body.peers],
        "networks": body.networks,
    }
    return await _protocol(services, actor, ctx, device_id, "bgp", params, body)


@router.post("/rip", response_model=RoutingChangeResponse)
async def rip(
    device_id: uuid.UUID, body: RipRequest, services: Services, ctx: Ctx, actor: Configure
) -> RoutingChangeResponse:
    params = {"process": body.process, "version": body.version, "networks": body.networks}
    return await _protocol(services, actor, ctx, device_id, "rip", params, body)


@router.post("/vrrp", response_model=RoutingChangeResponse)
async def vrrp(
    device_id: uuid.UUID, body: VrrpRequest, services: Services, ctx: Ctx, actor: Configure
) -> RoutingChangeResponse:
    params = {
        "interface": body.interface,
        "vrid": body.vrid,
        "virtual_ip": str(body.virtual_ip),
        "priority": body.priority,
        "preempt_delay": body.preempt_delay,
    }
    return await _protocol(services, actor, ctx, device_id, "vrrp", params, body)


@router.post("/route-policy", response_model=RoutingChangeResponse)
async def route_policy(
    device_id: uuid.UUID, body: RoutePolicyRequest, services: Services, ctx: Ctx, actor: Configure
) -> RoutingChangeResponse:
    params = {
        "name": body.name,
        "node": body.node,
        "action": body.action,
        "prefix_list": body.prefix_list,
        "prefixes": [p.to_domain() for p in body.prefixes],
        "apply_cost": body.apply_cost,
    }
    return await _protocol(services, actor, ctx, device_id, "route_policy", params, body)
