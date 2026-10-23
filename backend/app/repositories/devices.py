"""Device, group, credential and host-key repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import Select, Text, cast, func, or_, select

from app.models.device import Credential, Device, DeviceGroup, SshHostKey
from app.repositories.base import Repository


class DeviceRepository(Repository[Device]):
    model = Device
    sortable = {
        "hostname": Device.hostname,
        "mgmt_ip": Device.mgmt_ip,
        "status": Device.status,
        "family": Device.family,
        "last_seen": Device.last_seen,
        "created_at": Device.created_at,
    }
    filterable = {
        "status": Device.status,
        "family": Device.family,
        "protocol": Device.protocol,
        "group_id": Device.group_id,
        "site": Device.site,
    }

    def scoped_query(
        self, scope: frozenset[uuid.UUID], q: str | None = None, tag: str | None = None
    ) -> Select[Device]:
        stmt = select(Device)
        if scope:
            stmt = stmt.where(Device.group_id.in_(scope))
        if q:
            stmt = stmt.where(
                or_(
                    Device.hostname.icontains(q, autoescape=True),
                    Device.site.icontains(q, autoescape=True),
                )
            )
        if tag:
            # Portable JSON array membership: tags are validated [A-Za-z0-9_.:-]; match as text.
            stmt = stmt.where(cast(Device.tags, Text).contains(f'"{tag}"', autoescape=True))
        return stmt

    async def by_ip(self, mgmt_ip: str) -> Device | None:
        return (
            await self.session.execute(select(Device).where(Device.mgmt_ip == mgmt_ip))
        ).scalar_one_or_none()

    async def many(self, ids: Sequence[uuid.UUID]) -> list[Device]:
        if not ids:
            return []
        rows = await self.session.scalars(select(Device).where(Device.id.in_(ids)))
        return list(rows.all())

    async def uses_credential(self, credential_id: uuid.UUID) -> bool:
        stmt = select(func.count()).where(
            or_(Device.credential_id == credential_id, Device.snmp_credential_id == credential_id)
        )
        return int((await self.session.execute(stmt)).scalar_one()) > 0


class DeviceGroupRepository(Repository[DeviceGroup]):
    model = DeviceGroup
    sortable = {"name": DeviceGroup.name, "created_at": DeviceGroup.created_at}

    async def by_name(self, name: str) -> DeviceGroup | None:
        return (
            await self.session.execute(select(DeviceGroup).where(DeviceGroup.name == name))
        ).scalar_one_or_none()


class CredentialRepository(Repository[Credential]):
    model = Credential
    sortable = {"name": Credential.name, "created_at": Credential.created_at}
    filterable = {"kind": Credential.kind}

    async def by_name(self, name: str) -> Credential | None:
        return (
            await self.session.execute(select(Credential).where(Credential.name == name))
        ).scalar_one_or_none()

    async def all_not_on_key(self, key_id: str) -> list[Credential]:
        rows = await self.session.scalars(select(Credential).where(Credential.key_id != key_id))
        return list(rows.all())


class HostKeyRepository(Repository[SshHostKey]):
    model = SshHostKey

    async def for_device(self, device_id: uuid.UUID) -> list[SshHostKey]:
        rows = await self.session.scalars(
            select(SshHostKey)
            .where(SshHostKey.device_id == device_id)
            .order_by(SshHostKey.created_at)
        )
        return list(rows.all())
