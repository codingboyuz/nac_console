"""Job execution engine: claim, per-device fan-out with concurrency cap, stop-on-failure,
cancellation, progress persistence and Redis pub/sub events. Used by Celery and inline mode."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select, update

from app.core.events import JOB_CHANNEL
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.domain.identity import Principal, RequestContext
from app.domain.jobs import DeviceResultStatus, JobStatus, aggregate_status
from app.models.device import Device
from app.models.job import Job, JobDeviceResult
from app.models.user import User
from app.repositories.uow import UnitOfWork
from app.services.confirmation_service import payload_hash
from app.services.job_types import REGISTRY, JobContext

if TYPE_CHECKING:
    from app.services.container import ServiceContainer

log = get_logger(__name__)
STALE_AFTER = timedelta(hours=2)
CANCEL_KEY = "job:cancel:{}"
CONFIRM_KEY = "job:confirm:{}"


def job_digest(job_type: str, targets: list[str], params: dict[str, Any]) -> str:
    return payload_hash("job", job_type, *sorted(targets), sorted(params.items()))


class JobRunner:
    def __init__(self, services: ServiceContainer) -> None:
        self._s = services

    async def _claim(self, job_id: uuid.UUID) -> bool:
        """Atomic PENDING -> RUNNING (or reclaim a stale RUNNING job after a worker crash)."""
        now = datetime.now(UTC)
        async with UnitOfWork(self._s.db) as uow:
            result = await uow.session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    or_(
                        Job.status == JobStatus.PENDING,
                        (Job.status == JobStatus.RUNNING) & (Job.started_at < now - STALE_AFTER),
                    ),
                )
                .values(status=JobStatus.RUNNING, started_at=now)
            )
            await uow.commit()
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def _principal(self, job: Job) -> Principal | None:
        if job.created_by is None:
            return None
        async with UnitOfWork(self._s.db) as uow:
            user = await uow.session.get(User, job.created_by)
        if user is None or not user.is_active:
            return None
        return Principal(
            user.id,
            user.username,
            user.role,
            frozenset(uuid.UUID(g) for g in user.group_scope or []),
            is_service_account=user.is_service_account,
        )

    async def _publish(self, job_id: uuid.UUID, **message: Any) -> None:
        await self._s.events.publish(JOB_CHANNEL.format(job_id), {"job_id": str(job_id), **message})

    async def cancelled(self, job_id: uuid.UUID) -> bool:
        return bool(await self._s.redis.exists(CANCEL_KEY.format(job_id)))

    async def run(self, job_id: uuid.UUID) -> JobStatus | None:
        if not await self._claim(job_id):
            log.info("job_not_claimed", job_id=str(job_id))
            return None
        async with UnitOfWork(self._s.db) as uow:
            job = await uow.session.get(Job, job_id)
        assert job is not None  # noqa: S101 - claimed above
        await self._publish(job.id, type="status", status=JobStatus.RUNNING.value, progress=0)
        try:
            status = await self._execute(job)
        except Exception as exc:  # never leave a job RUNNING
            log.exception("job_crashed", job_id=str(job.id))
            status = await self._finish(
                job, JobStatus.FAILED, error=f"internal error: {type(exc).__name__}"
            )
        return status

    async def _execute(self, job: Job) -> JobStatus:
        job_type = REGISTRY.get(job.type)
        principal = await self._principal(job)
        if job_type is None or principal is None:
            return await self._finish(
                job, JobStatus.FAILED, error="unknown job type or inactive owner"
            )
        devices = await self._devices(principal, job)
        jc = JobContext(
            self._s,
            principal,
            RequestContext(user_agent=f"job:{job.id}"),
            job.params,
            job.dry_run,
            job.confirmed,
            job.reason,
        )
        async with UnitOfWork(self._s.db) as uow:
            for device in devices:
                uow.session.add(JobDeviceResult(job_id=job.id, device_id=device.id))
            await uow.commit()
        state = {"done": 0, "failures": 0, "stop": False, "dangerous": False}
        semaphore = asyncio.Semaphore(max(1, job.concurrency))

        async def one(device: Device) -> DeviceResultStatus:
            async with semaphore:
                if state["stop"] or await self.cancelled(job.id):
                    return await self._result(
                        job.id, device.id, DeviceResultStatus.SKIPPED, {}, "skipped"
                    )
                await self._result(job.id, device.id, DeviceResultStatus.RUNNING, {}, None)
                try:
                    output = await job_type.handler(jc, device)
                    outcome, error = DeviceResultStatus.SUCCESS, None
                    state["dangerous"] |= output.get("category") == "DANGEROUS"
                except (AppError, PermissionError, ValueError) as exc:
                    code = getattr(exc, "code", type(exc).__name__)
                    outcome, error, output = DeviceResultStatus.FAILED, f"{code}: {exc}"[:2000], {}
                except Exception as exc:
                    log.exception("job_device_failed", job_id=str(job.id), device_id=str(device.id))
                    outcome, error, output = DeviceResultStatus.FAILED, type(exc).__name__, {}
                if outcome is DeviceResultStatus.FAILED:
                    state["failures"] += 1
                    if job.stop_on_failure and state["failures"] >= job.stop_on_failure:
                        state["stop"] = True
                state["done"] += 1
                await self._result(job.id, device.id, outcome, output, error)
                await self._progress(job, int(state["done"] * 100 / max(1, len(devices))))
                return outcome

        outcomes = await asyncio.gather(*(one(d) for d in devices))
        missing = len(job.target_devices) - len(devices)
        output: dict[str, Any] = {"devices": len(devices), "missing_or_out_of_scope": missing}
        if job.dry_run and state["dangerous"]:
            token = await self._s.confirmations.issue(
                principal, "job", job_digest(job.type, job.target_devices, job.params)
            )
            await self._s.redis.set(CONFIRM_KEY.format(job.id), token, ex=self._s.confirmations.ttl)
            output["confirmation_required"] = True
        status = aggregate_status(outcomes, cancelled=await self.cancelled(job.id))
        if missing and status is JobStatus.SUCCESS:
            status = JobStatus.PARTIAL
        return await self._finish(job, status, output=output)

    async def _devices(self, principal: Principal, job: Job) -> list[Device]:
        ids = [uuid.UUID(d) for d in job.target_devices]
        async with UnitOfWork(self._s.db) as uow:
            rows = await uow.session.scalars(select(Device).where(Device.id.in_(ids)))
            return [d for d in rows.all() if principal.in_scope(d.group_id)]

    async def _result(
        self,
        job_id: uuid.UUID,
        device_id: uuid.UUID,
        status: DeviceResultStatus,
        output: dict[str, Any],
        error: str | None,
    ) -> DeviceResultStatus:
        now = datetime.now(UTC)
        values: dict[str, Any] = {"status": status, "output": output, "error": error}
        if status is DeviceResultStatus.RUNNING:
            values = {"status": status, "started_at": now, "attempts": JobDeviceResult.attempts + 1}
        else:
            values["completed_at"] = now
        async with UnitOfWork(self._s.db) as uow:
            await uow.session.execute(
                update(JobDeviceResult)
                .where(JobDeviceResult.job_id == job_id, JobDeviceResult.device_id == device_id)
                .values(**values)
            )
            await uow.commit()
        await self._publish(
            job_id, type="device", device_id=str(device_id), status=status.value, error=error
        )
        return status

    async def _progress(self, job: Job, progress: int) -> None:
        async with UnitOfWork(self._s.db) as uow:
            await uow.session.execute(update(Job).where(Job.id == job.id).values(progress=progress))
            await uow.commit()
        await self._publish(job.id, type="progress", progress=progress)

    async def _finish(
        self,
        job: Job,
        status: JobStatus,
        *,
        error: str | None = None,
        output: dict[str, Any] | None = None,
    ) -> JobStatus:
        async with UnitOfWork(self._s.db) as uow:
            await uow.session.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(
                    status=status,
                    completed_at=datetime.now(UTC),
                    progress=100,
                    error=error,
                    output=output or {},
                )
            )
            await uow.commit()
        await self._publish(job.id, type="status", status=status.value, progress=100, error=error)
        log.info("job_finished", job_id=str(job.id), status=status.value)
        return status
