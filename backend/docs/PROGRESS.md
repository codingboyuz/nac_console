# Progress (single source of truth)

Re-read at the start of every session.

## Phases

| Phase | Name | Status |
|------:|------|--------|
| 0 | Recon and scaffolding | done |
| 1 | Core platform | done |
| 2 | Auth, RBAC, audit | pending |
| 3 | Device inventory and credential vault | pending |
| 4 | Driver layer: SSH, Telnet, classifier, parsers | pending |
| 5 | NETCONF and SNMP | pending |
| 6 | L2 features | pending |
| 7 | ACL and routing | pending |
| 8 | Job engine, Celery, WebSocket | pending |
| 9 | Backups and config management | pending |
| 10 | Monitoring, alerts, discovery | pending |
| 11 | NAC and RADIUS | pending |
| 12 | CLI, hardening, docs, delivery | pending |

## Log

### Phase 0 - Recon and scaffolding
- Repo was empty except the prompt file. `git init`, `backend/` skeleton, `pyproject.toml` (uv, ruff,
  black, mypy strict, pytest, coverage, bandit), Makefile, pre-commit, Dockerfile, compose, `.env.example`.
- Verified: ruff, black --check, mypy app, pytest (smoke) - all green.

### Phase 1 - Core platform
- Settings with fail-fast in staging/prod, structlog JSON logging + central redaction (structlog
  processor and stdlib filter), async `Database`, problem+json handlers, ASGI middlewares (request id,
  secure headers, body size limit), CORS allowlist, trusted hosts, Redis rate limiter, health
  live/ready, Prometheus `/metrics` (network restricted).
- Tests run against embedded PostgreSQL 16 via pgserver.

## Open issues
- Docker / compose cannot be executed on the build host (no Docker). See ASSUMPTIONS A1.

## Next step
- Phase 2: users, Argon2id, JWT access/refresh rotation, RBAC matrix, audit log.
