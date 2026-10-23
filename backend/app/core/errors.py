"""FastAPI exception handlers producing RFC 9457 application/problem+json."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError, RateLimited
from app.core.logging import get_logger
from app.core.redaction import redact

PROBLEM_JSON = "application/problem+json"
log = get_logger(__name__)


def _request_id(request: Request) -> str | None:
    rid = getattr(request.state, "request_id", None)
    return str(rid) if rid else None


def problem(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a problem+json response with the platform's error envelope."""
    body = {
        "type": f"urn:huawei-nac:error:{code}",
        "title": title,
        "status": status,
        "detail": message,
        "code": code,
        "message": message,
        "instance": str(request.url.path),
        "request_id": _request_id(request),
        "details": redact(details) if details is not None else None,
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - registered for AppError only
    headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
    if exc.status_code >= 500:
        log.warning("app_error", code=exc.code, message=exc.message)
    return problem(
        request,
        status=exc.status_code,
        code=exc.code,
        title=exc.title,
        message=exc.message,
        details=exc.details,
        headers=headers,
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return problem(
        request,
        status=422,
        code="validation_failed",
        title="Validation failed",
        message="Request validation failed",
        details=errors,
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    return problem(
        request,
        status=exc.status_code,
        code=f"http_{exc.status_code}",
        title=str(exc.detail),
        message=str(exc.detail),
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", error_type=type(exc).__name__)
    return problem(
        request,
        status=500,
        code="internal_error",
        title="Internal Server Error",
        message="An unexpected error occurred",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled)
