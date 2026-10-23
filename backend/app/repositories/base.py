"""Generic async repository with whitelisted filtering, sorting and pagination."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.exceptions import NotFound, ValidationFailed
from app.models.base import Base


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: Sequence[T]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class ListQuery:
    limit: int = 50
    offset: int = 0
    sort: str | None = None
    filters: Mapping[str, Any] | None = None


class Repository[M: Base]:
    """CRUD for one model. Subclasses declare ``sortable`` / ``filterable`` columns."""

    model: type[M]
    sortable: Mapping[str, InstrumentedAttribute[Any]] = {}
    filterable: Mapping[str, InstrumentedAttribute[Any]] = {}

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: uuid.UUID) -> M | None:
        return await self.session.get(self.model, entity_id)

    async def get_or_404(self, entity_id: uuid.UUID) -> M:
        entity = await self.get(entity_id)
        if entity is None:
            raise NotFound(f"{self.model.__name__} not found")
        return entity

    def add(self, entity: M) -> M:
        self.session.add(entity)
        return entity

    async def delete(self, entity: M) -> None:
        await self.session.delete(entity)

    async def flush(self) -> None:
        await self.session.flush()

    def base_query(self) -> Select[M]:
        return select(self.model)

    def _apply_filters(self, stmt: Select[Any], filters: Mapping[str, Any] | None) -> Select[Any]:
        for name, value in (filters or {}).items():
            if value is None:
                continue
            column = self.filterable.get(name)
            if column is None:
                raise ValidationFailed(f"filter not allowed: {name}")
            stmt = stmt.where(column == value)
        return stmt

    def _apply_sort(self, stmt: Select[Any], sort: str | None) -> Select[Any]:
        if not sort:
            return stmt
        descending = sort.startswith("-")
        column = self.sortable.get(sort.lstrip("-+"))
        if column is None:
            raise ValidationFailed(f"sort not allowed: {sort}")
        return stmt.order_by(column.desc() if descending else column.asc())

    async def list(self, query: ListQuery, stmt: Select[M] | None = None) -> Page[M]:
        stmt = self._apply_filters(stmt if stmt is not None else self.base_query(), query.filters)
        total_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.session.execute(total_stmt)).scalar_one())
        stmt = self._apply_sort(stmt, query.sort).limit(query.limit).offset(query.offset)
        items: Sequence[M] = (await self.session.scalars(stmt)).all()
        return Page(items=items, total=total, limit=query.limit, offset=query.offset)
