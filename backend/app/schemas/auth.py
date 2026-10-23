"""Auth and user schemas. Password fields are write-only (never in responses)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, SecretStr, field_validator

from app.core.rbac import Role
from app.schemas.common import ApiModel

_USERNAME = r"^[A-Za-z0-9_.-]{3,64}$"


def _strong(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    classes = sum(
        [
            any(c.islower() for c in raw),
            any(c.isupper() for c in raw),
            any(c.isdigit() for c in raw),
            any(not c.isalnum() for c in raw),
        ]
    )
    if len(raw) < 12 or classes < 3:
        raise ValueError("password needs >= 12 chars and 3 of: lower, upper, digit, symbol")
    return value


class LoginRequest(ApiModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)


class TokenResponse(ApiModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class RefreshRequest(ApiModel):
    refresh_token: SecretStr = Field(min_length=10, max_length=256)


class LogoutRequest(ApiModel):
    refresh_token: SecretStr | None = None


class UserCreate(ApiModel):
    username: str = Field(pattern=_USERNAME)
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=128)
    password: SecretStr
    role: Role = Role.READ_ONLY
    group_scope: list[uuid.UUID] = Field(default_factory=list)

    _pw = field_validator("password")(_strong)


class UserUpdate(ApiModel):
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=128)
    role: Role | None = None
    is_active: bool | None = None
    group_scope: list[uuid.UUID] | None = None


class PasswordChange(ApiModel):
    current_password: SecretStr | None = None
    new_password: SecretStr

    _pw = field_validator("new_password")(_strong)


class UserOut(ApiModel):
    id: uuid.UUID
    username: str
    email: str | None
    full_name: str | None
    role: Role
    is_active: bool
    is_service_account: bool
    group_scope: list[uuid.UUID]
    last_login_at: datetime | None
    created_at: datetime


class MeOut(UserOut):
    permissions: list[str]


class AuditOut(ApiModel):
    id: uuid.UUID
    timestamp: datetime
    user_id: uuid.UUID | None
    username: str | None
    role: str | None
    source_ip: str | None
    user_agent: str | None
    device_id: uuid.UUID | None
    operation: str
    command_category: str | None
    request_id: str | None
    success: bool
    result_summary: str | None
    reason: str | None
