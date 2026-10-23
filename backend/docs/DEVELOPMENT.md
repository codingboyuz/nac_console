# Development

## Setup

```bash
cd backend
pip install uv            # or: https://docs.astral.sh/uv/
uv sync --all-groups      # Python 3.12, lockfile committed (uv.lock)
uv run pre-commit install
cp .env.example .env      # dev defaults work with ENVIRONMENT=dev
```

## Everyday commands

| make | without make |
|---|---|
| `make lint` | `uv run ruff check . && uv run black --check .` |
| `make type` | `uv run mypy app` (strict) |
| `make test` | `uv run pytest` |
| `make cov` | `uv run pytest --cov=app --cov-report=term-missing` |
| `make migrate` | `uv run alembic upgrade head` |
| `make run` | `uv run uvicorn app.main:create_app --factory --reload` |
| `make worker` | `uv run celery -A app.workers.celery_app worker -Q config,polling,discovery,alerts,default` |
| `make beat` | `uv run celery -A app.workers.celery_app beat` |
| `make openapi` | `uv run python -m app.cli openapi export --output openapi.json` |
| `make security` | `uv run bandit -c pyproject.toml -r app && uv run pip-audit` |

Without Redis/Celery locally set `JOB_DISPATCH=inline` to run jobs inside the API process.

## Tests

- No real Huawei device and no internet access are needed.
- Database: `TEST_DATABASE_URL` if set, otherwise an **embedded PostgreSQL 16** (`pgserver`),
  otherwise SQLite (`TEST_USE_SQLITE=1` forces SQLite). Migrations are applied with Alembic
  once per session; tables are truncated between tests.
- Redis: `fakeredis` (with Lua for distributed locks). Jobs run inline.
- Fake devices:
  - `tests/e2e/cli_emulator.py` - stateful VRP CLI (views, VLANs, interfaces, ACLs, save,
    commit/abort, schedule reboot) replaying fixtures from `tests/fixtures/huawei/`.
  - `tests/e2e/mock_huawei.py` - the emulator served over SSH (asyncssh) and Telnet.
  - `tests/e2e/mock_netconf.py` - NETCONF 1.0 server (candidate/confirmed-commit/validate).
  - `tests/e2e/snmp_simulator.py` - UDP SNMPv2c agent with a Huawei MIB view.
  - `tests/fakes.py` - in-process FakeDriver (fast service/job tests).
- Parser fixtures exist for S5735 (VRP 5.170 / V200R019) and CE6881 (VRP 8.180), plus error
  outputs; add real captures from lab devices as new fixture folders.
- Coverage gates (CI): services, network, parsers and NAC >= 85 %.

## Adding a feature

1. Pure logic in `app/domain` (with unit tests).
2. A typed builder in `app/network/builders` returning a `ConfigPlan` (lines, rollback,
   verifier, optional escalation) - never concatenate raw user input into CLI.
3. A service method that reads current state, builds the plan and calls `ChangeService.apply`.
4. An API route with `require(Permission.X)` and a `ChangeRequest`-based body.
5. Optional: a job type in `app/services/job_types.py` for bulk execution.
6. Tests: builder unit test, emulator-backed integration test, RBAC/denial test.
7. Update `docs/PROGRESS.md` (and `ASSUMPTIONS.md` / `HARDWARE_VALIDATION.md` when relevant).

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe change"   # review the generated file
uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head
```

`tests/integration/test_migrations.py` runs upgrade -> downgrade base -> upgrade -> `alembic
check` on a fresh database.
