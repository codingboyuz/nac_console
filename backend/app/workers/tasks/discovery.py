"""Discovery runs execute on the dedicated discovery queue."""

from __future__ import annotations

import uuid

from app.workers.celery_app import celery_app
from app.workers.runtime import get_runtime


@celery_app.task(name="app.workers.tasks.discovery.run")  # type: ignore[untyped-decorator]
def run(run_id: str) -> None:
    runtime = get_runtime()
    runtime.run(runtime.services.discovery.execute(uuid.UUID(run_id)))
