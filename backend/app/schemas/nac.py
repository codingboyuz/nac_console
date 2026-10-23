"""NAC / RADIUS schemas, including tolerant parsing of FreeRADIUS rlm_rest JSON bodies."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, SecretStr

from app.schemas.common import ApiModel


class RlmRestBody(BaseModel):
    """rlm_rest sends either flat attributes or {"Attr": {"type": .., "value": [..]}}."""

    model_config = ConfigDict(extra="allow")

    def attr(self, name: str) -> str | None:
        raw = (self.model_extra or {}).get(name)
        if isinstance(raw, dict):
            raw = raw.get("value")
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        return None if raw in (None, "") else str(raw)

    def attrs(self, name: str) -> list[str]:
        raw = (self.model_extra or {}).get(name)
        if isinstance(raw, dict):
            raw = raw.get("value")
        if raw is None:
            return []
        return [str(v) for v in (raw if isinstance(raw, list) else [raw])]


class EndpointOut(ApiModel):
    id: uuid.UUID
    mac: str
    ip: str | None
    hostname: str | None
    device_type: str | None
    os: str | None
    vendor: str | None
    user: str | None
    switch_id: uuid.UUID | None
    interface: str | None
    vlan: int | None
    status: str
    authorization_state: str | None
    profile_confidence: float
    source: str
    registered: bool
    endpoint_group: str | None
    description: str | None
    first_seen: datetime
    last_seen: datetime


class EndpointUpdate(ApiModel):
    registered: bool | None = None
    endpoint_group: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    description: str | None = Field(default=None, max_length=255)
    device_type: str | None = Field(default=None, max_length=64)
    status: Literal["AUTHORIZED", "UNKNOWN", "QUARANTINED", "BLOCKED", "PENDING"] | None = None


class PolicyIn(ApiModel):
    name: str
    priority: int
    condition: dict[str, Any]
    action: dict[str, Any]
    enabled: bool = True
    description: str | None = Field(default=None, max_length=255)

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "condition": self.condition,
            "action": self.action,
            "enabled": self.enabled,
        }


class PolicyOut(ApiModel):
    id: uuid.UUID
    name: str
    priority: int
    enabled: bool
    version: int
    condition: dict[str, Any]
    action: dict[str, Any]
    description: str | None
    is_default: bool
    updated_at: datetime


class PolicyVersionOut(ApiModel):
    version: int
    snapshot: dict[str, Any]
    changed_by: uuid.UUID | None
    changed_at: datetime


class SimulateIn(ApiModel):
    mac: str
    username: str | None = None
    auth_method: Literal["dot1x", "mab"] = "mab"
    user_groups: list[str] = Field(default_factory=list)
    site: str | None = None
    at: datetime | None = None
    registered: bool | None = Field(default=None, description="override; default = inventory")
    endpoint_group: str | None = None
    device_type: str | None = None
    status: str | None = None


class DecisionOut(ApiModel):
    policy_name: str
    policy_id: str | None
    result: str
    vlan: int | None
    endpoint_status: str
    reply: dict[str, str]


class SessionOut(ApiModel):
    id: uuid.UUID
    acct_session_id: str
    mac: str
    username: str | None
    nas_ip: str
    nas_port_id: str | None
    framed_ip: str | None
    vlan: int | None
    status: str
    started_at: datetime
    updated_at: datetime
    stopped_at: datetime | None
    input_octets: int
    output_octets: int


class CoaOut(ApiModel):
    session_id: str
    nas_ip: str
    kind: str
    acknowledged: bool
    detail: str


class RadiusServerIn(ApiModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    host: IPvAnyAddress
    shared_secret: SecretStr = Field(min_length=8, max_length=128)
    auth_port: int = Field(default=1812, ge=1, le=65535)
    acct_port: int = Field(default=1813, ge=1, le=65535)
    coa_port: int = Field(default=3799, ge=1, le=65535)
    timeout: int = Field(default=5, ge=1, le=60)
    retries: int = Field(default=3, ge=0, le=10)
    enabled: bool = True


class RadiusServerOut(ApiModel):
    id: uuid.UUID
    name: str
    host: str
    auth_port: int
    acct_port: int
    coa_port: int
    timeout: int
    retries: int
    enabled: bool
    key_id: str
    shared_secret_set: bool = True


class ProvisionIn(ApiModel):
    device_id: uuid.UUID
    radius_server_id: uuid.UUID
    interfaces: list[str] = Field(default_factory=list, max_length=512)
    mode: Literal["dot1x", "mab", "both"] = "both"
    guest_vlan: int | None = Field(default=None, ge=1, le=4094)
    quarantine_vlan: int | None = Field(default=None, ge=1, le=4094)


class ProvisionPreviewOut(ApiModel):
    lines: list[str]
    current: str
    diff: str


class DhcpFingerprintIn(ApiModel):
    mac: str
    parameter_request_list: str | None = Field(default=None, max_length=256)
    vendor_class: str | None = Field(default=None, max_length=128)
    hostname: str | None = Field(default=None, max_length=128)
