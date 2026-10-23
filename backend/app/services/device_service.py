"""Device inventory: CRUD, insecure-protocol gating, group scoping, host-key approval."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.database import Database
from app.core.exceptions import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.rbac import Permission
from app.domain.devices import CredentialKind, DeviceProtocol, HostKeyStatus
from app.domain.identity import Principal
from app.models.device import Device, DeviceGroup, SshHostKey
from app.repositories.base import ListQuery, Page
from app.repositories.devices import (
    CredentialRepository,
    DeviceGroupRepository,
    DeviceRepository,
    HostKeyRepository,
)
from app.repositories.uow import UnitOfWork
from app.schemas.devices import DeviceCreate, DeviceGroupIn, DeviceUpdate

_SNMP_KINDS = (CredentialKind.SNMP_V2C, CredentialKind.SNMP_V3)
_NON_NULLABLE = {
    "hostname",
    "mgmt_ip",
    "family",
    "protocol",
    "port",
    "requires_super",
    "timeout",
    "tags",
    "uplink_interfaces",
    "status",
    "allow_insecure_protocol",
}


class DeviceService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- helpers -------------------------------------------------------------------------
    @staticmethod
    def _check_scope(principal: Principal, device: Device) -> None:
        if not principal.in_scope(device.group_id):
            raise NotFound("Device not found")  # do not reveal out-of-scope devices

    @staticmethod
    def _check_insecure(principal: Principal, protocol: DeviceProtocol, allowed: bool) -> None:
        if not protocol.insecure:
            return
        if not allowed:
            raise ValidationFailed("telnet requires allow_insecure_protocol=true")
        if not principal.can(Permission.INSECURE_PROTOCOL):
            raise Forbidden("only ADMIN/NETWORK_ADMIN may register insecure protocols")

    @staticmethod
    async def _check_refs(uow: UnitOfWork, values: dict[str, Any], principal: Principal) -> None:
        creds = uow.repo(CredentialRepository)
        if values.get("credential_id"):
            cred = await creds.get(values["credential_id"])
            if cred is None:
                raise ValidationFailed("credential_id does not exist")
            if (
                cred.kind is not CredentialKind.CLI
                and values.get("protocol") != DeviceProtocol.SNMP
            ):
                raise ValidationFailed("credential_id must reference a 'cli' credential")
        if values.get("snmp_credential_id"):
            cred = await creds.get(values["snmp_credential_id"])
            if cred is None or cred.kind not in _SNMP_KINDS:
                raise ValidationFailed("snmp_credential_id must reference an SNMP credential")
        if values.get("group_id"):
            if await uow.repo(DeviceGroupRepository).get(values["group_id"]) is None:
                raise ValidationFailed("group_id does not exist")
            if not principal.in_scope(values["group_id"]):
                raise Forbidden("group outside your scope")

    # --- CRUD ----------------------------------------------------------------------------
    async def create(self, principal: Principal, data: DeviceCreate) -> Device:
        self._check_insecure(principal, data.protocol, data.allow_insecure_protocol)
        values = data.model_dump()
        values["mgmt_ip"] = str(data.mgmt_ip)
        values["port"] = data.port or data.protocol.default_port
        if principal.group_scope and not data.group_id:
            raise ValidationFailed("group_id is required for group-scoped users")
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(DeviceRepository)
            if await repo.by_ip(values["mgmt_ip"]):
                raise Conflict("a device with this mgmt_ip already exists")
            await self._check_refs(uow, values, principal)
            device = Device(**values, vendor="Huawei", capabilities={})
            repo.add(device)
            await uow.commit()
            return device

    async def get(self, principal: Principal, device_id: uuid.UUID) -> Device:
        async with UnitOfWork(self._db) as uow:
            device = await uow.repo(DeviceRepository).get_or_404(device_id)
        self._check_scope(principal, device)
        return device

    async def search(
        self, principal: Principal, query: ListQuery, q: str | None = None, tag: str | None = None
    ) -> Page[Device]:
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(DeviceRepository)
            return await repo.list(query, repo.scoped_query(principal.group_scope, q, tag))

    async def patch(self, principal: Principal, device_id: uuid.UUID, data: DeviceUpdate) -> Device:
        changes = data.model_dump(exclude_unset=True)
        for key in _NON_NULLABLE:
            if changes.get(key, "") is None:
                changes.pop(key)
        return await self._apply(principal, device_id, changes)

    async def replace(
        self, principal: Principal, device_id: uuid.UUID, data: DeviceCreate
    ) -> Device:
        changes = data.model_dump()
        changes["port"] = data.port or data.protocol.default_port
        return await self._apply(principal, device_id, changes)

    async def _apply(
        self, principal: Principal, device_id: uuid.UUID, changes: dict[str, Any]
    ) -> Device:
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(DeviceRepository)
            device = await repo.get_or_404(device_id)
            self._check_scope(principal, device)
            if changes.get("mgmt_ip") is not None:
                changes["mgmt_ip"] = str(changes["mgmt_ip"])
                other = await repo.by_ip(changes["mgmt_ip"])
                if other is not None and other.id != device.id:
                    raise Conflict("a device with this mgmt_ip already exists")
            protocol: DeviceProtocol = changes.get("protocol") or device.protocol
            allowed = changes.get("allow_insecure_protocol", device.allow_insecure_protocol)
            if "protocol" in changes:
                self._check_insecure(principal, protocol, bool(allowed))
                changes.setdefault("port", protocol.default_port)
            await self._check_refs(uow, {**changes, "protocol": protocol}, principal)
            for key, value in changes.items():
                setattr(device, key, value)
            await uow.commit()
            return device

    async def delete(self, principal: Principal, device_id: uuid.UUID) -> None:
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(DeviceRepository)
            device = await repo.get_or_404(device_id)
            self._check_scope(principal, device)
            await repo.delete(device)
            await uow.commit()

    async def update_facts(self, device_id: uuid.UUID, **facts: Any) -> None:
        """Persist detected facts (family, version, capabilities, status, last_seen)."""
        async with UnitOfWork(self._db) as uow:
            device = await uow.repo(DeviceRepository).get_or_404(device_id)
            for key, value in facts.items():
                setattr(device, key, value)
            await uow.commit()

    # --- groups --------------------------------------------------------------------------
    async def create_group(self, data: DeviceGroupIn) -> DeviceGroup:
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(DeviceGroupRepository)
            if await repo.by_name(data.name):
                raise Conflict("group name already exists")
            group = repo.add(DeviceGroup(**data.model_dump()))
            await uow.commit()
            return group

    async def list_groups(self, query: ListQuery) -> Page[DeviceGroup]:
        async with UnitOfWork(self._db) as uow:
            return await uow.repo(DeviceGroupRepository).list(query)

    async def update_group(self, group_id: uuid.UUID, data: DeviceGroupIn) -> DeviceGroup:
        async with UnitOfWork(self._db) as uow:
            group = await uow.repo(DeviceGroupRepository).get_or_404(group_id)
            for key, value in data.model_dump().items():
                setattr(group, key, value)
            await uow.commit()
            return group

    async def delete_group(self, group_id: uuid.UUID) -> None:
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(DeviceGroupRepository)
            await repo.delete(await repo.get_or_404(group_id))
            await uow.commit()

    # --- host keys -----------------------------------------------------------------------
    async def host_keys(self, principal: Principal, device_id: uuid.UUID) -> list[SshHostKey]:
        await self.get(principal, device_id)
        async with UnitOfWork(self._db) as uow:
            return await uow.repo(HostKeyRepository).for_device(device_id)

    async def approve_host_key(
        self, principal: Principal, device_id: uuid.UUID, key_id: uuid.UUID
    ) -> SshHostKey:
        """Approve a PENDING key (rotation); previously trusted keys become REJECTED."""
        await self.get(principal, device_id)
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(HostKeyRepository)
            key = await repo.get_or_404(key_id)
            if key.device_id != device_id:
                raise NotFound("host key not found")
            for other in await repo.for_device(device_id):
                if other.id != key.id and other.status is HostKeyStatus.TRUSTED:
                    other.status = HostKeyStatus.REJECTED
            key.status = HostKeyStatus.TRUSTED
            key.approved_by = principal.user_id
            key.approved_at = datetime.now(UTC)
            await uow.commit()
            return key
