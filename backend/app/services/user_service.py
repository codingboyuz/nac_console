"""User administration with last-admin and self-protection rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.database import Database
from app.core.exceptions import Conflict, Forbidden, Unauthorized
from app.core.rbac import Role
from app.core.security import hash_password, verify_password
from app.domain.identity import Principal
from app.models.user import User
from app.repositories.base import ListQuery, Page
from app.repositories.uow import UnitOfWork
from app.repositories.users import UserRepository
from app.schemas.auth import PasswordChange, UserCreate, UserUpdate
from app.services.auth_service import AuthService


class UserService:
    def __init__(self, db: Database, auth: AuthService) -> None:
        self._db = db
        self._auth = auth

    async def create(self, data: UserCreate, *, service_account: bool = False) -> User:
        async with UnitOfWork(self._db) as uow:
            users = uow.repo(UserRepository)
            if await users.by_username(data.username):
                raise Conflict("username already exists")
            user = User(
                username=data.username,
                email=str(data.email) if data.email else None,
                full_name=data.full_name,
                password_hash=hash_password(data.password.get_secret_value()),
                role=data.role,
                is_active=True,
                is_service_account=service_account,
                group_scope=[str(g) for g in data.group_scope],
                password_changed_at=datetime.now(UTC),
            )
            users.add(user)
            await uow.commit()
            return user

    async def get(self, user_id: uuid.UUID) -> User:
        async with UnitOfWork(self._db) as uow:
            return await uow.repo(UserRepository).get_or_404(user_id)

    async def list(self, query: ListQuery) -> Page[User]:
        async with UnitOfWork(self._db) as uow:
            return await uow.repo(UserRepository).list(query)

    async def update(self, actor: Principal, user_id: uuid.UUID, data: UserUpdate) -> User:
        revoke = False
        async with UnitOfWork(self._db) as uow:
            user = await uow.repo(UserRepository).get_or_404(user_id)
            demoting = data.role is not None and data.role != Role.ADMIN
            disabling = data.is_active is False
            if user.id == actor.user_id and (demoting or disabling):
                raise Forbidden("you cannot demote or disable yourself")
            if user.role == Role.ADMIN and (demoting or disabling):
                await self._ensure_other_admin(uow, user.id)
            for field in ("email", "full_name", "role", "is_active"):
                value = getattr(data, field)
                if value is not None:
                    revoke |= field in {"role", "is_active"} and value != getattr(user, field)
                    setattr(user, field, str(value) if field == "email" else value)
            if data.group_scope is not None:
                user.group_scope = [str(g) for g in data.group_scope]
                revoke = True
            await uow.commit()
        if revoke:
            await self._auth.revoke_all_for_user(user_id)
        return user

    async def change_password(
        self, actor: Principal, user_id: uuid.UUID, data: PasswordChange
    ) -> None:
        async with UnitOfWork(self._db) as uow:
            user = await uow.repo(UserRepository).get_or_404(user_id)
            if actor.user_id == user_id:
                current = data.current_password.get_secret_value() if data.current_password else ""
                if not verify_password(current, user.password_hash):
                    raise Unauthorized("current password is incorrect")
            elif actor.role != Role.ADMIN:
                raise Forbidden("only ADMIN can reset another user's password")
            user.password_hash = hash_password(data.new_password.get_secret_value())
            user.password_changed_at = datetime.now(UTC)
            await uow.commit()
        await self._auth.revoke_all_for_user(user_id)

    async def deactivate(self, actor: Principal, user_id: uuid.UUID) -> None:
        await self.update(actor, user_id, UserUpdate(is_active=False))

    @staticmethod
    async def _ensure_other_admin(uow: UnitOfWork, user_id: uuid.UUID) -> None:
        stmt = select(func.count()).where(
            User.role == Role.ADMIN, User.is_active.is_(True), User.id != user_id
        )
        if int((await uow.session.execute(stmt)).scalar_one()) == 0:
            raise Conflict("cannot remove the last active ADMIN")
