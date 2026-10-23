"""Device groups and the credential vault (write-only secrets)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import Paging, Services, require
from app.core.rbac import Permission
from app.domain.devices import CredentialKind
from app.domain.identity import Principal
from app.models.device import Credential
from app.repositories.base import ListQuery
from app.schemas.common import ERROR_RESPONSES, PageResponse, page_of
from app.schemas.devices import (
    CredentialCreate,
    CredentialOut,
    CredentialUpdate,
    DeviceGroupIn,
    DeviceGroupOut,
)

router = APIRouter(prefix="/device-groups", tags=["devices"], responses=ERROR_RESPONSES)
cred_router = APIRouter(prefix="/credentials", tags=["devices"], responses=ERROR_RESPONSES)

CanRead = Annotated[Principal, Depends(require(Permission.DEVICES_READ))]
CanWrite = Annotated[Principal, Depends(require(Permission.DEVICES_WRITE))]
CanCred = Annotated[Principal, Depends(require(Permission.CREDENTIALS_WRITE))]


@router.post("", response_model=DeviceGroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(body: DeviceGroupIn, services: Services, _: CanWrite) -> DeviceGroupOut:
    return DeviceGroupOut.model_validate(await services.devices.create_group(body))


@router.get("", response_model=PageResponse[DeviceGroupOut])
async def list_groups(
    services: Services, _: CanRead, paging: Paging
) -> PageResponse[DeviceGroupOut]:
    query = ListQuery(paging.limit, paging.offset, paging.sort or "name")
    return page_of(await services.devices.list_groups(query), DeviceGroupOut)


@router.put("/{group_id}", response_model=DeviceGroupOut)
async def update_group(
    group_id: uuid.UUID, body: DeviceGroupIn, services: Services, _: CanWrite
) -> DeviceGroupOut:
    return DeviceGroupOut.model_validate(await services.devices.update_group(group_id, body))


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: uuid.UUID, services: Services, _: CanWrite) -> None:
    await services.devices.delete_group(group_id)


def _cred_out(cred: Credential) -> CredentialOut:
    meta = cred.meta or {}
    return CredentialOut(
        id=cred.id,
        name=cred.name,
        kind=cred.kind,
        username=cred.username,
        description=cred.description,
        key_id=cred.key_id,
        secret_fields=list(meta.get("secret_fields", [])),
        auth_protocol=meta.get("auth_protocol"),
        priv_protocol=meta.get("priv_protocol"),
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )


@cred_router.post(
    "",
    response_model=CredentialOut,
    status_code=status.HTTP_201_CREATED,
    summary="Store an encrypted credential (secrets are write-only)",
)
async def create_credential(
    body: CredentialCreate, services: Services, _: CanCred
) -> CredentialOut:
    return _cred_out(await services.credentials.create(body))


@cred_router.get("", response_model=PageResponse[CredentialOut])
async def list_credentials(
    services: Services, _: CanRead, paging: Paging, kind: CredentialKind | None = None
) -> PageResponse[CredentialOut]:
    query = ListQuery(paging.limit, paging.offset, paging.sort or "name", {"kind": kind})
    page = await services.credentials.list(query)
    return PageResponse(
        items=[_cred_out(c) for c in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@cred_router.get("/{credential_id}", response_model=CredentialOut)
async def get_credential(credential_id: uuid.UUID, services: Services, _: CanRead) -> CredentialOut:
    return _cred_out(await services.credentials.get(credential_id))


@cred_router.patch("/{credential_id}", response_model=CredentialOut)
async def update_credential(
    credential_id: uuid.UUID, body: CredentialUpdate, services: Services, _: CanCred
) -> CredentialOut:
    return _cred_out(await services.credentials.update(credential_id, body))


@cred_router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(credential_id: uuid.UUID, services: Services, _: CanCred) -> None:
    await services.credentials.delete(credential_id)
