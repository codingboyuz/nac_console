"""Blanket audit for mutating requests and auth failures not audited by a service."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import audit_context
from app.core.logging import get_logger
from app.domain.identity import RequestContext

log = get_logger(__name__)
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_SKIP_PREFIXES = ("/health", "/metrics", "/internal/", "/docs", "/redoc", "/openapi.json")


class AuditMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "")
        if scope["type"] != "http" or path.startswith(_SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        marker = audit_context.begin_request()
        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            method = scope.get("method", "GET")
            code = status["code"]
            if not marker["audited"] and (method in _MUTATING or code in {401, 403}):
                await self._record(scope, method, path, code)

    @staticmethod
    async def _record(scope: Scope, method: str, path: str, code: int) -> None:
        state = scope.get("state", {})
        services = getattr(scope["app"].state, "services", None)
        if services is None:
            return
        headers = dict(scope.get("headers") or [])
        client = scope.get("client")
        ctx = RequestContext(
            source_ip=client[0] if client else None,
            user_agent=headers.get(b"user-agent", b"").decode(errors="replace") or None,
            request_id=state.get("request_id"),
        )
        try:
            await services.audit.record(
                operation=f"http:{method} {path}",
                success=code < 400,
                principal=state.get("principal"),
                ctx=ctx,
                result_summary=f"HTTP {code}",
            )
        except Exception as exc:  # audit failure must be visible but not crash the response
            log.error("audit_write_failed", error=type(exc).__name__, path=path)
