"""Top-level routers: public /api/v1 and network-restricted /internal/v1."""

from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")
internal_router = APIRouter(prefix="/internal/v1")
