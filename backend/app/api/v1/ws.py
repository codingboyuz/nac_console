"""WebSockets: live job status, monitoring metrics and alerts via Redis pub/sub fan-out.

Authentication: a single-use ticket from POST /api/v1/auth/ws-ticket, either as ``?ticket=``
(short-lived, burned on use) or as the first message ``{"type": "auth", "ticket": "..."}``.
Close codes: 4401 unauthenticated, 4403 forbidden, 4404 unknown job.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.events import ALERTS_CHANNEL, JOB_CHANNEL, MONITORING_CHANNEL
from app.core.exceptions import AppError, NotFound
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.services.container import ServiceContainer

router = APIRouter(prefix="/ws", tags=["ws"])
AUTH_TIMEOUT = 5.0


async def _authenticate(ws: WebSocket, ticket: str | None) -> Principal | None:
    services: ServiceContainer = ws.app.state.services
    await ws.accept()
    try:
        if not ticket:
            first = json.loads(await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT))
            ticket = first.get("ticket") if first.get("type") == "auth" else None
        if not ticket:
            raise ValueError("no ticket")
        return await services.auth.redeem_ws_ticket(str(ticket))
    except (AppError, ValueError, TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
        await ws.close(code=4401)
        return None


async def _pump(ws: WebSocket, channel: str, *, stop_on_final: bool = False) -> None:
    services: ServiceContainer = ws.app.state.services
    async with services.events.listen(channel) as pubsub:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None:
                continue
            data: dict[str, Any] = json.loads(message["data"])
            await ws.send_json(data)
            if stop_on_final and data.get("type") == "status" and data.get("progress") == 100:
                await ws.close()
                return


async def _guard(ws: WebSocket, principal: Principal, permission: Permission) -> bool:
    if not principal.can(permission):
        await ws.close(code=4403)
        return False
    return True


@router.websocket("/jobs/{job_id}")
async def job_events(
    ws: WebSocket, job_id: uuid.UUID, ticket: Annotated[str | None, Query()] = None
) -> None:
    principal = await _authenticate(ws, ticket)
    if principal is None or not await _guard(ws, principal, Permission.JOBS_READ):
        return
    services: ServiceContainer = ws.app.state.services
    try:
        job, _ = await services.jobs.get(principal, job_id)
    except NotFound:
        await ws.close(code=4404)
        return
    await ws.send_json(
        {
            "type": "snapshot",
            "job_id": str(job.id),
            "status": job.status.value,
            "progress": job.progress,
        }
    )
    if job.status.finished:
        await ws.close()
        return
    try:
        await _pump(ws, JOB_CHANNEL.format(job.id), stop_on_final=True)
    except WebSocketDisconnect:
        return


@router.websocket("/monitoring")
async def monitoring_events(ws: WebSocket, ticket: Annotated[str | None, Query()] = None) -> None:
    principal = await _authenticate(ws, ticket)
    if principal is None or not await _guard(ws, principal, Permission.MONITORING_READ):
        return
    try:
        await _pump(ws, MONITORING_CHANNEL)
    except WebSocketDisconnect:
        return


@router.websocket("/alerts")
async def alert_events(ws: WebSocket, ticket: Annotated[str | None, Query()] = None) -> None:
    principal = await _authenticate(ws, ticket)
    if principal is None or not await _guard(ws, principal, Permission.ALERTS_READ):
        return
    try:
        await _pump(ws, ALERTS_CHANNEL)
    except WebSocketDisconnect:
        return
