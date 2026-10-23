"""Backups: trigger (job), list, view (secrets masked), diff, known-good, drift, guarded restore."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import Ctx, Paging, Services, require
from app.core.exceptions import Forbidden
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.models.backup import BackupTrigger
from app.repositories.base import ListQuery
from app.schemas.common import ERROR_RESPONSES, ApiModel, JobAccepted, PageResponse, page_of
from app.schemas.l2 import ChangeRequest, ChangeResponse
from app.services.job_service import JobSubmit

router = APIRouter(prefix="/backups", tags=["backups"], responses=ERROR_RESPONSES)
device_router = APIRouter(
    prefix="/devices/{device_id}", tags=["backups"], responses=ERROR_RESPONSES
)
CanRead = Annotated[Principal, Depends(require(Permission.BACKUPS_READ))]


class BackupOut(ApiModel):
    id: uuid.UUID
    device_id: uuid.UUID
    created_at: datetime
    created_by: uuid.UUID | None
    trigger: BackupTrigger
    source: str
    status: str
    sha256: str | None
    size: int
    family: str | None
    vrp_version: str | None
    known_good: bool
    error: str | None
    change_operation: str | None


class BackupDetailOut(BackupOut):
    content: str | None


class DiffOut(ApiModel):
    diff: str


class DriftOut(ApiModel):
    device_id: uuid.UUID
    baseline_id: uuid.UUID | None
    drifted: bool
    diff: str


class RestoreRequest(ChangeRequest):
    rollback_minutes: int | None = 10


class RestoreResponse(ChangeResponse):
    diff: str
    notes: list[str]


@device_router.post(
    "/backup",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Back up running (and optionally saved) configuration as a job",
)
async def backup_device(
    device_id: uuid.UUID,
    response: Response,
    services: Services,
    ctx: Ctx,
    actor: Annotated[Principal, Depends(require(Permission.BACKUPS_CREATE))],
    include_saved: bool = False,
) -> JobAccepted:
    job = await services.jobs.submit(
        actor,
        ctx,
        JobSubmit(
            type="backup.run",
            target_devices=[device_id],
            params={"trigger": "manual", "include_saved": include_saved},
        ),
    )
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return JobAccepted(job_id=job.id, status=job.status.value, location=f"/api/v1/jobs/{job.id}")


@device_router.get("/drift", response_model=DriftOut, summary="Running config vs known-good")
async def drift(
    device_id: uuid.UUID,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.DEVICE_SHOW))],
) -> DriftOut:
    if not actor.can(Permission.BACKUPS_READ):
        raise Forbidden("drift needs backups:read")
    report = await services.backups.drift(actor, device_id)
    return DriftOut.model_validate(report, from_attributes=True)


@router.get("", response_model=PageResponse[BackupOut])
async def list_backups(
    services: Services, actor: CanRead, paging: Paging, device_id: uuid.UUID | None = None
) -> PageResponse[BackupOut]:
    query = ListQuery(
        paging.limit, paging.offset, paging.sort or "-created_at", {"device_id": device_id}
    )
    return page_of(await services.backups.list(actor, query), BackupOut)


@router.get("/{backup_id}", response_model=BackupDetailOut, summary="Backup with secrets masked")
async def get_backup(backup_id: uuid.UUID, services: Services, actor: CanRead) -> BackupDetailOut:
    return BackupDetailOut.model_validate(await services.backups.get(actor, backup_id))


@router.get("/{backup_id}/diff/{other_id}", response_model=DiffOut, summary="Unified diff")
async def diff(
    backup_id: uuid.UUID, other_id: uuid.UUID, services: Services, actor: CanRead
) -> DiffOut:
    return DiffOut(diff=await services.backups.diff(actor, backup_id, other_id))


@router.post("/{backup_id}/known-good", response_model=BackupOut)
async def known_good(
    backup_id: uuid.UUID,
    services: Services,
    ctx: Ctx,
    actor: Annotated[Principal, Depends(require(Permission.BACKUPS_CREATE))],
) -> BackupOut:
    return BackupOut.model_validate(await services.backups.mark_known_good(actor, ctx, backup_id))


@router.post(
    "/{backup_id}/restore",
    response_model=RestoreResponse,
    summary="Guarded restore: dry-run preview, then confirm with token + reason",
    description=(
        "Never automatic. Requires ADMIN/NETWORK_ADMIN, a dry-run to obtain a confirmation "
        "token, confirm=true, a reason, and matching device family/VRP version. A device-side "
        "rollback timer (CE commit trial / S schedule reboot) is armed when rollback_minutes "
        "is set."
    ),
)
async def restore(
    backup_id: uuid.UUID,
    body: RestoreRequest,
    services: Services,
    ctx: Ctx,
    actor: Annotated[Principal, Depends(require(Permission.BACKUPS_RESTORE))],
) -> RestoreResponse:
    result = await services.backups.restore(actor, ctx, backup_id, body.options())
    base = ChangeResponse.of(result.change).model_dump()
    return RestoreResponse(**base, diff=result.diff, notes=list(result.notes))
