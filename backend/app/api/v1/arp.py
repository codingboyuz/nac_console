"""ARP lookups, DHCP snooping bindings and L2/L3 anomaly reports."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import Ctx, Services, require
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.schemas.common import ERROR_RESPONSES
from app.schemas.l2 import ArpOut, ChangeResponse, DhcpBindingOut, DhcpSnoopingRequest, FindingOut

router = APIRouter(tags=["arp"], responses=ERROR_RESPONSES)
dhcp_router = APIRouter(prefix="/dhcp", tags=["dhcp"], responses=ERROR_RESPONSES)
Read = Annotated[Principal, Depends(require(Permission.DEVICES_READ))]
Show = Annotated[Principal, Depends(require(Permission.DEVICE_SHOW))]
Configure = Annotated[Principal, Depends(require(Permission.DEVICE_CONFIGURE))]


@router.get("/arp", response_model=list[ArpOut], summary="IP->MAC / MAC->IP over collected ARP")
async def search_arp(
    services: Services,
    actor: Read,
    ip: Annotated[str | None, Query(max_length=45)] = None,
    mac: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[ArpOut]:
    return [
        ArpOut.model_validate(r)
        for r in await services.arp.search(actor, ip=ip, mac=mac, limit=limit)
    ]


@router.post("/arp/collect/{device_id}", summary="Collect ARP + DHCP snooping bindings now")
async def collect(device_id: uuid.UUID, services: Services, actor: Show) -> dict[str, int]:
    return await services.arp.collect(actor, device_id)


@router.get(
    "/anomalies",
    response_model=list[FindingOut],
    summary="Duplicate IP, MAC flapping, ARP anomalies, binding/ARP mismatch",
)
async def anomalies(services: Services, actor: Read) -> list[FindingOut]:
    return [
        FindingOut.model_validate(f, from_attributes=True)
        for f in await services.arp.anomalies(actor)
    ]


@dhcp_router.get("/bindings", response_model=list[DhcpBindingOut])
async def bindings(
    services: Services, actor: Read, device_id: uuid.UUID | None = None
) -> list[DhcpBindingOut]:
    return [DhcpBindingOut.model_validate(b) for b in await services.arp.bindings(actor, device_id)]


@dhcp_router.get(
    "/trust-audit/{device_id}",
    response_model=list[FindingOut],
    summary="Unexpected DHCP server exposure: trusted non-uplink ports",
)
async def trust_audit(device_id: uuid.UUID, services: Services, actor: Show) -> list[FindingOut]:
    findings = await services.arp.dhcp_trust_audit(actor, device_id)
    return [FindingOut.model_validate(f, from_attributes=True) for f in findings]


@dhcp_router.put("/snooping/{device_id}", response_model=ChangeResponse)
async def configure_snooping(
    device_id: uuid.UUID, body: DhcpSnoopingRequest, services: Services, ctx: Ctx, actor: Configure
) -> ChangeResponse:
    vlans = [int(v) for v in body.vlans.split(",")] if body.vlans else []
    result = await services.arp.configure_snooping(
        actor, ctx, device_id, vlans, body.trusted_interfaces, body.options()
    )
    return ChangeResponse.of(result)
