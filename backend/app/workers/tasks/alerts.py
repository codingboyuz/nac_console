"""Alert delivery with retries; after MAX_ATTEMPTS the delivery is DEAD (dead-letter)."""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task

from app.services.alert_delivery import MAX_ATTEMPTS
from app.workers.celery_app import celery_app
from app.workers.runtime import get_runtime


class RetryDeliveryError(Exception):
    pass


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.alerts.deliver",
    bind=True,
    autoretry_for=(RetryDeliveryError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=MAX_ATTEMPTS,
)
def deliver(self: Task[Any, Any], delivery_id: str) -> str:
    runtime = get_runtime()
    status: str = runtime.run(runtime.services.alerts.deliver(uuid.UUID(delivery_id)))
    if status == "RETRY":
        raise RetryDeliveryError(delivery_id)
    return status
