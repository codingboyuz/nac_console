"""Liveness and readiness probes (DB, Redis, Celery broker)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.core.logging import get_logger

router = APIRouter(prefix="/health", tags=["health"])
log = get_logger(__name__)


@router.get("/live", summary="Liveness probe")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe (DB, Redis, broker)")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    checks: dict[str, str] = {}
    state = request.app.state
    try:
        await state.db.ping()
        checks["database"] = "ok"
    except Exception as exc:  # readiness must report, not raise
        log.warning("ready_db_failed", error=type(exc).__name__)
        checks["database"] = "fail"
    try:
        await state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        log.warning("ready_redis_failed", error=type(exc).__name__)
        checks["redis"] = "fail"
    # The Celery broker is Redis by default; a separate broker URL is checked by the worker.
    checks["broker"] = (
        checks["redis"] if state.settings.broker_url == state.settings.redis_url else "skipped"
    )
    healthy = all(v in {"ok", "skipped"} for v in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "checks": checks}
