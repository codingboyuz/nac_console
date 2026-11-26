# ruff: noqa: T201 - console launcher: prints are its user interface
"""Local all-in-one backend runner (no Docker / PostgreSQL / Redis install needed).

* Database: SQLite file backend/.localdev/nac.db, so devices, credentials and users survive
  restarts. Set NAC_DATABASE_URL (e.g. postgresql+asyncpg://...) to use PostgreSQL instead.
* Redis: in-process ``fakeredis``; jobs run inline in the API process.
* Secrets (JWT, internal token, credential-vault key) are generated once and kept in
  backend/.localdev/secrets.json - losing that file makes stored credentials unreadable.
* First start creates user 'admin' with a random password (printed once, also saved to
  backend/.localdev/ADMIN_PASSWORD.txt).

For local use only: production runs with real PostgreSQL / Redis / Celery (docs/DEPLOYMENT.md).
Environment overrides: NAC_HOST (127.0.0.1), NAC_PORT (8000), NAC_DISCOVERY_SUBNETS
(comma separated, default private ranges), NAC_CONTROLLER_IPS, NAC_INTERNAL_ALLOWED_NETWORKS
(source networks allowed to call /internal/*, e.g. the FreeRADIUS server; default localhost).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND / ".localdev"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)


def _secrets() -> dict[str, str]:
    path = DATA / "secrets.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    values = {
        "jwt_secret": secrets.token_urlsafe(48),
        "internal_api_token": secrets.token_urlsafe(48),
        "credential_keys": "k1:" + base64.b64encode(secrets.token_bytes(32)).decode(),
    }
    path.write_text(json.dumps(values, indent=2), encoding="utf-8")
    return values


def _database() -> str:
    explicit = os.environ.get("NAC_DATABASE_URL")
    if explicit:
        return explicit
    return f"sqlite+aiosqlite:///{(DATA / 'nac.db').as_posix()}"


def _migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


async def _seed_admin(app: object) -> None:
    from pydantic import SecretStr
    from sqlalchemy import func, select

    from app.core.rbac import Role
    from app.models.user import User
    from app.schemas.auth import UserCreate

    while not hasattr(app.state, "services"):  # type: ignore[attr-defined]  # noqa: ASYNC110 - state is set by the app lifespan
        await asyncio.sleep(0.1)
    services = app.state.services  # type: ignore[attr-defined]
    async with app.state.db.session() as session:  # type: ignore[attr-defined]
        users = await session.scalar(select(func.count()).select_from(User))
    if users:
        return
    password = secrets.token_urlsafe(12) + "!9a"
    await services.users.create(
        UserCreate(username="admin", password=SecretStr(password), role=Role.ADMIN)
    )
    (DATA / "ADMIN_PASSWORD.txt").write_text(f"admin\n{password}\n", encoding="utf-8")
    bar = "=" * 64
    print(
        f"\n{bar}\n  Birinchi ishga tushirish: administrator yaratildi\n"
        f"  login:  admin\n  parol:  {password}\n"
        f"  (backend/.localdev/ADMIN_PASSWORD.txt faylida ham saqlandi)\n{bar}\n",
        flush=True,
    )


async def _serve(url: str, keys: dict[str, str]) -> None:
    import uvicorn
    from fakeredis import FakeAsyncRedis

    from app.core.config import Environment, Settings
    from app.main import create_app

    host = os.environ.get("NAC_HOST", "127.0.0.1")
    port = int(os.environ.get("NAC_PORT", "8000"))
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment=Environment.DEV,
        database_url=url,
        redis_url="redis://in-process",
        job_dispatch="inline",
        jwt_secret=keys["jwt_secret"],
        internal_api_token=keys["internal_api_token"],
        credential_keys=keys["credential_keys"],
        credential_active_key_id="k1",
        log_json=False,
        log_level="INFO",
        discovery_allowed_subnets=os.environ.get(
            "NAC_DISCOVERY_SUBNETS", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
        ),
        controller_ips=os.environ.get("NAC_CONTROLLER_IPS", ""),
        internal_allowed_networks=os.environ.get(
            "NAC_INTERNAL_ALLOWED_NETWORKS", "127.0.0.1/32,::1/128"
        ),
    )
    app = create_app(settings, redis=FakeAsyncRedis(decode_responses=True))
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="info", ws="websockets-sansio")
    )
    print(f"[nac] backend: http://{host}:{port}  (API docs: http://{host}:{port}/docs)", flush=True)
    await asyncio.gather(server.serve(), _seed_admin(app))


def main() -> None:
    DATA.mkdir(exist_ok=True)
    keys = _secrets()
    url = _database()
    print(
        f"[nac] ma'lumotlar bazasi: {url.split('://', 1)[0]} ({url.rsplit('/', 1)[-1]})", flush=True
    )
    print("[nac] migratsiyalar...", flush=True)
    _migrate(url)
    asyncio.run(_serve(url, keys))


if __name__ == "__main__":
    main()
