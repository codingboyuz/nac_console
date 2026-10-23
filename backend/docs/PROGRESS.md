# Progress (single source of truth)

Re-read at the start of every session.

## Phases

| Phase | Name | Status |
|------:|------|--------|
| 0 | Recon and scaffolding | done |
| 1 | Core platform | done |
| 2 | Auth, RBAC, audit | done |
| 3 | Device inventory and credential vault | done |
| 4 | Driver layer: SSH, Telnet, classifier, parsers | done |
| 5 | NETCONF and SNMP | done |
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

### Phase 3 - Device inventory and credential vault
- `CredentialVault`: AES-256-GCM, envelope `v1:<kid>:<b64>`, AAD bound to the credential row,
  multi-key ring with active key + `rotate_keys()`.
- Models: Credential (cli / snmp_v2c / snmp_v3), DeviceGroup, Device (INET unique mgmt_ip, family,
  protocol, capabilities/tags JSONB, lockout-guard fields: mgmt_interface, mgmt_vlan, uplinks),
  SshHostKey (TOFU store with PENDING/TRUSTED/REJECTED + approve endpoint).
- Device CRUD (POST/GET/PUT/PATCH/DELETE) with whitelisted filter/sort, search, tags, group
  scoping (out-of-scope = 404), Telnet gating (flag + INSECURE_PROTOCOL permission) and
  `security_warning` in responses. Interface names normalized in `app/domain/interfaces.py`.
- Tests: secrets never in API responses, DB plaintext or logs (even explicit log attempts);
  constant query count for device listing (N+1 guard); migration 0002 round-trip.

### Phase 4 - Driver layer
- `DeviceDriver` port (`app/network/ports.py`), `DeviceConnectionFactory` (adapter per protocol,
  per-device session semaphore, default 2), Redis per-device config lock (423 when held).
- Netmiko drivers: `huawei` (S/AR, `save`) and `huawei_vrpv8` (CE, `commit`, `commit trial`,
  `abort` on error), Telnet (`huawei_telnet`, always `security_warning`). Bounded retries with
  exponential backoff + jitter (transport errors only). Exceptions mapped to domain errors
  without credentials; VRP `Error:` markers fail operations.
- SSH host keys: TOFU pin in DB on first contact (audited), strict known_hosts afterwards,
  mismatch -> 409 + PENDING key + approve endpoint.
- `CommandClassifier` (deny-by-default, view/context aware escalation), injection guard,
  `LockoutGuard`, single-use confirmation tokens bound to user+device+command hash.
- Parsers for 11 areas with golden fixtures for S5735 (VRP 5.170) and CE6881 (VRP 8.180)
  (written by a parallel subagent, 299 tests, 99% coverage).
- Endpoints: `POST /devices/{id}/test-connection`, `POST /devices/{id}/execute-command`.
- E2E: asyncssh mock Huawei CLI (S and CE behaviour) + asyncio Telnet mock.

### Phase 5 - NETCONF and SNMP
- `ModelRegistry` from <hello> capabilities (candidate/confirmed-commit/validate/... + YANG
  module selection per feature). ncclient driver with lock -> snapshot -> edit -> validate ->
  confirmed commit -> verify -> confirming commit; rollback via cancel-commit/discard-changes.
- huawei-vlan NETCONF builders (ElementTree, escaped).
- pysnmp 7 async client (v2c, v3 authPriv), collectors for system, IF-MIB (64-bit preferred),
  Huawei entity CPU/memory/temperature/fan/power; counter wrap/reset handling.
- E2E: mock NETCONF server (asyncssh subsystem), UDP SNMPv2c simulator.
- Full suite at end of phase 5: 500 passed (embedded PostgreSQL).

## Open issues
- Docker / compose cannot be executed on the build host (no Docker). See ASSUMPTIONS A1.

## Next step
- Phase 6: L2 features (VLAN, interfaces, MAC, ARP, DHCP snooping, STP) with guarded change executor.
