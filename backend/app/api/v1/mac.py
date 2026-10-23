"""MAC search/locate, collection, static entries and the block/allow convenience."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import Ctx, Services, require
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.schemas.common import ERROR_RESPONSES
from app.schemas.l2 import ChangeResponse, MacBlockResponse, MacEntryOut, MacLocationOut, MacRequest
from app.services.mac_service import MAC_BLOCK_NOTICE

router = APIRouter(prefix="/mac-addresses", tags=["mac"], responses=ERROR_RESPONSES)
Read = Annotated[Principal, Depends(require(Permission.DEVICES_READ))]
Show = Annotated[Principal, Depends(require(Permission.DEVICE_SHOW))]
Configure = Annotated[Principal, Depends(require(Permission.DEVICE_CONFIGURE))]


@router.get("", response_model=list[MacEntryOut], summary="Search collected MAC tables")
async def search(
    services: Services,
    actor: Read,
    mac: Annotated[str | None, Query(max_length=32)] = None,
    vlan: Annotated[int | None, Query(ge=1, le=4094)] = None,
    interface: Annotated[str | None, Query(max_length=64)] = None,
    device_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[MacEntryOut]:
    rows = await services.macs.search(
        actor, mac=mac, vlan=vlan, interface=interface, device_id=device_id, limit=limit
    )
    return [MacEntryOut.model_validate(r) for r in rows]


@router.get("/{mac}/locate", response_model=list[MacLocationOut], summary="Locate the switch port")
async def locate(mac: str, services: Services, actor: Read) -> list[MacLocationOut]:
    return [
        MacLocationOut.model_validate(loc, from_attributes=True)
        for loc in await services.macs.locate(actor, mac)
    ]


@router.post("/collect/{device_id}", summary="Collect the MAC table of one device now")
async def collect(device_id: uuid.UUID, services: Services, actor: Show) -> dict[str, int]:
    return {"entries": await services.macs.collect(actor, device_id)}


@router.get("/aging/{device_id}", summary="MAC aging time (seconds)")
async def aging(device_id: uuid.UUID, services: Services, actor: Show) -> dict[str, int | None]:
    return {"aging_seconds": await services.macs.aging(actor, device_id)}


@router.post("/static", response_model=ChangeResponse, summary="Add a static MAC entry")
async def add_static(
    body: MacRequest, services: Services, ctx: Ctx, actor: Configure
) -> ChangeResponse:
    result = await services.macs.static(
        actor,
        ctx,
        body.device_id,
        body.mac,
        body.vlan,
        body.interface,
        remove=False,
        blackhole=False,
        options=body.options(),
    )
    return ChangeResponse.of(result)


@router.post("/static/remove", response_model=ChangeResponse, summary="Remove a static MAC entry")
async def remove_static(
    body: MacRequest, services: Services, ctx: Ctx, actor: Configure
) -> ChangeResponse:
    result = await services.macs.static(
        actor,
        ctx,
        body.device_id,
        body.mac,
        body.vlan,
        body.interface,
        remove=True,
        blackhole=False,
        options=body.options(),
    )
    return ChangeResponse.of(result)


@router.post(
    "/block",
    response_model=MacBlockResponse,
    summary="Blackhole a MAC (convenience only - NOT a security control)",
    description=MAC_BLOCK_NOTICE,
)
async def block(
    body: MacRequest, services: Services, ctx: Ctx, actor: Configure
) -> MacBlockResponse:
    result = await services.macs.static(
        actor,
        ctx,
        body.device_id,
        body.mac,
        body.vlan,
        None,
        remove=False,
        blackhole=True,
        options=body.options(),
    )
    return MacBlockResponse(**ChangeResponse.of(result).model_dump(), notice=MAC_BLOCK_NOTICE)


@router.post("/allow", response_model=MacBlockResponse, summary="Remove a MAC blackhole entry")
async def allow(
    body: MacRequest, services: Services, ctx: Ctx, actor: Configure
) -> MacBlockResponse:
    result = await services.macs.static(
        actor,
        ctx,
        body.device_id,
        body.mac,
        body.vlan,
        None,
        remove=True,
        blackhole=True,
        options=body.options(),
    )
    return MacBlockResponse(**ChangeResponse.of(result).model_dump(), notice=MAC_BLOCK_NOTICE)
