"""Async SQLAlchemy engine/session management. One Database instance per process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import Settings


class Database:
    """Owns the engine and session factory; stored on app.state / worker context."""

    def __init__(self, url: str, *, echo: bool = False, pool_size: int = 10, overflow: int = 10):
        kwargs: dict[str, object] = {"echo": echo, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs = {"echo": echo, "poolclass": StaticPool}
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs.update(pool_size=pool_size, max_overflow=overflow)
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self.sessionmaker = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        return cls(
            settings.database_url,
            echo=settings.database_echo,
            pool_size=settings.database_pool_size,
            overflow=settings.database_max_overflow,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session; commit is the caller's (Unit of Work) responsibility."""
        async with self.sessionmaker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def ping(self) -> bool:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self.engine.dispose()
