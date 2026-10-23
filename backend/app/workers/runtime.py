"""Per-worker-process runtime: one event loop + one ServiceContainer, created on fork.

asyncpg connections and the Redis client are bound to an event loop, so each worker process
keeps a persistent loop and runs coroutines with ``run()`` instead of ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.logging import configure_logging
from app.core.redis import create_redis
from app.services.container import ServiceContainer

T = TypeVar("T")


@dataclass
class WorkerRuntime:
    loop: asyncio.AbstractEventLoop
    services: ServiceContainer

    @classmethod
    def create(cls, settings: Settings | None = None) -> WorkerRuntime:
        settings = settings or get_settings()
        configure_logging(settings.log_level, settings.log_json)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        db = Database.from_settings(settings)
        redis = create_redis(settings.redis_url)
        return cls(loop, ServiceContainer.build(settings, db, redis, dispatch="celery"))

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        return self.loop.run_until_complete(coro)

    def close(self) -> None:
        self.run(self.services.aclose())
        self.run(self.services.redis.aclose())
        self.run(self.services.db.dispose())
        self.loop.close()


class _Holder:
    """Process-local slot (populated by the worker_process_init signal)."""

    runtime: WorkerRuntime | None = None


def get_runtime() -> WorkerRuntime:
    if _Holder.runtime is None:
        _Holder.runtime = WorkerRuntime.create()
    return _Holder.runtime


def set_runtime(runtime: WorkerRuntime | None) -> None:
    _Holder.runtime = runtime
