"""Device, group, credential and host-key schemas. Secrets are write-only."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, IPvAnyAddress, SecretStr, field_validator, model_validator

from app.domain.devices import (
    TELNET_SECURITY_WARNING,
    CredentialKind,
    DeviceFamily,
    DeviceProtocol,
    DeviceStatus,
    HostKeyStatus,
)
from app.domain.interfaces import InvalidInterfaceName, normalize_interface
from app.domain.platforms import Platform
from app.schemas.common import ApiModel

_HOSTNAME = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
_TAG = r"^[A-Za-z0-9_.:-]{1,64}$"


def _norm_iface(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return normalize_interface(value)
    except InvalidInterfaceName as exc:
        raise ValueError(str(exc)) from exc


class _DeviceFields(ApiModel):
    hostname: str = Field(pattern=_HOSTNAME, examples=["SW-CORE-01"])
    mgmt_ip: IPvAnyAddress = Field(examples=["192.0.2.10"])
    model: str | None = Field(default=None, max_length=64)
    platform: Platform = Field(
        default=Platform.HUAWEI_VRP,
        description="Vendor platform: selects the driver, CLI dialect and builders.",
    )
    family: DeviceFamily = DeviceFamily.UNKNOWN
    vrp_version: str | None = Field(default=None, max_length=64)
    protocol: DeviceProtocol = DeviceProtocol.SSH
    port: int | None = Field(default=None, ge=1, le=65535)
    credential_id: uuid.UUID | None = None
    snmp_credential_id: uuid.UUID | None = None
    snmp_port: int = Field(default=161, ge=1, le=65535)
    requires_super: bool = False
    timeout: int = Field(default=30, ge=5, le=600)
    group_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=32)
    mgmt_interface: str | None = Field(default=None, examples=["MEth0/0/1"])
    mgmt_vlan: int | None = Field(default=None, ge=1, le=4094)
    uplink_interfaces: list[str] = Field(default_factory=list, max_length=64)
    site: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=255)

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        for tag in value:
            if not re.match(_TAG, tag):
                raise ValueError(f"invalid tag: {tag!r}")
        return sorted(set(value))

    @field_validator("mgmt_interface")
    @classmethod
    def _mgmt(cls, value: str | None) -> str | None:
        return _norm_iface(value)

    @field_validator("uplink_interfaces")
    @classmethod
    def _uplinks(cls, value: list[str]) -> list[str]:
        return [n for n in (_norm_iface(v) for v in value) if n]


class DeviceCreate(_DeviceFields):
    allow_insecure_protocol: bool = Field(
        default=False, description="Must be true to register a Telnet device (ADMIN/NETWORK_ADMIN)."
    )

    @model_validator(mode="after")
    def _insecure(self) -> DeviceCreate:
        if self.protocol.insecure and not self.allow_insecure_protocol:
            raise ValueError("telnet requires allow_insecure_protocol=true")
        return self


class DeviceUpdate(ApiModel):
    """PATCH: every field optional."""

    hostname: str | None = Field(default=None, pattern=_HOSTNAME)
    mgmt_ip: IPvAnyAddress | None = None
    model: str | None = Field(default=None, max_length=64)
    platform: Platform | None = None
    family: DeviceFamily | None = None
    vrp_version: str | None = Field(default=None, max_length=64)
    protocol: DeviceProtocol | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    credential_id: uuid.UUID | None = None
    snmp_credential_id: uuid.UUID | None = None
    snmp_port: int | None = Field(default=None, ge=1, le=65535)
    requires_super: bool | None = None
    timeout: int | None = Field(default=None, ge=5, le=600)
    group_id: uuid.UUID | None = None
    tags: list[str] | None = None
    mgmt_interface: str | None = None
    mgmt_vlan: int | None = Field(default=None, ge=1, le=4094)
    uplink_interfaces: list[str] | None = None
    site: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    status: Literal["MAINTENANCE", "UNKNOWN"] | None = None
    allow_insecure_protocol: bool | None = None

    @field_validator("mgmt_interface")
    @classmethod
    def _mgmt(cls, value: str | None) -> str | None:
        return _norm_iface(value)

    @field_validator("uplink_interfaces")
    @classmethod
    def _uplinks(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else [n for n in (_norm_iface(v) for v in value) if n]


class DeviceOut(ApiModel):
    id: uuid.UUID
    hostname: str
    mgmt_ip: str
    vendor: str
    platform: Platform
    model: str | None
    family: DeviceFamily
    vrp_version: str | None
    protocol: DeviceProtocol
    port: int
    credential_id: uuid.UUID | None
    snmp_credential_id: uuid.UUID | None
    snmp_port: int
    requires_super: bool
    timeout: int
    status: DeviceStatus
    last_seen: datetime | None
    group_id: uuid.UUID | None
    tags: list[str]
    capabilities: dict[str, Any]
    mgmt_interface: str | None
    mgmt_vlan: int | None
    uplink_interfaces: list[str]
    site: str | None
    description: str | None
    created_at: datetime
    updated_at: datetime
    security_warning: str | None = None

    @model_validator(mode="after")
    def _warning(self) -> DeviceOut:
        if self.protocol.insecure:
            self.security_warning = TELNET_SECURITY_WARNING
        return self


class DeviceGroupIn(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    site: str | None = Field(default=None, max_length=128)


class DeviceGroupOut(DeviceGroupIn):
    id: uuid.UUID
    created_at: datetime


SnmpAuthProto = Literal["SHA", "SHA224", "SHA256", "SHA384", "SHA512", "MD5"]
SnmpPrivProto = Literal["AES128", "AES192", "AES256", "DES"]


class CredentialCreate(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    kind: CredentialKind
    username: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    password: SecretStr | None = Field(default=None, max_length=256)
    private_key: SecretStr | None = Field(default=None, max_length=16384)
    super_password: SecretStr | None = Field(default=None, max_length=256)
    community: SecretStr | None = Field(default=None, max_length=128)
    auth_protocol: SnmpAuthProto | None = None
    auth_key: SecretStr | None = Field(default=None, min_length=8, max_length=128)
    priv_protocol: SnmpPrivProto | None = None
    priv_key: SecretStr | None = Field(default=None, min_length=8, max_length=128)
    # omada_api: username = client_id, password = client_secret
    controller_url: str | None = Field(
        default=None, max_length=255, pattern=r"^https?://[A-Za-z0-9.\-\[\]:]+(:\d+)?/?$"
    )
    omadac_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9]+$")
    omada_site: str | None = Field(default=None, max_length=128)
    verify_tls: bool = True

    @model_validator(mode="after")
    def _shape(self) -> CredentialCreate:
        if self.kind is CredentialKind.OMADA_API:
            if not (self.username and self.password and self.controller_url and self.omadac_id):
                raise ValueError(
                    "omada_api credentials need username (client_id), password (client_secret), "
                    "controller_url and omadac_id"
                )
            return self
        if self.kind is CredentialKind.CLI:
            if not self.username or not (self.password or self.private_key):
                raise ValueError("cli credentials need username and password or private_key")
        elif self.kind is CredentialKind.SNMP_V2C:
            if not self.community:
                raise ValueError("snmp_v2c credentials need a community")
        elif not self.username or not self.auth_key or not self.auth_protocol:
            raise ValueError("snmp_v3 credentials need username, auth_protocol and auth_key")
        if self.priv_key and not self.priv_protocol:
            raise ValueError("priv_key requires priv_protocol")
        return self


class CredentialUpdate(ApiModel):
    description: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=128)
    password: SecretStr | None = Field(default=None, max_length=256)
    private_key: SecretStr | None = Field(default=None, max_length=16384)
    super_password: SecretStr | None = Field(default=None, max_length=256)
    community: SecretStr | None = Field(default=None, max_length=128)
    auth_key: SecretStr | None = Field(default=None, min_length=8, max_length=128)
    priv_key: SecretStr | None = Field(default=None, min_length=8, max_length=128)


class CredentialOut(ApiModel):
    """No secret values, only which secret fields are set."""

    id: uuid.UUID
    name: str
    kind: CredentialKind
    username: str | None
    description: str | None
    key_id: str
    secret_fields: list[str]
    auth_protocol: str | None = None
    priv_protocol: str | None = None
    controller_url: str | None = None
    omadac_id: str | None = None
    omada_site: str | None = None
    created_at: datetime
    updated_at: datetime


class HostKeyOut(ApiModel):
    id: uuid.UUID
    device_id: uuid.UUID
    key_type: str
    fingerprint: str
    status: HostKeyStatus
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    created_at: datetime
