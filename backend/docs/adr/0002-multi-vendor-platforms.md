# ADR 0002 - Multi-vendor platforms (Huawei, Cisco, TP-Link)

Status: accepted (2026-11-25)

## Context

The platform was built for Huawei VRP only. Services issued `display ...` commands, called the
Huawei builders and parsers directly, and the command classifier, lockout guard, rollback
timers and secret redaction all assumed VRP syntax. Support for Cisco (IOS / IOS-XE, NX-OS,
Small Business) and TP-Link (JetStream / Omada switches over CLI, and Omada controller API)
was requested at full feature parity.

## Decision

1. **`Platform` is the single dispatch key** (`app/domain/platforms.py`), stored on every
   device (`devices.platform`, migration 0009) and on every backup (`backups.platform`).
   `DeviceFamily` remains the Huawei hardware family (S / CE / AR); for Huawei the detected
   family selects VRP V5 vs V8. `vendor` is derived from the platform.
2. **One immutable `PlatformAdapter` per platform** (`app/network/platforms/`) bundles:
   logical read queries (`Q`) mapped to CLI commands, a parser namespace, builder namespaces
   (L2, ACL, routing, NAC), configuration-text handling (normalize / hash / sanitize / diff /
   restore plan), the command classifier, the lockout guard, the commit model, the device-side
   rollback timer, routing probes and transport drivers.
3. **Typed contracts** (`contracts.py`) mirror the original Huawei signatures; mypy strict
   checks every vendor module against them. Parsers return the shared value objects in
   `app.network.parsers`, so services and API schemas stay vendor-neutral.
4. **Safety model is unchanged and enforced per platform**: deny-by-default classification,
   confirmation tokens for DANGEROUS changes, lockout guard. Rollback timers are typed:
   `COMMIT_TRIAL` (CE `commit trial`, IOS `configure terminal revert timer` +
   `configure confirm`), `SCHEDULED_RELOAD` (Huawei S/AR `schedule reboot`), `NONE`. On
   `NONE` platforms a lockout override is refused outright, because there is no device-side
   safety net that survives losing the management path.
5. **Secret redaction is global** and now also masks Cisco / TP-Link typed secrets
   (`secret 9`, `password 7`, `key 7`, `key-string`, RADIUS/TACACS keys, SNMPv3 user keys).
6. Platforms are registered lazily; an unregistered platform returns 422
   `unsupported_capability`, so a vendor can only be used once its adapter exists.

## Consequences

- Adding a vendor = one adapter package + driver + mock emulator + fixtures; no service edits.
- Phase plan: 13 abstraction (this ADR), 14 Cisco IOS/IOS-XE, 15 NX-OS, 16 Cisco SMB,
  17 TP-Link CLI, 18 TP-Link Omada controller API, 19 frontend.
- Like Huawei, every non-Huawei command set is verified against mocks only until checked on
  real hardware (HARDWARE_VALIDATION.md).
