# Assumptions and decisions

Each entry: the ambiguity, the choice made (safest reasonable option), and why.

| # | Topic | Decision | Reason |
|---|-------|----------|--------|
| A1 | Build host tooling | The build host has no Docker, `make`, PostgreSQL or Redis. `uv` was installed via pip. Tests run against an **embedded PostgreSQL 16** (`pgserver`, dev dependency) and **fakeredis**. | Keeps tests real-Postgres without Docker. `docker compose` verification could not be executed here and is listed in `HARDWARE_VALIDATION.md` / PROGRESS. |
| A2 | Repository layout | Code lives in `backend/`; the git repo root is the parent so a `frontend/` can sit beside it. | Prompt's directory tree starts with `backend/`. |
| A3 | Packaging | `[tool.uv] package = false` (application, not a library). | No need to build a wheel; Docker copies sources. |
| A4 | SQLite fallback | Models use portable types (`InetType`, `JsonType`, `UtcDateTime`) so tests can fall back to SQLite with `TEST_USE_SQLITE=1`. PostgreSQL-only features (partitioning, append-only trigger) are applied conditionally in migrations. | Fast local runs where pgserver is unavailable; production is PostgreSQL only. |
| A5 | Lint rules | Ruff `N818` (Error suffix) and `PLC0415` (local imports) are disabled. | Domain exceptions (`NotFound`, `Forbidden`) are clearer; heavy blocking network libs are imported lazily inside workers/drivers only. |
| A6 | Proxy headers | The API trusts `X-Forwarded-For` only from `127.0.0.1` (uvicorn `--forwarded-allow-ips`). | Internal endpoint IP allowlists must not be spoofable. Change explicitly when a reverse proxy is deployed. |
| A7 | `/metrics` | Not in OpenAPI, restricted to `INTERNAL_ALLOWED_NETWORKS`. | Prompt: internal endpoints network-restricted. |
| A8 | Display abbreviations | `dis`/`disp` and unambiguous first-word abbreviations of known display topics (`dis ver`) are expanded; anything else unknown stays DANGEROUS. | Operators type abbreviations; display is read-only on VRP. |
| A9 | Config commands via execute-command | WARNING-class configuration lines sent in user view are reclassified DANGEROUS. Configuration goes through typed feature endpoints / jobs. | Keeps execute-command display-oriented. |
| A10 | AAA / RADIUS / VTY | `aaa`, `local-user`, `radius-server`, `user-interface`, `domain` lines are DANGEROUS, so NAC switch provisioning always needs a confirmation token. | Prompt: aaa/user changes are DANGEROUS. |
| A11 | Rollback timers | CE: `commit trial <seconds>` (confirm with `commit`); S/AR have no trial commit, the planned fallback is `schedule reboot delay` without `save` (VRP-VERSION-DEPENDENT). | Only device-side timers survive losing the management path. |
| A12 | SSH host keys | Pre-flight Paramiko key exchange fetches the key; DB-pinned TRUSTED key is then enforced via a temporary known_hosts file (Netmiko) or `hostkey_b64` (ncclient). | Avoids TOCTOU: the session itself is strict. |
| A13 | NETCONF running-only devices | When `:candidate` is missing the driver edits `running` only if `:writable-running` is advertised; automatic rollback is not available there (snapshot is returned for manual restore). | Never assume uniform YANG support. |
| A14 | NETCONF/SNMP and CLI | NETCONF and SNMP adapters raise `unsupported_capability` for CLI operations rather than emulate them. | Clear 422 reasons. |
| A15 | Parser fixtures | Fixtures are hand-written to match documented VRP output, not captured from devices (see HARDWARE_VALIDATION). | No hardware available. |
| A16 | SNMP entity indexes | Huawei fan/power tables use multi-part indexes (`0.1`); they are kept as strings. | Real MIB layout. |
| A17 | Unexpected DHCP server | Detected as DHCP-snooping `trusted` on ports that are not declared uplinks (config audit), plus binding/ARP mismatches. Packet-level rogue server detection needs syslog/trap ingestion. | `display` output has no per-server data. |
| A18 | Interface names in URLs | Routes use `{interface:path}`; clients may URL-encode `/` as `%2F`. | Huawei names contain slashes. |
| A19 | Rollback on verification failure | Builders emit a best-effort inverse plan; for CE the candidate is never committed when the push fails. | Device-side undo for S-series. |
| A20 | Routing capability detection | Probe display commands per feature; "Unrecognized command" means unsupported. Cached until `refresh=true`. | VRP licences/models differ; no YANG on S-series CLI. |
| A21 | BGP/OSPF verification | Post-change checks confirm configuration acceptance and route-table diff; neighbor Established/Full is reported, not required (sessions may take minutes). | Avoids false rollbacks. |
| A22 | ACL desired state | The platform stores the last successfully deployed definition per device; rule CRUD edits that definition and redeploys the diff. ACLs never deployed through the platform are read-only (live view). | Prevents silently overwriting hand-made ACLs. |
| A23 | Bulk DANGEROUS jobs | Confirmation is job-level: a dry-run job produces one token for the exact (type, targets, params); submitting with it marks the job `confirmed` and devices run with `preconfirmed` + reason. | One confirmation per bulk change instead of per device; still bound to user + payload. |
| A24 | Schedules | Stored in `settings['schedules']`, evaluated every 15 s by beat; jobs run as a service account (`is_service_account=true`, created by the CLI). Defaults: daily backup, CPU 30 s, interfaces 60 s, MAC/ARP 15 min. | Configurable from the settings API without restarting beat. |
| A25 | Inline dispatch | `JOB_DISPATCH=inline` runs jobs in the API process (tests/dev). Production uses Celery. | Tests without Redis/Celery. |
| A26 | Backup storage | Masked copy for display/diff; exact copy encrypted (AES-256-GCM) for restore. Configs are treated as sensitive (backups:read is not granted to READ_ONLY). | Prompt: strip or encrypt secrets; treat configs as sensitive. |
| A27 | Restore mechanism | Line-level merge plan pushed through ChangeService (not file replace + reboot). Lines only on the device inside a section are undone; whole extra sections are reported as notes for manual review. | Safest non-disruptive option without file transfer; listed in HARDWARE_VALIDATION. |
| A28 | Interface-down alerts | Only admin-up interfaces produce `if_oper` samples, so shut ports never alert. | Avoids noise. |
| A29 | ICMP in discovery | Uses the system `ping` binary (fixed argv, validated IP, no shell) instead of raw sockets, so the worker needs no extra capabilities beyond NET_RAW for ping itself. | Least privilege. |
| A30 | Telegram/webhook destinations | Destinations live in settings; tokens/secrets only in env. Webhooks must be https unless `allow_http` is set for a lab receiver. | No secrets in DB. |
