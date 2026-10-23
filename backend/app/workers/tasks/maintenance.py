"""Housekeeping: create metric partitions ahead of time and apply retention."""

from __future__ import annotations

from typing import Any

from app.workers.celery_app import celery_app
from app.workers.runtime import get_runtime


@celery_app.task(name="app.workers.tasks.maintenance.metrics")  # type: ignore[untyped-decorator]
def metrics() -> dict[str, Any]:
    runtime = get_runtime()
    result: dict[str, Any] = runtime.run(runtime.services.monitoring.maintain())
    return result
