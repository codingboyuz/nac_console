# Deployment

## 1. Prepare configuration

```bash
cd backend
cp .env.example .env
python -c "import secrets;print(secrets.token_urlsafe(48))"              # JWT_SECRET, INTERNAL_API_TOKEN
python -c "import os,base64;print('k1:'+base64.b64encode(os.urandom(32)).decode())"  # CREDENTIAL_KEYS
```

Set at least: `ENVIRONMENT=prod`, `POSTGRES_PASSWORD`, `JWT_SECRET`, `INTERNAL_API_TOKEN`,
`CREDENTIAL_KEYS`, `TRUSTED_HOSTS`, `CORS_ORIGINS`, `CONTROLLER_IPS` (the address the platform
uses towards switches - used by the lockout guard and as the CoA source),
`DISCOVERY_ALLOWED_SUBNETS`, `INTERNAL_ALLOWED_NETWORKS` (FreeRADIUS + monitoring scrapers).
The application refuses to start in staging/prod with weak or missing secrets.

## 2. Start the stack

```bash
docker compose up -d --build                     # postgres, redis, migrate (one-shot), backend, worker, beat
docker compose --profile flower up -d flower     # optional Celery UI on 127.0.0.1:5555
docker compose --profile radius up -d freeradius # optional FreeRADIUS with deploy/freeradius templates
docker compose ps                                # all services healthy
```

- Migrations run once in the `migrate` service (`alembic upgrade head`), not on every API start.
- Workers stop gracefully (`stop_grace_period: 120s`, `acks_late` re-queues unfinished tasks).
- Scale workers per queue: `celery -A app.workers.celery_app worker -Q polling -c 8`.
- Put a TLS reverse proxy in front of `backend:8000`; adjust uvicorn `--forwarded-allow-ips`
  to the proxy address only. Protect `/internal/v1` with mTLS at the proxy and keep it off the
  public listener.

## 3. Bootstrap accounts

```bash
docker compose exec backend python -m app.cli user create admin --role ADMIN
docker compose exec backend python -m app.cli user create svc-scheduler --role NETWORK_ADMIN --service-account
docker compose exec backend python -m app.cli user create svc-cli --role NETWORK_ADMIN --service-account
```

Schedules (`settings.schedules`) run as `svc-scheduler`; the CLI acts as `CLI_USERNAME`
(default `svc-cli`). Both go through RBAC, the classifier and the audit trail.

## 4. Operations

- Key rotation: append a new key to `CREDENTIAL_KEYS`, set `CREDENTIAL_ACTIVE_KEY_ID`, restart,
  `python -m app.cli vault rotate-keys`, then remove the old key.
- Metrics partitions/retention: hourly beat task; manual `python -m app.cli db maintain`.
- Backups of the platform itself: `pg_dump` the database; encrypted columns stay encrypted,
  keep `CREDENTIAL_KEYS` in your secret manager (losing them makes credentials unrecoverable).
- Export the OpenAPI contract for the frontend: `python -m app.cli openapi export --output openapi.json`.

## 5. Huawei device prerequisites

All snippets are **VRP-VERSION-DEPENDENT** - verify against the configuration guide of your
exact model and VRP release, and test in a lab first (see `HARDWARE_VALIDATION.md`). Replace
every `<PLACEHOLDER>`.

### 5.1 SSH (STelnet) and a local AAA user - S-series V200R019 / AR V300R021

```text
system-view
 rsa local-key-pair create              # or: dsa/ecc local-key-pair create (VRP-VERSION-DEPENDENT)
 stelnet server enable
 ssh server-source -i Vlanif100           # V200R020+ only (VRP-VERSION-DEPENDENT)
 aaa
  local-user netops password irreversible-cipher <DEVICE_PASSWORD>
  local-user netops privilege level 15
  local-user netops service-type ssh
 quit
 ssh user netops authentication-type password
 ssh user netops service-type stelnet
 user-interface vty 0 4
  authentication-mode aaa
  protocol inbound ssh
  acl 2000 inbound                       # restrict to the controller subnet
 quit
 acl number 2000
  rule 5 permit source <CONTROLLER_IP> 0
 quit
return
save
```

### 5.2 SSH - CloudEngine (VRP V8)

```text
system-view
 stelnet server enable
 aaa
  local-user netops password irreversible-cipher <DEVICE_PASSWORD>
  local-user netops service-type ssh
  local-user netops level 3
 quit
 ssh user netops
 ssh user netops authentication-type password
 ssh user netops service-type stelnet
 user-interface vty 0 4
  authentication-mode aaa
  protocol inbound ssh
 quit
 commit
return
```

