"""Redis client construction (one client per process, owned by app.state / worker)."""

from __future__ import annotations

from redis.asyncio import Redis


def create_redis(url: str) -> Redis:
    """Create an asyncio Redis client with sane timeouts."""
    client: Redis = Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
        health_check_interval=30,
    )
    return client
