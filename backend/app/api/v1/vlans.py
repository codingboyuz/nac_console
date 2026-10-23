"""VLAN endpoints (per device). Changes go through the guarded change executor."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Ctx, Services, require
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.schemas.common import ERROR_RESPONSES
from app.schemas.l2 import (
    ChangeResponse,
    PortVlanRequest,
    TrunkVlansRequest,
    VlanCreateRequest,
    VlanDeleteRequest,
    VlanOut,
    VlanRenameRequest,
)
from app.services import vlan_service

router = APIRouter(prefix="/devices/{device_id}", tags=["vlans"], responses=ERROR_RESPONSES)
Show = Annotated[Principal, Depends(require(Permission.DEVICE_SHOW))]
Configure = Annotated[Principal, Depends(require(Permission.DEVICE_CONFIGURE))]


def _ids(spec: str | None) -> list[int]:
    return [int(v) for v in spec.split(",")] if spec else []


@router.get("/vlans", response_model=list[VlanOut], summary="Live VLAN table (also cached)")
async def list_vlans(device_id: uuid.UUID, services: Services, actor: Show) -> list[VlanOut]:
    vlans = await services.vlans.list_live(actor, device_id)
    return [
        VlanOut(
            vlan_id=v.vlan_id,
            name=v.name,
            status=v.status,
            vlan_type=v.vlan_type,
            tagged_ports=list(v.tagged_ports),
            untagged_ports=list(v.untagged_ports),
        )
        for v in vlans
    ]


@router.post("/vlans", response_model=ChangeResponse, summary="Create VLANs (idempotent)")
async def create_vlans(
    device_id: uuid.UUID, body: VlanCreateRequest, services: Services, ctx: Ctx, actor: Configure
) -> ChangeResponse:
    wanted = {v.id: v.name for v in body.vlans}
    result = await services.vlans.create(actor, ctx, device_id, wanted, body.options())
    return ChangeResponse.of(result)


@router.post("/vlans/delete", response_model=ChangeResponse, summary="Delete VLANs")
async def delete_vlans(
    device_id: uuid.UUID, body: VlanDeleteRequest, services: Services, ctx: Ctx, actor: Configure
) -> ChangeResponse:
    result = await services.vlans.delete(actor, ctx, device_id, _ids(body.vlans), body.options())
    return ChangeResponse.of(result)


@router.patch("/vlans/{vlan_id}", response_model=ChangeResponse, summary="Rename a VLAN")
async def rename_vlan(
    device_id: uuid.UUID,
    vlan_id: int,
    body: VlanRenameRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    result = await services.vlans.rename(actor, ctx, device_id, vlan_id, body.name, body.options())
    return ChangeResponse.of(result)


@router.put(
    "/interfaces/{interface:path}/vlan",
    response_model=ChangeResponse,
    summary="Set port mode (access/trunk/hybrid), PVID and VLAN membership",
)
async def set_port_vlan(
    device_id: uuid.UUID,
    interface: str,
    body: PortVlanRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    req = vlan_service.PortVlanRequest(
        interface=interface,
        mode=body.mode,
        access_vlan=body.access_vlan,
        trunk_vlans=_ids(body.trunk_vlans),
        pvid=body.pvid,
        tagged=_ids(body.tagged),
        untagged=_ids(body.untagged),
    )
    return ChangeResponse.of(
        await services.vlans.set_port_vlan(actor, ctx, device_id, req, body.options())
    )


@router.post(
    "/interfaces/{interface:path}/trunk-vlans",
    response_model=ChangeResponse,
    summary="Add/remove VLANs on a trunk allowed list",
)
async def trunk_vlans(
    device_id: uuid.UUID,
    interface: str,
    body: TrunkVlansRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    result = await services.vlans.trunk_vlans(
        actor, ctx, device_id, interface, _ids(body.add), _ids(body.remove), body.options()
    )
    return ChangeResponse.of(result)
