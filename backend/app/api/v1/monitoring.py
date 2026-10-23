"""Monitoring (history, latest, on-demand poll), alerts lifecycle/rules/maintenance, discovery,
settings."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, status
from pydantic import Field

from app.api.deps import Ctx, Paging, Services, require
from app.core.exceptions import NotFound
from app.core.rbac import Permission
from app.domain.identity import Principal
from app.repositories.base import ListQuery
from app.schemas.common import ERROR_RESPONSES, ApiModel, PageResponse, page_of
from app.schemas.devices import DeviceCreate, DeviceOut

router = APIRouter(tags=["monitoring"], responses=ERROR_RESPONSES)
MonRead = Annotated[Principal, Depends(require(Permission.MONITORING_READ))]
AlertRead = Annotated[Principal, Depends(require(Permission.ALERTS_READ))]
AlertWrite = Annotated[Principal, Depends(require(Permission.ALERTS_WRITE))]


class MetricPoint(ApiModel):
    ts: datetime
    value: float | None
    interface: str | None


class AlertOut(ApiModel):
    id: uuid.UUID
    alert_type: str
    device_id: uuid.UUID | None
    interface: str | None
    state: str
    severity: str
    message: str
    value: float | None
    first_seen: datetime
    last_seen: datetime
    occurrences: int
    suppressed: bool
    acknowledged_by: uuid.UUID | None
    resolved_at: datetime | None


class DeliveryOut(ApiModel):
    channel: str
    status: str
    attempts: int
    last_error: str | None
    delivered_at: datetime | None


class AlertRuleIn(ApiModel):
    name: str = Field(pattern=r"^[a-z0-9_.-]{1,64}$")
    alert_type: str = Field(max_length=32)
    metric: str | None = Field(default=None, max_length=32)
    operator: str = Field(default="gt", pattern=r"^(gt|lt)$")
    threshold: float | None = None
    clear_threshold: float | None = None
    for_seconds: int = Field(default=0, ge=0, le=86400)
    severity: str = Field(default="warning", pattern=r"^(info|warning|critical)$")
    enabled: bool = True
    scope: dict[str, str] = Field(default_factory=dict)
    channels: list[str] = Field(default_factory=lambda: ["webhook"])
    flap_window_seconds: int = Field(default=600, ge=60)
    flap_max_transitions: int = Field(default=4, ge=2)


class AlertRuleOut(AlertRuleIn):
    id: uuid.UUID


class WindowIn(ApiModel):
    scope: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime
    ends_at: datetime
    reason: str | None = Field(default=None, max_length=500)


class DiscoveryIn(ApiModel):
    cidr: str = Field(examples=["192.0.2.0/24"])
    snmp_credential_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5)


@router.post("/devices/{device_id}/poll", summary="Poll metrics now (SNMP if configured, else CLI)")
async def poll(
    device_id: uuid.UUID,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.DEVICE_SHOW))],
    scope: Annotated[str, Query(pattern=r"^(cpu|interfaces|all)$")] = "all",
) -> list[dict[str, Any]]:
    device = await services.devices.get(actor, device_id)
    samples = await services.monitoring.poll(actor, device, scope)
    return [{"metric": s.metric, "value": s.value, "interface": s.interface} for s in samples]


@router.get("/devices/{device_id}/metrics", response_model=list[MetricPoint])
async def history(
    device_id: uuid.UUID,
    services: Services,
    actor: MonRead,
    metric: Annotated[str, Query(max_length=32)],
    since: datetime | None = None,
    until: datetime | None = None,
    interface: Annotated[str | None, Query(max_length=64)] = None,
) -> list[MetricPoint]:
    rows = await services.monitoring.history(
        actor,
        device_id,
        metric,
        since=since or datetime.now(UTC) - timedelta(hours=1),
        until=until,
        interface=interface,
    )
    return [MetricPoint.model_validate(r) for r in rows]


@router.get("/devices/{device_id}/metrics/latest")
async def latest(
    device_id: uuid.UUID, services: Services, actor: MonRead
) -> dict[str, float | None]:
    return await services.monitoring.latest(actor, device_id)


@router.get("/alerts", response_model=PageResponse[AlertOut], tags=["alerts"])
async def list_alerts(
    services: Services,
    actor: AlertRead,
    paging: Paging,
    state: Annotated[str | None, Query(pattern=r"^(FIRING|ACKNOWLEDGED|RESOLVED)$")] = None,
    severity: Annotated[str | None, Query(max_length=16)] = None,
    device_id: uuid.UUID | None = None,
) -> PageResponse[AlertOut]:
    query = ListQuery(
        paging.limit,
        paging.offset,
        paging.sort or "-last_seen",
        {"state": state, "severity": severity, "device_id": device_id},
    )
    return page_of(await services.alerts.search(query), AlertOut)


@router.get("/alerts/{alert_id}/deliveries", response_model=list[DeliveryOut], tags=["alerts"])
async def deliveries(
    alert_id: uuid.UUID, services: Services, actor: AlertRead
) -> list[DeliveryOut]:
    return [DeliveryOut.model_validate(d) for d in await services.alerts.deliveries(alert_id)]


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut, tags=["alerts"])
async def ack(
    alert_id: uuid.UUID,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.ALERTS_ACK))],
) -> AlertOut:
    return AlertOut.model_validate(await services.alerts.acknowledge(actor, alert_id))


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut, tags=["alerts"])
async def resolve(
    alert_id: uuid.UUID,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.ALERTS_ACK))],
) -> AlertOut:
    return AlertOut.model_validate(await services.alerts.resolve(actor, alert_id))


@router.get("/alert-rules", response_model=list[AlertRuleOut], tags=["alerts"])
async def rules(services: Services, actor: AlertRead) -> list[AlertRuleOut]:
    return [AlertRuleOut.model_validate(r) for r in await services.alerts.rules()]


@router.put(
    "/alert-rules",
    response_model=AlertRuleOut,
    tags=["alerts"],
    summary="Create/replace a rule by name",
)
async def upsert_rule(body: AlertRuleIn, services: Services, actor: AlertWrite) -> AlertRuleOut:
    return AlertRuleOut.model_validate(await services.alerts.upsert_rule(body.model_dump()))


@router.post("/maintenance-windows", status_code=status.HTTP_201_CREATED, tags=["alerts"])
async def add_window(body: WindowIn, services: Services, actor: AlertWrite) -> dict[str, str]:
    row = await services.alerts.add_maintenance(body.model_dump(), actor.user_id)
    return {"id": str(row.id)}


@router.post(
    "/discovery",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["discovery"],
    summary="Discover an allowed subnet (ICMP -> SNMP -> SSH banner); proposals only",
)
async def discover(
    body: DiscoveryIn,
    services: Services,
    ctx: Ctx,
    actor: Annotated[Principal, Depends(require(Permission.DISCOVERY_RUN))],
) -> dict[str, str]:
    run = await services.discovery.start(actor, ctx, body.cidr, body.snmp_credential_ids)
    return {"run_id": str(run.id), "location": f"/api/v1/discovery/{run.id}"}


@router.get("/discovery/{run_id}", tags=["discovery"])
async def discovery_run(
    run_id: uuid.UUID,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.DISCOVERY_RUN))],
) -> dict[str, Any]:
    run, rows = await services.discovery.run(run_id)
    return {
        "id": str(run.id),
        "cidr": run.cidr,
        "status": run.status,
        "hosts_scanned": run.hosts_scanned,
        "hosts_found": run.hosts_found,
        "error": run.error,
        "devices": [
            {
                "id": str(r.id),
                "ip": r.ip,
                "sys_name": r.sys_name,
                "family": r.family,
                "is_huawei": r.is_huawei,
                "ssh_banner": r.ssh_banner,
                "status": r.status,
                "adopted_device_id": str(r.adopted_device_id) if r.adopted_device_id else None,
            }
            for r in rows
        ],
    }


@router.post(
    "/discovery/devices/{discovered_id}/adopt", response_model=DeviceOut, tags=["discovery"]
)
async def adopt(
    discovered_id: uuid.UUID,
    body: DeviceCreate,
    services: Services,
    ctx: Ctx,
    actor: Annotated[Principal, Depends(require(Permission.DISCOVERY_ADOPT))],
) -> DeviceOut:
    if not actor.can(Permission.DEVICES_WRITE):
        raise NotFound("Discovered device not found")
    return DeviceOut.model_validate(await services.discovery.adopt(actor, ctx, discovered_id, body))


@router.post(
    "/discovery/devices/{discovered_id}/ignore",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["discovery"],
)
async def ignore(
    discovered_id: uuid.UUID,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.DISCOVERY_ADOPT))],
) -> None:
    await services.discovery.ignore(discovered_id)


@router.get("/settings/{key}", tags=["settings"])
async def get_setting(
    key: str,
    services: Services,
    actor: Annotated[Principal, Depends(require(Permission.SETTINGS_READ))],
) -> dict[str, Any]:
    return await services.settings_store.get(key)


@router.put("/settings/{key}", tags=["settings"])
async def put_setting(
    key: str,
    services: Services,
    ctx: Ctx,
    actor: Annotated[Principal, Depends(require(Permission.SETTINGS_WRITE))],
    value: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    return await services.settings_store.put(actor, ctx, key, value)
