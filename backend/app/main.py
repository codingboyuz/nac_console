"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from redis.asyncio import Redis

from app.api import health
from app.api.audit_middleware import AuditMiddleware
from app.api.router import api_router, internal_router
from app.api.v1 import ws
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.metrics import metrics_router, setup_metrics
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RequestIdMiddleware,
    SecureHeadersMiddleware,
)
from app.core.redis import create_redis
from app.services.container import ServiceContainer

log = get_logger(__name__)

OPENAPI_TAGS = [
    {"name": "health", "description": "Liveness / readiness probes"},
    {"name": "auth", "description": "Login, token refresh/rotation, logout, WebSocket tickets"},
    {"name": "users", "description": "User administration (RBAC)"},
    {"name": "devices", "description": "Device inventory, connection tests, guarded commands"},
    {"name": "jobs", "description": "Asynchronous job engine"},
    {"name": "nac", "description": "Endpoints, NAC policies, sessions"},
    {"name": "internal", "description": "Network-restricted callbacks (RADIUS)"},
]


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    redis: Redis | None = None,
) -> FastAPI:
    """Build the application. Tests inject ``db``/``redis``; production builds its own."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned_db = db is None
        owned_redis = redis is None
        app.state.settings = settings
        app.state.db = db or Database.from_settings(settings)
        app.state.redis = redis or create_redis(settings.redis_url)
        app.state.services = ServiceContainer.build(settings, app.state.db, app.state.redis)
        log.info("startup", environment=settings.environment.value)
        try:
            yield
        finally:
            await app.state.services.aclose()
            if owned_redis:
                await app.state.redis.aclose()
            if owned_db:
                await app.state.db.dispose()
            log.info("shutdown")

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
        docs_url=None if settings.environment.value == "prod" else "/docs",
        redoc_url=None if settings.environment.value == "prod" else "/redoc",
    )
    register_exception_handlers(app)

    # Middleware order: last added runs first.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(SecureHeadersMiddleware, hsts=settings.is_production_like)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Location", "Retry-After"],
        )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(api_router)
    # WebSockets are mounted outside api_router: its HTTP rate-limit dependency needs a Request.
    app.include_router(ws.router, prefix="/api/v1")
    app.include_router(internal_router)
    if settings.metrics_enabled:
        setup_metrics(app)
        app.include_router(metrics_router)
    return app
