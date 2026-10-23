"""Beat tick task: evaluates DB-backed schedules and submits due jobs."""

from __future__ import annotations

from app.workers.celery_app import celery_app
from app.workers.runtime import get_runtime
from app.workers.schedules import tick as schedule_tick


@celery_app.task(name="app.workers.tasks.schedule.tick", ignore_result=True)  # type: ignore[untyped-decorator]
def tick() -> int:
    runtime = get_runtime()
    return len(runtime.run(schedule_tick(runtime.services)))
