"""Generate (preview) and deploy switch-side NAC configuration. Deploy = `nac.provision` job."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.config import Settings
from app.core.exceptions import ValidationFailed
from app.core.redaction import redact_text
from app.domain import config_text
from app.domain.identity import Principal, RequestContext
from app.models.device import Device
from app.network.builders import cli_nac
from app.network.builders.plan import ConfigPlan
from app.services.change_service import ChangeOptions, ChangeResult, ChangeService
from app.services.collection_service import CollectionService
from app.services.device_service import DeviceService
from app.services.radius_service import RadiusServerService


@dataclass(frozen=True, slots=True)
class ProvisionRequest:
    radius_server_id: uuid.UUID
    interfaces: tuple[str, ...]
    mode: cli_nac.Mode = "both"
    guest_vlan: int | None = None
    quarantine_vlan: int | None = None


@dataclass(frozen=True, slots=True)
class ProvisionPreview:
    lines: list[str]
    current: str
    diff: str


class NacProvisioningService:
    def __init__(
        self,
        settings: Settings,
        devices: DeviceService,
        collection: CollectionService,
        changes: ChangeService,
        radius: RadiusServerService,
    ) -> None:
        self._settings = settings
        self._devices = devices
        self._collection = collection
        self._changes = changes
        self._radius = radius

    async def plan(self, device: Device, req: ProvisionRequest) -> ConfigPlan:
        server = await self._radius.get(req.radius_server_id)
        if not self._settings.controller_ips:
            raise ValidationFailed("CONTROLLER_IPS must be configured (CoA source address)")
        spec = cli_nac.NacProvisionSpec(
            radius_host=server.host,
            shared_secret=self._radius.secret(server),
            controller_ip=self._settings.controller_ips[0],
            auth_port=server.auth_port,
            acct_port=server.acct_port,
            interfaces=req.interfaces,
            mode=req.mode,
            guest_vlan=req.guest_vlan,
            quarantine_vlan=req.quarantine_vlan or self._settings.nac_quarantine_vlan,
            retransmit=server.retries,
            timeout=server.timeout,
        )
        try:
            return cli_nac.provision(device.family, spec)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc

    async def preview(
        self, principal: Principal, device_id: uuid.UUID, req: ProvisionRequest
    ) -> ProvisionPreview:
        device = await self._devices.get(principal, device_id)
        plan = await self.plan(device, req)
        running = await self._collection.show(device, "display current-configuration", principal)
        current = redact_text(cli_nac.related_config(running))
        generated = "\n".join(redact_text(line) for line in plan.lines)
        return ProvisionPreview(
            lines=generated.split("\n"),
            current=current,
            diff=config_text.unified_diff(
                current, generated, old_name="device", new_name="generated"
            ),
        )

    async def deploy(
        self,
        principal: Principal,
        ctx: RequestContext,
        device: Device,
        req: ProvisionRequest,
        options: ChangeOptions,
    ) -> ChangeResult:
        return await self._changes.apply(
            principal, ctx, device.id, await self.plan(device, req), options
        )
