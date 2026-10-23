"""Login with lockout, access/refresh issuance, refresh rotation with reuse detection."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.database import Database
from app.core.exceptions import Locked, Unauthorized
from app.core.security import (
    AccessClaims,
    TokenService,
    hash_password,
    needs_rehash,
    new_opaque_token,
    sha256_hex,
    verify_password,
)
from app.domain.identity import Principal, RequestContext
from app.models.user import RefreshToken, User
from app.repositories.uow import UnitOfWork
from app.repositories.users import RefreshTokenRepository, UserRepository
from app.services.audit_service import AuditService

_REVOKED_JTI = "auth:revoked:jti:{}"
_VALID_AFTER = "auth:valid_after:{}"
_WS_TICKET = "auth:ws_ticket:{}"


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret


class AuthService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        redis: Redis,
        tokens: TokenService,
        audit: AuditService,
    ) -> None:
        self._settings = settings
        self._db = db
        self._redis = redis
        self._tokens = tokens
        self._audit = audit

    # --- login -------------------------------------------------------------------------
    async def login(self, username: str, password: str, ctx: RequestContext) -> TokenPair:
        now = datetime.now(UTC)
        async with UnitOfWork(self._db) as uow:
            users = uow.repo(UserRepository)
            user = await users.by_username(username)
            if user is not None and user.locked_until and user.locked_until > now:
                await self._audit_login(username, ctx, False, "account locked")
                raise Locked("account temporarily locked after repeated failures")
            if not verify_password(password, user.password_hash if user else None) or (
                user is None or not user.is_active
            ):
                if user is not None:
                    user.failed_logins += 1
                    if user.failed_logins >= self._settings.login_max_failures:
                        user.locked_until = now + timedelta(
                            seconds=self._settings.login_lockout_seconds
                        )
                        user.failed_logins = 0
                    await uow.commit()
                await self._audit_login(username, ctx, False, "invalid credentials")
                raise Unauthorized("invalid username or password")
            user.failed_logins = 0
            user.locked_until = None
            user.last_login_at = now
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(password)
            pair, _ = await self._issue(uow, user, uuid.uuid4(), ctx)
            await uow.commit()
        await self._audit_login(username, ctx, True, None)
        return pair

    async def _audit_login(
        self, username: str, ctx: RequestContext, success: bool, reason: str | None
    ) -> None:
        await self._audit.record(
            operation="auth.login", success=success, ctx=ctx, username=username[:64], reason=reason
        )

    async def _issue(
        self, uow: UnitOfWork, user: User, family_id: uuid.UUID, ctx: RequestContext
    ) -> tuple[TokenPair, RefreshToken]:
        access, _ = self._tokens.issue_access(user.id, user.role)
        refresh = new_opaque_token("rt")
        record = RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=sha256_hex(refresh),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=self._settings.refresh_token_ttl_seconds),
            created_ip=ctx.source_ip,
            user_agent=(ctx.user_agent or "")[:255] or None,
        )
        uow.repo(RefreshTokenRepository).add(record)
        await uow.session.flush()
        return TokenPair(access, refresh, self._tokens.access_ttl), record

    # --- refresh rotation ---------------------------------------------------------------
    async def refresh(self, refresh_token: str, ctx: RequestContext) -> TokenPair:
        now = datetime.now(UTC)
        async with UnitOfWork(self._db) as uow:
            tokens = uow.repo(RefreshTokenRepository)
            record = await tokens.by_hash(sha256_hex(refresh_token))
            if record is None:
                raise Unauthorized("invalid refresh token")
            if record.revoked_at is not None:
                # Reuse of a rotated token: assume theft, kill the whole family.
                await tokens.revoke_family(record.family_id, now)
                await uow.commit()
                await self._audit.record(
                    operation="auth.refresh_reuse_detected",
                    success=False,
                    ctx=ctx,
                    extra={"user_id": str(record.user_id), "family_id": str(record.family_id)},
                )
                raise Unauthorized("refresh token reuse detected; session revoked")
            if record.expires_at <= now:
                raise Unauthorized("refresh token expired")
            user = await uow.repo(UserRepository).get(record.user_id)
            if user is None or not user.is_active:
                raise Unauthorized("user disabled")
            pair, new_record = await self._issue(uow, user, record.family_id, ctx)
            record.revoked_at = now
            record.replaced_by_id = new_record.id
            await uow.commit()
        return pair

    # --- logout / revocation -------------------------------------------------------------
    async def logout(self, claims: AccessClaims, refresh_token: str | None) -> None:
        await self.revoke_access(claims)
        if not refresh_token:
            return
        async with UnitOfWork(self._db) as uow:
            tokens = uow.repo(RefreshTokenRepository)
            record = await tokens.by_hash(sha256_hex(refresh_token))
            if record is not None and record.user_id == claims.user_id:
                await tokens.revoke_family(record.family_id, datetime.now(UTC))
                await uow.commit()

    async def revoke_access(self, claims: AccessClaims) -> None:
        ttl = int((claims.expires_at - datetime.now(UTC)).total_seconds()) + 1
        if ttl > 0:
            await self._redis.set(_REVOKED_JTI.format(claims.jti), "1", ex=ttl)

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        """Invalidate every access token issued before now and all refresh tokens."""
        now = datetime.now(UTC)
        await self._redis.set(
            _VALID_AFTER.format(user_id),
            str(int(now.timestamp() * 1000)),
            ex=self._settings.access_token_ttl_seconds + 5,
        )
        async with UnitOfWork(self._db) as uow:
            await uow.repo(RefreshTokenRepository).revoke_user(user_id, now)
            await uow.commit()

    # --- WebSocket tickets ---------------------------------------------------------------
    async def issue_ws_ticket(self, principal: Principal) -> str:
        """Single-use, short-lived ticket so long-lived JWTs never appear in WebSocket URLs."""
        ticket = new_opaque_token("wst")
        await self._redis.set(
            _WS_TICKET.format(sha256_hex(ticket)),
            str(principal.user_id),
            ex=self._settings.ws_ticket_ttl_seconds,
        )
        return ticket

    async def redeem_ws_ticket(self, ticket: str) -> Principal:
        user_id = await self._redis.getdel(_WS_TICKET.format(sha256_hex(ticket)))
        if not user_id:
            raise Unauthorized("invalid or expired WebSocket ticket")
        async with UnitOfWork(self._db) as uow:
            user = await uow.repo(UserRepository).get(uuid.UUID(str(user_id)))
        if user is None or not user.is_active:
            raise Unauthorized("user disabled")
        return Principal(
            user.id,
            user.username,
            user.role,
            frozenset(uuid.UUID(g) for g in user.group_scope or []),
            is_service_account=user.is_service_account,
        )

    # --- per-request authentication ---------------------------------------------------
    async def authenticate(self, access_token: str) -> tuple[Principal, AccessClaims]:
        claims = self._tokens.decode_access(access_token)
        if await self._redis.exists(_REVOKED_JTI.format(claims.jti)):
            raise Unauthorized("token revoked")
        valid_after = await self._redis.get(_VALID_AFTER.format(claims.user_id))
        if valid_after and int(claims.issued_at.timestamp() * 1000) < int(valid_after):
            raise Unauthorized("token revoked")
        async with UnitOfWork(self._db) as uow:
            user = await uow.repo(UserRepository).get(claims.user_id)
        if user is None or not user.is_active:
            raise Unauthorized("user disabled")
        principal = Principal(
            user_id=user.id,
            username=user.username,
            role=user.role,  # current DB role wins over the token claim
            group_scope=frozenset(uuid.UUID(g) for g in (user.group_scope or [])),
            token_jti=claims.jti,
            is_service_account=user.is_service_account,
        )
        return principal, claims
