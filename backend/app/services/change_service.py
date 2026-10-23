"""Guarded configuration change executor used by every feature service.

classify -> lockout guard -> authorize -> (dry-run | confirm token) -> per-device lock ->
pre-change hooks (mandatory backup) -> push -> post-verify (rollback on failure) -> persist ->
post-change hooks -> audit.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.exceptions import AppError, DeviceError, Forbidden, LockoutRisk, ValidationFailed
from app.core.rbac import Role
from app.domain.commands.categories import Classification, CommandCategory
from app.domain.commands.classifier import CommandClassifier, DeviceContext
from app.domain.commands.lockout import LockoutContext, LockoutFinding, LockoutGuard
from app.domain.devices import DeviceProtocol
from app.domain.identity import Principal, RequestContext
from app.models.device import Device
from app.network.builders.plan import ConfigPlan
from app.network.locks import device_config_lock
from app.network.ports import DeviceDriver
from app.services.audit_service import AuditService
from app.services.command_service import authorize_category
from app.services.confirmation_service import ConfirmationService, payload_hash
from app.services.connection_service import DeviceConnector
from app.services.device_service import DeviceService

ChangeHook = Callable[[Principal | None, Device, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ChangeOptions:
    dry_run: bool = False
    confirm: bool = False
    confirmation_token: str | None = None
    reason: str | None = None
    save: bool = True
    override_lockout: bool = False
    rollback_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class ChangeResult:
    device_id: uuid.UUID
    operation: str
    status: str  # dry_run | noop | applied | rolled_back
    category: CommandCategory
    reason: str
    lines: list[str]
    lockout_findings: list[LockoutFinding] = field(default_factory=list)
    confirmation_token: str | None = None
    output: str | None = None


def device_context(device: Device) -> DeviceContext:
    return DeviceContext(
        mgmt_interface=device.mgmt_interface,
        mgmt_vlan=device.mgmt_vlan,
        uplink_interfaces=frozenset(device.uplink_interfaces or []),
    )


class ChangeService:
    def __init__(
        self,
        settings: Settings,
        redis: Redis,
        devices: DeviceService,
        connector: DeviceConnector,
        confirmations: ConfirmationService,
        audit: AuditService,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._devices = devices
        self._connector = connector
        self._confirm = confirmations
        self._audit = audit
        self._classifier = CommandClassifier()
        self._guard = LockoutGuard()
        self.pre_change_hooks: list[ChangeHook] = []
        self.post_change_hooks: list[ChangeHook] = []

    def assess(
        self, device: Device, plan: ConfigPlan
    ) -> tuple[Classification, list[LockoutFinding]]:
        """Pure evaluation of a plan against the device context (no I/O)."""
        if plan.netconf_xml and not plan.lines:
            return Classification(CommandCategory.WARNING, "NETCONF edit-config"), []
        ctx = device_context(device)
        classification = self._classifier.classify_config(list(plan.lines), ctx)
        findings = self._guard.check(
            list(plan.lines),
            LockoutContext(ctx, tuple(self._settings.controller_ips), plan.mgmt_acls),
        )
        if plan.min_category.rank > classification.category.rank:
            classification = Classification(
                plan.min_category, plan.min_category_reason, classification.lines
            )
        return classification, findings

    async def apply(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        plan: ConfigPlan,
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._devices.get(principal, device_id)
        if plan.noop:
            return ChangeResult(
                device.id,
                plan.operation,
                "noop",
                CommandCategory.SAFE,
                "desired state already present",
                [],
            )
        classification, findings = self.assess(device, plan)
        category = classification.category
        lines = plan.preview()

        async def audit(success: bool, summary: str) -> None:
            await self._audit.record(
                operation=f"change:{plan.operation}",
                success=success,
                principal=principal,
                ctx=ctx,
                device_id=device.id,
                command_category=category.value,
                reason=options.reason,
                result_summary=summary,
                extra={"lines": lines[:50], "lockout": [f.reason for f in findings]},
            )

        try:
            if findings:
                category = CommandCategory.DANGEROUS
                self._check_override(principal, options, findings)
            authorize_category(principal, category)
        except (Forbidden, LockoutRisk) as exc:
            await audit(False, f"denied: {exc.message}")
            raise
        digest = payload_hash("change", device.id, *lines)
        if options.dry_run:
            token = None
            if category is CommandCategory.DANGEROUS:
                token = await self._confirm.issue(principal, device.id, digest)
            await audit(True, f"dry-run {plan.summary}")
            return ChangeResult(
                device.id,
                plan.operation,
                "dry_run",
                category,
                classification.reason,
                lines,
                findings,
                token,
            )
        if category is CommandCategory.DANGEROUS:
            await self._confirm.require(
                confirm=options.confirm,
                token=options.confirmation_token,
                reason=options.reason,
                principal=principal,
                target_id=device.id,
                digest=digest,
            )
        try:
            output, status = await self._execute(principal, device, plan, options, bool(findings))
        except AppError as exc:
            await audit(False, f"{exc.code}: {exc.message}")
            raise
        await audit(status == "applied", f"{status}: {plan.summary}")
        if status == "rolled_back":
            raise DeviceError("post-change verification failed; change was rolled back")
        return ChangeResult(
            device.id,
            plan.operation,
            status,
            category,
            classification.reason,
            lines,
            findings,
            None,
            output,
        )

    @staticmethod
    def _check_override(
        principal: Principal, options: ChangeOptions, findings: list[LockoutFinding]
    ) -> None:
        minutes = options.rollback_minutes or 0
        if options.override_lockout and principal.role is Role.ADMIN and 1 <= minutes <= 30:
            return
        raise LockoutRisk(
            "change would cut the management path; only ADMIN may override with "
            "override_lockout=true and rollback_minutes (1-30)",
            details=[{"line": f.line, "reason": f.reason} for f in findings],
        )

    async def _execute(
        self,
        principal: Principal,
        device: Device,
        plan: ConfigPlan,
        options: ChangeOptions,
        risky: bool,
    ) -> tuple[str, str]:
        timer = options.rollback_minutes if risky else None
        async with device_config_lock(
            self._redis, str(device.id), ttl=self._settings.device_lock_ttl_seconds
        ):
            for hook in self.pre_change_hooks:
                await hook(principal, device, plan.operation)

            def operation(driver: DeviceDriver) -> tuple[str, str]:
                return _run_plan(driver, device, plan, options.save, timer)

            output, status = await self._connector.run(device, operation, principal)
            for hook in self.post_change_hooks:
                await hook(principal, device, plan.operation)
        return output, status


def _call(driver: Any, name: str, *args: Any) -> str:
    method = getattr(driver, name, None)
    return str(method(*args)) if callable(method) else ""


def _run_plan(
    driver: DeviceDriver, device: Device, plan: ConfigPlan, save: bool, timer: int | None
) -> tuple[str, str]:
    """Blocking part, executed in a worker thread."""
    if device.protocol is DeviceProtocol.NETCONF:
        if plan.netconf_xml is None:
            raise ValidationFailed("this change has no NETCONF payload for the device's models")
        result = driver.apply_edit(plan.netconf_xml)  # type: ignore[attr-defined]
        return str(result.output)[:2000], "applied"
    uses_commit = device.family.uses_commit
    output = ""
    if timer and not uses_commit:
        output += _call(driver, "arm_rollback_timer", timer)
    trial = timer * 60 if timer and uses_commit else None
    result = driver.push_config(list(plan.lines), save=False, trial_seconds=trial)
    output += result.output
    if plan.verify is not None and not plan.verify(driver):
        if plan.rollback:
            output += driver.push_config(list(plan.rollback), save=False).output
        if timer and not uses_commit:
            output += _call(driver, "disarm_rollback_timer")
        return output, "rolled_back"
    if timer and not uses_commit:
        output += _call(driver, "disarm_rollback_timer")
    if trial:
        output += _call(driver, "confirm_trial")
    if save and not uses_commit:
        output += _call(driver, "save")
    return output[-4000:], "applied"
