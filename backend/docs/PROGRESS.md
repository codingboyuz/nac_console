# Progress (single source of truth)

Re-read at the start of every session.

## Phases

| Phase | Name | Status |
|------:|------|--------|
| 0 | Recon and scaffolding | done |
| 1 | Core platform | done |
| 2 | Auth, RBAC, audit | done |
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

### Phase 2 - Auth, RBAC, audit
- Users (Argon2id via argon2-cffi, rehash on login), lockout after N failures (423), JWT access
  (PyJWT, iss/exp/jti, typ=access), opaque refresh tokens stored as SHA-256 with rotation and
  family revocation on reuse, Redis jti denylist and per-user `valid_after` revocation.
- RBAC: 5 roles, permission matrix in `app/core/rbac.py`, enforced by `require(Permission)`
  dependencies; denials are audited.
- Audit: append-only `audit_logs` (PostgreSQL trigger blocks UPDATE/DELETE), service-level
  records + blanket ASGI audit middleware for mutating requests and 401/403 (no duplicates via a
  per-request context marker).
- Migration 0001; alembic upgrade/downgrade/check round-trip test on a fresh PostgreSQL database.

## Open issues
- Docker / compose cannot be executed on the build host (no Docker). See ASSUMPTIONS A1.

## Next step
- Phase 3: device inventory, groups, credential vault (AES-256-GCM, key rotation).
