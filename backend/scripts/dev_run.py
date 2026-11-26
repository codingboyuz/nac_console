# ruff: noqa: T201 - console launcher: prints are its user interface
"""Local all-in-one backend runner (no Docker / PostgreSQL / Redis install needed).

* Settings: repo-root ``nac.conf`` (created from ``nac.conf.example``; not in git; NAC_CONF
  points elsewhere). Environment
  variables override it: NAC_HOST, NAC_PORT, NAC_PUBLIC_HOST, NAC_ADMIN_USERNAME,
  NAC_ADMIN_PASSWORD, NAC_INTERNAL_ALLOWED_NETWORKS, NAC_DISCOVERY_SUBNETS, NAC_CONTROLLER_IPS,
  NAC_DATABASE_URL.
* Database: SQLite file backend/.localdev/nac.db, so devices, credentials and users survive
  restarts. NAC_DATABASE_URL (e.g. postgresql+asyncpg://...) switches to PostgreSQL.
* Redis: in-process ``fakeredis``; jobs run inline in the API process.
* Secrets (JWT, internal token, credential-vault key) are generated once and kept in
  backend/.localdev/secrets.json - losing that file makes stored credentials unreadable.
* Administrator: user ADMIN_USERNAME always exists with role ADMIN. With ADMIN_PASSWORD set its
  password follows nac.conf; otherwise a random one is generated once and saved to
  backend/.localdev/ADMIN_PASSWORD.txt.

For local use only: production runs with real PostgreSQL / Redis / Celery (docs/DEPLOYMENT.md).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
DATA = BACKEND / ".localdev"
CONF = Path(os.environ.get("NAC_CONF") or ROOT / "nac.conf")
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

_PRIVATE = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def _read_conf() -> dict[str, str]:
    """KEY=VALUE lines; '#' comments; optional surrounding quotes."""
    if not CONF.exists():
        example = ROOT / "nac.conf.example"
        if example.exists():
            CONF.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[nac] {CONF.name} yaratildi (nac.conf.example asosida)", flush=True)
        else:
            return {}
    values: dict[str, str] = {}
    for raw in CONF.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip().upper()] = value
    return values


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    public_host: str
    admin_username: str
    admin_password: str
    internal_networks: str
    discovery_subnets: str
    controller_ips: str


def _config() -> Config:
    conf = _read_conf()

    def get(env: str, key: str, default: str = "") -> str:
        return os.environ.get(env) or conf.get(key) or default

    public_host = get("NAC_PUBLIC_HOST", "PUBLIC_HOST")
    host = get("NAC_HOST", "BACKEND_HOST", "127.0.0.1")
    if public_host and host == "127.0.0.1":
        host = "0.0.0.0"  # noqa: S104 - reachable from other machines (browser WebSocket)
    port_raw = get("NAC_PORT", "BACKEND_PORT", "8000")
    if not port_raw.isdigit() or not 1 <= int(port_raw) <= 65535:
        sys.exit(f"[nac] XATO: BACKEND_PORT noto'g'ri: {port_raw!r}")
    username = get("NAC_ADMIN_USERNAME", "ADMIN_USERNAME", "admin")
    if not _USERNAME.match(username):
        sys.exit(
            f"[nac] XATO: ADMIN_USERNAME noto'g'ri: {username!r} "
            "(3-64 belgi: harf, raqam, '_', '.', '-')"
        )
    password = get("NAC_ADMIN_PASSWORD", "ADMIN_PASSWORD")
    if password:
        classes = sum(
            [
                any(c.islower() for c in password),
                any(c.isupper() for c in password),
                any(c.isdigit() for c in password),
                any(not c.isalnum() for c in password),
            ]
        )
        if len(password) < 12 or classes < 3:
            sys.exit(
                "[nac] XATO: nac.conf dagi ADMIN_PASSWORD juda oddiy. Talab: kamida 12 belgi, "
                "kichik harf / katta harf / raqam / belgidan kamida 3 turi."
            )
    return Config(
        host=host,
        port=int(port_raw),
        public_host=public_host,
        admin_username=username,
        admin_password=password,
        internal_networks=get(
            "NAC_INTERNAL_ALLOWED_NETWORKS", "INTERNAL_ALLOWED_NETWORKS", "127.0.0.1/32,::1/128"
        ),
        discovery_subnets=get("NAC_DISCOVERY_SUBNETS", "DISCOVERY_SUBNETS", _PRIVATE),
        controller_ips=get("NAC_CONTROLLER_IPS", "CONTROLLER_IPS"),
    )


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
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def _banner(*lines: str) -> None:
    bar = "=" * 64
    print(
        "\n" + bar + "\n" + "\n".join(f"  {line}" for line in lines) + "\n" + bar + "\n", flush=True
    )


async def _ensure_admin(app: object, cfg: Config) -> None:
    """User cfg.admin_username exists, is an active ADMIN and (if configured) has the password."""
    from datetime import UTC, datetime

    from pydantic import SecretStr
    from sqlalchemy import select

    from app.core.rbac import Role
    from app.core.security import hash_password, verify_password
    from app.models.user import User
    from app.schemas.auth import UserCreate

    while not hasattr(app.state, "services"):  # type: ignore[attr-defined]  # noqa: ASYNC110 - state is set by the app lifespan
        await asyncio.sleep(0.1)
    services = app.state.services  # type: ignore[attr-defined]
    pw_file = DATA / "ADMIN_PASSWORD.txt"

    async with app.state.db.session() as session:  # type: ignore[attr-defined]
        user = await session.scalar(select(User).where(User.username == cfg.admin_username))
        if user is not None:
            changes = []
            if cfg.admin_password and not verify_password(cfg.admin_password, user.password_hash):
                user.password_hash = hash_password(cfg.admin_password)
                user.password_changed_at = datetime.now(UTC)
                changes.append("parol nac.conf bo'yicha yangilandi")
            if user.role != Role.ADMIN or not user.is_active or user.locked_until:
                user.role, user.is_active, user.locked_until = Role.ADMIN, True, None
                user.failed_logins = 0
                changes.append("ADMIN roli / faollik tiklandi")
            if changes:
                await session.commit()
                print(
                    f"[nac] administrator '{cfg.admin_username}': {', '.join(changes)}", flush=True
                )
            if cfg.admin_password and pw_file.exists():
                pw_file.unlink()  # the password now lives in nac.conf
            return

    password = cfg.admin_password or (secrets.token_urlsafe(12) + "!9a")
    await services.users.create(
        UserCreate(username=cfg.admin_username, password=SecretStr(password), role=Role.ADMIN)
    )
    if cfg.admin_password:
        pw_file.unlink(missing_ok=True)
        _banner(
            f"Administrator yaratildi: login {cfg.admin_username}",
            "parol: nac.conf dagi ADMIN_PASSWORD",
        )
    else:
        pw_file.write_text(f"{cfg.admin_username}\n{password}\n", encoding="utf-8")
        _banner(
            "Administrator yaratildi",
            f"login:  {cfg.admin_username}",
            f"parol:  {password}",
            "(backend/.localdev/ADMIN_PASSWORD.txt faylida ham saqlandi;",
            " o'z parolingizni nac.conf dagi ADMIN_PASSWORD ga yozishingiz mumkin)",
        )


async def _serve(url: str, keys: dict[str, str], cfg: Config) -> None:
    import uvicorn
    from fakeredis import FakeAsyncRedis

    from app.core.config import Environment, Settings
    from app.main import create_app

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
        discovery_allowed_subnets=cfg.discovery_subnets,
        controller_ips=cfg.controller_ips,
        internal_allowed_networks=cfg.internal_networks,
    )
    app = create_app(settings, redis=FakeAsyncRedis(decode_responses=True))
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="info", ws="websockets-sansio")
    )
    shown = cfg.public_host or cfg.host
    print(
        f"[nac] backend: http://{shown}:{cfg.port}  (API: http://{shown}:{cfg.port}/docs)",
        flush=True,
    )
    await asyncio.gather(server.serve(), _ensure_admin(app, cfg))


def main() -> None:
    DATA.mkdir(exist_ok=True)
    cfg = _config()
    keys = _secrets()
    url = _database()
    print(
        f"[nac] ma'lumotlar bazasi: {url.split('://', 1)[0]} ({url.rsplit('/', 1)[-1]})", flush=True
    )
    print("[nac] migratsiyalar...", flush=True)
    _migrate(url)
    asyncio.run(_serve(url, keys, cfg))


if __name__ == "__main__":
    main()
