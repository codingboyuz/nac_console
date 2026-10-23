"""Password hashing (Argon2id), JWT access tokens and opaque refresh/one-time tokens."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.core.exceptions import Unauthorized
from app.core.rbac import Role

_hasher = PasswordHasher()  # argon2id, library-recommended parameters (RFC 9106 profile)
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time verify; a dummy hash is checked when the user does not exist."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_opaque_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: uuid.UUID
    role: Role
    jti: str
    issued_at: datetime
    expires_at: datetime


class TokenService:
    """Signs/validates short-lived JWT access tokens."""

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret
        self._algorithm = settings.jwt_algorithm
        self._issuer = settings.jwt_issuer
        self.access_ttl = settings.access_token_ttl_seconds

    def issue_access(self, user_id: uuid.UUID, role: Role) -> tuple[str, AccessClaims]:
        now = datetime.now(UTC)
        claims = AccessClaims(
            user_id=user_id,
            role=role,
            jti=uuid.uuid4().hex,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.access_ttl),
        )
        payload: dict[str, Any] = {
            "sub": str(user_id),
            "role": role.value,
            "jti": claims.jti,
            "iat": int(now.timestamp()),
            "iat_ms": int(now.timestamp() * 1000),
            "nbf": int(now.timestamp()),
            "exp": int(claims.expires_at.timestamp()),
            "iss": self._issuer,
            "typ": "access",
        }
        token = jwt.encode(payload, self._secret.get_secret_value(), algorithm=self._algorithm)
        return token, claims

    def decode_access(self, token: str) -> AccessClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret.get_secret_value(),
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "jti", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise Unauthorized("invalid or expired token") from exc
        if payload.get("typ") != "access":
            raise Unauthorized("wrong token type")
        try:
            return AccessClaims(
                user_id=uuid.UUID(payload["sub"]),
                role=Role(payload["role"]),
                jti=str(payload["jti"]),
                issued_at=datetime.fromtimestamp(
                    int(payload.get("iat_ms", payload["iat"] * 1000)) / 1000, UTC
                ),
                expires_at=datetime.fromtimestamp(payload["exp"], UTC),
            )
        except (KeyError, ValueError) as exc:
            raise Unauthorized("malformed token claims") from exc
