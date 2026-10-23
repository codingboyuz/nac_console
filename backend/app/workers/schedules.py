"""DB-backed schedules (settings key 'schedules') evaluated by a frequent beat tick.

Each schedule submits an ordinary job (so RBAC, audit and per-device results apply) as the
configured service account. Last-run timestamps live in Redis (``sched:last:<name>``).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.domain.identity import Principal, RequestContext
from app.domain.schedules import DEFAULT_SCHEDULES, ScheduleEntry
from app.models.device import Device
from app.models.job import Setting
from app.models.user import User
from app.repositories.uow import UnitOfWork
from app.services.job_service import JobSubmit

log = get_logger(__name__)
SETTINGS_KEY = "schedules"
LAST_RUN = "sched:last:{}"


async def load_schedules(services: Any) -> list[ScheduleEntry]:
    async with UnitOfWork(services.db) as uow:
        row = await uow.session.get(Setting, SETTINGS_KEY)
    if row is None:
        return list(DEFAULT_SCHEDULES)
    return [ScheduleEntry.model_validate(e) for e in row.value.get("entries", [])]


async def tick(services: Any, now: float | None = None) -> list[uuid.UUID]:
    """Submit a job for every enabled schedule whose interval has elapsed."""
    from app.services.job_types import REGISTRY

    now = now or time.time()
    submitted: list[uuid.UUID] = []
    for entry in await load_schedules(services):
        if not entry.enabled or entry.job_type not in REGISTRY:
            continue
        key = LAST_RUN.format(entry.name)
        last = await services.redis.get(key)
        if last and now - float(last) < entry.interval_seconds:
            continue
        principal = await _service_principal(services, entry.run_as)
        if principal is None:
            log.warning("schedule_no_service_account", schedule=entry.name, run_as=entry.run_as)
            continue
        targets = await _targets(services, entry.device_filter)
        await services.redis.set(key, str(now))
        if not targets:
            continue
        job = await services.jobs.submit(
            principal,
            RequestContext.system(f"schedule:{entry.name}"),
            JobSubmit(
                type=entry.job_type,
                target_devices=targets,
                params=entry.params,
                name=f"scheduled {entry.name}",
                concurrency=10,
            ),
        )
        submitted.append(job.id)
    return submitted


async def _service_principal(services: Any, username: str) -> Principal | None:
    async with UnitOfWork(services.db) as uow:
        user = await uow.session.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active or not user.is_service_account:
        return None
    return Principal(user.id, user.username, user.role, is_service_account=True)


async def _targets(services: Any, device_filter: dict[str, str]) -> list[uuid.UUID]:
    stmt = select(Device.id).where(Device.status != "MAINTENANCE")
    if "site" in device_filter:
        stmt = stmt.where(Device.site == device_filter["site"])
    if "group_id" in device_filter:
        stmt = stmt.where(Device.group_id == uuid.UUID(device_filter["group_id"]))
    async with UnitOfWork(services.db) as uow:
        return list((await uow.session.scalars(stmt)).all())
