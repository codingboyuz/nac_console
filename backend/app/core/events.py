"""Domain events: in-process handlers + Redis pub/sub fan-out (WebSocket, other workers).

Channels: ``events:job:<id>``, ``events:monitoring``, ``events:alerts``, ``events:domain``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from app.core.logging import get_logger

log = get_logger(__name__)

JOB_CHANNEL = "events:job:{}"
MONITORING_CHANNEL = "events:monitoring"
ALERTS_CHANNEL = "events:alerts"
DOMAIN_CHANNEL = "events:domain"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """config_changed, device_offline, device_online, endpoint_authorized, ..."""

    name: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


Handler = Callable[[DomainEvent], Awaitable[None]]


class EventBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, name: str, handler: Handler) -> None:
        self._handlers.setdefault(name, []).append(handler)

    async def emit(self, name: str, **payload: Any) -> None:
        """Run local handlers, then publish to Redis for other processes / WebSockets."""
        event = DomainEvent(name, payload)
        for handler in self._handlers.get(name, []) + self._handlers.get("*", []):
            try:
                await handler(event)
            except Exception as exc:  # one failing subscriber must not break the emitter
                log.error("event_handler_failed", event_name=name, error=type(exc).__name__)
        await self.publish(DOMAIN_CHANNEL, {"type": "domain_event", **asdict(event)})

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        try:
            await self._redis.publish(channel, json.dumps(message, default=str))
        except Exception as exc:  # pub/sub is best effort; state lives in the DB
            log.warning("event_publish_failed", channel=channel, error=type(exc).__name__)

    @asynccontextmanager
    async def listen(self, channel: str) -> AsyncIterator[PubSub]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            yield pubsub
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()  # type: ignore[no-untyped-call]
