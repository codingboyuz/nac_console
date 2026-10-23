"""DeviceConnector: credentials + host-key pinning + bounded sessions + threadpool execution."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

import anyio

from app.core.config import Settings
from app.core.database import Database
from app.core.events import EventBus
from app.core.exceptions import (
    DeviceAuthFailed,
    DeviceUnreachable,
    HostKeyMismatch,
    ValidationFailed,
)
from app.domain.devices import CredentialKind, DeviceProtocol, DeviceStatus, HostKeyStatus
from app.domain.identity import Principal
from app.models.device import Device, SshHostKey
from app.network.factory import DeviceConnectionFactory
from app.network.hostkeys import fetch_host_key
from app.network.ports import ConnectionParams, DeviceDriver, HostKey
from app.repositories.devices import CredentialRepository, DeviceRepository, HostKeyRepository
from app.repositories.uow import UnitOfWork
from app.services.audit_service import AuditService
from app.services.credential_service import CredentialService, ResolvedSecret

T = TypeVar("T")
HostKeyFetcher = Callable[[str, int, float], HostKey]
_SSH_TRANSPORTS = {DeviceProtocol.SSH, DeviceProtocol.NETCONF}


class DeviceConnector:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        credentials: CredentialService,
        factory: DeviceConnectionFactory,
        audit: AuditService,
        *,
        host_key_fetcher: HostKeyFetcher = fetch_host_key,
    ) -> None:
        self._settings = settings
        self._db = db
        self._credentials = credentials
        self.factory = factory
        self._audit = audit
        self._fetch_key = host_key_fetcher
        self.events: EventBus | None = None  # set by the container (device_online/offline)

    async def _secret(self, device: Device) -> ResolvedSecret | None:
        cred_id = device.credential_id
        if device.protocol is DeviceProtocol.SNMP:
            cred_id = device.snmp_credential_id or device.credential_id
        if cred_id is None:
            raise ValidationFailed("device has no credential configured")
        async with UnitOfWork(self._db) as uow:
            cred = await uow.repo(CredentialRepository).get_or_404(cred_id)
        if device.protocol is not DeviceProtocol.SNMP:
            CredentialService.ensure_kind(cred, CredentialKind.CLI)
        return self._credentials.resolve_row(cred)

    async def params_for(
        self, device: Device, principal: Principal | None = None
    ) -> ConnectionParams:
        secret = await self._secret(device)
        pinned: HostKey | None = None
        strict = self._settings.ssh_strict_host_key
        if device.protocol in _SSH_TRANSPORTS and strict:
            pinned = await self._verify_host_key(device, principal)
        snmp: dict[str, object] = {}
        if secret is not None and secret.kind is not CredentialKind.CLI:
            snmp = {"kind": secret.kind.value, "secret": secret}
        return ConnectionParams(
            device_id=str(device.id),
            host=device.mgmt_ip,
            port=device.port,
            protocol=device.protocol,
            family=device.family,
            username=secret.username if secret else None,
            password=secret.password if secret else None,
            private_key=secret.private_key if secret else None,
            super_password=secret.super_password if secret else None,
            requires_super=device.requires_super,
            timeout=device.timeout,
            connect_retries=self._settings.device_connect_retries,
            pinned_host_key=pinned,
            strict_host_key=strict,
            snmp=snmp,
        )

    async def _verify_host_key(self, device: Device, principal: Principal | None) -> HostKey:
        """Trust-on-first-use; afterwards only TRUSTED pinned keys are accepted."""
        remote = await anyio.to_thread.run_sync(
            self._fetch_key, device.mgmt_ip, device.port, float(device.timeout)
        )
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(HostKeyRepository)
            keys = await repo.for_device(device.id)
            trusted = [k for k in keys if k.status is HostKeyStatus.TRUSTED]
            known = {k.fingerprint: k for k in keys}
            if not trusted:
                repo.add(self._key_row(device, remote, HostKeyStatus.TRUSTED))
                await uow.commit()
                await self._audit.record(
                    operation="hostkey.tofu_pinned",
                    success=True,
                    principal=principal,
                    device_id=device.id,
                    result_summary=remote.fingerprint,
                )
                return remote
            match = next((k for k in trusted if k.fingerprint == remote.fingerprint), None)
            if match is not None:
                return HostKey(match.key_type, match.public_key, match.fingerprint)
            if remote.fingerprint not in known:
                repo.add(self._key_row(device, remote, HostKeyStatus.PENDING))
                await uow.commit()
        await self._audit.record(
            operation="hostkey.mismatch",
            success=False,
            principal=principal,
            device_id=device.id,
            result_summary=f"presented {remote.fingerprint}",
        )
        raise HostKeyMismatch(
            "SSH host key changed; approve the pending key via "
            f"/api/v1/devices/{device.id}/host-keys if the change is expected"
        )

    @staticmethod
    def _key_row(device: Device, key: HostKey, status: HostKeyStatus) -> SshHostKey:
        return SshHostKey(
            device_id=device.id,
            key_type=key.key_type,
            fingerprint=key.fingerprint,
            public_key=key.public_key_b64,
            status=status,
        )

    async def run_snmp(
        self,
        device: Device,
        operation: Callable[[DeviceDriver], T],
        principal: Principal | None = None,
    ) -> T:
        """Poll over SNMP regardless of the device's CLI protocol (needs snmp_credential_id)."""
        if device.snmp_credential_id is None:
            raise ValidationFailed("device has no SNMP credential")
        async with UnitOfWork(self._db) as uow:
            cred = await uow.repo(CredentialRepository).get_or_404(device.snmp_credential_id)
        secret = self._credentials.resolve_row(cred)
        params = ConnectionParams(
            device_id=str(device.id),
            host=device.mgmt_ip,
            port=device.snmp_port,
            protocol=DeviceProtocol.SNMP,
            family=device.family,
            timeout=min(device.timeout, 10),
            snmp={"kind": secret.kind.value, "secret": secret},
        )
        return await anyio.to_thread.run_sync(self._execute, params, operation)

    async def run(
        self,
        device: Device,
        operation: Callable[[DeviceDriver], T],
        principal: Principal | None = None,
    ) -> T:
        """Open a short-lived session, run ``operation`` in a worker thread, track status."""
        try:
            params = await self.params_for(device, principal)
            async with self.factory.slot(str(device.id)):
                result = await anyio.to_thread.run_sync(self._execute, params, operation)
        except DeviceUnreachable:
            await self._set_status(device, DeviceStatus.UNREACHABLE)
            raise
        except DeviceAuthFailed:
            await self._set_status(device, DeviceStatus.AUTH_FAILED)
            raise
        await self._set_status(device, DeviceStatus.ONLINE, seen=True)
        return result

    def _execute(self, params: ConnectionParams, operation: Callable[[DeviceDriver], T]) -> T:
        with self.factory.create(params) as driver:
            return operation(driver)

    async def _set_status(
        self, device: Device, status: DeviceStatus, *, seen: bool = False
    ) -> None:
        if device.status is DeviceStatus.MAINTENANCE:
            return
        async with UnitOfWork(self._db) as uow:
            row = await uow.repo(DeviceRepository).get(device.id)
            if row is None:
                return
            previous = row.status
            row.status = status
            if seen:
                row.last_seen = datetime.now(UTC)
            await uow.commit()
        if self.events is None or previous is status:
            return
        name = "device_online" if status is DeviceStatus.ONLINE else "device_offline"
        await self.events.emit(
            name,
            device_id=str(device.id),
            hostname=device.hostname,
            status=status.value,
            previous=previous.value,
        )
