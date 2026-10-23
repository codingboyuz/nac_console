# API

- REST under `/api/v1`, OpenAPI 3.1 at `/openapi.json` (also exported to `backend/openapi.json`
  by `python -m app.cli openapi export` and in CI) for `openapi-typescript` client generation.
- Interactive docs: `/docs` (disabled when `ENVIRONMENT=prod`).
- Internal (network-restricted, not for the frontend): `/internal/v1/radius/authorize`,
  `/internal/v1/radius/accounting`, `/internal/v1/profiling/dhcp`, `/metrics`.

## Conventions

| Topic | Rule |
|---|---|
| Errors | RFC 9457 `application/problem+json`: `type`, `title`, `status`, `detail`, `code`, `message`, `instance`, `request_id`, `details` |
| Pagination | `limit` (1-500, default 50) + `offset`; responses `{items, total, limit, offset}` |
| Sorting | `sort=field` or `sort=-field`; only whitelisted fields (422 otherwise) |
| Filtering | whitelisted query params per resource |
| Status codes | 200, 201, 202 (jobs), 204, 400 (unsafe command), 401, 403, 404, 409 (conflict / confirmation / lockout / host key), 422, 423 (locked account or device), 429 (+ `Retry-After`), 502 (device rejected / rolled back), 503, 504 (device unreachable/timeout) |
| Correlation | `X-Request-ID` echoed (generated when absent) |
| Idempotency | `Idempotency-Key` header on `POST /api/v1/jobs` (per user; reuse with a different payload -> 409) |
| Health | `/health/live`, `/health/ready` (DB, Redis, broker) |

Guarded change fields accepted by every configuration endpoint: `dry_run`, `confirm`,
`confirmation_token`, `reason`, `save`, `override_lockout` (ADMIN), `rollback_minutes`.

## Examples (curl)

```bash
API=https://nac.example.test/api/v1

# Login (rate limited) and refresh rotation
TOKENS=$(curl -s $API/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<PASSWORD>"}')
ACCESS=$(echo "$TOKENS" | jq -r .access_token)
curl -s $API/auth/refresh -H 'Content-Type: application/json' \
  -d "{\"refresh_token\":\"$(echo "$TOKENS" | jq -r .refresh_token)\"}"
H="Authorization: Bearer $ACCESS"

# Credential (secrets are write-only) and device
CRED=$(curl -s $API/credentials -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"core-cli","kind":"cli","username":"netops","password":"<DEVICE_PASSWORD>"}' | jq -r .id)
DEV=$(curl -s $API/devices -H "$H" -H 'Content-Type: application/json' -d "{
  \"hostname\":\"SW-CORE-01\",\"mgmt_ip\":\"192.0.2.10\",\"credential_id\":\"$CRED\",
  \"mgmt_interface\":\"Vlanif100\",\"mgmt_vlan\":100,\"uplink_interfaces\":[\"XGE0/0/1\"]}" | jq -r .id)

# Connect (pins the SSH host key on first use) and run a SAFE command
curl -s -X POST $API/devices/$DEV/test-connection -H "$H"
curl -s $API/devices/$DEV/execute-command -H "$H" -H 'Content-Type: application/json' \
  -d '{"command":"display ip interface brief"}'

# DANGEROUS command: dry-run -> token -> confirm with reason
TOKEN=$(curl -s $API/devices/$DEV/execute-command -H "$H" -H 'Content-Type: application/json' \
  -d '{"command":"reset arp dynamic","dry_run":true}' | jq -r .confirmation_token)
curl -s $API/devices/$DEV/execute-command -H "$H" -H 'Content-Type: application/json' \
  -d "{\"command\":\"reset arp dynamic\",\"confirm\":true,\"confirmation_token\":\"$TOKEN\",\"reason\":\"CHG-1234\"}"

# VLAN on one device (idempotent; preview first)
curl -s $API/devices/$DEV/vlans -H "$H" -H 'Content-Type: application/json' \
  -d '{"vlans":[{"id":100,"name":"Lab"}],"dry_run":true}'

# Bulk: VLAN 100 on many switches as a job (202 + Location)
curl -si $API/jobs -H "$H" -H 'Content-Type: application/json' -H 'Idempotency-Key: chg-1234' \
  -d "{\"type\":\"vlan.create\",\"target_devices\":[\"$DEV\"],\"params\":{\"vlans\":[{\"id\":100}]},
       \"concurrency\":10,\"stop_on_failure\":3}"

# Backup, diff, drift, guarded restore
curl -s -X POST $API/devices/$DEV/backup -H "$H"
curl -s "$API/backups?device_id=$DEV" -H "$H"
curl -s $API/devices/$DEV/drift -H "$H"
curl -s $API/backups/<BACKUP_ID>/restore -H "$H" -H 'Content-Type: application/json' -d '{"dry_run":true}'

# NAC: simulate a decision, register an endpoint
curl -s $API/nac/policies/simulate -H "$H" -H 'Content-Type: application/json' -d '{"mac":"00:e0:fc:12:34:56"}'
curl -s -X PUT $API/nac/endpoints/00:e0:fc:12:34:56 -H "$H" -H 'Content-Type: application/json' \
  -d '{"registered":true,"endpoint_group":"staff"}'

# Internal (FreeRADIUS) - from INTERNAL_ALLOWED_NETWORKS only
curl -s http://backend:8000/internal/v1/radius/authorize -u "freeradius:<INTERNAL_API_TOKEN>" \
  -H 'Content-Type: application/json' \
  -d '{"User-Name":{"value":["00e0fc123456"]},"Calling-Station-Id":{"value":["00-e0-fc-12-34-56"]},
       "NAS-IP-Address":{"value":["192.0.2.10"]}}'
```