### 5.3 NETCONF (SNETCONF over SSH, port 830) - CE (VRP V8)

```text
system-view
 snetconf server enable
 netconf
  protocol inbound ssh port 830
 quit
 ssh user netops service-type snetconf   # or: service-type all
 commit
```

S-series with NETCONF support (V200R019+, model dependent): `snetconf server enable` and
`netconf` + `protocol inbound ssh port 830`; the platform reads `<hello>` capabilities and only
uses advertised YANG modules (VRP-VERSION-DEPENDENT).

### 5.4 SNMPv3 (authPriv) - S and CE

```text
system-view
 snmp-agent
 snmp-agent sys-info version v3
 snmp-agent mib-view included nac-view iso
 snmp-agent group v3 nac-group privacy read-view nac-view acl 2000
 snmp-agent usm-user v3 nac-mon group nac-group
 snmp-agent usm-user v3 nac-mon authentication-mode sha <AUTH_KEY>
 snmp-agent usm-user v3 nac-mon privacy-mode aes128 <PRIV_KEY>
return
```

On CE add `commit`. Store the matching `snmp_v3` credential (SHA/AES128) and set
`snmp_credential_id` on the device.

### 5.5 RADIUS and AAA - S-series V200R019 (generated by the platform)

Use `POST /api/v1/nac/provisioning/preview` to generate this for a device and deploy it with
the `nac.provision` job (DANGEROUS, confirmation required):

```text
radius-server template nac
 radius-server shared-key cipher <RADIUS_SHARED_SECRET>
 radius-server authentication <FREERADIUS_IP> 1812 weight 80
 radius-server accounting <FREERADIUS_IP> 1813 weight 80
 radius-server retransmit 3 timeout 5
quit
radius-server authorization <CONTROLLER_IP> shared-key cipher <RADIUS_SHARED_SECRET>
aaa
 authentication-scheme nac-auth
  authentication-mode radius
 accounting-scheme nac-acct
  accounting-mode radius
  accounting realtime 15
 domain default
  authentication-scheme nac-auth
  accounting-scheme nac-acct
  radius-server nac
```

### 5.6 802.1X, MAC authentication, guest/quarantine and dynamic VLAN - S-series (NAC unified mode)

```text
authentication unified-mode                # V200R009+; changing the mode reboots - plan it
dot1x-access-profile name nac-dot1x
mac-access-profile name nac-mac
authentication-profile name nac-auth
 dot1x-access-profile nac-dot1x
 mac-access-profile nac-mac
 authentication event authen-server-down action authorize vlan <QUARANTINE_VLAN>
 authentication event authen-server-up action re-authen
 authentication event pre-authen action authorize vlan <GUEST_VLAN>
quit
interface GigabitEthernet0/0/1
 port link-type access
 authentication-profile nac-auth
```

Dynamic VLAN: the policy engine returns `Tunnel-Type=VLAN`, `Tunnel-Medium-Type=IEEE-802`,
`Tunnel-Private-Group-Id=<vlan>`; the VLAN must exist on the switch. `Filter-Id` maps to an ACL
on the switch (number or UCL name; VRP-VERSION-DEPENDENT).

### 5.7 802.1X / MAB - CloudEngine (VRP V8)

```text
dot1x enable
dot1x-access-profile name nac-dot1x
mac-access-profile name nac-mac
authentication-profile name nac-auth
 dot1x-access-profile nac-dot1x
 mac-access-profile nac-mac
quit
interface 10GE1/0/1
 authentication-profile nac-auth
commit
```

## 6. FreeRADIUS

Templates: `deploy/freeradius/mods-available/rest`, `sites-available/nac`, `clients.conf`.
FreeRADIUS needs `NAC_BACKEND_URL` and `NAC_INTERNAL_TOKEN` (HTTP Basic password). Configure EAP
(PEAP/EAP-TLS) and your directory in FreeRADIUS; the backend only authorizes. Create the same
server in the platform (`POST /api/v1/radius/servers`) so provisioning and CoA know the secret.

## 7. What could not be verified on the build host

Docker was not available on the build host, so `docker compose up --build` and the image
healthchecks were not executed there; CI performs the Docker build and Trivy scan. See
`PROGRESS.md` and `HARDWARE_VALIDATION.md`.
