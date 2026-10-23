"""Authentication endpoints: login, refresh rotation, logout, current user."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import Ctx, CurrentUser, Services, get_claims, rate_limited
from app.core.rbac import ROLE_PERMISSIONS
from app.core.security import AccessClaims
from app.schemas.auth import LoginRequest, LogoutRequest, MeOut, RefreshRequest, TokenResponse
from app.schemas.common import ERROR_RESPONSES

router = APIRouter(prefix="/auth", tags=["auth"], responses=ERROR_RESPONSES)
_auth_limit = Depends(rate_limited("auth", "rate_limit_auth"))


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[_auth_limit],
    summary="Exchange username/password for an access + refresh token pair",
)
async def login(body: LoginRequest, services: Services, ctx: Ctx) -> TokenResponse:
    pair = await services.auth.login(body.username, body.password.get_secret_value(), ctx)
    return TokenResponse(
        access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[_auth_limit],
    summary="Rotate a refresh token (reuse revokes the whole session family)",
)
async def refresh(body: RefreshRequest, services: Services, ctx: Ctx) -> TokenResponse:
    pair = await services.auth.refresh(body.refresh_token.get_secret_value(), ctx)
    return TokenResponse(
        access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke current tokens")
async def logout(
    body: LogoutRequest,
    services: Services,
    claims: Annotated[AccessClaims, Depends(get_claims)],
) -> None:
    token = body.refresh_token.get_secret_value() if body.refresh_token else None
    await services.auth.logout(claims, token)


@router.post(
    "/ws-ticket",
    summary="Issue a single-use WebSocket ticket (valid for WS_TICKET_TTL_SECONDS)",
)
async def ws_ticket(principal: CurrentUser, services: Services) -> dict[str, str | int]:
    ticket = await services.auth.issue_ws_ticket(principal)
    return {"ticket": ticket, "expires_in": services.settings.ws_ticket_ttl_seconds}


@router.get("/me", response_model=MeOut, summary="Current user and effective permissions")
async def me(principal: CurrentUser, services: Services) -> MeOut:
    user = await services.users.get(principal.user_id)
    return MeOut.model_validate(
        {
            **{f: getattr(user, f) for f in MeOut.model_fields if f != "permissions"},
            "permissions": sorted(p.value for p in ROLE_PERMISSIONS[user.role]),
        }
    )
