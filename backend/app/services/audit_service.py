"""Audit trail writer. Uses its own transaction so denied/failed attempts persist."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.audit_context import mark_audited
from app.core.database import Database
from app.core.logging import get_logger
from app.core.redaction import redact, redact_text
from app.domain.identity import Principal, RequestContext
from app.models.audit import AuditLog
from app.repositories.base import ListQuery, Page
from app.repositories.uow import UnitOfWork
from app.repositories.users import AuditRepository

log = get_logger(__name__)
_MAX_SUMMARY = 2000


class AuditService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(
        self,
        *,
        operation: str,
        success: bool,
        principal: Principal | None = None,
        ctx: RequestContext | None = None,
        device_id: uuid.UUID | None = None,
        command_category: str | None = None,
        result_summary: str | None = None,
        reason: str | None = None,
        extra: dict[str, Any] | None = None,
        username: str | None = None,
    ) -> uuid.UUID:
        """Append one audit row. Secrets are redacted; summaries are truncated."""
        ctx = ctx or RequestContext()
        entry = AuditLog(
            user_id=principal.user_id if principal else None,
            username=principal.username if principal else username,
            role=principal.role.value if principal else None,
            source_ip=ctx.source_ip,
            user_agent=(ctx.user_agent or "")[:255] or None,
            device_id=device_id,
            operation=operation,
            command_category=command_category,
            request_id=ctx.request_id,
            success=success,
            result_summary=redact_text(result_summary)[:_MAX_SUMMARY] if result_summary else None,
            reason=redact_text(reason)[:_MAX_SUMMARY] if reason else None,
            extra=redact(extra or {}),
        )
        async with UnitOfWork(self._db) as uow:
            uow.repo(AuditRepository).add(entry)
            await uow.commit()
        mark_audited()
        log.info(
            "audit",
            operation=operation,
            success=success,
            user=entry.username,
            device_id=str(device_id) if device_id else None,
            category=command_category,
        )
        return entry.id

    async def list(self, query: ListQuery) -> Page[AuditLog]:
        async with UnitOfWork(self._db) as uow:
            return await uow.repo(AuditRepository).list(query)
