"""Credential vault service: encrypt on write, never return secrets, key rotation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from app.core.crypto import CredentialVault
from app.core.database import Database
from app.core.exceptions import Conflict, ValidationFailed
from app.domain.devices import CredentialKind
from app.models.device import Credential
from app.repositories.base import ListQuery, Page
from app.repositories.devices import CredentialRepository, DeviceRepository
from app.repositories.uow import UnitOfWork
from app.schemas.devices import CredentialCreate, CredentialUpdate

_SECRET_FIELDS = ("password", "private_key", "super_password", "community", "auth_key", "priv_key")


@dataclass(frozen=True, slots=True)
class ResolvedSecret:
    """Decrypted credential handed to drivers only (never serialized)."""

    kind: CredentialKind
    username: str | None
    password: SecretStr | None = None
    private_key: SecretStr | None = None
    super_password: SecretStr | None = None
    community: SecretStr | None = None
    auth_protocol: str | None = None
    auth_key: SecretStr | None = None
    priv_protocol: str | None = None
    priv_key: SecretStr | None = None

    def __repr__(self) -> str:  # never leak through logs / tracebacks
        return f"ResolvedSecret(kind={self.kind.value}, username={self.username!r})"


def _aad(credential_id: uuid.UUID) -> str:
    return f"credential:{credential_id}"


def _collect(data: CredentialCreate | CredentialUpdate) -> dict[str, str]:
    out: dict[str, str] = {}
    for field in _SECRET_FIELDS:
        value: SecretStr | None = getattr(data, field, None)
        if value is not None:
            out[field] = value.get_secret_value()
    return out


class CredentialService:
    def __init__(self, db: Database, vault: CredentialVault) -> None:
        self._db = db
        self._vault = vault

    async def create(self, data: CredentialCreate) -> Credential:
        async with UnitOfWork(self._db) as uow:
            repo = uow.repo(CredentialRepository)
            if await repo.by_name(data.name):
                raise Conflict("credential name already exists")
            cred_id = uuid.uuid4()
            secrets = _collect(data)
            cred = Credential(
                id=cred_id,
                name=data.name,
                kind=data.kind,
                username=data.username,
                description=data.description,
                secret_ciphertext=self._vault.encrypt_json(secrets, _aad(cred_id)),
                key_id=self._vault.active_kid,
                meta={
                    "secret_fields": sorted(secrets),
                    "auth_protocol": data.auth_protocol,
                    "priv_protocol": data.priv_protocol,
                },
            )
            repo.add(cred)
            await uow.commit()
            return cred

    async def update(self, credential_id: uuid.UUID, data: CredentialUpdate) -> Credential:
        async with UnitOfWork(self._db) as uow:
            cred = await uow.repo(CredentialRepository).get_or_404(credential_id)
            if data.description is not None:
                cred.description = data.description
            if data.username is not None:
                cred.username = data.username
            changes = _collect(data)
            if changes:
                secrets = self._vault.decrypt_json(cred.secret_ciphertext, _aad(cred.id))
                secrets.update(changes)
                cred.secret_ciphertext = self._vault.encrypt_json(secrets, _aad(cred.id))
                cred.key_id = self._vault.active_kid
                cred.meta = {**cred.meta, "secret_fields": sorted(secrets)}
            await uow.commit()
            return cred

    async def get(self, credential_id: uuid.UUID) -> Credential:
        async with UnitOfWork(self._db) as uow:
            return await uow.repo(CredentialRepository).get_or_404(credential_id)

    async def list(self, query: ListQuery) -> Page[Credential]:
        async with UnitOfWork(self._db) as uow:
            return await uow.repo(CredentialRepository).list(query)

    async def delete(self, credential_id: uuid.UUID) -> None:
        async with UnitOfWork(self._db) as uow:
            cred = await uow.repo(CredentialRepository).get_or_404(credential_id)
            if await uow.repo(DeviceRepository).uses_credential(cred.id):
                raise Conflict("credential is used by one or more devices")
            await uow.repo(CredentialRepository).delete(cred)
            await uow.commit()

    async def resolve(self, credential_id: uuid.UUID) -> ResolvedSecret:
        """Decrypt for a driver. Callers must not log or return the result."""
        async with UnitOfWork(self._db) as uow:
            cred = await uow.repo(CredentialRepository).get_or_404(credential_id)
        return self.resolve_row(cred)

    def resolve_row(self, cred: Credential) -> ResolvedSecret:
        raw = self._vault.decrypt_json(cred.secret_ciphertext, _aad(cred.id))

        def secret(name: str) -> SecretStr | None:
            return SecretStr(raw[name]) if raw.get(name) else None

        meta: dict[str, Any] = cred.meta or {}
        return ResolvedSecret(
            kind=cred.kind,
            username=cred.username,
            password=secret("password"),
            private_key=secret("private_key"),
            super_password=secret("super_password"),
            community=secret("community"),
            auth_protocol=meta.get("auth_protocol"),
            auth_key=secret("auth_key"),
            priv_protocol=meta.get("priv_protocol"),
            priv_key=secret("priv_key"),
        )

    async def rotate_keys(self) -> int:
        """Re-encrypt every credential not on the active key. Returns rows rotated."""
        async with UnitOfWork(self._db) as uow:
            stale = await uow.repo(CredentialRepository).all_not_on_key(self._vault.active_kid)
            for cred in stale:
                cred.secret_ciphertext = self._vault.rotate(cred.secret_ciphertext, _aad(cred.id))
                cred.key_id = self._vault.active_kid
            await uow.commit()
            return len(stale)

    @staticmethod
    def ensure_kind(cred: Credential, *allowed: CredentialKind) -> None:
        if cred.kind not in allowed:
            names = ", ".join(k.value for k in allowed)
            raise ValidationFailed(f"credential {cred.name!r} must be of kind {names}")
