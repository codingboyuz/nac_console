"""Backups: collection, hash dedup, secret masking + encrypted original, diff, drift, guarded
restore (dry-run + DANGEROUS token + role + family/version match + rollback timer)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, update

from app.core.crypto import CredentialVault
from app.core.database import Database
from app.core.events import EventBus
from app.core.exceptions import AppError, Conflict, Forbidden, NotFound
from app.core.rbac import Permission, Role
from app.domain.commands.categories import CommandCategory
from app.domain.identity import Principal, RequestContext
from app.domain.platforms import Platform
from app.models.backup import Backup, BackupSource, BackupTrigger
from app.models.device import Device
from app.network.builders.plan import ConfigPlan
from app.network.platforms import Q, adapter_of
from app.repositories.base import ListQuery, Page, Repository
from app.repositories.uow import UnitOfWork
from app.services.audit_service import AuditService
from app.services.change_service import ChangeOptions, ChangeResult, ChangeService
from app.services.collection_service import CollectionService
from app.services.device_service import DeviceService

_QUERIES = {BackupSource.CURRENT: Q.RUNNING_CONFIG, BackupSource.SAVED: Q.SAVED_CONFIG}


class BackupRepository(Repository[Backup]):
    model = Backup
    sortable = {"created_at": Backup.created_at, "size": Backup.size}
    filterable = {"device_id": Backup.device_id, "trigger": Backup.trigger, "status": Backup.status}


@dataclass(frozen=True, slots=True)
class DriftReport:
    device_id: uuid.UUID
    baseline_id: uuid.UUID | None
    drifted: bool
    diff: str


@dataclass(frozen=True, slots=True)
class RestoreResult:
    change: ChangeResult
    diff: str
    notes: tuple[str, ...]


def _aad(backup_id: uuid.UUID) -> str:
    return f"backup:{backup_id}"


class BackupService:
    def __init__(
        self,
        db: Database,
        vault: CredentialVault,
        devices: DeviceService,
        collection: CollectionService,
        changes: ChangeService,
        events: EventBus,
        audit: AuditService,
    ) -> None:
        self._db = db
        self._vault = vault
        self._devices = devices
        self._collection = collection
        self._changes = changes
        self._events = events
        self._audit = audit

    async def _latest(self, device_id: uuid.UUID, source: BackupSource) -> Backup | None:
        async with UnitOfWork(self._db) as uow:
            return await uow.session.scalar(
                select(Backup)
                .where(
                    Backup.device_id == device_id,
                    Backup.source == source,
                    Backup.status == "SUCCESS",
                )
                .order_by(Backup.created_at.desc())
                .limit(1)
            )

    async def backup(
        self,
        principal: Principal | None,
        device: Device,
        trigger: BackupTrigger,
        *,
        source: BackupSource = BackupSource.CURRENT,
        change_operation: str | None = None,
        strict: bool = False,
    ) -> tuple[Backup, bool]:
        """Return (backup, created). Unchanged configs return the previous version."""
        try:
            text = await self._collection.query(device, _QUERIES[source], principal)
        except AppError as exc:
            row = Backup(
                device_id=device.id,
                trigger=trigger,
                source=source,
                status="FAILED",
                error=f"{exc.code}: {exc.message}"[:2000],
                created_by=principal.user_id if principal else None,
            )
            await self._save(row)
            if strict:
                raise
            return row, True
        cfg = adapter_of(device).config
        digest = cfg.config_hash(text)
        latest = await self._latest(device.id, source)
        if latest is not None and latest.sha256 == digest:
            return latest, False
        backup_id = uuid.uuid4()
        normalized = cfg.normalize(text)
        row = Backup(
            id=backup_id,
            device_id=device.id,
            created_by=principal.user_id if principal else None,
            trigger=trigger,
            source=source,
            sha256=digest,
            size=len(normalized.encode()),
            family=device.family.value,
            platform=adapter_of(device).platform.value,
            vrp_version=device.vrp_version,
            content=cfg.sanitize(text),
            content_ciphertext=self._vault.encrypt(normalized.encode(), _aad(backup_id)),
            key_id=self._vault.active_kid,
            change_operation=change_operation,
        )
        await self._save(row)
        if latest is not None and trigger in {BackupTrigger.SCHEDULED, BackupTrigger.MANUAL}:
            # Change made outside the platform (or by a missed path): raise an alert.
            await self._events.emit(
                "configuration_change",
                device_id=str(device.id),
                hostname=device.hostname,
                previous_backup=str(latest.id),
                backup=str(row.id),
            )
        return row, True

    async def _save(self, row: Backup) -> None:
        async with UnitOfWork(self._db) as uow:
            uow.session.add(row)
            await uow.commit()

    # --- change hooks (mandatory pre-change backup) --------------------------------------
    async def pre_change_hook(
        self, principal: Principal | None, device: Device, operation: str
    ) -> None:
        await self.backup(
            principal, device, BackupTrigger.PRE_CHANGE, change_operation=operation, strict=True
        )

    async def post_change_hook(
        self, principal: Principal | None, device: Device, operation: str
    ) -> None:
        await self.backup(principal, device, BackupTrigger.POST_CHANGE, change_operation=operation)

    # --- queries ---------------------------------------------------------------------------
    async def list(self, principal: Principal, query: ListQuery) -> Page[Backup]:
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(BackupRepository)
            stmt = repo.base_query()
            if principal.group_scope:
                stmt = stmt.join(Device, Device.id == Backup.device_id).where(
                    Device.group_id.in_(principal.group_scope)
                )
            return await repo.list(query, stmt)

    async def get(self, principal: Principal, backup_id: uuid.UUID) -> Backup:
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(Backup, backup_id)
        if row is None:
            raise NotFound("Backup not found")
        await self._devices.get(principal, row.device_id)  # scope check
        return row

    async def diff(self, principal: Principal, a: uuid.UUID, b: uuid.UUID) -> str:
        old, new = await self.get(principal, a), await self.get(principal, b)
        if old.device_id != new.device_id:
            raise Conflict("backups belong to different devices")
        device = await self._devices.get(principal, old.device_id)
        return adapter_of(device).config.unified_diff(
            old.content or "",
            new.content or "",
            old_name=f"backup:{old.id}",
            new_name=f"backup:{new.id}",
        )

    async def mark_known_good(
        self, principal: Principal, ctx: RequestContext, backup_id: uuid.UUID
    ) -> Backup:
        row = await self.get(principal, backup_id)
        if row.status != "SUCCESS":
            raise Conflict("only successful backups can be known-good")
        async with UnitOfWork(self._db) as uow:
            await uow.session.execute(
                update(Backup).where(Backup.device_id == row.device_id).values(known_good=False)
            )
            await uow.session.execute(
                update(Backup).where(Backup.id == row.id).values(known_good=True)
            )
            await uow.commit()
        await self._audit.record(
            operation="backup.known_good",
            success=True,
            principal=principal,
            ctx=ctx,
            device_id=row.device_id,
            result_summary=str(row.id),
        )
        row.known_good = True
        return row

    async def _baseline(self, device_id: uuid.UUID) -> Backup | None:
        async with UnitOfWork(self._db) as uow:
            known = await uow.session.scalar(
                select(Backup).where(Backup.device_id == device_id, Backup.known_good.is_(True))
            )
        return known or await self._latest(device_id, BackupSource.CURRENT)

    async def drift(self, principal: Principal, device_id: uuid.UUID) -> DriftReport:
        device = await self._devices.get(principal, device_id)
        baseline = await self._baseline(device.id)
        running = await self._collection.query(device, Q.RUNNING_CONFIG, principal)
        if baseline is None:
            return DriftReport(device.id, None, False, "")
        cfg = adapter_of(device).config
        drifted = cfg.config_hash(running) != baseline.sha256
        diff = (
            cfg.unified_diff(
                baseline.content or "",
                cfg.sanitize(running),
                old_name="baseline",
                new_name="running",
            )
            if drifted
            else ""
        )
        return DriftReport(device.id, baseline.id, drifted, diff)

    def original(self, row: Backup) -> str:
        if not row.content_ciphertext:
            raise Conflict("backup has no stored content")
        return self._vault.decrypt(row.content_ciphertext, _aad(row.id)).decode()

    # --- guarded restore ---------------------------------------------------------------------
    async def restore(
        self,
        principal: Principal,
        ctx: RequestContext,
        backup_id: uuid.UUID,
        options: ChangeOptions,
    ) -> RestoreResult:
        if not (
            principal.can(Permission.BACKUPS_RESTORE) and principal.at_least(Role.NETWORK_ADMIN)
        ):
            raise Forbidden("restore needs ADMIN or NETWORK_ADMIN")
        row = await self.get(principal, backup_id)
        device = await self._devices.get(principal, row.device_id)
        if row.status != "SUCCESS" or row.source is not BackupSource.CURRENT:
            raise Conflict("only successful current-configuration backups can be restored")
        adapter = adapter_of(device)
        backup_platform = row.platform or Platform.HUAWEI_VRP.value
        same_platform = backup_platform == adapter.platform.value or (
            row.platform is None and adapter.platform.is_huawei
        )
        if (
            not same_platform
            or row.family != device.family.value
            or (row.vrp_version and device.vrp_version and row.vrp_version != device.vrp_version)
        ):
            raise Conflict(
                f"backup was taken on {backup_platform} {row.family}/{row.vrp_version}, device "
                f"is {adapter.platform.value} {device.family.value}/{device.vrp_version}; "
                "refusing restore"
            )
        running = await self._collection.query(device, Q.RUNNING_CONFIG, principal)
        original = self.original(row)
        plan_text = adapter.config.restore_plan(running, original)
        diff = adapter.config.unified_diff(
            adapter.config.sanitize(running),
            row.content or "",
            old_name="running",
            new_name=f"backup:{row.id}",
        )
        plan = ConfigPlan(
            "backup.restore",
            plan_text.lines,
            summary=f"restore backup {row.id}",
            notes=plan_text.notes,
            min_category=CommandCategory.DANGEROUS,
            min_category_reason="configuration restore",
            wants_rollback_timer=bool(options.rollback_minutes),
        )
        change = await self._changes.apply(principal, ctx, device.id, plan, options)
        return RestoreResult(change, diff, plan_text.notes)
