"""Alembic upgrade/downgrade round-trip on a clean database."""

from __future__ import annotations

import asyncio
import secrets
import tempfile
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import Base
from tests.conftest import run_alembic


async def _admin(url: str, sql: str) -> None:
    engine = create_async_engine(url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    await engine.dispose()
    return names


def test_upgrade_downgrade_roundtrip(database_url: str) -> None:
    if database_url.startswith("postgresql"):
        name = f"nac_migr_{secrets.token_hex(4)}"
        asyncio.run(_admin(database_url, f"CREATE DATABASE {name}"))
        url = database_url.rsplit("/", 1)[0] + f"/{name}"
    else:
        name = ""
        url = f"sqlite+aiosqlite:///{(Path(tempfile.mkdtemp()) / 'm.db').as_posix()}"
    try:
        run_alembic(url, "upgrade", "head")
        tables = asyncio.run(_tables(url))
        assert {t.name for t in Base.metadata.sorted_tables} <= tables
        run_alembic(url, "downgrade", "base")
        assert asyncio.run(_tables(url)) <= {"alembic_version"}
        run_alembic(url, "upgrade", "head")
        run_alembic(url, "check")  # models and migrations are in sync
    finally:
        if name:
            asyncio.run(_admin(database_url, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
