"""DeviceDriver port and the value objects exchanged with driver adapters.

Drivers are synchronous (Netmiko/Paramiko/ncclient block). They are only ever called from a
Celery worker or through ``anyio.to_thread`` by ``DeviceSession`` - never on the event loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable

from pydantic import SecretStr

from app.domain.devices import DeviceFamily, DeviceProtocol
from app.domain.platforms import Platform


@dataclass(frozen=True, slots=True)
class HostKey:
    key_type: str
    public_key_b64: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ConnectionParams:
    device_id: str
    host: str
    port: int
    protocol: DeviceProtocol
    family: DeviceFamily = DeviceFamily.UNKNOWN
    platform: Platform = Platform.HUAWEI_VRP
    username: str | None = None
    password: SecretStr | None = None
    private_key: SecretStr | None = None
    super_password: SecretStr | None = None
    requires_super: bool = False
    timeout: int = 30
    connect_retries: int = 2
    pinned_host_key: HostKey | None = None
    strict_host_key: bool = True
    # SNMP specifics
    snmp: dict[str, Any] = field(default_factory=dict)
    # Controller API specifics (Omada): controller_url, omadac_id, site, verify_tls
    api: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"ConnectionParams(device_id={self.device_id!r}, host={self.host!r}, "
            f"port={self.port}, protocol={self.protocol.value})"
        )


@dataclass(frozen=True, slots=True)
class CommandOutput:
    command: str
    output: str
    elapsed_ms: int
    security_warning: str | None = None


@dataclass(frozen=True, slots=True)
class ConfigResult:
    commands: tuple[str, ...]
    output: str
    committed: bool
    saved: bool
    elapsed_ms: int
    security_warning: str | None = None


@dataclass(frozen=True, slots=True)
class DeviceFacts:
    family: DeviceFamily
    vrp_version: str | None
    software_version: str | None
    model: str | None
    hostname: str | None = None
    uptime_seconds: int | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DeviceDriver(Protocol):
    """Port implemented by SSH, Telnet, NETCONF and SNMP adapters."""

    protocol: DeviceProtocol

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def run_show(self, command: str) -> CommandOutput: ...

    def push_config(
        self,
        commands: list[str],
        *,
        save: bool = False,
        trial_seconds: int | None = None,
    ) -> ConfigResult: ...

    def capabilities(self) -> DeviceFacts: ...

    def health(self) -> bool: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
