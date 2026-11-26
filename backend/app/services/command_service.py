"""Guarded single-command execution and connection tests.

Flow for every command: classify -> authorize (RBAC + category gates) -> audit -> execute in a
threadpool through DeviceConnector. Unknown commands are DANGEROUS (deny by default).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.exceptions import AppError, Forbidden
from app.core.rbac import Permission, Role
from app.domain.commands.categories import Classification, CommandCategory
from app.domain.commands.classifier import CommandClassifier
from app.domain.identity import Principal, RequestContext
from app.network.platforms import adapter_of
from app.network.ports import CommandOutput, DeviceDriver, DeviceFacts
from app.services.audit_service import AuditService
from app.services.confirmation_service import ConfirmationService, payload_hash
from app.services.connection_service import DeviceConnector
from app.services.device_service import DeviceService

_SUMMARY_CHARS = 500


@dataclass(frozen=True, slots=True)
class CommandRequest:
    command: str
    dry_run: bool = False
    confirm: bool = False
    confirmation_token: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: str
    classification: Classification
    dry_run: bool
    output: str | None = None
    elapsed_ms: int | None = None
    confirmation_token: str | None = None
    confirmation_expires_in: int | None = None
    security_warning: str | None = None


def authorize_category(principal: Principal, category: CommandCategory) -> None:
    """Category gates on top of RBAC permissions."""
    if category is CommandCategory.SAFE:
        required = Permission.DEVICE_SHOW
    elif category is CommandCategory.WARNING:
        required = Permission.DEVICE_CONFIGURE
    else:
        required = Permission.DEVICE_DANGEROUS
    if not principal.can(required) or (
        category is CommandCategory.DANGEROUS and not principal.at_least(Role.NETWORK_ADMIN)
    ):
        raise Forbidden(f"{category.value} commands need permission {required.value}")


class CommandService:
    def __init__(
        self,
        devices: DeviceService,
        connector: DeviceConnector,
        confirmations: ConfirmationService,
        audit: AuditService,
        classifier: CommandClassifier | None = None,
    ) -> None:
        self._devices = devices
        self._connector = connector
        self._confirm = confirmations
        self._audit = audit
        self._classifier = classifier  # explicit override (tests); else per-platform rules

    async def execute(
        self, principal: Principal, ctx: RequestContext, device_id: uuid.UUID, req: CommandRequest
    ) -> CommandResult:
        device = await self._devices.get(principal, device_id)
        classifier = self._classifier or adapter_of(device).classifier
        classification = classifier.classify_exec(req.command)
        command = classification.lines[0].line
        category = classification.category

        async def audit(operation: str, success: bool, summary: str) -> None:
            await self._audit.record(
                operation=operation,
                success=success,
                principal=principal,
                ctx=ctx,
                device_id=device.id,
                command_category=category.value,
                reason=req.reason,
                result_summary=summary,
            )

        try:
            authorize_category(principal, category)
        except Forbidden as exc:
            await audit(
                "device.execute_command", False, f"denied: {exc.message}; command={command}"
            )
            raise
        digest = payload_hash("exec", device.id, command)
        if req.dry_run:
            token = None
            if classification.is_dangerous:
                token = await self._confirm.issue(principal, device.id, digest)
            await audit("device.execute_command.dry_run", True, f"{category.value}: {command}")
            return CommandResult(
                command,
                classification,
                True,
                confirmation_token=token,
                confirmation_expires_in=self._confirm.ttl if token else None,
            )
        if classification.is_dangerous:
            await self._confirm.require(
                confirm=req.confirm,
                token=req.confirmation_token,
                reason=req.reason,
                principal=principal,
                target_id=device.id,
                digest=digest,
            )
        try:
            output: CommandOutput = await self._connector.run(
                device, lambda drv: drv.run_show(command), principal
            )
        except AppError as exc:
            await audit("device.execute_command", False, f"{exc.code}: {command}")
            raise
        await audit("device.execute_command", True, f"{command} -> {len(output.output)} bytes")
        return CommandResult(
            command,
            classification,
            False,
            output=output.output,
            elapsed_ms=output.elapsed_ms,
            security_warning=output.security_warning,
        )

    async def test_connection(
        self, principal: Principal, ctx: RequestContext, device_id: uuid.UUID
    ) -> DeviceFacts:
        device = await self._devices.get(principal, device_id)

        def probe(driver: DeviceDriver) -> DeviceFacts:
            return driver.capabilities()

        try:
            facts = await self._connector.run(device, probe, principal)
        except AppError as exc:
            await self._audit.record(
                operation="device.test_connection",
                success=False,
                principal=principal,
                ctx=ctx,
                device_id=device.id,
                result_summary=exc.code,
            )
            raise
        await self._devices.update_facts(
            device.id,
            family=facts.family,
            vrp_version=facts.vrp_version or device.vrp_version,
            model=facts.model or device.model,
            capabilities={**(device.capabilities or {}), **facts.capabilities},
        )
        await self._audit.record(
            operation="device.test_connection",
            success=True,
            principal=principal,
            ctx=ctx,
            device_id=device.id,
            result_summary=f"{facts.family.value} {facts.model} VRP {facts.vrp_version}"[
                :_SUMMARY_CHARS
            ],
        )
        return facts
