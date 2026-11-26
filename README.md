# Huawei Network Automation & NAC Platform - Backend

Backend that automates, monitors, audits and secures Huawei S-series, CloudEngine and AR devices
over SSH, Telnet, NETCONF and SNMP, with an integrated NAC engine (802.1X, MAB, RADIUS,
dynamic / guest / quarantine VLAN). It serves a separate React + TypeScript frontend through a
complete OpenAPI 3.1 contract and WebSockets. The web console lives in [`frontend/`](frontend/README.md)
(Next.js 16, `npm run dev` on port 3000).

**For authorized network administration only.** The platform acts only on explicitly
registered devices and explicitly allowed subnets.

## Highlights

- Multi-vendor platforms (ADR 0002): Huawei VRP V5 / V8, Cisco IOS / IOS-XE, NX-OS and Small
  Business (CBS/SG), TP-Link JetStream / Omada switches over CLI, and Omada-managed switches
  through the Omada Controller Open API. `GET /api/v1/platforms` lists what each one supports;
  lab-validation status per vendor is in `backend/docs/HARDWARE_VALIDATION.md`.
- Device inventory with AES-256-GCM credential vault (key rotation), SSH host-key pinning,
  Telnet only with an explicit flag and a security warning.
- Deny-by-default command classifier (SAFE / WARNING / DANGEROUS), CLI-injection guard,
  confirmation tokens, self-lockout guard with device-side rollback timers.
- Typed, idempotent configuration builders: VLANs, port modes, trunks, interfaces, MAC, STP,
  DHCP snooping, ACLs (rule-level diff), static/default routes, OSPF, BGP, RIP, VRRP,
  route-policy - all through one guarded change pipeline with pre/post backups and rollback.
- NETCONF with capability discovery and a YANG model registry; SNMPv2c/v3 collectors.
- Job engine (Celery): bulk changes with dry-run, concurrency caps, stop-on-failure,
  cancellation, idempotency keys, per-device results, live WebSocket progress.
- Backups with dedup, secret masking + encrypted originals, diff, drift, guarded restore.
- Monitoring with monthly-partitioned metrics, alert engine (hysteresis, for-duration, dedup,
  flap suppression, maintenance windows) and email / signed webhook / Telegram delivery.
- Safe discovery (allowlisted subnets, ICMP -> SNMP -> SSH banner, proposals only).
- NAC: declarative policy engine, FreeRADIUS integration, profiling, RFC 5176 CoA, switch-side
  provisioning generator.
- RBAC (5 roles), JWT with refresh rotation, append-only audit trail, problem+json errors.

## Quick start (Windows, one click)

`start.bat` (repo root) starts the backend and the web console in two windows and opens
http://localhost:3000; `stop.bat` closes them. Needs only Python 3.12 and Node.js 20+:
the first run installs dependencies, the backend uses a SQLite file
(`backend/.localdev/nac.db`) and in-process Redis, so no Docker, PostgreSQL or Redis.
The first start creates user `admin` with a random password, shown in the backend window
and saved to `backend/.localdev/ADMIN_PASSWORD.txt`. Keep `backend/.localdev/secrets.json`:
it holds the key that decrypts stored device credentials. For local use only - production
runs on PostgreSQL / Redis / Celery (below). Set `NAC_DATABASE_URL` to use PostgreSQL locally.

## Quick start (Docker)

```bash
cd backend
cp .env.example .env            # fill every <PLACEHOLDER>
docker compose up -d --build
docker compose exec backend python -m app.cli user create admin --role ADMIN
open http://localhost:8000/docs
```

## Quick start (local)

```bash
cd backend
uv sync --all-groups
JOB_DISPATCH=inline uv run uvicorn app.main:create_app --factory --reload
uv run pytest                    # embedded PostgreSQL + fakeredis, no devices needed
```

## Documentation

| Document | Content |
|---|---|
| [ARCHITECTURE.md](backend/docs/ARCHITECTURE.md) | Components, layers, guarded change pipeline, jobs, NAC (Mermaid diagrams) |
| [API.md](backend/docs/API.md) | Conventions, curl examples, WebSocket usage and message JSON Schemas, RADIUS contract |
| [SECURITY.md](backend/docs/SECURITY.md) | Threat model, RBAC matrix, command safety, secret handling, fail-open vs fail-closed |
| [DEVELOPMENT.md](backend/docs/DEVELOPMENT.md) | Setup, commands, test infrastructure (emulators/simulators), adding features |
| [DEPLOYMENT.md](backend/docs/DEPLOYMENT.md) | Docker deployment, bootstrap, operations, Huawei device prerequisites |
| [ASSUMPTIONS.md](backend/docs/ASSUMPTIONS.md) | Decisions taken where the brief was ambiguous |
| [HARDWARE_VALIDATION.md](backend/docs/HARDWARE_VALIDATION.md) | Checklist for validation on real devices |
| [PROGRESS.md](backend/docs/PROGRESS.md) | Phase log and open issues |
| [adr/](backend/docs/adr/) | Architecture decision records |

## Project tree

```text
backend/
  app/
    api/          routers (v1/*), deps, audit middleware, internal RADIUS callbacks, WebSockets
    cli/          Typer CLI (python -m app.cli)
    core/         config, logging + redaction, database, errors, middleware, RBAC, security,
                  crypto vault, rate limiting, events, metrics, network guard
    domain/       classifier, sanitizer, lockout guard, VLAN/MAC/interface values, ACL model,
                  L2 analysis, metrics math, alerting, config text, jobs, NAC policy/profiling
    models/       SQLAlchemy 2 models
    network/      ports, factory, locks, host keys, drivers (ssh/telnet/netconf/snmp),
                  builders (cli_l2, cli_acl, cli_routing, cli_nac, netconf_vlan), parsers, snmp
    nac/          RADIUS dictionary, reference server (dev only), portable UDP exchange
    repositories/ generic repository, unit of work
    schemas/      Pydantic v2 request/response models
    services/     use cases (devices, commands, changes, L2, ACL, routing, jobs, backups,
                  monitoring, alerts, discovery, NAC, CoA, settings)
    workers/      Celery app, runtime, tasks, schedules
  deploy/freeradius/   FreeRADIUS templates
  migrations/          Alembic
  tests/               unit / integration / e2e, fixtures/huawei
  docs/
```
