"""Schemas for guarded command execution and connection tests."""

from __future__ import annotations

from typing import Any

from pydantic import Field, SecretStr

from app.domain.commands.categories import CommandCategory
from app.domain.devices import DeviceFamily
from app.schemas.common import ApiModel


class ExecuteCommandRequest(ApiModel):
    command: str = Field(min_length=1, max_length=512, examples=["display version"])
    dry_run: bool = Field(default=False, description="Classify only; issues a token if DANGEROUS")
    confirm: bool = False
    confirmation_token: SecretStr | None = None
    reason: str | None = Field(default=None, max_length=500)


class ClassifiedLine(ApiModel):
    line: str
    category: CommandCategory
    reason: str


class ExecuteCommandResponse(ApiModel):
    command: str
    category: CommandCategory
    reason: str
    dry_run: bool
    output: str | None = None
    elapsed_ms: int | None = None
    confirmation_token: str | None = None
    confirmation_expires_in: int | None = None
    security_warning: str | None = None


class TestConnectionResponse(ApiModel):
    reachable: bool
    family: DeviceFamily
    vrp_version: str | None
    software_version: str | None
    model: str | None
    uptime_seconds: int | None
    capabilities: dict[str, Any]
    security_warning: str | None = None
