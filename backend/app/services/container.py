"""Composition root: wires settings, DB, Redis and services once per process."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.crypto import CredentialVault
from app.core.database import Database
from app.core.rate_limit import RateLimiter
from app.core.security import TokenService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.credential_service import CredentialService
from app.services.device_service import DeviceService
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
    vault: CredentialVault
    credentials: CredentialService
    devices: DeviceService

    @classmethod
    def build(cls, settings: Settings, db: Database, redis: Redis) -> ServiceContainer:
        tokens = TokenService(settings)
        audit = AuditService(db)
        auth = AuthService(settings, db, redis, tokens, audit)
        vault = CredentialVault(settings.credential_keys, settings.credential_active_key_id)
        return cls(
            settings=settings,
            db=db,
            redis=redis,
            rate_limiter=RateLimiter(redis),
            tokens=tokens,
            audit=audit,
            auth=auth,
            users=UserService(db, auth),
            vault=vault,
            credentials=CredentialService(db, vault),
            devices=DeviceService(db),
        )

    async def aclose(self) -> None:
        """Release resources owned by services (connection pools etc.)."""
