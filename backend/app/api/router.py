"""Top-level routers: public /api/v1 and network-restricted /internal/v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import rate_limited
from app.api.v1 import auth, users

api_router = APIRouter(
    prefix="/api/v1", dependencies=[Depends(rate_limited("global", "rate_limit_default"))]
)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(users.audit_router)

internal_router = APIRouter(prefix="/internal/v1")
