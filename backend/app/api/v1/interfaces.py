"""Interface inventory, live detail and guarded admin/description/speed changes."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import Ctx, Services, require
from app.core.exceptions import Forbidden, NotFound
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.schemas.common import ERROR_RESPONSES
from app.schemas.l2 import (
    AdminStateRequest,
    ChangeResponse,
    DescriptionRequest,
    InterfaceOut,
    SpeedDuplexRequest,
)

router = APIRouter(
    prefix="/devices/{device_id}/interfaces", tags=["interfaces"], responses=ERROR_RESPONSES
)
Read = Annotated[Principal, Depends(require(Permission.DEVICES_READ))]
Show = Annotated[Principal, Depends(require(Permission.DEVICE_SHOW))]
Configure = Annotated[Principal, Depends(require(Permission.DEVICE_CONFIGURE))]


@router.get("", response_model=list[InterfaceOut], summary="Interfaces (refresh=true polls live)")
async def list_interfaces(
    device_id: uuid.UUID, services: Services, actor: Read, refresh: bool = False
) -> list[InterfaceOut]:
    if refresh:
        if not actor.can(Permission.DEVICE_SHOW):
            raise Forbidden("refresh needs device:show")
        rows = await services.interfaces.refresh(actor, device_id)
    else:
        rows = await services.interfaces.cached(actor, device_id)
    return [InterfaceOut.model_validate(r) for r in rows]


@router.get("/{interface:path}", summary="Live 'display interface' detail incl. traffic/errors/CRC")
async def interface_detail(
    device_id: uuid.UUID, interface: str, services: Services, actor: Show
) -> dict[str, Any]:
    detail = await services.interfaces.detail(actor, device_id, interface)
    if detail is None:
        raise NotFound("interface not found")
    return asdict(detail)


@router.put(
    "/{interface:path}/admin-state",
    response_model=ChangeResponse,
    summary="shutdown / undo shutdown",
)
async def admin_state(
    device_id: uuid.UUID,
    interface: str,
    body: AdminStateRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    result = await services.interfaces.set_admin_state(
        actor, ctx, device_id, interface, body.enabled, body.options()
    )
    return ChangeResponse.of(result)


@router.put("/{interface:path}/description", response_model=ChangeResponse)
async def description(
    device_id: uuid.UUID,
    interface: str,
    body: DescriptionRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    result = await services.interfaces.set_description(
        actor, ctx, device_id, interface, body.description, body.options()
    )
    return ChangeResponse.of(result)


@router.put(
    "/{interface:path}/speed", response_model=ChangeResponse, summary="Speed/duplex/negotiation"
)
async def speed(
    device_id: uuid.UUID,
    interface: str,
    body: SpeedDuplexRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    result = await services.interfaces.set_speed_duplex(
        actor, ctx, device_id, interface, body.speed, body.duplex, body.auto, body.options()
    )
    return ChangeResponse.of(result)
