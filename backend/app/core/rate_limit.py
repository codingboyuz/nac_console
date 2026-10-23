"""Redis fixed-window rate limiter (global, per-user and per-route buckets)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.exceptions import RateLimited

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


@dataclass(frozen=True, slots=True)
class RateLimit:
    limit: int
    window_seconds: int

    @classmethod
    def parse(cls, spec: str) -> RateLimit:
        """Parse '120/minute' or '10/second' style specs."""
        count, _, unit = spec.partition("/")
        unit = unit.strip().rstrip("s")
        if unit not in _UNITS or not count.strip().isdigit():
            raise ValueError(f"invalid rate limit spec: {spec!r}")
        return cls(int(count), _UNITS[unit])


class RateLimiter:
    """INCR + EXPIRE per (bucket, identity, window). Atomic via pipeline."""

    def __init__(self, redis: Redis, *, prefix: str = "rl") -> None:
        self.redis = redis
        self.prefix = prefix

    async def hit(self, bucket: str, identity: str, rule: RateLimit) -> int:
        """Register a hit; raise RateLimited when the window budget is exhausted."""
        window = int(time.time() // rule.window_seconds)
        key = f"{self.prefix}:{bucket}:{identity}:{window}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, rule.window_seconds + 1)
            count, _ = await pipe.execute()
        if int(count) > rule.limit:
            retry_after = rule.window_seconds - int(time.time() % rule.window_seconds)
            raise RateLimited(f"rate limit exceeded for {bucket}", retry_after=max(retry_after, 1))
        return int(count)
