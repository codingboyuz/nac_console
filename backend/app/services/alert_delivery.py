"""Alert delivery attempts (every attempt recorded), retries and dead-lettering."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core.database import Database
from app.models.job import Setting
from app.models.monitoring import Alert, AlertDelivery
from app.repositories.uow import UnitOfWork
from app.services.notifications import Channel, ChannelError, Notification

MAX_ATTEMPTS = 5
DeliveryDispatcher = Callable[[uuid.UUID], Awaitable[None]]


def alert_message(alert: Alert) -> dict[str, Any]:
    """WebSocket / webhook payload for an alert (documented in API.md)."""
    return {
        "type": "alert",
        "alert_id": str(alert.id),
        "rule": alert.alert_type,
        "severity": alert.severity,
        "state": alert.state,
        "message": alert.message,
        "device_id": str(alert.device_id) if alert.device_id else None,
        "timestamp": alert.last_seen.isoformat(),
        "suppressed": alert.suppressed,
    }


class AlertDeliverer:
    def __init__(self, db: Database, channels: dict[str, Channel]) -> None:
        self._db = db
        self.channels = channels
        self.dispatch: DeliveryDispatcher = self._inline_dispatch
        self._tasks: set[asyncio.Task[None]] = set()

    async def deliver(self, delivery_id: uuid.UUID) -> str:
        """One attempt; records the outcome. Returns SENT | RETRY | DEAD."""
        async with UnitOfWork(self._db) as uow:
            delivery = await uow.session.get(AlertDelivery, delivery_id)
            if delivery is None or delivery.status in {"SENT", "DEAD"}:
                return delivery.status if delivery else "DEAD"
            alert = await uow.session.get(Alert, delivery.alert_id)
            destinations = await uow.session.get(Setting, "alert_channels")
            assert alert is not None  # noqa: S101 - FK guarantees the alert exists
            channel = self.channels.get(delivery.channel)
            destination = (destinations.value if destinations else {}).get(delivery.channel, {})
            delivery.attempts += 1
            note = Notification(
                alert.alert_type, alert.message, alert.severity, alert_message(alert)
            )
            try:
                if channel is None:
                    raise ChannelError(f"unknown channel {delivery.channel}")
                await channel.send(note, destination)
                delivery.status, delivery.delivered_at = "SENT", datetime.now(UTC)
                delivery.last_error = None
            except ChannelError as exc:
                delivery.last_error = str(exc)[:500]
                delivery.status = "DEAD" if delivery.attempts >= MAX_ATTEMPTS else "RETRY"
            except Exception as exc:  # transport errors are retried as well
                delivery.last_error = type(exc).__name__
                delivery.status = "DEAD" if delivery.attempts >= MAX_ATTEMPTS else "RETRY"
            status = delivery.status
            await uow.commit()
        return status

    async def _inline_dispatch(self, delivery_id: uuid.UUID) -> None:
        async def attempt_all() -> None:
            for attempt in range(MAX_ATTEMPTS):
                if await self.deliver(delivery_id) != "RETRY":
                    return
                await asyncio.sleep(min(2**attempt * 0.05, 1))

        task = asyncio.create_task(attempt_all())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
