"""Celery task in eager mode with a real worker runtime, routing config, schedule tick."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from pydantic import SecretStr

from app.core.config import Settings
from app.core.database import Database
from app.core.rbac import Role
from app.domain.jobs import JobStatus
from app.models.device import Device
from app.models.job import Job, Setting
from app.repositories.uow import UnitOfWork
from app.schemas.auth import UserCreate
from app.schemas.devices import CredentialCreate
from app.services.container import ServiceContainer
from app.workers.celery_app import celery_app
from app.workers.runtime import WorkerRuntime, set_runtime
from app.workers.schedules import tick
from tests.conftest import _truncate_all
from tests.fakes import FakeLab, install


class _App:
    def __init__(self, services: ServiceContainer) -> None:
        self.state = type("S", (), {"services": services})()


@pytest.fixture
def runtime(settings: Settings) -> Any:
    loop = asyncio.new_event_loop()
    db = Database(settings.database_url)
    redis = FakeAsyncRedis(decode_responses=True)
    services = ServiceContainer.build(settings, db, redis, dispatch="celery")
    rt = WorkerRuntime(loop, services)
    set_runtime(rt)
    yield rt
    set_runtime(None)
    loop.run_until_complete(_truncate_all(db))
    loop.run_until_complete(db.dispose())
    loop.close()


async def _seed(
    services: ServiceContainer, lab: FakeLab, *, service_account: bool = False
) -> tuple[Any, uuid.UUID]:
    user = await services.users.create(
        UserCreate(
            username=f"w_{uuid.uuid4().hex[:6]}",
            password=SecretStr("Str0ng!Passw0rd"),
            role=Role.ADMIN,
        ),
        service_account=service_account,
    )
    lab.add("192.0.2.9")
    cred = await services.credentials.create(
        CredentialCreate(
            name=f"c{uuid.uuid4().hex[:4]}",
            kind="cli",
            username="u",
            password=SecretStr("Fake-Passw0rd!"),
        )
    )
    async with UnitOfWork(services.db) as uow:
        device = Device(
            hostname="SW-9",
            mgmt_ip="192.0.2.9",
            protocol="ssh",
            port=22,
            family="S",
            credential_id=cred.id,
            capabilities={},
            tags=[],
            uplink_interfaces=[],
        )
        uow.session.add(device)
        await uow.commit()
    return user, device.id


def test_celery_routing_and_reliability_config() -> None:
    celery_app.loader.import_default_modules()  # what the worker does on startup
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert {q.name for q in conf.task_queues} == {
        "config",
        "polling",
        "discovery",
        "alerts",
        "default",
    }
    assert "app.workers.tasks.jobs.run_job" in celery_app.tasks
    assert "schedule-tick" in conf.beat_schedule


def test_run_job_task_eager(runtime: WorkerRuntime) -> None:
    from app.workers.tasks.jobs import run_job

    lab = FakeLab()
    install(_App(runtime.services), lab)
    user, device_id = runtime.run(_seed(runtime.services, lab))

    async def make_job() -> uuid.UUID:
        async with UnitOfWork(runtime.services.db) as uow:
            job = Job(
                name="eager",
                type="collect.vlans",
                created_by=user.id,
                target_devices=[str(device_id)],
            )
            uow.session.add(job)
            await uow.commit()
            return job.id

    job_id = runtime.run(make_job())
    celery_app.conf.task_always_eager = True
    try:
        result = run_job.apply(args=[str(job_id)])
        assert result.get() == JobStatus.SUCCESS.value
        assert (
            run_job.apply(args=[str(job_id)]).get() is None
        )  # already claimed -> idempotent no-op
    finally:
        celery_app.conf.task_always_eager = False


def test_schedule_tick_submits_due_jobs(
    runtime: WorkerRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab = FakeLab()
    install(_App(runtime.services), lab)
    sent: list[str] = []

    async def fake_dispatch(job: Job) -> str:
        sent.append(job.type)
        return "task-1"

    runtime.services.jobs.dispatcher = fake_dispatch
    user, _ = runtime.run(_seed(runtime.services, lab, service_account=True))

    async def configure() -> None:
        async with UnitOfWork(runtime.services.db) as uow:
            uow.session.add(
                Setting(
                    key="schedules",
                    value={
                        "entries": [
                            {
                                "name": "mac",
                                "job_type": "collect.mac",
                                "interval_seconds": 60,
                                "run_as": user.username,
                            },
                            {
                                "name": "off",
                                "job_type": "collect.arp",
                                "interval_seconds": 60,
                                "enabled": False,
                                "run_as": user.username,
                            },
                        ]
                    },
                )
            )
            await uow.commit()

    runtime.run(configure())
    first = runtime.run(tick(runtime.services, now=1000.0))
    assert len(first) == 1
    assert sent == ["collect.mac"]
    assert runtime.run(tick(runtime.services, now=1030.0)) == []
    assert len(runtime.run(tick(runtime.services, now=1061.0))) == 1
