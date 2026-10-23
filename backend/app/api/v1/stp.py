"""STP status and guarded settings."""

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
    StpEdgeRequest,
    StpPortOut,
    StpSettingsRequest,
    StpStatusOut,
)

router = APIRouter(prefix="/devices/{device_id}/stp", tags=["stp"], responses=ERROR_RESPONSES)
Show = Annotated[Principal, Depends(require(Permission.DEVICE_SHOW))]
Configure = Annotated[Principal, Depends(require(Permission.DEVICE_CONFIGURE))]


@router.get(
    "", response_model=StpStatusOut, summary="Mode, root bridge, port roles/states, TC count"
)
async def status(device_id: uuid.UUID, services: Services, actor: Show) -> StpStatusOut:
    st = await services.stp.status(actor, device_id)
    g = st.global_info
    return StpStatusOut(
        mode=g.mode,
        bridge_id=g.bridge_id,
        root_id=g.root_id,
        root_path_cost=g.root_path_cost,
        root_port=g.root_port,
        tc_count=g.tc_count,
        time_since_last_tc=g.time_since_last_tc,
        bpdu_protection=g.bpdu_protection,
        is_root=st.is_root,
        loop_suspected=st.loop_suspected,
        ports=[StpPortOut.model_validate(p, from_attributes=True) for p in st.ports],
    )


@router.put("", response_model=ChangeResponse, summary="STP mode / bridge priority")
async def configure(
    device_id: uuid.UUID, body: StpSettingsRequest, services: Services, ctx: Ctx, actor: Configure
) -> ChangeResponse:
    result = await services.stp.configure(
        actor, ctx, device_id, body.mode, body.priority, body.instance, body.options()
    )
    return ChangeResponse.of(result)


@router.put("/edge-ports/{interface:path}", response_model=ChangeResponse)
async def edge_port(
    device_id: uuid.UUID,
    interface: str,
    body: StpEdgeRequest,
    services: Services,
    ctx: Ctx,
    actor: Configure,
) -> ChangeResponse:
    result = await services.stp.edge_port(
        actor, ctx, device_id, interface, body.enabled, body.options()
    )
    return ChangeResponse.of(result)
