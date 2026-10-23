"""Job submission, queries and cancellation; dispatch to Celery (or inline in tests/dev)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.database import Database
from app.core.exceptions import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.rbac import Role
from app.domain.identity import Principal, RequestContext
from app.domain.jobs import JobStatus
from app.models.job import Job, JobDeviceResult
from app.repositories.base import ListQuery, Page, Repository
from app.repositories.uow import UnitOfWork
from app.services.audit_service import AuditService
from app.services.confirmation_service import ConfirmationService
from app.services.device_service import DeviceService
from app.services.job_runner import CANCEL_KEY, CONFIRM_KEY, job_digest
from app.services.job_types import REGISTRY

Dispatcher = Callable[[Job], Awaitable[str | None]]
MAX_TARGETS = 1000


class JobRepository(Repository[Job]):
    model = Job
    sortable = {"created_at": Job.created_at, "status": Job.status, "type": Job.type}
    filterable = {"status": Job.status, "type": Job.type, "created_by": Job.created_by}


@dataclass(frozen=True, slots=True)
class JobSubmit:
    type: str
    target_devices: list[uuid.UUID]
    params: dict[str, Any] = field(default_factory=dict)
    name: str | None = None
    dry_run: bool = False
    confirm: bool = False
    confirmation_token: str | None = None
    reason: str | None = None
    concurrency: int = 5
    stop_on_failure: int | None = None
    idempotency_key: str | None = None


class InlineDispatcher:
    """Runs jobs as asyncio tasks in this process (tests and single-process dev)."""

    def __init__(self, runner_factory: Callable[[], Any]) -> None:
        self._runner_factory = runner_factory
        self.tasks: set[asyncio.Task[Any]] = set()

    async def __call__(self, job: Job) -> str | None:
        task = asyncio.create_task(self._runner_factory().run(job.id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return None

    async def drain(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)


async def celery_dispatcher(job: Job) -> str | None:
    from app.workers.celery_app import celery_app

    result = celery_app.send_task(
        "app.workers.tasks.jobs.run_job", args=[str(job.id)], queue=job.queue
    )
    return str(result.id)


class JobService:
    def __init__(
        self,
        db: Database,
        redis: Redis,
        devices: DeviceService,
        confirmations: ConfirmationService,
        audit: AuditService,
        dispatcher: Dispatcher,
    ) -> None:
        self._db = db
        self._redis = redis
        self._devices = devices
        self._confirm = confirmations
        self._audit = audit
        self.dispatcher = dispatcher

    async def submit(self, principal: Principal, ctx: RequestContext, req: JobSubmit) -> Job:
        job_type = REGISTRY.get(req.type)
        if job_type is None:
            raise ValidationFailed(
                f"unknown job type {req.type!r}", details={"types": sorted(REGISTRY)}
            )
        if not principal.can(job_type.permission):
            await self._audit.record(
                operation=f"job.submit:{req.type}",
                success=False,
                principal=principal,
                ctx=ctx,
                reason=f"missing permission {job_type.permission.value}",
            )
            raise Forbidden(f"job type {req.type} needs {job_type.permission.value}")
        try:
            params = job_type.params.model_validate(req.params).model_dump(mode="json")
        except ValidationError as exc:
            raise ValidationFailed(
                "invalid job parameters", details=exc.errors(include_url=False)
            ) from exc
        targets = sorted({str(t) for t in req.target_devices})
        if not targets or len(targets) > MAX_TARGETS:
            raise ValidationFailed(f"1-{MAX_TARGETS} target devices required")
        for device_id in targets:
            await self._devices.get(principal, uuid.UUID(device_id))  # 404 when out of scope
        if req.idempotency_key:
            existing = await self._by_idempotency(principal, req.idempotency_key)
            if existing is not None:
                if (
                    existing.type != req.type
                    or existing.target_devices != targets
                    or existing.params != params
                ):
                    raise Conflict("Idempotency-Key reused with a different request")
                return existing
        confirmed = False
        if req.confirm and job_type.changes_config and not req.dry_run:
            await self._confirm.require(
                confirm=True,
                token=req.confirmation_token,
                reason=req.reason,
                principal=principal,
                target_id="job",
                digest=job_digest(req.type, targets, params),
            )
            confirmed = True
        job = Job(
            name=req.name or f"{req.type} on {len(targets)} device(s)",
            type=req.type,
            created_by=principal.user_id,
            target_devices=targets,
            params=params,
            dry_run=req.dry_run,
            confirmed=confirmed,
            reason=req.reason,
            idempotency_key=req.idempotency_key,
            concurrency=max(1, min(req.concurrency, 20)),
            stop_on_failure=req.stop_on_failure,
            queue=job_type.queue.value,
        )
        async with UnitOfWork(self._db) as uow:
            uow.repo(JobRepository).add(job)
            await uow.commit()
        await self._audit.record(
            operation=f"job.submit:{req.type}",
            success=True,
            principal=principal,
            ctx=ctx,
            reason=req.reason,
            result_summary=f"job {job.id} targets={len(targets)} dry_run={req.dry_run}",
        )
        task_id = await self.dispatcher(job)
        if task_id:
            async with UnitOfWork(self._db) as uow:
                row = await uow.session.get(Job, job.id)
                if row is not None:
                    row.task_id = task_id
                    await uow.commit()
        return job

    async def _by_idempotency(self, principal: Principal, key: str) -> Job | None:
        async with UnitOfWork(self._db) as uow:
            return await uow.session.scalar(
                select(Job).where(Job.created_by == principal.user_id, Job.idempotency_key == key)
            )

    @staticmethod
    def _visible(principal: Principal, job: Job) -> bool:
        return (
            job.created_by == principal.user_id
            or principal.at_least(Role.NETWORK_ADMIN)
            or (principal.role is Role.AUDITOR)
        )

    async def get(
        self, principal: Principal, job_id: uuid.UUID
    ) -> tuple[Job, list[JobDeviceResult]]:
        async with UnitOfWork(self._db) as uow:
            job = await uow.session.get(Job, job_id)
            if job is None or not self._visible(principal, job):
                raise NotFound("Job not found")
            results = list(
                (
                    await uow.session.scalars(
                        select(JobDeviceResult).where(JobDeviceResult.job_id == job_id)
                    )
                ).all()
            )
        return job, results

    async def confirmation_token(self, principal: Principal, job: Job) -> str | None:
        if job.created_by != principal.user_id:
            return None
        token = await self._redis.get(CONFIRM_KEY.format(job.id))
        return str(token) if token else None

    async def list(self, principal: Principal, query: ListQuery) -> Page[Job]:
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(JobRepository)
            stmt = repo.base_query()
            if not (principal.at_least(Role.NETWORK_ADMIN) or principal.role is Role.AUDITOR):
                stmt = stmt.where(Job.created_by == principal.user_id)
            return await repo.list(query, stmt)

    async def cancel(self, principal: Principal, ctx: RequestContext, job_id: uuid.UUID) -> Job:
        job, _ = await self.get(principal, job_id)
        if job.created_by != principal.user_id and not principal.at_least(Role.NETWORK_ADMIN):
            raise Forbidden("only the owner or NETWORK_ADMIN+ can cancel")
        if job.status.finished:
            raise Conflict(f"job already {job.status.value}")
        await self._redis.set(CANCEL_KEY.format(job.id), "1", ex=86400)
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(Job, job.id)
            assert row is not None  # noqa: S101
            row.cancel_requested = True
            if row.status is JobStatus.PENDING:
                row.status = JobStatus.CANCELLED
            await uow.commit()
            job = row
        if job.task_id:
            from app.workers.celery_app import celery_app

            celery_app.control.revoke(job.task_id)  # running tasks stop at the next device
        await self._audit.record(
            operation="job.cancel",
            success=True,
            principal=principal,
            ctx=ctx,
            result_summary=str(job.id),
        )
        return job
