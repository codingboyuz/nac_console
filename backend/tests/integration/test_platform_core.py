"""Phase 1: health, problem+json, middleware, metrics restriction."""

from __future__ import annotations

from httpx import AsyncClient


async def test_live(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_checks_db_and_redis(client: AsyncClient) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"


async def test_request_id_generated_and_echoed(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert len(resp.headers["x-request-id"]) == 32
    resp = await client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert resp.headers["x-request-id"] == "abc-123"
    resp = await client.get("/health/live", headers={"X-Request-ID": "bad id\r\n"})
    assert resp.headers["x-request-id"] != "bad id"


async def test_secure_headers(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]


async def test_not_found_is_problem_json(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == 404
    assert body["request_id"] == resp.headers["x-request-id"]


async def test_body_size_limit(client: AsyncClient) -> None:
    resp = await client.post("/health/live", content=b"x" * 2_000_000)
    assert resp.status_code == 413


async def test_metrics_restricted_to_internal_networks(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "nac_http_requests_total" in resp.text


async def test_cors_preflight_allows_configured_origin(client: AsyncClient) -> None:
    resp = await client.options(
        "/health/live",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    resp = await client.options(
        "/health/live",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in resp.headers
