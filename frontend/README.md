# NAC Console — frontend

Next.js 16 (App Router, Turbopack, Cache Components) + TypeScript + Tailwind CSS 4 + TanStack Query
web console for the Huawei NAC backend in `../backend`. The UI language is Uzbek (Latin).

## Run

```bash
cd frontend
cp .env.example .env.local      # BACKEND_URL, NEXT_PUBLIC_WS_URL, COOKIE_SECURE
npm install
npm run dev                     # http://localhost:3000
```

Production: `npm run build && npm start`. Checks: `npm run lint`, `npm run typecheck`.
After the backend API changes: `npm run gen:api` (regenerates `src/lib/api-schema.ts` from
`../backend/openapi.json`).

| Variable | Where | Meaning |
|---|---|---|
| `BACKEND_URL` | server only | FastAPI base URL the BFF calls, e.g. `http://backend:8000` |
| `NEXT_PUBLIC_WS_URL` | browser | Backend WebSocket base, e.g. `wss://nac.example.test`. Empty = same origin as the UI |
| `COOKIE_SECURE` | server only | `false` only for local http; must stay `true` behind TLS |

## Architecture

```
Browser ──fetch /api/backend/*──▶ Next.js BFF (route handlers) ──Bearer JWT──▶ FastAPI /api/v1/*
   │        httpOnly cookies: nac_at (access), nac_rt (refresh)
   └──WebSocket (single-use ticket from /auth/ws-ticket, sent as first frame)──▶ FastAPI /api/v1/ws/*
```

- **Tokens never reach browser JavaScript.** `POST /api/auth/login` stores the access and
  refresh tokens in `httpOnly`, `SameSite=Strict` cookies; `/api/backend/[...path]` attaches
  the bearer token and forwards the call. Only plain path segments under `/api/v1` are proxied.
- **Refresh rotation is single-flighted** (`src/lib/server/refresh.ts`). The backend revokes the
  whole session family when a refresh token is reused, so parallel requests that hit an expired
  access token share one refresh instead of each sending the same token. This is per Node
  process: run one instance, or sticky sessions, when scaling out.
- `src/proxy.ts` (formerly middleware) redirects to `/login` when there is no session cookie;
  real authorization is always the backend's.
- **Guarded changes** (`src/components/guarded-change.tsx`): every configuration change runs a
  dry-run first and shows the classification, CLI lines, diff and lockout findings. DANGEROUS
  changes need the single-use confirmation token plus an audit reason; changes that could cut
  the management path can only be overridden by ADMIN with an automatic rollback timer.
- Navigation and buttons are hidden per the permissions from `/auth/me`; the backend enforces them.

## Pages

| Route | Contents |
|---|---|
| `/` | Device / alert / job / endpoint summary |
| `/devices`, `/devices/[id]` | Inventory, filters, add/edit; tabs: overview + test connection + metrics, interfaces (admin state, VLAN mode, trunk, speed, description), VLANs, terminal (classified commands), backups + drift, monitoring charts, STP / routing table / static routes, SSH host keys (TOFU approve) |
| `/jobs`, `/jobs/[id]` | Bulk jobs with dry-run → confirm, live progress over WebSocket, per-device results, cancel |
| `/backups`, `/backups/[id]` | Masked config, download, diff between backups, known-good, guarded restore |
| `/monitoring` | Live metric stream, L2 anomalies |
| `/alerts` | Alerts (live), ack / resolve, alert rules |
| `/mac` | MAC table / ARP search, locate port, collect now, MAC blackhole |
| `/discovery` | Subnet scan, adopt / ignore proposals |
| `/nac/endpoints`, `/nac/sessions`, `/nac/policies`, `/nac/radius` | Endpoints (register, block), RADIUS sessions (CoA disconnect / reauth), policy editor + versions + simulator, RADIUS servers + switch provisioning preview / deploy job |
| `/credentials`, `/users`, `/audit` | Write-only credential vault + device groups, users / roles / passwords, audit trail |

## Deployment notes

- Behind the BFF, the backend sees the Next.js server as the client. The BFF forwards
  `X-Forwarded-For`; run uvicorn with `--proxy-headers --forwarded-allow-ips=<next server IP>`
  if audit rows should carry the real browser IP.
- Browser→backend traffic is only the WebSocket, so the backend needs no CORS entry for the UI;
  expose `/api/v1/ws/*` through the same reverse proxy as the UI (then leave
  `NEXT_PUBLIC_WS_URL` empty) or set it to the backend's public `wss://` URL.
