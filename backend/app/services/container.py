"""Composition root: wires settings, DB, Redis and services once per process."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.database import Database
from app.core.rate_limit import RateLimiter
from app.core.security import TokenService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.user_service import UserService


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    db: Database
    redis: Redis
    rate_limiter: RateLimiter
    tokens: TokenService
    audit: AuditService
    auth: AuthService
    users: UserService

    @classmethod
    def build(cls, settings: Settings, db: Database, redis: Redis) -> ServiceContainer:
        tokens = TokenService(settings)
        audit = AuditService(db)
        auth = AuthService(settings, db, redis, tokens, audit)
        return cls(
            settings=settings,
            db=db,
            redis=redis,
            rate_limiter=RateLimiter(redis),
            tokens=tokens,
            audit=audit,
            auth=auth,
            users=UserService(db, auth),
        )

    async def aclose(self) -> None:
        """Release resources owned by services (connection pools etc.)."""