## WebSockets

1. `POST /api/v1/auth/ws-ticket` -> `{"ticket": "wst_...", "expires_in": 30}` (single use).
2. Connect with `?ticket=<ticket>` **or** send `{"type": "auth", "ticket": "<ticket>"}` as the
   first message within 5 s. Long-lived JWTs never appear in URLs.
3. Close codes: `4401` unauthenticated, `4403` missing permission, `4404` unknown job.

| Endpoint | Permission | Messages |
|---|---|---|
| `/api/v1/ws/jobs/{job_id}` | `jobs:read` + job visible | `snapshot`, then `status` / `progress` / `device`; closes after the final status |
| `/api/v1/ws/monitoring` | `monitoring:read` | `metric` |
| `/api/v1/ws/alerts` | `alerts:read` | `alert` |

```js
const { ticket } = await (await fetch("/api/v1/auth/ws-ticket", {method: "POST", headers})).json();
const ws = new WebSocket(`wss://nac.example.test/api/v1/ws/jobs/${jobId}?ticket=${ticket}`);
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

### Message JSON Schemas

`WsJobStatus`

```json
{
  "properties": {
    "type": {
      "enum": [
        "status",
        "progress",
        "device",
        "snapshot"
      ],
      "title": "Type",
      "type": "string"
    },
    "job_id": {
      "title": "Job Id",
      "type": "string"
    },
    "status": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Status"
    },
    "progress": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Progress"
    },
    "device_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Device Id"
    },
    "error": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Error"
    }
  },
  "required": [
    "type",
    "job_id"
  ],
  "title": "WsJobStatus",
  "type": "object"
}
```

`WsMetric`

```json
{
  "properties": {
    "type": {
      "const": "metric",
      "title": "Type",
      "type": "string"
    },
    "device_id": {
      "title": "Device Id",
      "type": "string"
    },
    "metric": {
      "title": "Metric",
      "type": "string"
    },
    "value": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Value"
    },
    "labels": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Labels",
      "type": "object"
    },
    "timestamp": {
      "title": "Timestamp",
      "type": "string"
    }
  },
  "required": [
    "type",
    "device_id",
    "metric",
    "value",
    "timestamp"
  ],
  "title": "WsMetric",
  "type": "object"
}
```

`WsAlert`

```json
{
  "properties": {
    "type": {
      "const": "alert",
      "title": "Type",
      "type": "string"
    },
    "alert_id": {
      "title": "Alert Id",
      "type": "string"
    },
    "rule": {
      "title": "Rule",
      "type": "string"
    },
    "severity": {
      "title": "Severity",
      "type": "string"
    },
    "state": {
      "title": "State",
      "type": "string"
    },
    "message": {
      "title": "Message",
      "type": "string"
    },
    "device_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Device Id"
    },
    "timestamp": {
      "title": "Timestamp",
      "type": "string"
    },
    "suppressed": {
      "default": false,
      "title": "Suppressed",
      "type": "boolean"
    }
  },
  "required": [
    "type",
    "alert_id",
    "rule",
    "severity",
    "state",
    "message",
    "timestamp"
  ],
  "title": "WsAlert",
  "type": "object"
}
```

## Job types

`GET /api/v1/jobs/types` lists every type with its JSON Schema for `params`:
`vlan.create`, `vlan.delete`, `interface.admin_state`, `interface.description`, `collect.mac`,
`collect.arp`, `collect.interfaces`, `collect.vlans`, `device.test_connection`, `device.show`
(SAFE commands only), `backup.run`, `poll.metrics`, `nac.provision`.

## RADIUS callback contract (rlm_rest)

- Request: rlm_rest JSON (`{"Attr": {"type": "...", "value": ["..."]}}`) or flat
  `{"Attr": "value"}`. Used attributes: `User-Name`, `Calling-Station-Id`, `NAS-IP-Address`,
  `NAS-Port-Id`, `Framed-IP-Address`, `EAP-Message` (presence = 802.1X), optional `user_groups`.
- Authorize response: `200` with `reply:Tunnel-Type`, `reply:Tunnel-Medium-Type`,
  `reply:Tunnel-Private-Group-Id`, optional `reply:Filter-Id`, `reply:Session-Timeout`, and
  `policy`; `401` = reject (`reply:Reply-Message`).
- Accounting: `Acct-Status-Type` (Start / Interim-Update / Stop), `Acct-Session-Id`,
  octet counters, `Acct-Terminate-Cause`; `204`.
