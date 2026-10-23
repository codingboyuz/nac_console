# Architecture

Clean Architecture with ports and adapters. Domain logic (`app/domain`) is framework-free and
unit-tested without a database or network. Dependency rule:
`api -> services -> (repositories, network ports)`; network drivers implement ports; models and
schemas never import the API; the API never imports drivers.

## Components

```mermaid
flowchart LR
    FE[React + TS frontend] -->|REST /api/v1, WS| API
    FR[FreeRADIUS rlm_rest] -->|/internal/v1/radius| API
    subgraph Backend
      API[FastAPI app<br/>middleware: request-id, audit,<br/>secure headers, CORS, rate limit] --> SVC[Services<br/>ChangeService, JobService,<br/>NAC, Alerts, Backups...]
      SVC --> REPO[(Repositories / UoW)]
      SVC --> NET[Network ports<br/>DeviceConnector + Factory]
      SVC --> BUS[EventBus]
      WRK[Celery workers<br/>queues: config, polling,<br/>discovery, alerts, default] --> SVC
      BEAT[Celery beat<br/>15 s schedule tick] --> WRK
    end
    REPO --> PG[(PostgreSQL 16<br/>metric_samples partitioned)]
    BUS --> RD[(Redis 7<br/>broker, locks, rate limits,<br/>pub/sub, tokens)]
    API -->|pub/sub| RD
    NET -->|SSH / Telnet: Netmiko| SW[Huawei S / CE / AR]
    NET -->|NETCONF: ncclient| SW
    NET -->|SNMP v2c/v3: pysnmp| SW
    SVC -->|CoA / Disconnect UDP 3799| SW
    SW -->|RADIUS 1812/1813| FR
```

## Layers and packages

| Package | Responsibility |
|---|---|
| `app/api` | Routers, dependencies (auth, RBAC `require()`, rate limits), problem+json, WebSockets, internal RADIUS callbacks |
| `app/services` | Use cases: devices, credentials, commands, changes, L2/ACL/routing features, jobs, backups, monitoring, alerts, discovery, NAC, CoA |
| `app/domain` | Pure logic: command classifier/sanitizer/lockout guard, VLAN/MAC/interface value objects, ACL model, L2 anomaly heuristics, metrics math, alert evaluation, config text (hash/diff/restore), NAC policy engine + profiling |
| `app/network` | `DeviceDriver` port, factory with per-device session cap, Redis config lock, host-key pinning, Netmiko SSH/Telnet, ncclient NETCONF + ModelRegistry, pysnmp client/collectors, typed CLI/NETCONF builders, parsers |
| `app/repositories` | Generic repository (whitelisted filter/sort/paging) and Unit of Work |
| `app/models` | SQLAlchemy 2 typed models (portable types: INET/JSONB on PostgreSQL) |
| `app/workers` | Celery app, per-process runtime (event loop + container), tasks, DB-backed schedule tick |
| `app/cli` | Typer CLI on the same services (runs as `CLI_USERNAME`) |

## Guarded change pipeline

Every configuration change - API call, bulk job, restore, NAC provisioning - goes through
`ChangeService.apply`:

```mermaid
sequenceDiagram
    participant C as Caller (API/job/CLI)
    participant CS as ChangeService
    participant G as Classifier + LockoutGuard
    participant T as ConfirmationService
    participant L as Redis device lock
    participant B as BackupService (hook)
    participant D as DeviceConnector -> Driver
    C->>CS: plan (typed builder output)
    CS->>G: classify lines, lockout check
    G-->>CS: SAFE/WARNING/DANGEROUS, findings
    alt lockout findings
        CS-->>C: 409 lockout_risk (unless ADMIN override + rollback timer)
    end
    CS->>CS: RBAC by category
    alt dry_run
        CS->>T: issue token (DANGEROUS)
        CS-->>C: preview + token
    else DANGEROUS
        CS->>T: consume token (user+device+payload hash), reason required
    end
    CS->>L: acquire lock:device:<id>
    CS->>B: pre-change backup (failure aborts)
    CS->>D: push (CE commit / S save), verify
    alt verify fails
        D->>D: push rollback plan
        CS-->>C: 502 rolled back
    end
    CS->>B: post-change backup, emit config_changed
    CS-->>C: applied (audited)
```

## Device access

- `DeviceConnector` resolves the credential (vault), verifies the SSH host key (TOFU, strict
  afterwards), builds `ConnectionParams` and runs the blocking driver in a worker thread
  (`anyio.to_thread`) under a per-device semaphore (default 2 sessions, protects VTY lines).
- Drivers: `huawei` (S/AR) and `huawei_vrpv8` (CE, two-stage commit) via Netmiko; Telnet via
  `huawei_telnet` with a security warning on every result; NETCONF via ncclient with a
  capability-driven `ModelRegistry`; SNMP via pysnmp (asyncio) for monitoring/identification.
- Huawei error markers (`Error:`, `Unrecognized command`, ...) fail operations explicitly;
  library exceptions map to `DeviceUnreachable`, `DeviceAuthFailed`, `DeviceTimeout`,
  `CommandRejected`, `HostKeyMismatch` without credentials in messages.

## Jobs

`POST /api/v1/jobs` -> 202 + `Location`. `JobRunner` claims the job atomically, fans out per
device with a concurrency cap, honours stop-on-failure and cancellation, records
`job_device_results`, and publishes progress on `events:job:<id>` (WebSocket fan-out). Bulk
DANGEROUS changes use a job-level confirmation token from a dry-run job.

## Data

- PostgreSQL with Alembic migrations only; append-only `audit_logs` (trigger); `metric_samples`
  partitioned by month (DEFAULT partition + maintenance task creating partitions ahead and
  enforcing retention).
- Redis: Celery broker/results, rate-limit counters, jti denylist, confirmation tokens, WS
  tickets, device locks, alert state, pub/sub.

## NAC

See `docs/adr/0001-radius-architecture.md`. FreeRADIUS terminates RADIUS/EAP and calls
`/internal/v1/radius/authorize|accounting`; the declarative policy engine decides (first match by
ascending priority, mandatory default quarantine), decisions are audited, CoA re-authorizes or
disconnects sessions, and switch-side configuration is generated and deployed through jobs.

```mermaid
flowchart LR
    EP[Endpoint] -- 802.1X / MAB --> SW[Huawei switch]
    SW -- Access-Request --> FR[FreeRADIUS]
    FR -- POST authorize --> NAC[NacService]
    NAC --> PE[Policy engine]
    PE -- VLAN / Filter-Id / reject --> FR
    FR -- Access-Accept/Reject --> SW
    SW -- Accounting --> FR -- POST accounting --> NAC
    OP[Operator] -- block / policy change --> API --> COA[CoaService] -- Disconnect/CoA --> SW
```
