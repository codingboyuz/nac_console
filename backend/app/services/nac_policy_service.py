"""NAC policy CRUD with versioning, mandatory default policy, and side-effect-free simulation."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.database import Database
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.domain.identity import Principal, RequestContext
from app.domain.nac import policy as engine
from app.models.nac import NacPolicy, NacPolicyVersion
from app.repositories.uow import UnitOfWork
from app.services.audit_service import AuditService


def _spec(row: NacPolicy) -> engine.PolicySpec:
    return engine.PolicySpec.model_validate(
        {
            "name": row.name,
            "priority": row.priority,
            "condition": row.condition,
            "action": row.action,
            "enabled": row.enabled,
        }
    )


def _snapshot(row: NacPolicy) -> dict[str, Any]:
    return {
        "name": row.name,
        "priority": row.priority,
        "enabled": row.enabled,
        "condition": row.condition,
        "action": row.action,
        "description": row.description,
    }


class NacPolicyService:
    def __init__(self, db: Database, audit: AuditService) -> None:
        self._db = db
        self._audit = audit

    async def ensure_defaults(self) -> None:
        """Seed the example set into an empty table; always guarantee the mandatory default."""
        async with UnitOfWork(self._db) as uow:
            empty = not await uow.session.scalar(select(func.count()).select_from(NacPolicy))
            has_default = bool(
                await uow.session.scalar(select(func.count()).where(NacPolicy.is_default.is_(True)))
            )
            if has_default and not empty:
                return
            wanted = (
                engine.DEFAULT_POLICIES
                if empty
                else [
                    p
                    for p in engine.DEFAULT_POLICIES
                    if p["priority"] == engine.DEFAULT_POLICY_PRIORITY
                ]
            )
            for data in wanted:
                spec = engine.PolicySpec.model_validate(data)
                if not empty and await uow.session.scalar(
                    select(NacPolicy).where(NacPolicy.name == spec.name)
                ):
                    continue
                uow.session.add(
                    NacPolicy(
                        name=spec.name,
                        priority=spec.priority,
                        enabled=True,
                        condition=spec.condition.model_dump(),
                        action=spec.action.model_dump(),
                        is_default=spec.priority == engine.DEFAULT_POLICY_PRIORITY,
                    )
                )
            await uow.commit()

    async def all(self) -> list[NacPolicy]:
        await self.ensure_defaults()
        async with UnitOfWork(self._db) as uow:
            rows = await uow.session.scalars(
                select(NacPolicy).order_by(NacPolicy.priority, NacPolicy.name)
            )
            return list(rows.all())

    async def active(self) -> list[tuple[str | None, engine.PolicySpec]]:
        return [(str(r.id), _spec(r)) for r in await self.all() if r.enabled]

    @staticmethod
    def validate(data: dict[str, Any]) -> engine.PolicySpec:
        try:
            return engine.PolicySpec.model_validate(data)
        except ValidationError as exc:
            raise ValidationFailed("invalid policy", details=exc.errors(include_url=False)) from exc

    async def create(
        self,
        principal: Principal,
        ctx: RequestContext,
        data: dict[str, Any],
        description: str | None = None,
    ) -> NacPolicy:
        spec = self.validate(data)
        if spec.priority == engine.DEFAULT_POLICY_PRIORITY:
            raise ValidationFailed("this priority is reserved for the default policy")
        await self.ensure_defaults()
        async with UnitOfWork(self._db) as uow:
            if await uow.session.scalar(select(NacPolicy).where(NacPolicy.name == spec.name)):
                raise Conflict("policy name already exists")
            row = NacPolicy(
                name=spec.name,
                priority=spec.priority,
                enabled=spec.enabled,
                condition=spec.condition.model_dump(),
                action=spec.action.model_dump(),
                description=description,
                updated_by=principal.user_id,
            )
            uow.session.add(row)
            await uow.session.flush()
            uow.session.add(
                NacPolicyVersion(
                    policy_id=row.id,
                    version=1,
                    snapshot=_snapshot(row),
                    changed_by=principal.user_id,
                )
            )
            await uow.commit()
        await self._audit.record(
            operation="nac.policy.create",
            success=True,
            principal=principal,
            ctx=ctx,
            result_summary=spec.name,
            extra={"policy_id": str(row.id)},
        )
        return row

    async def update(
        self,
        principal: Principal,
        ctx: RequestContext,
        policy_id: uuid.UUID,
        data: dict[str, Any],
        description: str | None = None,
    ) -> NacPolicy:
        spec = self.validate(data)
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(NacPolicy, policy_id)
            if row is None:
                raise NotFound("Policy not found")
            if row.is_default:
                if spec.priority != engine.DEFAULT_POLICY_PRIORITY or not spec.enabled:
                    raise Conflict("the default policy must stay enabled at the last priority")
                if spec.action.type == "accept":
                    raise Conflict("the default policy must not grant unrestricted access")
            elif spec.priority == engine.DEFAULT_POLICY_PRIORITY:
                raise ValidationFailed("this priority is reserved for the default policy")
            row.name, row.priority, row.enabled = spec.name, spec.priority, spec.enabled
            row.condition, row.action = spec.condition.model_dump(), spec.action.model_dump()
            row.description = description if description is not None else row.description
            row.version += 1
            row.updated_by = principal.user_id
            uow.session.add(
                NacPolicyVersion(
                    policy_id=row.id,
                    version=row.version,
                    snapshot=_snapshot(row),
                    changed_by=principal.user_id,
                )
            )
            await uow.commit()
        await self._audit.record(
            operation="nac.policy.update",
            success=True,
            principal=principal,
            ctx=ctx,
            result_summary=f"{spec.name} v{row.version}",
            extra={"policy_id": str(row.id)},
        )
        return row

    async def delete(self, principal: Principal, ctx: RequestContext, policy_id: uuid.UUID) -> None:
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(NacPolicy, policy_id)
            if row is None:
                raise NotFound("Policy not found")
            if row.is_default:
                raise Conflict("the default policy cannot be deleted")
            await uow.session.delete(row)
            await uow.commit()
        await self._audit.record(
            operation="nac.policy.delete",
            success=True,
            principal=principal,
            ctx=ctx,
            result_summary=row.name,
            extra={"policy_id": str(policy_id)},
        )

    async def versions(self, policy_id: uuid.UUID) -> list[NacPolicyVersion]:
        async with UnitOfWork(self._db) as uow:
            rows = await uow.session.scalars(
                select(NacPolicyVersion)
                .where(NacPolicyVersion.policy_id == policy_id)
                .order_by(NacPolicyVersion.version)
            )
            return list(rows.all())

    async def simulate(
        self, facts: engine.RequestFacts, *, guest_vlan: int, quarantine_vlan: int
    ) -> engine.Decision:
        return engine.evaluate(
            await self.active(), facts, guest_vlan=guest_vlan, quarantine_vlan=quarantine_vlan
        )
