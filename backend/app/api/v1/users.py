"""User administration (ADMIN) and audit log access (AUDITOR+)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, Paging, Services, require
from app.core.rbac import Permission, Role
from app.domain.identity import Principal
from app.repositories.base import ListQuery
from app.schemas.auth import AuditOut, PasswordChange, UserCreate, UserOut, UserUpdate
from app.schemas.common import ERROR_RESPONSES, PageResponse

router = APIRouter(prefix="/users", tags=["users"], responses=ERROR_RESPONSES)
audit_router = APIRouter(prefix="/logs", tags=["logs"], responses=ERROR_RESPONSES)

CanRead = Annotated[Principal, Depends(require(Permission.USERS_READ))]
CanWrite = Annotated[Principal, Depends(require(Permission.USERS_WRITE))]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, services: Services, _: CanWrite) -> UserOut:
    return UserOut.model_validate(await services.users.create(body))


@router.get("", response_model=PageResponse[UserOut])
async def list_users(
    services: Services,
    _: CanRead,
    paging: Paging,
    role: Annotated[Role | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> PageResponse[UserOut]:
    query = ListQuery(
        paging.limit, paging.offset, paging.sort, {"role": role, "is_active": is_active}
    )
    page = await services.users.list(query)
    return PageResponse(
        items=[UserOut.model_validate(u) for u in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, services: Services, _: CanRead) -> UserOut:
    return UserOut.model_validate(await services.users.get(user_id))


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, services: Services, actor: CanWrite
) -> UserOut:
    return UserOut.model_validate(await services.users.update(actor, user_id, body))


@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    user_id: uuid.UUID, body: PasswordChange, services: Services, actor: CurrentUser
) -> None:
    await services.users.change_password(actor, user_id, body)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(user_id: uuid.UUID, services: Services, actor: CanWrite) -> None:
    await services.users.deactivate(actor, user_id)


@audit_router.get("/audit", response_model=PageResponse[AuditOut], summary="Audit trail")
async def list_audit(
    services: Services,
    _: Annotated[Principal, Depends(require(Permission.AUDIT_READ))],
    paging: Paging,
    user_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    operation: Annotated[str | None, Query(max_length=96)] = None,
    success: bool | None = None,
) -> PageResponse[AuditOut]:
    query = ListQuery(
        paging.limit,
        paging.offset,
        paging.sort or "-timestamp",
        {"user_id": user_id, "device_id": device_id, "operation": operation, "success": success},
    )
    page = await services.audit.list(query)
    return PageResponse(
        items=[AuditOut.model_validate(a) for a in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
