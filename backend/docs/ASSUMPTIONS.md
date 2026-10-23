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
