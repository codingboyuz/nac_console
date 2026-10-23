"""Request/response schemas for guarded changes and L2 features."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr, field_validator

from app.domain.commands.categories import CommandCategory
from app.domain.interfaces import InvalidInterfaceName, normalize_interface
from app.domain.mac import InvalidMac, normalize_mac
from app.domain.vlans import InvalidVlan, parse_vlan_list
from app.schemas.common import ApiModel
from app.services.change_service import ChangeOptions, ChangeResult


class ChangeRequest(ApiModel):
    """Common guard fields on every configuration endpoint."""

    dry_run: bool = Field(default=False, description="Preview + classification; token if DANGEROUS")
    confirm: bool = False
    confirmation_token: SecretStr | None = None
    reason: str | None = Field(default=None, max_length=500)
    save: bool = Field(default=True, description="Persist (S/AR 'save'); CE persists on commit")
    override_lockout: bool = Field(default=False, description="ADMIN only, needs rollback_minutes")
    rollback_minutes: int | None = Field(default=None, ge=1, le=30)

    def options(self) -> ChangeOptions:
        return ChangeOptions(
            dry_run=self.dry_run,
            confirm=self.confirm,
            confirmation_token=(
                self.confirmation_token.get_secret_value() if self.confirmation_token else None
            ),
            reason=self.reason,
            save=self.save,
            override_lockout=self.override_lockout,
            rollback_minutes=self.rollback_minutes,
        )


class LockoutOut(ApiModel):
    line: str
    reason: str


class ChangeResponse(ApiModel):
    device_id: uuid.UUID
    operation: str
    status: str
    category: CommandCategory
    reason: str
    lines: list[str]
    lockout_findings: list[LockoutOut]
    confirmation_token: str | None = None

    @classmethod
    def of(cls, result: ChangeResult) -> ChangeResponse:
        return cls(
            device_id=result.device_id,
            operation=result.operation,
            status=result.status,
            category=result.category,
            reason=result.reason,
            lines=result.lines,
            lockout_findings=[
                LockoutOut(line=f.line, reason=f.reason) for f in result.lockout_findings
            ],
            confirmation_token=result.confirmation_token,
        )


def _iface(value: str) -> str:
    try:
        return normalize_interface(value)
    except InvalidInterfaceName as exc:
        raise ValueError(str(exc)) from exc


def _vlans(value: str) -> list[int]:
    try:
        return parse_vlan_list(value, allow_reserved=False)
    except InvalidVlan as exc:
        raise ValueError(str(exc)) from exc


class VlanItem(ApiModel):
    id: int = Field(ge=2, le=4094)
    name: str | None = Field(default=None, max_length=31, pattern=r"^[A-Za-z0-9_.\- ]*$")


class VlanCreateRequest(ChangeRequest):
    vlans: list[VlanItem] = Field(min_length=1, max_length=4093)


class VlanDeleteRequest(ChangeRequest):
    vlans: str = Field(examples=["10,20-30"], description="VLAN list/range (VLAN 1 is reserved)")

    _v = field_validator("vlans")(lambda v: ",".join(map(str, _vlans(v))))


class VlanRenameRequest(ChangeRequest):
    name: str = Field(min_length=1, max_length=31, pattern=r"^[A-Za-z0-9_.\- ]+$")


class VlanOut(ApiModel):
    vlan_id: int
    name: str | None
    status: str | None
    vlan_type: str | None
    tagged_ports: list[str]
    untagged_ports: list[str]


class PortVlanRequest(ChangeRequest):
    mode: Literal["access", "trunk", "hybrid"]
    access_vlan: int | None = Field(default=None, ge=1, le=4094)
    trunk_vlans: str | None = None
    pvid: int | None = Field(default=None, ge=1, le=4094)
    tagged: str | None = None
    untagged: str | None = None

    @field_validator("trunk_vlans", "tagged", "untagged")
    @classmethod
    def _ranges(cls, value: str | None) -> str | None:
        return None if value is None else ",".join(map(str, parse_vlan_list(value)))


class TrunkVlansRequest(ChangeRequest):
    add: str | None = None
    remove: str | None = None

    @field_validator("add", "remove")
    @classmethod
    def _ranges(cls, value: str | None) -> str | None:
        return None if value is None else ",".join(map(str, parse_vlan_list(value)))


class AdminStateRequest(ChangeRequest):
    enabled: bool


class DescriptionRequest(ChangeRequest):
    description: str | None = Field(default=None, max_length=242)


class SpeedDuplexRequest(ChangeRequest):
    auto: bool = True
    speed: Literal[10, 100, 1000, 10000, 25000, 40000, 100000] | None = None
    duplex: Literal["full", "half"] | None = None


class InterfaceOut(ApiModel):
    name: str
    description: str | None
    admin_up: bool | None
    oper_up: bool | None
    speed_mbps: int | None
    duplex: str | None
    link_type: str | None
    pvid: int | None
    crc_errors: int | None
    input_errors: int | None
    output_errors: int | None
    is_uplink: bool


class MacRequest(ChangeRequest):
    mac: str
    vlan: int = Field(ge=1, le=4094)
    interface: str | None = None
    device_id: uuid.UUID

    @field_validator("mac")
    @classmethod
    def _mac(cls, value: str) -> str:
        try:
            return normalize_mac(value)
        except InvalidMac as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("interface")
    @classmethod
    def _interface(cls, value: str | None) -> str | None:
        return None if value is None else _iface(value)


class MacEntryOut(ApiModel):
    device_id: uuid.UUID
    mac: str
    vlan: int | None
    interface: str | None
    entry_type: str
    is_edge: bool
    first_seen: datetime
    last_seen: datetime
    moves: int


class MacLocationOut(ApiModel):
    mac: str
    device_id: uuid.UUID
    hostname: str
    interface: str | None
    vlan: int | None
    is_edge: bool
    last_seen: str


class MacBlockResponse(ChangeResponse):
    notice: str


class ArpOut(ApiModel):
    device_id: uuid.UUID
    ip: str
    mac: str
    vlan: int | None
    interface: str | None
    entry_type: str
    vpn: str


class DhcpBindingOut(ApiModel):
    device_id: uuid.UUID
    ip: str
    mac: str
    vlan: int | None
    interface: str | None
    lease_expire: str | None


class FindingOut(ApiModel):
    kind: str
    key: str
    detail: str
    severity: str


class DhcpSnoopingRequest(ChangeRequest):
    vlans: str | None = None
    trusted_interfaces: list[str] = Field(default_factory=list, max_length=64)

    _t = field_validator("trusted_interfaces")(lambda v: [_iface(i) for i in v])


class StpSettingsRequest(ChangeRequest):
    mode: Literal["stp", "rstp", "mstp"] | None = None
    priority: int | None = Field(default=None, ge=0, le=61440, multiple_of=4096)
    instance: int = Field(default=0, ge=0, le=4094)


class StpEdgeRequest(ChangeRequest):
    enabled: bool


class StpPortOut(ApiModel):
    instance: int
    interface: str
    role: str
    state: str
    protection: str


class StpStatusOut(ApiModel):
    mode: str | None
    bridge_id: str | None
    root_id: str | None
    root_path_cost: int | None
    root_port: str | None
    tc_count: int | None
    time_since_last_tc: str | None
    bpdu_protection: bool | None
    is_root: bool | None
    loop_suspected: bool
    ports: list[StpPortOut]
