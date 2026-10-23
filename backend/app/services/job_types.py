"""Registry of job types: parameter schema, permission, queue and per-device handler.

Handlers run inside the job runner (Celery worker or inline) with the submitting user's
principal, so RBAC, classification, lockout guard and audit apply exactly as for API calls.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.rbac import Permission
from app.domain.identity import Principal, RequestContext
from app.domain.jobs import JobQueue
from app.domain.vlans import parse_vlan_list
from app.models.device import Device
from app.services.change_service import ChangeOptions, ChangeResult

if TYPE_CHECKING:
    from app.services.container import ServiceContainer


@dataclass(frozen=True, slots=True)
class JobContext:
    services: ServiceContainer
    principal: Principal
    ctx: RequestContext
    params: dict[str, Any]
    dry_run: bool
    confirmed: bool
    reason: str | None

    def change_options(self) -> ChangeOptions:
        return ChangeOptions(
            dry_run=self.dry_run, preconfirmed=self.confirmed, reason=self.reason, save=True
        )


Handler = Callable[[JobContext, Device], Awaitable[dict[str, Any]]]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoParams(_Params):
    pass


class VlanItemParam(_Params):
    id: int = Field(ge=2, le=4094)
    name: str | None = Field(default=None, max_length=31, pattern=r"^[A-Za-z0-9_.\- ]*$")


class VlanCreateParams(_Params):
    vlans: list[VlanItemParam] = Field(min_length=1, max_length=4093)


class VlanDeleteParams(_Params):
    vlans: str


class AdminStateParams(_Params):
    interface: str
    enabled: bool


class DescriptionParams(_Params):
    interface: str
    description: str | None = Field(default=None, max_length=242)


class BackupParams(_Params):
    trigger: Literal["manual", "scheduled"] = "manual"
    include_saved: bool = False


class PollParams(_Params):
    scope: Literal["cpu", "interfaces", "all"] = "all"


class NacProvisionParams(_Params):
    radius_server_id: str
    interfaces: list[str] = Field(default_factory=list, max_length=512)
    mode: Literal["dot1x", "mab", "both"] = "both"
    guest_vlan: int | None = Field(default=None, ge=1, le=4094)
    quarantine_vlan: int | None = Field(default=None, ge=1, le=4094)


class ShowParams(_Params):
    command: str = Field(min_length=1, max_length=512)


@dataclass(frozen=True, slots=True)
class JobType:
    name: str
    permission: Permission
    queue: JobQueue
    params: type[_Params]
    handler: Handler
    changes_config: bool = False


def _change(result: ChangeResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "category": result.category.value,
        "reason": result.reason,
        "lines": result.lines,
        "lockout": [f.reason for f in result.lockout_findings],
    }


async def _vlan_create(jc: JobContext, device: Device) -> dict[str, Any]:
    params = VlanCreateParams.model_validate(jc.params)
    wanted = {v.id: v.name for v in params.vlans}
    result = await jc.services.vlans.create(
        jc.principal, jc.ctx, device.id, wanted, jc.change_options()
    )
    return _change(result)


async def _vlan_delete(jc: JobContext, device: Device) -> dict[str, Any]:
    vlans = parse_vlan_list(VlanDeleteParams.model_validate(jc.params).vlans, allow_reserved=False)
    result = await jc.services.vlans.delete(
        jc.principal, jc.ctx, device.id, vlans, jc.change_options()
    )
    return _change(result)


async def _admin_state(jc: JobContext, device: Device) -> dict[str, Any]:
    p = AdminStateParams.model_validate(jc.params)
    result = await jc.services.interfaces.set_admin_state(
        jc.principal, jc.ctx, device.id, p.interface, p.enabled, jc.change_options()
    )
    return _change(result)


async def _description(jc: JobContext, device: Device) -> dict[str, Any]:
    p = DescriptionParams.model_validate(jc.params)
    result = await jc.services.interfaces.set_description(
        jc.principal, jc.ctx, device.id, p.interface, p.description, jc.change_options()
    )
    return _change(result)


async def _collect_mac(jc: JobContext, device: Device) -> dict[str, Any]:
    return {"entries": await jc.services.collection.collect_mac(device, jc.principal)}


async def _collect_arp(jc: JobContext, device: Device) -> dict[str, Any]:
    return {
        "arp": await jc.services.collection.collect_arp(device, jc.principal),
        "dhcp_bindings": await jc.services.collection.collect_dhcp(device, jc.principal),
    }


async def _collect_interfaces(jc: JobContext, device: Device) -> dict[str, Any]:
    return {
        "interfaces": len(await jc.services.collection.collect_interfaces(device, jc.principal))
    }


async def _collect_vlans(jc: JobContext, device: Device) -> dict[str, Any]:
    return {"vlans": len(await jc.services.collection.collect_vlans(device, jc.principal))}


async def _test_connection(jc: JobContext, device: Device) -> dict[str, Any]:
    facts = await jc.services.commands.test_connection(jc.principal, jc.ctx, device.id)
    return {"family": facts.family.value, "model": facts.model, "vrp_version": facts.vrp_version}


async def _backup(jc: JobContext, device: Device) -> dict[str, Any]:
    from app.models.backup import BackupSource, BackupTrigger

    p = BackupParams.model_validate(jc.params)
    trigger = BackupTrigger(p.trigger)
    row, created = await jc.services.backups.backup(jc.principal, device, trigger)
    if row.status != "SUCCESS":
        raise RuntimeError(row.error or "backup failed")
    out: dict[str, Any] = {"backup_id": str(row.id), "created": created, "sha256": row.sha256}
    if p.include_saved:
        saved, _ = await jc.services.backups.backup(
            jc.principal, device, trigger, source=BackupSource.SAVED
        )
        out["saved_backup_id"] = str(saved.id)
    return out


async def _poll(jc: JobContext, device: Device) -> dict[str, Any]:
    scope = PollParams.model_validate(jc.params).scope
    samples = await jc.services.monitoring.poll(jc.principal, device, scope)
    return {"samples": len(samples)}


async def _nac_provision(jc: JobContext, device: Device) -> dict[str, Any]:
    import uuid

    from app.services.nac_provisioning import ProvisionRequest

    p = NacProvisionParams.model_validate(jc.params)
    req = ProvisionRequest(
        uuid.UUID(p.radius_server_id), tuple(p.interfaces), p.mode, p.guest_vlan, p.quarantine_vlan
    )
    result = await jc.services.nac_provisioning.deploy(
        jc.principal, jc.ctx, device, req, jc.change_options()
    )
    return _change(result)


async def _show(jc: JobContext, device: Device) -> dict[str, Any]:
    from app.services.command_service import CommandRequest

    p = ShowParams.model_validate(jc.params)
    result = await jc.services.commands.execute(
        jc.principal, jc.ctx, device.id, CommandRequest(p.command)
    )
    if not result.classification.is_safe:
        raise PermissionError("bulk show jobs accept SAFE display commands only")
    return {"output": (result.output or "")[:20000]}


C, P, D = JobQueue.CONFIG, JobQueue.POLLING, JobQueue.DEFAULT
REGISTRY: dict[str, JobType] = {
    t.name: t
    for t in (
        JobType(
            "vlan.create", Permission.DEVICE_CONFIGURE, C, VlanCreateParams, _vlan_create, True
        ),
        JobType(
            "vlan.delete", Permission.DEVICE_CONFIGURE, C, VlanDeleteParams, _vlan_delete, True
        ),
        JobType(
            "interface.admin_state",
            Permission.DEVICE_CONFIGURE,
            C,
            AdminStateParams,
            _admin_state,
            True,
        ),
        JobType(
            "interface.description",
            Permission.DEVICE_CONFIGURE,
            C,
            DescriptionParams,
            _description,
            True,
        ),
        JobType("collect.mac", Permission.DEVICE_SHOW, P, NoParams, _collect_mac),
        JobType("collect.arp", Permission.DEVICE_SHOW, P, NoParams, _collect_arp),
        JobType("collect.interfaces", Permission.DEVICE_SHOW, P, NoParams, _collect_interfaces),
        JobType("collect.vlans", Permission.DEVICE_SHOW, P, NoParams, _collect_vlans),
        JobType("device.test_connection", Permission.DEVICE_TEST, D, NoParams, _test_connection),
        JobType("device.show", Permission.DEVICE_SHOW, D, ShowParams, _show),
        JobType("backup.run", Permission.BACKUPS_CREATE, D, BackupParams, _backup),
        JobType("poll.metrics", Permission.DEVICE_SHOW, P, PollParams, _poll),
        JobType(
            "nac.provision",
            Permission.NAC_POLICY_WRITE,
            C,
            NacProvisionParams,
            _nac_provision,
            True,
        ),
    )
}


def register(job_type: JobType) -> None:
    """Later phases (backups, monitoring, discovery, NAC) add their job types here."""
    REGISTRY[job_type.name] = job_type
