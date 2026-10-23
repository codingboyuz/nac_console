# ADR 0001 - RADIUS architecture for the NAC engine

- Status: accepted
- Date: 2026-10-23

## Context

The platform must authorize 802.1X and MAC-authentication-bypass (MAB) clients on Huawei
switches, assign dynamic / guest / quarantine VLANs, react to policy changes on live sessions
and keep a full audit trail. Two options exist for the RADIUS protocol itself:

1. Implement a RADIUS server inside the Python backend (pyrad / custom asyncio).
2. Run a dedicated RADIUS server (FreeRADIUS) and let it call the backend for policy decisions.

RADIUS servers handle EAP (PEAP/EAP-TLS/TTLS state machines, TLS session caching), retransmit
detection, Message-Authenticator, proxying, client tables and very high packet rates. Getting
these right is a security product of its own. The backend's job is policy, endpoint inventory,
audit and orchestration.

## Decision

- **Production:** FreeRADIUS 3.2 terminates RADIUS/EAP. Its `rest` module (rlm_rest) calls the
  backend's internal API:
  - `POST /internal/v1/radius/authorize` (post-auth / authorize section) returns reply attributes
    (`Tunnel-Type`, `Tunnel-Medium-Type`, `Tunnel-Private-Group-Id`, `Filter-Id`,
    `Session-Timeout`) or HTTP 401 for reject.
  - `POST /internal/v1/radius/accounting` ingests Start / Interim-Update / Stop.
  - User credentials for 802.1X are verified by FreeRADIUS against the directory (LDAP/AD or
    EAP-TLS certificates). The backend never sees user passwords.
- The internal API is mounted under `/internal/v1`, excluded from the public OpenAPI tags'
  frontend contract, restricted to `INTERNAL_ALLOWED_NETWORKS`, authenticated with a shared
  token (`X-Internal-Token`, constant-time compare; mTLS can be added at the reverse proxy) and
  rate-limited separately (`RATE_LIMIT_INTERNAL`).
- **Development/testing only:** a small pyrad-based asyncio server (`python -m app.cli radius
  serve`) supports MAB (PAP User-Name = MAC) and accounting, guarded by
  `RADIUS_REFERENCE_SERVER_ENABLED=true` and refused in staging/prod. It has no EAP support.
- **Change of Authorization:** the backend is an RFC 5176 client. Policy/endpoint changes send
  CoA-Request (re-authenticate) or Disconnect-Request to the NAS (switch mgmt IP, UDP 3799) with
  retries, timeouts and an audit row per attempt.
- **Failure mode:** configurable per site. *Fail-closed* (default): if the policy engine cannot
  decide (DB unavailable, internal error), reply with the quarantine VLAN. *Fail-open*: accept
  without VLAN override (port's configured VLAN). Switch-side, `authentication event
  authen-server-down action authorize vlan <quarantine>` covers an unreachable RADIUS server.

## Consequences

- Two moving parts in production (FreeRADIUS + backend); compose ships an optional
  `freeradius` profile with templates under `deploy/freeradius/`.
- EAP correctness, certificate handling and RADIUS packet security stay in a hardened,
  widely deployed server.
- Policy evaluation is deterministic, typed and testable in pure Python (no eval, no CLI).
- Fail-open trades availability for security and is opt-in per site; the trade-off is
  documented in SECURITY.md.
