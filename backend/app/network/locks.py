"""Per-device distributed lock (Redis) so two jobs never configure one device at once."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from redis.asyncio import Redis
from redis.exceptions import LockError

from app.core.exceptions import Locked


@asynccontextmanager
async def device_config_lock(
    redis: Redis, device_id: str, *, ttl: int = 600, wait: float = 5.0
) -> AsyncIterator[None]:
    """Hold ``lock:device:<id>``; raise Locked (423) when another operation owns it."""
    lock = redis.lock(f"lock:device:{device_id}", timeout=ttl, blocking_timeout=wait)
    acquired = await lock.acquire()
    if not acquired:
        raise Locked(f"device {device_id} is being configured by another operation")
    try:
        yield
    finally:
        # LockError: the TTL expired while we worked, so the lock is already gone.
        with suppress(LockError):
            await lock.release()
