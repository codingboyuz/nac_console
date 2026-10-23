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
| 6 | L2 features | done |
| 7 | ACL and routing | done |
| 8 | Job engine, Celery, WebSocket | done |
| 9 | Backups and config management | done |
| 10 | Monitoring, alerts, discovery | done |
| 11 | NAC and RADIUS | done |
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

### Phase 6 - L2 features
- `ChangeService`: classify -> lockout guard (ADMIN override only with rollback timer) ->
  category RBAC -> dry-run/confirmation token -> Redis device lock -> pre/post-change hooks ->
  push -> post-verify -> automatic rollback -> save -> audit. Rollback timers: CE `commit
  trial`, S/AR `schedule reboot delay` + disarm before `save`.
- Typed idempotent builders (`app/network/builders/cli_l2.py`) driven by current state
  (`display vlan`, `display current-configuration interface X`): VLAN create/delete/rename, port
  mode access/trunk/hybrid (resets VLAN config before link-type changes), trunk allowed list,
  admin state, description, speed/duplex, static/blackhole MAC, STP mode/priority/edge, DHCP
  snooping. NETCONF VLAN path when the device advertises the huawei-vlan module.
- State tables: interfaces, vlans, mac_entries (+ mac_moves history), arp_entries, dhcp_bindings
  (migration 0003). MAC search/locate (edge ports first), ARP IP<->MAC, anomaly report
  (duplicate IP, MAC flapping, many-IPs-per-MAC, binding/ARP mismatch, duplicate leases,
  DHCP-trusted non-uplink ports, unknown MACs).
- Bulk multi-device execution is delivered with the job engine (phase 8).
- Full suite: 519 passed.

### Phase 7 - ACL and routing
- ACL domain (`app/domain/acl.py`): basic/advanced/layer-2 numbering, IPv4/IPv6, strict rule
  validation, wildcard rendering. Rule-level diff against `display acl` (undo + re-add only
  changed rules), auto rule ids (step 5), named ACLs, apply/remove `traffic-filter` (S vs CE
  syntax), global filters only on S and always DANGEROUS.
- Gating: deny-any (classifier), removing the last permit and touching a VTY-bound ACL
  (plan-level escalation) are DANGEROUS; ACLs denying the controller on VTY/management -> lockout.
- Routing: probe-based capability registry per device (static/ospf/bgp/rip/vrrp/route-policy,
  cached in `device.capabilities.routing`, 422 `unsupported_capability` otherwise); static and
  default routes (idempotent, verify route active -> rollback), OSPF, BGP, RIP, VRRP,
  route-policy/ip-prefix builders; pre/post routing-table snapshots with diff in the response;
  OSPF/BGP neighbor views. Models acls/acl_rules/routes (migration 0004).
- Confirmation gate unified (`ConfirmationService.require`): missing confirm -> 409, missing
  reason -> 422, bad token -> 409.
- Netmiko now uses `cmd_verify=True` and `fast_cli=True` (library defaults, pattern based).

### Phase 8 - Job engine, Celery, WebSocket
- Models jobs / job_device_results / settings (migration 0005). Job type registry
  (`app/services/job_types.py`) with typed params, permission and queue per type.
- `JobService`: 202 + Location, Idempotency-Key (per user; payload mismatch -> 409), scope
  checks on every target, job-level confirmation for DANGEROUS bulk changes (dry-run job issues
  a token visible only to its owner; consumed at submission), cancel (Redis flag + revoke).
- `JobRunner`: atomic PENDING->RUNNING claim (stale RUNNING reclaimed), per-device fan-out with
  concurrency cap, stop-on-failure threshold (rest SKIPPED), cancellation, progress, aggregate
  SUCCESS/PARTIAL/FAILED/CANCELLED, Redis pub/sub events. Devices run with the submitter's
  principal, so classifier/lockout/RBAC/audit apply per device.
- Celery: queues config/polling/discovery/alerts/default, acks_late, reject_on_worker_lost,
  prefetch 1, soft/hard limits, retry backoff+jitter; per-process worker runtime (persistent
  loop + container). Beat runs a 15 s tick that evaluates DB-backed schedules (settings key
  `schedules`) and submits jobs as a service account - edits apply without restarting beat.
- WebSockets `/api/v1/ws/jobs/{id}`, `/ws/monitoring`, `/ws/alerts`: single-use tickets
  (`POST /auth/ws-ticket`, query or first message), per-channel permission, close codes
  4401/4403/4404. Domain events `config_changed`, `device_online`, `device_offline`.
- Tests: in-process FakeDriver over the CLI emulator; Celery task eager run with a real worker
  runtime; schedule tick; WebSocket via httpx-ws. Full suite: 551 passed.

### Phase 9 - Backups and config management
- `backups` table (migration 0006): trigger manual/scheduled/pre-change/post-change, source
  current/saved, SHA-256 over normalized text (volatile `!Last configuration...` headers
  ignored), dedup (unchanged -> previous version), masked `content` + exact original encrypted
  with the vault (AAD `backup:<id>`), family/VRP version captured.
