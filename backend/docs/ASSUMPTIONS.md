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
