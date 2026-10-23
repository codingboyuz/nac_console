"""Phase 2: authentication, token rotation, RBAC matrix and audit trail."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.core.rbac import Role
from app.models.audit import AuditLog
from tests.conftest import TEST_PASSWORD


async def _audit_rows(app: Any, operation_prefix: str) -> list[AuditLog]:
    async with app.state.db.session() as s:
        rows = (await s.execute(select(AuditLog).order_by(AuditLog.timestamp))).scalars().all()
    return [r for r in rows if r.operation.startswith(operation_prefix)]


async def test_login_and_me(client: AsyncClient, user_factory: Any, login: Any) -> None:
    user = await user_factory(Role.OPERATOR)
    tokens = await login(user.username)
    assert tokens["token_type"] == "bearer"
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "OPERATOR"
    assert "device:show" in body["permissions"]
    assert "password_hash" not in body
    assert "password" not in body


async def test_bad_password_audited_and_lockout(
    client: AsyncClient, app: Any, user_factory: Any
) -> None:
    user = await user_factory(Role.READ_ONLY)
    for _ in range(5):
        resp = await client.post(
            "/api/v1/auth/login", json={"username": user.username, "password": "wrong"}
        )
        assert resp.status_code == 401
    resp = await client.post(
        "/api/v1/auth/login", json={"username": user.username, "password": TEST_PASSWORD}
    )
    assert resp.status_code == 423
    rows = await _audit_rows(app, "auth.login")
    assert len(rows) == 6
    assert all(not r.success for r in rows)
    assert all("wrong" not in (r.reason or "") for r in rows)


async def test_unknown_user_gets_generic_401(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
    assert resp.status_code == 401
    assert resp.json()["message"] == "invalid username or password"


async def test_refresh_rotation_and_reuse_detection(
    client: AsyncClient, user_factory: Any, login: Any
) -> None:
    user = await user_factory(Role.OPERATOR)
    first = await login(user.username)
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert resp.status_code == 200
    second = resp.json()
    assert second["refresh_token"] != first["refresh_token"]
    # Replay of the rotated token revokes the family, including the newest token.
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert resp.status_code == 401
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert resp.status_code == 401


async def test_logout_revokes_access_token(
    client: AsyncClient, user_factory: Any, login: Any
) -> None:
    user = await user_factory(Role.OPERATOR)
    tokens = await login(user.username)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=headers
    )
    assert resp.status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert resp.status_code == 401


async def test_missing_and_garbage_tokens(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


RBAC_CASES = [
    # (method, path, json, allowed roles)
    ("GET", "/api/v1/users", None, {Role.ADMIN, Role.NETWORK_ADMIN, Role.AUDITOR}),
    (
        "POST",
        "/api/v1/users",
        {"username": "newuser_x", "password": "An0ther!Passw0rd", "role": "READ_ONLY"},
        {Role.ADMIN},
    ),
    ("GET", "/api/v1/logs/audit", None, {Role.ADMIN, Role.NETWORK_ADMIN, Role.AUDITOR}),
]


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("method", "path", "body", "allowed"), RBAC_CASES)
async def test_rbac_matrix(
    client: AsyncClient,
    app: Any,
    auth_headers: Any,
    role: Role,
    method: str,
    path: str,
    body: Any,
    allowed: set[Role],
) -> None:
    headers = await auth_headers(role)
    resp = await client.request(method, path, json=body, headers=headers)
    if role in allowed:
        assert resp.status_code in {200, 201}, resp.text
    else:
        assert resp.status_code == 403
        denied = await _audit_rows(app, "denied:")
        assert denied
        assert denied[-1].success is False
        assert denied[-1].role == role.value


async def test_mutations_are_audited(client: AsyncClient, app: Any, auth_headers: Any) -> None:
    headers = await auth_headers(Role.ADMIN)
    resp = await client.post(
        "/api/v1/users",
        json={"username": "audited_user", "password": "An0ther!Passw0rd", "role": "OPERATOR"},
        headers=headers,
    )
    assert resp.status_code == 201
    rows = await _audit_rows(app, "http:POST /api/v1/users")
    assert len(rows) == 1
    assert rows[0].success


async def test_weak_password_rejected(client: AsyncClient, auth_headers: Any) -> None:
    headers = await auth_headers(Role.ADMIN)
    resp = await client.post(
        "/api/v1/users", json={"username": "weak1", "password": "short"}, headers=headers
    )
    assert resp.status_code == 422


async def test_self_demotion_forbidden(client: AsyncClient, user_factory: Any, login: Any) -> None:
    admin = await user_factory(Role.ADMIN)
    tokens = await login(admin.username)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.patch(
        f"/api/v1/users/{admin.id}", json={"role": "OPERATOR"}, headers=headers
    )
    assert resp.status_code == 403
    other = await user_factory(Role.NETWORK_ADMIN)
    resp = await client.patch(f"/api/v1/users/{other.id}", json={"role": "ADMIN"}, headers=headers)
    assert resp.status_code == 200


async def test_role_change_revokes_tokens(
    client: AsyncClient, user_factory: Any, login: Any, auth_headers: Any
) -> None:
    target = await user_factory(Role.OPERATOR)
    target_tokens = await login(target.username)
    admin_headers = await auth_headers(Role.ADMIN)
    resp = await client.patch(
        f"/api/v1/users/{target.id}", json={"role": "READ_ONLY"}, headers=admin_headers
    )
    assert resp.status_code == 200
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {target_tokens['access_token']}"}
    )
    assert resp.status_code == 401


async def test_audit_log_is_append_only(app: Any, client: AsyncClient) -> None:
    await client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
    async with app.state.db.session() as s:
        if s.bind.dialect.name != "postgresql":
            pytest.skip("append-only trigger is PostgreSQL-specific")
        with pytest.raises(DBAPIError, match="append-only"):
            await s.execute(update(AuditLog).values(success=True))
        await s.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            await s.execute(text("DELETE FROM audit_logs"))
