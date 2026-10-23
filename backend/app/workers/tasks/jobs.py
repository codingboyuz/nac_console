"""Celery task wrapping the job runner. Idempotent: the runner claims PENDING jobs atomically."""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_process_init, worker_process_shutdown

from app.core.logging import get_logger
from app.services.job_runner import JobRunner
from app.workers.celery_app import celery_app
from app.workers.runtime import get_runtime, set_runtime

log = get_logger(__name__)


@worker_process_init.connect
def _init_worker(**_: Any) -> None:
    set_runtime(None)
    get_runtime()


@worker_process_shutdown.connect
def _shutdown_worker(**_: Any) -> None:
    runtime = get_runtime()
    runtime.close()
    set_runtime(None)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.jobs.run_job",
    bind=True,
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def run_job(self: Task[Any, Any], job_id: str) -> str | None:
    runtime = get_runtime()
    try:
        status = runtime.run(JobRunner(runtime.services).run(uuid.UUID(job_id)))
    except SoftTimeLimitExceeded:
        log.error("job_time_limit", job_id=job_id)
        raise
    return status.value if status else None
