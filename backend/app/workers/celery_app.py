"""Celery application: Redis broker, queue separation, reliability settings, beat tick."""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from app.core.config import get_settings
from app.domain.jobs import JobQueue


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery("huawei_nac", broker=settings.broker_url, backend=settings.result_backend)
    app.conf.update(
        task_queues=[Queue(q.value) for q in JobQueue],
        task_default_queue=JobQueue.DEFAULT.value,
        task_routes={
            "app.workers.tasks.jobs.run_job": {"queue": JobQueue.CONFIG.value},
            "app.workers.tasks.schedule.tick": {"queue": JobQueue.DEFAULT.value},
            "app.workers.tasks.alerts.*": {"queue": JobQueue.ALERTS.value},
            "app.workers.tasks.polling.*": {"queue": JobQueue.POLLING.value},
            "app.workers.tasks.discovery.*": {"queue": JobQueue.DISCOVERY.value},
        },
        task_acks_late=True,  # redeliver if a worker dies mid-task
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        task_soft_time_limit=3600,
        task_time_limit=3900,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_expires=86400,
        broker_connection_retry_on_startup=True,
        worker_cancel_long_running_tasks_on_connection_loss=True,
        timezone="UTC",
        enable_utc=True,
        # The tick reads schedules from the settings table, so schedule edits made through the
        # settings API take effect without restarting beat.
        beat_schedule={
            "schedule-tick": {"task": "app.workers.tasks.schedule.tick", "schedule": 15.0},
        },
        imports=(
            "app.workers.tasks.jobs",
            "app.workers.tasks.schedule",
        ),
    )
    return app


celery_app = create_celery()
