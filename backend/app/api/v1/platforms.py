"""Supported vendor platforms and what each one can do (drives the UI's device form)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import require
from app.core.exceptions import UnsupportedCapability
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.domain.platforms import CommitModel, Platform, RollbackTimer, Vendor
from app.network.platforms import adapter_for
from app.schemas.common import ERROR_RESPONSES

router = APIRouter(prefix="/platforms", tags=["devices"], responses=ERROR_RESPONSES)
CanRead = Annotated[Principal, Depends(require(Permission.DEVICES_READ))]


class PlatformOut(BaseModel):
    platform: Platform
    vendor: Vendor
    label: str
    available: bool
    transports: list[str]
    commit_model: CommitModel | None
    rollback_timer: RollbackTimer | None
    hybrid_ports: bool
    notes: list[str]


@router.get("", response_model=list[PlatformOut], summary="Vendor platforms and capabilities")
async def list_platforms(_: CanRead) -> list[PlatformOut]:
    out: list[PlatformOut] = []
    for platform in Platform:
        try:
            adapter = adapter_for(platform)
        except UnsupportedCapability:
            out.append(
                PlatformOut(
                    platform=platform,
                    vendor=platform.vendor,
                    label=platform.label,
                    available=False,
                    transports=[],
                    commit_model=None,
                    rollback_timer=None,
                    hybrid_ports=False,
                    notes=["not implemented yet"],
                )
            )
            continue
        out.append(
            PlatformOut(
                platform=platform,
                vendor=platform.vendor,
                label=platform.label,
                available=True,
                transports=sorted(adapter.drivers),
                commit_model=adapter.commit_model,
                rollback_timer=adapter.rollback_timer,
                hybrid_ports=platform.is_huawei,
                notes=list(adapter.notes),
            )
        )
    return out