- Mandatory pre-change backup hook on every ChangeService apply (failure aborts the change),
  post-change backup; out-of-band changes detected by scheduled/manual backups emit
  `configuration_change`.
- Unified diff between any two backups, known-good baseline, drift report vs running config.
- Guarded restore: ADMIN/NETWORK_ADMIN + backups:restore, dry-run preview (lines + diff, never
  secrets), DANGEROUS token + reason, family/VRP-version match, device-side rollback timer,
  audit. Restore = best-effort merge plan (`app/domain/config_text.restore_plan`, VLAN batch
  as set difference, removals last).
- `backup.run` job type + `POST /devices/{id}/backup` (202). Full suite: 563 passed.

### Phase 10 - Monitoring, alerts, discovery
- `metric_samples` partitioned by RANGE (ts) on PostgreSQL (DEFAULT partition in migration
  0007; hourly maintenance creates 3 monthly partitions ahead - moving any rows out of DEFAULT
  first - and drops partitions past `METRIC_RETENTION_DAYS`). SQLite falls back to deletes.
- Polling (`poll.metrics` job, `POST /devices/{id}/poll`): SNMP when `snmp_credential_id`
  is set (system, entity CPU/memory/temperature/fan/power, IF-MIB 64-bit counters -> bps,
  error rates, utilisation), CLI fallback (`display cpu-usage/memory-usage/interface brief`).
  Live fan-out on `events:monitoring`; history/latest APIs.
- Alert engine: rules with for-duration + hysteresis, dedup by key, flap suppression,
  maintenance windows, FIRING -> ACKNOWLEDGED -> RESOLVED, event alerts (device_offline/online,
  configuration_change, unknown_mac, authentication_failures). Default rules seeded.
  Channels: SMTP STARTTLS, HMAC-SHA256 signed webhook (`X-NAC-Signature`), Telegram
  ("Huawei SW-01 GigabitEthernet0/0/2 is DOWN"). Every attempt recorded; 5 attempts then DEAD;
  Celery `alerts.deliver` with backoff in production.
- Discovery: CIDR must be <= /22 and inside `DISCOVERY_ALLOWED_SUBNETS`; paced ICMP (rate +
  concurrency caps) -> SNMP with chosen stored credentials -> SSH banner read; proposals only,
  explicit adopt (409 on re-adopt) / ignore. Audited.
- Settings API (`schedules`, `alert_channels`), typed and audited; secrets stay in env.
- Found and fixed a real PostgreSQL issue (cannot attach a month partition while DEFAULT holds
  rows for it). Full suite: 582 passed.

### Phase 11 - NAC and RADIUS
- ADR `docs/adr/0001-radius-architecture.md`: FreeRADIUS (rlm_rest) in production, backend
  owns policy/endpoints/audit; pyrad reference server for dev/test only (flag, refused in prod).
- Models endpoints / nac_policies (+versions) / nac_sessions / radius_servers (migration 0008).
- Policy engine (`app/domain/nac/policy.py`): pydantic discriminated unions of whitelisted
  conditions (mac_registered, endpoint_group_is, user_in_group, device_type_is, oui_in,
  time_window, status_is, auth_method_is, site_is, all_of/any_of/not, always) and actions
  (assign_vlan with Filter-Id/Session-Timeout, guest, quarantine, reject, accept); ascending
  priority, first match, mandatory default (unknown -> quarantine VLAN), versioning,
  `POST /nac/policies/simulate`.
- Internal API `/internal/v1/radius/authorize|accounting`, `/internal/v1/profiling/dhcp`:
  network allowlist + token (header or HTTP Basic for rlm_rest) + separate rate limit.
  rlm_rest JSON accepted; reply `reply:<Attr>`; 401 = reject. Decisions audited with policy id;
  fail-closed (quarantine) by default, per-site fail-open via settings `nac.site_fail_modes`.
  5 rejects in 5 min -> `authentication_failures` alert.
- Profiling: OUI, DHCP option 55 / vendor class, LLDP hints with confidence + reasons.
- CoA client (RFC 5176 Disconnect/CoA-Request) over a portable UDP exchange (pyrad's client
  needs select.poll, unavailable on Windows), audited; endpoint block disconnects sessions.
- Switch-side provisioning generator (S + CE, VRP-VERSION-DEPENDENT), preview with masked
  secrets and diff against current AAA/RADIUS config, deploy only via `nac.provision` job with
  job-level DANGEROUS confirmation. FreeRADIUS templates in `deploy/freeradius/`.
- Fixed during the phase: default NAC policy missing when a custom policy was created first;
  classification reasons quoted raw lines (could expose RADIUS keys) - now redacted at source.
- Full suite: 621 passed.

## Open issues
- Docker / compose cannot be executed on the build host (no Docker). See ASSUMPTIONS A1.
- A real Celery worker against a real Redis broker was not run on the build host (no Redis); the task runs eagerly in tests.

## Next step
- Phase 12: Typer CLI, security review (bandit, pip-audit), docs, CI workflow, OpenAPI export, HARDWARE_VALIDATION.
