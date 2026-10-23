"""Prometheus metrics for the platform itself (per-app registry, network-restricted)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram
from prometheus_client.exposition import generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.net_guard import require_internal_network


class HttpMetrics:
    """Request counters and latency histogram bound to one registry."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self.registry = registry
        self.requests = Counter(
            "nac_http_requests_total",
            "HTTP requests",
            ["method", "route", "status"],
            registry=registry,
        )
        self.latency = Histogram(
            "nac_http_request_duration_seconds",
            "HTTP request latency",
            ["method", "route"],
            registry=registry,
        )


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, *, metrics: HttpMetrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", "unmatched")
            method = scope.get("method", "GET")
            self.metrics.requests.labels(method, template, str(status_holder["status"])).inc()
            self.metrics.latency.labels(method, template).observe(time.perf_counter() - start)


def setup_metrics(app: FastAPI) -> None:
    registry = CollectorRegistry(auto_describe=True)
    metrics = HttpMetrics(registry)
    app.state.metrics_registry = registry
    app.add_middleware(MetricsMiddleware, metrics=metrics)


metrics_router = APIRouter(tags=["internal"], include_in_schema=False)


@metrics_router.get("/metrics", dependencies=[Depends(require_internal_network)])
async def metrics(request: Request) -> Response:
    registry: CollectorRegistry = request.app.state.metrics_registry
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
