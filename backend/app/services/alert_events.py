"""Turns domain events (device_offline/online, configuration_change, unknown_mac,
authentication_failures) into alerts."""

from __future__ import annotations

import uuid

from app.core.database import Database
from app.core.events import DomainEvent
from app.models.device import Device
from app.repositories.uow import UnitOfWork
from app.services.alert_service import AlertService

EVENTS = (
    "device_offline",
    "device_online",
    "configuration_change",
    "unknown_mac",
    "authentication_failures",
)


class AlertEventHandler:
    def __init__(self, db: Database, alerts: AlertService) -> None:
        self._db = db
        self._alerts = alerts

    async def _device(self, device_id: str) -> Device | None:
        async with UnitOfWork(self._db) as uow:
            return await uow.session.get(Device, uuid.UUID(device_id))

    async def on_event(self, event: DomainEvent) -> None:
        p = event.payload
        device = await self._device(p["device_id"]) if p.get("device_id") else None
        if event.name == "device_offline" and device is not None:
            await self._alerts.open(
                "device_offline",
                device,
                f"Huawei {device.hostname} is unreachable",
                severity="critical",
            )
        elif event.name == "device_online" and device is not None:
            await self._alerts.resolve_key(f"device_offline:{device.id}:-")
        elif event.name == "configuration_change" and device is not None:
            await self._alerts.open(
                "configuration_change",
                device,
                f"Huawei {device.hostname}: configuration changed outside the platform",
                severity="info",
                key_suffix=str(p.get("backup")),
            )
        elif event.name in {"unknown_mac", "authentication_failures"}:
            await self._alerts.open(
                event.name,
                device,
                str(p.get("message", event.name)),
                severity=str(p.get("severity", "warning")),
                key_suffix=str(p.get("key", "-")),
            )
