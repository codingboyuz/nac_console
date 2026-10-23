"""Typed platform settings stored in the ``settings`` table (no secrets: those stay in env)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, HttpUrl, ValidationError

from app.core.database import Database
from app.core.exceptions import NotFound, ValidationFailed
from app.domain.identity import Principal, RequestContext
from app.domain.schedules import DEFAULT_SCHEDULES, ScheduleEntry
from app.models.job import Setting
from app.repositories.uow import UnitOfWork
from app.services.audit_service import AuditService


class SchedulesValue(BaseModel):
    entries: list[ScheduleEntry] = Field(max_length=200)


class EmailDest(BaseModel):
    to: list[str] = Field(default_factory=list, max_length=50)


class WebhookDest(BaseModel):
    url: HttpUrl | None = None
    allow_http: bool = False


class TelegramDest(BaseModel):
    chat_id: str | None = Field(default=None, pattern=r"^-?\d{1,20}$|^@[A-Za-z0-9_]{5,32}$")


class AlertChannelsValue(BaseModel):
    email: EmailDest = Field(default_factory=EmailDest)
    webhook: WebhookDest = Field(default_factory=WebhookDest)
    telegram: TelegramDest = Field(default_factory=TelegramDest)


SCHEMAS: dict[str, type[BaseModel]] = {
    "schedules": SchedulesValue,
    "alert_channels": AlertChannelsValue,
}
DEFAULTS: dict[str, dict[str, Any]] = {
    "schedules": {"entries": [e.model_dump() for e in DEFAULT_SCHEDULES]},
    "alert_channels": AlertChannelsValue().model_dump(mode="json"),
}


class SettingsService:
    def __init__(self, db: Database, audit: AuditService) -> None:
        self._db = db
        self._audit = audit

    async def get(self, key: str) -> dict[str, Any]:
        if key not in SCHEMAS:
            raise NotFound(f"unknown setting {key!r}")
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(Setting, key)
        return row.value if row else DEFAULTS[key]

    async def put(
        self, principal: Principal, ctx: RequestContext, key: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        schema = SCHEMAS.get(key)
        if schema is None:
            raise NotFound(f"unknown setting {key!r}")
        try:
            clean = schema.model_validate(value).model_dump(mode="json")
        except ValidationError as exc:
            raise ValidationFailed(
                "invalid setting value", details=exc.errors(include_url=False)
            ) from exc
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(Setting, key)
            if row is None:
                uow.session.add(Setting(key=key, value=clean, updated_by=principal.user_id))
            else:
                row.value, row.updated_by = clean, principal.user_id
            await uow.commit()
        await self._audit.record(
            operation=f"settings.put:{key}", success=True, principal=principal, ctx=ctx
        )
        return clean
