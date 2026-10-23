"""Shared fixtures: settings, database (embedded PostgreSQL or SQLite), fake Redis, app client."""

from __future__ import annotations

import base64
import os
import secrets
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import Environment, Settings
from app.core.database import Database

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "huawei"


def _test_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def _discover_database_url() -> tuple[str, Any]:
    """TEST_DATABASE_URL > embedded PostgreSQL (pgserver) > SQLite file."""
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit, None
    if os.environ.get("TEST_USE_SQLITE") != "1":
        try:
            import pgserver

            server = pgserver.get_server(tempfile.mkdtemp(prefix="nacpg-"), cleanup_mode="stop")
            server.psql("DROP DATABASE IF EXISTS nac_test;")
            server.psql("CREATE DATABASE nac_test;")
            uri = server.get_uri().rsplit("/", 1)[0] + "/nac_test"
            return uri.replace("postgresql://", "postgresql+asyncpg://", 1), server
        except Exception as exc:  # fall back to sqlite when pgserver is unavailable
            print(f"pgserver unavailable, using SQLite: {exc}")  # noqa: T201
    path = Path(tempfile.mkdtemp(prefix="nacsqlite-")) / "test.db"
    return f"sqlite+aiosqlite:///{path.as_posix()}", None


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url, server = _discover_database_url()
    yield url
    if server is not None:
        server.cleanup()


def run_alembic(url: str, *args: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    verb, *rest = args
    getattr(command, verb)(cfg, *rest)


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> str:
    run_alembic(database_url, "upgrade", "head")
    return database_url


def make_settings(database_url: str = "sqlite+aiosqlite://", **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": Environment.TEST,
        "database_url": database_url,
        "redis_url": "redis://fake",
        "jwt_secret": "test-jwt-secret-" + "x" * 32,
        "internal_api_token": "test-internal-token-" + "y" * 32,
        "credential_keys": f"k1:{_test_key()}",
        "credential_active_key_id": "k1",
        "log_json": True,
        "log_level": "WARNING",
        "trusted_hosts": ["*"],
        "cors_origins": ["http://localhost:5173"],
        "discovery_allowed_subnets": ["10.0.0.0/16", "192.0.2.0/24"],
        "controller_ips": ["10.0.0.5"],
        "job_dispatch": "inline",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def settings(migrated_database: str) -> Settings:
    return make_settings(migrated_database)


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
async def db(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings.database_url)
    yield database
    await _truncate_all(database)
    await database.dispose()


async def _truncate_all(database: Database) -> None:
    from app.models import Base

    tables = [t.name for t in reversed(Base.metadata.sorted_tables)]
    if not tables:
        return
    async with database.engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            quoted = ", ".join(f'"{name}"' for name in tables)
            await conn.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
        else:
            for name in tables:
                await conn.execute(text(f'DELETE FROM "{name}"'))  # noqa: S608 - test helper


@pytest.fixture
async def app(settings: Settings, db: Database, redis: FakeAsyncRedis) -> AsyncIterator[Any]:
    from app.main import create_app

    application = create_app(settings, db=db, redis=redis)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


# --- identity helpers ---------------------------------------------------------------------

TEST_PASSWORD = "Str0ng!Passw0rd"  # test-only placeholder


@pytest.fixture
def user_factory(app: Any) -> Any:
    """Create users directly through the service layer."""
    from pydantic import SecretStr

    from app.core.rbac import Role
    from app.schemas.auth import UserCreate

    async def _create(role: Role = Role.ADMIN, username: str | None = None) -> Any:
        name = username or f"{role.value.lower()}_{secrets.token_hex(3)}"
        data = UserCreate(username=name, password=SecretStr(TEST_PASSWORD), role=role)
        return await app.state.services.users.create(data)

    return _create


@pytest.fixture
def login(client: AsyncClient) -> Any:
    async def _login(username: str, password: str = TEST_PASSWORD) -> dict[str, Any]:
        resp = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    return _login


@pytest.fixture
def auth_headers(user_factory: Any, login: Any) -> Any:
    """``await auth_headers(Role.X)`` -> Authorization header for a fresh user of that role."""

    async def _headers(role: Any) -> dict[str, str]:
        user = await user_factory(role)
        tokens = await login(user.username)
        return {"Authorization": f"Bearer {tokens['access_token']}"}

    return _headers
