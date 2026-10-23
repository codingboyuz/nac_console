"""ACL endpoints: live view, managed definitions, rule CRUD, interface/global application."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Ctx, Services, require
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.network.builders.cli_acl import AclDefinition
from app.schemas.acl_routing import (
    AclBindRequest,
    AclDeployRequest,
    AclOut,
    AclRuleRequest,
    LiveAclOut,
)
from app.schemas.common import ERROR_RESPONSES
from app.schemas.l2 import ChangeRequest, ChangeResponse

router = APIRouter(prefix="/devices/{device_id}/acls", tags=["acl"], responses=ERROR_RESPONSES)
Read = Annotated[Principal, Depends(require(Permission.DEVICES_READ))]
Show = Annotated[Principal, Depends(require(Permission.DEVICE_SHOW))]
Configure = Annotated[Principal, Depends(require(Permission.DEVICE_CONFIGURE))]


@router.get("", response_model=list[AclOut], summary="ACLs managed by the platform")
async def stored(device_id: uuid.UUID, services: Services, actor: Read) -> list[AclOut]:
    return [AclOut.model_validate(a) for a in await services.acls.stored(actor, device_id)]


@router.get("/live", response_model=list[LiveAclOut], summary="'display acl all' from the device")
async def live(device_id: uuid.UUID, services: Services, actor: Show) -> list[LiveAclOut]:
    return [
        LiveAclOut.model_validate(a, from_attributes=True)
        for a in await services.acls.live(actor, device_id)
    ]


@router.put("", response_model=ChangeResponse, summary="Create/replace an ACL (rule-level diff)")
async def deploy(
    device_id: uuid.UUID, body: AclDeployRequest, services: Services, ctx: Ctx, actor: Configure
) -> ChangeResponse:
    rules = []
    for index, rule in enumerate(body.rules, start=1):
        spec = rule.to_domain()
        rules.append(spec if spec.rule_id else replace(spec, rule_id=index * 5))
    definition = AclDefinition(
        body.kind, body.number, body.name, body.ipv6, body.description, tuple(rules)
    )
    return ChangeResponse.of(
        await services.acls.deploy(actor, ctx, device_id, definition, body.options())
    )


@router.post("/{identifier}/rules", response_model=ChangeResponse, summary="Add or modify one rule")
async def upsert_rule(
    device_id: uuid.UUID,
    identifier: str,
    body: AclRuleRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    result = await services.acls.upsert_rule(
        actor, ctx, device_id, identifier, body.rule.to_domain(), options=body.options()
    )
    return ChangeResponse.of(result)


@router.post("/{identifier}/rules/{rule_id}/delete", response_model=ChangeResponse)
async def delete_rule(
    device_id: uuid.UUID,
    identifier: str,
    rule_id: int,
    body: ChangeRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    result = await services.acls.upsert_rule(
        actor, ctx, device_id, identifier, None, remove_rule_id=rule_id, options=body.options()
    )
    return ChangeResponse.of(result)


@router.post("/{identifier}/delete", response_model=ChangeResponse, summary="Delete an ACL")
async def delete_acl(
    device_id: uuid.UUID,
    identifier: str,
    body: ChangeRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    return ChangeResponse.of(
        await services.acls.delete(actor, ctx, device_id, identifier, body.options())
    )


@router.post(
    "/{identifier}/apply",
    response_model=ChangeResponse,
    summary="traffic-filter on interface/global",
)
async def apply(
    device_id: uuid.UUID,
    identifier: str,
    body: AclBindRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    result = await services.acls.bind(
        actor,
        ctx,
        device_id,
        identifier,
        body.direction,
        body.interface,
        remove=False,
        options=body.options(),
    )
    return ChangeResponse.of(result)


@router.post(
    "/{identifier}/unapply", response_model=ChangeResponse, summary="Remove a traffic-filter"
)
async def unapply(
    device_id: uuid.UUID,
    identifier: str,
    body: AclBindRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    result = await services.acls.bind(
        actor,
        ctx,
        device_id,
        identifier,
        body.direction,
        body.interface,
        remove=True,
        options=body.options(),
    )
    return ChangeResponse.of(result)
