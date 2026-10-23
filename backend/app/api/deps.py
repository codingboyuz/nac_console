"""FastAPI dependencies: services, request context, authentication, RBAC, rate limits."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import Forbidden, Unauthorized
from app.core.net_guard import client_ip
from app.core.rate_limit import RateLimit
from app.core.rbac import Permission
from app.core.security import AccessClaims
from app.domain.identity import Principal, RequestContext
from app.repositories.base import ListQuery
from app.services.container import ServiceContainer

_bearer = HTTPBearer(auto_error=False, description="JWT access token")


def get_services(request: Request) -> ServiceContainer:
    services: ServiceContainer = request.app.state.services
    return services


Services = Annotated[ServiceContainer, Depends(get_services)]


def get_ctx(request: Request) -> RequestContext:
    return RequestContext(
        source_ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )


Ctx = Annotated[RequestContext, Depends(get_ctx)]


async def _authenticate(
    request: Request,
    services: Services,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> tuple[Principal, AccessClaims]:
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("missing bearer token")
    principal, claims = await services.auth.authenticate(creds.credentials)
    request.state.principal = principal
    return principal, claims


async def get_principal(
    auth: Annotated[tuple[Principal, AccessClaims], Depends(_authenticate)],
) -> Principal:
    return auth[0]


async def get_claims(
    auth: Annotated[tuple[Principal, AccessClaims], Depends(_authenticate)],
) -> AccessClaims:
    return auth[1]


CurrentUser = Annotated[Principal, Depends(get_principal)]


def require(permission: Permission) -> Callable[..., Awaitable[Principal]]:
    """Dependency enforcing one permission; denials are audited."""

    async def dependency(
        request: Request, principal: CurrentUser, services: Services, ctx: Ctx
    ) -> Principal:
        if not principal.can(permission):
            await services.audit.record(
                operation=f"denied:{request.method} {request.url.path}",
                success=False,
                principal=principal,
                ctx=ctx,
                reason=f"missing permission {permission.value}",
            )
            raise Forbidden(f"missing permission: {permission.value}")
        return principal

    dependency.__name__ = f"require_{permission.name.lower()}"
    return dependency


def rate_limited(bucket: str, setting_name: str) -> Callable[..., Awaitable[None]]:
    """Per-identity (user id when authenticated, else source IP) rate limit."""

    async def dependency(request: Request, services: Services) -> None:
        spec: str = getattr(services.settings, setting_name)
        principal = getattr(request.state, "principal", None)
        identity = str(principal.user_id) if principal else client_ip(request)
        await services.rate_limiter.hit(bucket, identity, RateLimit.parse(spec))

    return dependency


def list_query(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str | None, Query(max_length=64, pattern=r"^[-+]?[a-z_]+$")] = None,
) -> ListQuery:
    return ListQuery(limit=limit, offset=offset, sort=sort)


Paging = Annotated[ListQuery, Depends(list_query)]
