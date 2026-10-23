"""Job engine API: submit (202 + Location), list, detail with per-device results, cancel."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.deps import Ctx, Paging, Services, require
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.domain.jobs import JobStatus
from app.repositories.base import ListQuery
from app.schemas.common import ERROR_RESPONSES, JobAccepted, PageResponse, page_of
from app.schemas.jobs import JobCreate, JobDetailOut, JobDeviceResultOut, JobOut, JobTypeOut
from app.services.job_service import JobSubmit
from app.services.job_types import REGISTRY

router = APIRouter(prefix="/jobs", tags=["jobs"], responses=ERROR_RESPONSES)
CanRead = Annotated[Principal, Depends(require(Permission.JOBS_READ))]


@router.get("/types", response_model=list[JobTypeOut], summary="Available job types and params")
async def job_types(_: CanRead) -> list[JobTypeOut]:
    return [
        JobTypeOut(
            name=t.name,
            permission=t.permission.value,
            queue=t.queue.value,
            params_schema=t.params.model_json_schema(),
            changes_config=t.changes_config,
        )
        for t in sorted(REGISTRY.values(), key=lambda t: t.name)
    ]


@router.post(
    "",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a bulk job (dry-run first for DANGEROUS changes)",
)
async def submit(
    body: JobCreate,
    response: Response,
    services: Services,
    ctx: Ctx,
    actor: Annotated[Principal, Depends(require(Permission.JOBS_CREATE))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> JobAccepted:
    job = await services.jobs.submit(
        actor,
        ctx,
        JobSubmit(
            type=body.type,
            target_devices=body.target_devices,
            params=body.params,
            name=body.name,
            dry_run=body.dry_run,
            confirm=body.confirm,
            confirmation_token=(
                body.confirmation_token.get_secret_value() if body.confirmation_token else None
            ),
            reason=body.reason,
            concurrency=body.concurrency,
            stop_on_failure=body.stop_on_failure,
            idempotency_key=idempotency_key,
        ),
    )
    location = f"/api/v1/jobs/{job.id}"
    response.headers["Location"] = location
    return JobAccepted(job_id=job.id, status=job.status.value, location=location)


@router.get("", response_model=PageResponse[JobOut])
async def list_jobs(
    services: Services,
    actor: CanRead,
    paging: Paging,
    status_: Annotated[JobStatus | None, Query(alias="status")] = None,
    type_: Annotated[str | None, Query(alias="type", max_length=64)] = None,
) -> PageResponse[JobOut]:
    query = ListQuery(
        paging.limit,
        paging.offset,
        paging.sort or "-created_at",
        {"status": status_, "type": type_},
    )
    return page_of(await services.jobs.list(actor, query), JobOut)


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(job_id: uuid.UUID, services: Services, actor: CanRead) -> JobDetailOut:
    job, results = await services.jobs.get(actor, job_id)
    out = JobDetailOut.model_validate(job)
    out.per_device_results = [JobDeviceResultOut.model_validate(r) for r in results]
    out.confirmation_token = await services.jobs.confirmation_token(actor, job)
    return out


@router.post(
    "/{job_id}/cancel", response_model=JobOut, summary="Cancel (remaining devices are skipped)"
)
async def cancel(
    job_id: uuid.UUID,
    services: Services,
    ctx: Ctx,
    actor: Annotated[Principal, Depends(require(Permission.JOBS_CANCEL))],
) -> JobOut:
    return JobOut.model_validate(await services.jobs.cancel(actor, ctx, job_id))
