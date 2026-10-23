# Security

This platform administers production network equipment. It is for **authorized network
administration only**: it acts exclusively on explicitly registered devices and explicitly
allowed subnets, and contains no scanning, credential guessing, stealth or persistence features.

## Threat model (summary)

| Asset | Threat | Control |
|---|---|---|
| Device credentials, SNMP communities/keys, RADIUS secrets | DB dump, log leakage, API exposure | AES-256-GCM vault with key ids + rotation (`vault rotate-keys`), AAD bound to the row id; write-only API fields; central redaction (structlog processor + stdlib filter + transport output); `SecretStr` everywhere |
| Device configuration (backups) | Disclosure of cipher strings / topology | Masked copy for display/diff; exact copy encrypted; `backups:read` not granted to READ_ONLY |
| Network availability | Operator error, malicious change, self-lockout | Deny-by-default command classifier, DANGEROUS gate (NETWORK_ADMIN+, dry-run token bound to user+device+payload hash, confirm, reason), lockout guard (mgmt interface/VLAN/uplinks/VTY ACL) with ADMIN-only override + device-side rollback timer, mandatory pre-change backup, post-verify + rollback, per-device Redis lock, VTY session cap |
| CLI injection | Newline/control chars, chaining, free text in CLI | `validate_cli_line` (printable ASCII only, no `\r`/`\n`/`;`/`&&`/backticks/`$(`, pipes only as display filters), typed builders, `quote_free_text` for descriptions/names; no endpoint accepts raw configuration |
| Identity | Credential stuffing, token theft | Argon2id, lockout (423), rate limits on auth, short-lived JWT access tokens, refresh rotation with family revocation on reuse, jti denylist, per-user `valid_after` revocation on role/password change, single-use WebSocket tickets (no JWT in URLs) |
| Internal RADIUS API | Spoofed authorization decisions | Source-network allowlist, shared token (constant-time compare; header or HTTP Basic), separate rate limit, not used by the frontend; put mTLS at the reverse proxy |
| SSH MITM | Rogue device answering on the mgmt IP | Host-key TOFU pin in DB, strict verification afterwards, mismatch -> 409 + PENDING key requiring `devices:hostkey_approve` |
| Audit integrity | Tampering, repudiation | Append-only `audit_logs` (PostgreSQL trigger blocks UPDATE/DELETE), service-level records for every device/admin operation, blanket middleware for other mutations and all 401/403 denials |
| Discovery misuse | Scanning third-party networks | CIDR must be <= /22 **and** inside `DISCOVERY_ALLOWED_SUBNETS`; ICMP paced (rate + concurrency caps); SNMP only with chosen stored credentials; SSH banner read only; proposals require explicit adoption |

## RBAC matrix

Defined in `app/core/rbac.py` and enforced by `require(Permission.X)` dependencies; a test keeps
this table in sync with the code. DANGEROUS operations additionally require `role >=
NETWORK_ADMIN`. Group scoping: users with a non-empty `group_scope` only see devices in those
groups (out-of-scope objects return 404).

