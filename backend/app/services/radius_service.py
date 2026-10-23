"""RADIUS server definitions (write-only encrypted shared secret)."""

from __future__ import annotations

import uuid

from pydantic import SecretStr
from sqlalchemy import select

from app.core.crypto import CredentialVault
from app.core.database import Database
from app.core.exceptions import Conflict, NotFound
from app.models.nac import RadiusServer
from app.repositories.uow import UnitOfWork


class RadiusServerService:
    def __init__(self, db: Database, vault: CredentialVault) -> None:
        self._db = db
        self._vault = vault

    async def create(
        self,
        *,
        name: str,
        host: str,
        shared_secret: SecretStr,
        auth_port: int,
        acct_port: int,
        coa_port: int,
        request_timeout: int,
        retries: int,
        enabled: bool,
    ) -> RadiusServer:
        server_id = uuid.uuid4()
        async with UnitOfWork(self._db) as uow:
            if await uow.session.scalar(select(RadiusServer).where(RadiusServer.name == name)):
                raise Conflict("RADIUS server name already exists")
            row = RadiusServer(
                id=server_id,
                name=name,
                host=host,
                auth_port=auth_port,
                acct_port=acct_port,
                coa_port=coa_port,
                timeout=request_timeout,
                retries=retries,
                enabled=enabled,
                shared_secret_ciphertext=self._vault.encrypt(
                    shared_secret.get_secret_value().encode(), f"radius:{server_id}"
                ),
                key_id=self._vault.active_kid,
            )
            uow.session.add(row)
            await uow.commit()
            return row

    async def list(self) -> list[RadiusServer]:
        async with UnitOfWork(self._db) as uow:
            return list(
                (await uow.session.scalars(select(RadiusServer).order_by(RadiusServer.name))).all()
            )

    async def get(self, server_id: uuid.UUID) -> RadiusServer:
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(RadiusServer, server_id)
        if row is None:
            raise NotFound("RADIUS server not found")
        return row

    async def update_secret(self, server_id: uuid.UUID, secret: SecretStr) -> RadiusServer:
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(RadiusServer, server_id)
            if row is None:
                raise NotFound("RADIUS server not found")
            row.shared_secret_ciphertext = self._vault.encrypt(
                secret.get_secret_value().encode(), f"radius:{row.id}"
            )
            row.key_id = self._vault.active_kid
            await uow.commit()
            return row

    async def delete(self, server_id: uuid.UUID) -> None:
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.get(RadiusServer, server_id)
            if row is None:
                raise NotFound("RADIUS server not found")
            await uow.session.delete(row)
            await uow.commit()

    def secret(self, row: RadiusServer) -> str:
        """Decrypted shared secret for config generation inside the worker only."""
        return self._vault.decrypt(row.shared_secret_ciphertext, f"radius:{row.id}").decode()
