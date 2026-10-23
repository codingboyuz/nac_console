"""Unit of Work: one session, lazily created repositories, explicit commit."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Database
from app.repositories.base import Repository

R = TypeVar("R", bound=Repository[Any])


class UnitOfWork:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._session: AsyncSession | None = None
        self._repos: dict[type[Any], Any] = {}

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside 'async with'")
        return self._session

    def repo(self, repo_cls: type[R]) -> R:
        """Return the repository instance bound to this unit's session."""
        if repo_cls not in self._repos:
            self._repos[repo_cls] = repo_cls(self.session)
        repo: R = self._repos[repo_cls]
        return repo

    async def __aenter__(self) -> Self:
        self._session = self._db.sessionmaker()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self.session
        try:
            if exc_type is not None:
                await session.rollback()
        finally:
            await session.close()
            self._session = None
            self._repos.clear()

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