<!-- RBAC:BEGIN -->
| Permission | ADMIN | NETWORK_ADMIN | OPERATOR | AUDITOR | READ_ONLY |
|---|---|---|---|---|---|
| `users:read` | yes | yes | - | yes | - |
| `users:write` | yes | - | - | - | - |
| `devices:read` | yes | yes | yes | yes | yes |
| `devices:write` | yes | yes | - | - | - |
| `devices:delete` | yes | yes | - | - | - |
| `credentials:write` | yes | yes | - | - | - |
| `devices:insecure_protocol` | yes | yes | - | - | - |
| `devices:hostkey_approve` | yes | yes | - | - | - |
| `device:test` | yes | yes | yes | - | - |
| `device:show` | yes | yes | yes | - | - |
| `device:configure` | yes | yes | yes | - | - |
| `device:dangerous` | yes | yes | - | - | - |
| `backups:read` | yes | yes | yes | yes | - |
| `backups:create` | yes | yes | yes | - | - |
| `backups:restore` | yes | yes | - | - | - |
| `jobs:read` | yes | yes | yes | yes | yes |
| `jobs:create` | yes | yes | yes | - | - |
| `jobs:cancel` | yes | yes | yes | - | - |
| `monitoring:read` | yes | yes | yes | yes | yes |
| `alerts:read` | yes | yes | yes | yes | yes |
| `alerts:ack` | yes | yes | yes | - | - |
| `alerts:write` | yes | yes | - | - | - |
| `discovery:run` | yes | yes | yes | - | - |
| `discovery:adopt` | yes | yes | - | - | - |
| `nac:read` | yes | yes | yes | yes | yes |
| `nac:write` | yes | yes | yes | - | - |
| `nac:policy_write` | yes | yes | - | - | - |
| `radius:read` | yes | yes | yes | yes | yes |
| `radius:write` | yes | yes | - | - | - |
| `audit:read` | yes | yes | - | yes | - |
| `settings:read` | yes | yes | yes | yes | - |
| `settings:write` | yes | - | - | - | - |
<!-- RBAC:END -->

## Command safety

1. Every line is validated (`app/domain/commands/sanitize.py`).
2. The classifier (`app/domain/commands/classifier.py`, rules in `rules.py`) returns SAFE /
   WARNING / DANGEROUS. Unknown commands are DANGEROUS. Context escalation: shutdown or VLAN
   removal on uplinks/LAG/management, removing the management address or VLAN, ACLs on
   protected interfaces, deny-any rules, AAA/VTY/user changes.
3. `execute-command` accepts display-class commands for roles below NETWORK_ADMIN; configuration
   happens through typed endpoints and jobs.
4. Plans may escalate further (removing the last ACL permit, VTY-bound ACLs, restores, NAC
   provisioning).
5. The lockout guard refuses changes cutting the controller's management path unless an ADMIN
   sets `override_lockout=true` **and** `rollback_minutes` (1-30); then the device-side timer is
   armed (CE `commit trial`, S/AR `schedule reboot delay`, not saved until disarmed).

## Secret handling

- Configuration only via environment / `.env` (`.env.example` holds placeholders). In
  staging/prod the app refuses to start without strong `JWT_SECRET`, `INTERNAL_API_TOKEN`,
  `CREDENTIAL_KEYS` and with `TRUSTED_HOSTS=*`.
- Key rotation: add a new `kid:<base64>` to `CREDENTIAL_KEYS`, set `CREDENTIAL_ACTIVE_KEY_ID`,
  restart, run `python -m app.cli vault rotate-keys`, then remove the old key. Backups encrypted
  with an old key stay readable while the old key is configured.
- Never logged: passwords, tokens, communities, SNMPv3 keys, RADIUS secrets, private keys,
  full configurations (only masked summaries and lengths).

## NAC fail-open vs fail-closed

If the policy engine cannot decide (database outage, internal error) the backend answers
FreeRADIUS with the configured fail mode: **fail-closed (default)** returns the quarantine
VLAN - safe, but users lose normal access during an outage; **fail-open** (per site, settings
`nac.site_fail_modes`) accepts without VLAN override - availability first, but unknown devices
get the port's default VLAN. Switch-side `authentication event authen-server-down action
authorize vlan <quarantine>` covers an unreachable RADIUS server.

## MAC blocking

`/api/v1/mac-addresses/block|allow` create MAC blackhole entries as a convenience only. MAC
addresses are trivially spoofed - this is **not** a security control. Use 802.1X (or MAB with
profiling and policies) through the NAC engine.

## Supply chain and runtime

CI runs ruff, black, mypy --strict, pytest with coverage gates, bandit, pip-audit, a Docker
build and a Trivy image scan. The container runs as a non-root user (uid 10001) with a
read-only root filesystem, `no-new-privileges` and all capabilities dropped (the worker adds
`NET_RAW` for ICMP only).

## Reporting

Security issues: contact the platform owners privately; do not open public issues.
