"""NAC: endpoints, policies (versioned, simulate), sessions + CoA, RADIUS servers, provisioning."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Ctx, Paging, Services, require
from app.core.exceptions import NotFound
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.domain.mac import normalize_mac
from app.domain.nac.policy import RequestFacts
from app.models.nac import EndpointStatus
from app.repositories.base import ListQuery
from app.schemas.common import ERROR_RESPONSES, PageResponse, page_of
from app.schemas.nac import (
    CoaOut,
    DecisionOut,
    EndpointOut,
    EndpointUpdate,
    PolicyIn,
    PolicyOut,
    PolicyVersionOut,
    ProvisionIn,
    ProvisionPreviewOut,
    RadiusServerIn,
    RadiusServerOut,
    SessionOut,
    SimulateIn,
)
from app.services.nac_provisioning import ProvisionRequest

router = APIRouter(prefix="/nac", tags=["nac"], responses=ERROR_RESPONSES)
radius_router = APIRouter(prefix="/radius", tags=["radius"], responses=ERROR_RESPONSES)
Read = Annotated[Principal, Depends(require(Permission.NAC_READ))]
Write = Annotated[Principal, Depends(require(Permission.NAC_WRITE))]
PolicyWrite = Annotated[Principal, Depends(require(Permission.NAC_POLICY_WRITE))]


@router.get("/endpoints", response_model=PageResponse[EndpointOut])
async def endpoints(
    services: Services,
    actor: Read,
    paging: Paging,
    status_: Annotated[EndpointStatus | None, Query(alias="status")] = None,
    mac: Annotated[str | None, Query(max_length=32)] = None,
    switch_id: uuid.UUID | None = None,
    vlan: Annotated[int | None, Query(ge=1, le=4094)] = None,
) -> PageResponse[EndpointOut]:
    query = ListQuery(
        paging.limit,
        paging.offset,
        paging.sort or "-last_seen",
        {"status": status_, "switch_id": switch_id, "vlan": vlan},
    )
    return page_of(await services.nac.endpoints(query, mac), EndpointOut)


@router.get("/endpoints/{mac}", response_model=EndpointOut)
async def endpoint(mac: str, services: Services, actor: Read) -> EndpointOut:
    return EndpointOut.model_validate(await services.nac.endpoint(mac))


@router.put("/endpoints/{mac}", response_model=EndpointOut, summary="Register / update an endpoint")
async def update_endpoint(
    mac: str, body: EndpointUpdate, services: Services, ctx: Ctx, actor: Write
) -> EndpointOut:
    return EndpointOut.model_validate(
        await services.nac.upsert_endpoint(actor, ctx, mac, body.model_dump())
    )


@router.post(
    "/endpoints/{mac}/block",
    response_model=list[CoaOut],
    summary="Block + disconnect live sessions",
)
async def block(mac: str, services: Services, ctx: Ctx, actor: Write) -> list[CoaOut]:
    await services.nac.upsert_endpoint(actor, ctx, mac, {"status": "BLOCKED"})
    sessions = await services.nac.sessions(active_only=True, mac=mac)
    results = await services.coa.for_mac(actor, ctx, sessions, "disconnect")
    return [CoaOut.model_validate(r, from_attributes=True) for r in results]


@router.post("/endpoints/{mac}/unblock", response_model=EndpointOut)
async def unblock(mac: str, services: Services, ctx: Ctx, actor: Write) -> EndpointOut:
    return EndpointOut.model_validate(
        await services.nac.upsert_endpoint(actor, ctx, mac, {"status": "UNKNOWN"})
    )


@router.get("/policies", response_model=list[PolicyOut])
async def policies(services: Services, actor: Read) -> list[PolicyOut]:
    return [PolicyOut.model_validate(p) for p in await services.nac_policies.all()]


@router.post("/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
async def create_policy(
    body: PolicyIn, services: Services, ctx: Ctx, actor: PolicyWrite
) -> PolicyOut:
    row = await services.nac_policies.create(actor, ctx, body.spec(), body.description)
    return PolicyOut.model_validate(row)


@router.put(
    "/policies/{policy_id}", response_model=PolicyOut, summary="Update (creates a new version)"
)
async def update_policy(
    policy_id: uuid.UUID, body: PolicyIn, services: Services, ctx: Ctx, actor: PolicyWrite
) -> PolicyOut:
    return PolicyOut.model_validate(
        await services.nac_policies.update(actor, ctx, policy_id, body.spec(), body.description)
    )


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    policy_id: uuid.UUID, services: Services, ctx: Ctx, actor: PolicyWrite
) -> None:
    await services.nac_policies.delete(actor, ctx, policy_id)


@router.get("/policies/{policy_id}/versions", response_model=list[PolicyVersionOut])
async def policy_versions(
    policy_id: uuid.UUID, services: Services, actor: Read
) -> list[PolicyVersionOut]:
    return [
        PolicyVersionOut.model_validate(v) for v in await services.nac_policies.versions(policy_id)
    ]


@router.post("/policies/simulate", response_model=DecisionOut, summary="Dry-run the policy engine")
async def simulate(body: SimulateIn, services: Services, actor: Read) -> DecisionOut:
    mac = normalize_mac(body.mac)
    try:
        known = await services.nac.endpoint(mac)
    except NotFound:  # unknown endpoints simulate with defaults
        known = None
    facts = RequestFacts(
        mac=mac,
        username=body.username,
        auth_method=body.auth_method,
        user_groups=frozenset(body.user_groups),
        site=body.site,
        now=body.at,
        registered=(
            body.registered if body.registered is not None else bool(known and known.registered)
        ),
        endpoint_group=body.endpoint_group or (known.endpoint_group if known else None),
        device_type=body.device_type or (known.device_type if known else None),
        status=body.status or (known.status.value if known else "UNKNOWN"),
    )
    decision = await services.nac_policies.simulate(
        facts,
        guest_vlan=services.settings.nac_guest_vlan,
        quarantine_vlan=services.settings.nac_quarantine_vlan,
    )
    return DecisionOut.model_validate(decision, from_attributes=True)


@router.get("/sessions", response_model=list[SessionOut])
async def sessions(
    services: Services,
    actor: Read,
    active: bool = True,
    mac: Annotated[str | None, Query(max_length=32)] = None,
) -> list[SessionOut]:
    return [
        SessionOut.model_validate(s)
        for s in await services.nac.sessions(active_only=active, mac=mac)
    ]


@router.post(
    "/sessions/{session_id}/disconnect",
    response_model=CoaOut,
    summary="RFC 5176 Disconnect-Request",
)
async def disconnect(session_id: uuid.UUID, services: Services, ctx: Ctx, actor: Write) -> CoaOut:
    return CoaOut.model_validate(
        await services.coa.send(actor, ctx, session_id, "disconnect"), from_attributes=True
    )


@router.post(
    "/sessions/{session_id}/reauthenticate", response_model=CoaOut, summary="RFC 5176 CoA-Request"
)
async def reauthenticate(
    session_id: uuid.UUID, services: Services, ctx: Ctx, actor: Write
) -> CoaOut:
    return CoaOut.model_validate(
        await services.coa.send(actor, ctx, session_id, "reauthenticate"), from_attributes=True
    )


@router.post(
    "/provisioning/preview",
    response_model=ProvisionPreviewOut,
    summary=(
        "Generate switch AAA/RADIUS/802.1X/MAB config (VRP-VERSION-DEPENDENT); "
        "deploy via job nac.provision"
    ),
)
async def provisioning_preview(
    body: ProvisionIn,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.NAC_POLICY_WRITE))],
) -> ProvisionPreviewOut:
    req = ProvisionRequest(
        body.radius_server_id,
        tuple(body.interfaces),
        body.mode,
        body.guest_vlan,
        body.quarantine_vlan,
    )
    preview = await services.nac_provisioning.preview(actor, body.device_id, req)
    return ProvisionPreviewOut.model_validate(preview, from_attributes=True)


def _server_out(row: object) -> RadiusServerOut:
    return RadiusServerOut.model_validate(row)


@radius_router.get("/servers", response_model=list[RadiusServerOut])
async def list_servers(
    services: Services, actor: Annotated[Principal, Depends(require(Permission.RADIUS_READ))]
) -> list[RadiusServerOut]:
    return [_server_out(s) for s in await services.radius.list()]


@radius_router.post("/servers", response_model=RadiusServerOut, status_code=status.HTTP_201_CREATED)
async def create_server(
    body: RadiusServerIn,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.RADIUS_WRITE))],
) -> RadiusServerOut:
    row = await services.radius.create(
        name=body.name,
        host=str(body.host),
        shared_secret=body.shared_secret,
        auth_port=body.auth_port,
        acct_port=body.acct_port,
        coa_port=body.coa_port,
        request_timeout=body.timeout,
        retries=body.retries,
        enabled=body.enabled,
    )
    return _server_out(row)


@radius_router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: uuid.UUID,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.RADIUS_WRITE))],
) -> None:
    await services.radius.delete(server_id)
