"""CLI runtime: builds the same ServiceContainer the API uses and resolves the acting principal.

The CLI never bypasses RBAC, the classifier or audit: every command runs as the configured
user (``CLI_USERNAME``, normally a service account) through the service layer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.exceptions import Unauthorized
from app.core.redis import create_redis
from app.domain.identity import Principal, RequestContext
from app.models.user import User
from app.repositories.uow import UnitOfWork
from app.services.container import ServiceContainer

T = TypeVar("T")
CLI_CTX = RequestContext(source_ip=None, user_agent="cli", request_id=None)


@asynccontextmanager
async def _default_services(settings: Settings) -> Any:
    db = Database.from_settings(settings)
    redis = create_redis(settings.redis_url)
    services = ServiceContainer.build(settings, db, redis)
    try:
        yield services
    finally:
        await services.aclose()
        await redis.aclose()
        await db.dispose()


class CliRuntime:
    """Replaceable in tests via ``CliRuntime.services_factory``."""

    services_factory: Callable[[Settings], Any] = staticmethod(_default_services)  # type: ignore[assignment]

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def run(self, fn: Callable[[ServiceContainer], Awaitable[T]]) -> T:
        async def main() -> T:
            async with self.services_factory(self.settings) as services:
                return await fn(services)

        return _run(main())

    async def principal(self, services: ServiceContainer, username: str | None = None) -> Principal:
        name = username or self.settings.cli_username
        async with UnitOfWork(services.db) as uow:
            user = await uow.session.scalar(select(User).where(User.username == name))
        if user is None or not user.is_active:
            raise Unauthorized(f"CLI user {name!r} does not exist or is disabled")
        return Principal(
            user.id, user.username, user.role, is_service_account=user.is_service_account
        )


def _run[R](coro: Coroutine[Any, Any, R]) -> R:
    return asyncio.run(coro)
