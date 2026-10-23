"""Caller identity and request context passed from the API into services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.core.rbac import Permission, Role, has_permission, role_at_least


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    username: str
    role: Role
    group_scope: frozenset[uuid.UUID] = field(default_factory=frozenset)
    token_jti: str | None = None
    is_service_account: bool = False

    def can(self, permission: Permission) -> bool:
        return has_permission(self.role, permission)

    def at_least(self, role: Role) -> bool:
        return role_at_least(self.role, role)

    def in_scope(self, group_id: uuid.UUID | None) -> bool:
        """Device-group scoping: empty scope means all groups."""
        if not self.group_scope:
            return True
        return group_id is not None and group_id in self.group_scope


@dataclass(frozen=True, slots=True)
class RequestContext:
    source_ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None

    @classmethod
    def system(cls, label: str = "system") -> RequestContext:
        return cls(source_ip=None, user_agent=label, request_id=None)
