"""Roles, permissions and the RBAC matrix (documented in docs/SECURITY.md)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    NETWORK_ADMIN = "NETWORK_ADMIN"
    OPERATOR = "OPERATOR"
    AUDITOR = "AUDITOR"
    READ_ONLY = "READ_ONLY"


# Rank is used only for "role >= X" gates (dangerous commands, insecure protocols).
# AUDITOR is a read-only role and ranks below OPERATOR.
ROLE_RANK: dict[Role, int] = {
    Role.READ_ONLY: 0,
    Role.AUDITOR: 1,
    Role.OPERATOR: 2,
    Role.NETWORK_ADMIN: 3,
    Role.ADMIN: 4,
}


def role_at_least(role: Role, minimum: Role) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


class Permission(StrEnum):
    USERS_READ = "users:read"
    USERS_WRITE = "users:write"
    DEVICES_READ = "devices:read"
    DEVICES_WRITE = "devices:write"
    DEVICES_DELETE = "devices:delete"
    CREDENTIALS_WRITE = "credentials:write"
    INSECURE_PROTOCOL = "devices:insecure_protocol"
    HOSTKEY_APPROVE = "devices:hostkey_approve"
    DEVICE_TEST = "device:test"
    DEVICE_SHOW = "device:show"
    DEVICE_CONFIGURE = "device:configure"
    DEVICE_DANGEROUS = "device:dangerous"
    BACKUPS_READ = "backups:read"
    BACKUPS_CREATE = "backups:create"
    BACKUPS_RESTORE = "backups:restore"
    JOBS_READ = "jobs:read"
    JOBS_CREATE = "jobs:create"
    JOBS_CANCEL = "jobs:cancel"
    MONITORING_READ = "monitoring:read"
    ALERTS_READ = "alerts:read"
    ALERTS_ACK = "alerts:ack"
    ALERTS_WRITE = "alerts:write"
    DISCOVERY_RUN = "discovery:run"
    DISCOVERY_ADOPT = "discovery:adopt"
    NAC_READ = "nac:read"
    NAC_WRITE = "nac:write"
    NAC_POLICY_WRITE = "nac:policy_write"
    RADIUS_READ = "radius:read"
    RADIUS_WRITE = "radius:write"
    AUDIT_READ = "audit:read"
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"


P = Permission

_READ = frozenset(
    {P.DEVICES_READ, P.JOBS_READ, P.MONITORING_READ, P.ALERTS_READ, P.NAC_READ, P.RADIUS_READ}
)

_OPERATOR = _READ | {
    P.DEVICE_TEST,
    P.DEVICE_SHOW,
    P.DEVICE_CONFIGURE,
    P.BACKUPS_READ,
    P.BACKUPS_CREATE,
    P.JOBS_CREATE,
    P.JOBS_CANCEL,
    P.ALERTS_ACK,
    P.DISCOVERY_RUN,
    P.NAC_WRITE,
    P.SETTINGS_READ,
}

_NETWORK_ADMIN = _OPERATOR | {
    P.USERS_READ,
    P.DEVICES_WRITE,
    P.DEVICES_DELETE,
    P.CREDENTIALS_WRITE,
    P.INSECURE_PROTOCOL,
    P.HOSTKEY_APPROVE,
    P.DEVICE_DANGEROUS,
    P.BACKUPS_RESTORE,
    P.ALERTS_WRITE,
    P.DISCOVERY_ADOPT,
    P.NAC_POLICY_WRITE,
    P.RADIUS_WRITE,
    P.AUDIT_READ,
}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.NETWORK_ADMIN: frozenset(_NETWORK_ADMIN),
    Role.OPERATOR: frozenset(_OPERATOR),
    Role.AUDITOR: _READ | {P.AUDIT_READ, P.BACKUPS_READ, P.USERS_READ, P.SETTINGS_READ},
    Role.READ_ONLY: _READ,
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def permission_matrix_markdown() -> str:
    """Render the matrix for SECURITY.md (kept in sync by a test)."""
    roles = list(Role)
    header = "| Permission | " + " | ".join(r.value for r in roles) + " |"
    sep = "|---|" + "---|" * len(roles)
    rows = [
        f"| `{p.value}` | "
        + " | ".join("yes" if has_permission(r, p) else "-" for r in roles)
        + " |"
        for p in Permission
    ]
    return "\n".join([header, sep, *rows])
