"""Device inventory CRUD and host-key management."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Paging, Services, require
from app.core.rbac import Permission
from app.domain.devices import DeviceFamily, DeviceProtocol, DeviceStatus
from app.domain.identity import Principal
from app.repositories.base import ListQuery
from app.schemas.common import ERROR_RESPONSES, PageResponse, page_of
from app.schemas.devices import DeviceCreate, DeviceOut, DeviceUpdate, HostKeyOut

router = APIRouter(prefix="/devices", tags=["devices"], responses=ERROR_RESPONSES)

CanRead = Annotated[Principal, Depends(require(Permission.DEVICES_READ))]
CanWrite = Annotated[Principal, Depends(require(Permission.DEVICES_WRITE))]


@router.post(
    "",
    response_model=DeviceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a device (Telnet needs allow_insecure_protocol=true)",
)
async def create_device(body: DeviceCreate, services: Services, actor: CanWrite) -> DeviceOut:
    return DeviceOut.model_validate(await services.devices.create(actor, body))


@router.get("", response_model=PageResponse[DeviceOut], summary="List devices")
async def list_devices(
    services: Services,
    actor: CanRead,
    paging: Paging,
    status_: Annotated[DeviceStatus | None, Query(alias="status")] = None,
    family: DeviceFamily | None = None,
    protocol: DeviceProtocol | None = None,
    group_id: uuid.UUID | None = None,
    site: Annotated[str | None, Query(max_length=128)] = None,
    q: Annotated[str | None, Query(max_length=64, description="hostname/site search")] = None,
    tag: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")] = None,
) -> PageResponse[DeviceOut]:
    filters = {
        "status": status_,
        "family": family,
        "protocol": protocol,
        "group_id": group_id,
        "site": site,
    }
    query = ListQuery(paging.limit, paging.offset, paging.sort or "hostname", filters)
    return page_of(await services.devices.search(actor, query, q=q, tag=tag), DeviceOut)


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(device_id: uuid.UUID, services: Services, actor: CanRead) -> DeviceOut:
    return DeviceOut.model_validate(await services.devices.get(actor, device_id))


@router.put("/{device_id}", response_model=DeviceOut, summary="Replace a device")
async def replace_device(
    device_id: uuid.UUID, body: DeviceCreate, services: Services, actor: CanWrite
) -> DeviceOut:
    return DeviceOut.model_validate(await services.devices.replace(actor, device_id, body))


@router.patch("/{device_id}", response_model=DeviceOut, summary="Partially update a device")
async def patch_device(
    device_id: uuid.UUID, body: DeviceUpdate, services: Services, actor: CanWrite
) -> DeviceOut:
    return DeviceOut.model_validate(await services.devices.patch(actor, device_id, body))


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: uuid.UUID,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.DEVICES_DELETE))],
) -> None:
    await services.devices.delete(actor, device_id)


@router.get("/{device_id}/host-keys", response_model=list[HostKeyOut], summary="Pinned SSH keys")
async def list_host_keys(
    device_id: uuid.UUID, services: Services, actor: CanRead
) -> list[HostKeyOut]:
    keys = await services.devices.host_keys(actor, device_id)
    return [HostKeyOut.model_validate(k) for k in keys]


@router.post(
    "/{device_id}/host-keys/{key_id}/approve",
    response_model=HostKeyOut,
    summary="Approve a pending SSH host key (rotation)",
)
async def approve_host_key(
    device_id: uuid.UUID,
    key_id: uuid.UUID,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.HOSTKEY_APPROVE))],
) -> HostKeyOut:
    key = await services.devices.approve_host_key(actor, device_id, key_id)
    return HostKeyOut.model_validate(key)
