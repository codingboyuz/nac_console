# Huawei NAC Platform - Claude Code rules
Stack: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, Celery, Redis, PostgreSQL.
Commands: make install | make lint | make type | make test | make migrate | make run | make worker
Layering: api -> services -> repositories/network drivers. Never import api from services. Never import drivers from api.
Every device-touching operation: classify command -> authorize (RBAC) -> audit -> execute in worker or threadpool.
Never log secrets. Use SecretStr and the central redaction filter.
Network tests use fake Huawei CLI fixtures under tests/fixtures/huawei/. No real device is required.
Update docs/PROGRESS.md after each phase.

## Local notes
- All commands run from `backend/`. Without `make`, use the raw commands in the Makefile
  (`uv run ruff check .`, `uv run black --check .`, `uv run mypy app`, `uv run pytest`).
- Integration tests use an embedded PostgreSQL (`pgserver`) when available, otherwise SQLite;
  set `TEST_DATABASE_URL` to force a specific database. Redis is `fakeredis` in tests.
- Source of truth for status: `docs/PROGRESS.md`. Decisions: `docs/ASSUMPTIONS.md`, `docs/adr/`.
