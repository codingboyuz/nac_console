"""User, refresh token and audit repositories."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update

from app.models.audit import AuditLog
from app.models.user import RefreshToken, User
from app.repositories.base import Repository


class UserRepository(Repository[User]):
    model = User
    sortable = {"username": User.username, "created_at": User.created_at, "role": User.role}
    filterable = {"role": User.role, "is_active": User.is_active}

    async def by_username(self, username: str) -> User | None:
        result = await self.session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()


class RefreshTokenRepository(Repository[RefreshToken]):
    model = RefreshToken

    async def by_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke_family(self, family_id: uuid.UUID, at: datetime) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at)
        )

    async def revoke_user(self, user_id: uuid.UUID, at: datetime) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at)
        )


class AuditRepository(Repository[AuditLog]):
    """Append + read only. No update/delete methods by design."""

    model = AuditLog
    sortable = {"timestamp": AuditLog.timestamp, "operation": AuditLog.operation}
    filterable = {
        "user_id": AuditLog.user_id,
        "device_id": AuditLog.device_id,
        "operation": AuditLog.operation,
        "success": AuditLog.success,
        "command_category": AuditLog.command_category,
    }

    async def delete(self, entity: AuditLog) -> None:
        raise PermissionError("audit log is append-only")
