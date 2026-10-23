"""Composition root: wires settings, DB, Redis and services once per process."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.database import Database
from app.core.rate_limit import RateLimiter


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    db: Database
    redis: Redis
    rate_limiter: RateLimiter

    @classmethod
    def build(cls, settings: Settings, db: Database, redis: Redis) -> ServiceContainer:
        return cls(settings=settings, db=db, redis=redis, rate_limiter=RateLimiter(redis))

    async def aclose(self) -> None:
        """Release resources owned by services (connection pools etc.)."""
