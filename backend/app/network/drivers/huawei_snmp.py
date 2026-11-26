"""SNMP adapter for the DeviceDriver port (monitoring/identification only, no CLI/config).

pysnmp is asyncio-native; this adapter runs its coroutines with ``asyncio.run`` because the
port is synchronous and always executed in a worker thread or Celery task.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from app.core.exceptions import DeviceError, UnsupportedCapability, ValidationFailed
from app.domain.devices import DeviceFamily, DeviceProtocol
from app.network.drivers.base import DriverBase
from app.network.ports import CommandOutput, ConfigResult, ConnectionParams, DeviceFacts
from app.network.snmp.client import SnmpAuth, SnmpClient, SnmpTarget
from app.network.snmp.collector import (
    EntitySnapshot,
    InterfaceSample,
    SystemSnapshot,
    collect_entity,
    collect_interfaces,
    collect_system,
)

T = TypeVar("T")


def snmp_auth_from_params(params: ConnectionParams) -> SnmpAuth:
    secret = params.snmp.get("secret")
    kind = params.snmp.get("kind")
    if secret is None or kind not in {"snmp_v2c", "snmp_v3"}:
        raise ValidationFailed("device has no SNMP credential")
    if kind == "snmp_v2c":
        return SnmpAuth("v2c", community=secret.community)
    return SnmpAuth(
        "v3",
        username=secret.username,
        auth_protocol=secret.auth_protocol,
        auth_key=secret.auth_key,
        priv_protocol=secret.priv_protocol,
        priv_key=secret.priv_key,
    )


_CE = re.compile(r"CloudEngine|\bCE\d{3,5}|Version 8\.\d+", re.I)
_AR = re.compile(r"\bAR\d{3,5}", re.I)
_S = re.compile(r"\bS\d{4,5}|Quidway", re.I)


def family_from_descr(descr: str | None) -> DeviceFamily:
    """Infer the platform family from sysDescr (VRP-VERSION-DEPENDENT wording)."""
    text = descr or ""
    for pattern, family in ((_CE, DeviceFamily.CE), (_AR, DeviceFamily.AR), (_S, DeviceFamily.S)):
        if pattern.search(text):
            return family
    return DeviceFamily.UNKNOWN


class HuaweiSnmpDriver(DriverBase):
    protocol = DeviceProtocol.SNMP

    def __init__(self, params: ConnectionParams, *, client: SnmpClient | None = None) -> None:
        self.params = params
        self._client = client
        self._alive = False

    def _run(self, collect: Callable[[SnmpClient], Coroutine[Any, Any, T]]) -> T:
        """Run one collection in a fresh event loop; the pysnmp engine is bound to that loop,
        so it is closed inside the same loop before ``asyncio.run`` returns."""
        client = self._c()

        async def runner() -> T:
            try:
                return await collect(client)
            finally:
                client.close()

        return asyncio.run(runner())

    def connect(self) -> None:
        if self._client is None:
            target = SnmpTarget(
                self.params.host, self.params.port, timeout=min(self.params.timeout, 5)
            )
            self._client = SnmpClient(target, snmp_auth_from_params(self.params))

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None

    def _c(self) -> SnmpClient:
        self.connect()
        if self._client is None:
            raise RuntimeError("invariant violated: self._client missing")
        return self._client

    def run_show(self, command: str) -> CommandOutput:
        raise UnsupportedCapability("SNMP transport cannot run CLI commands")

    def push_config(
        self, commands: list[str], *, save: bool = False, trial_seconds: int | None = None
    ) -> ConfigResult:
        raise UnsupportedCapability("configuration over SNMP is not supported (read-only)")

    def system(self) -> SystemSnapshot:
        return self._run(collect_system)

    def interfaces(self) -> list[InterfaceSample]:
        return self._run(collect_interfaces)

    def entity(self) -> EntitySnapshot:
        return self._run(collect_entity)

    def capabilities(self) -> DeviceFacts:
        snap = self.system()
        return DeviceFacts(
            family=family_from_descr(snap.sys_descr),
            vrp_version=None,
            software_version=None,
            model=None,
            hostname=snap.sys_name,
            uptime_seconds=snap.uptime_seconds,
            capabilities={"snmp": True, "huawei_enterprise": snap.is_huawei, "transport": "snmp"},
        )

    def health(self) -> bool:
        try:
            self._alive = self.system().uptime_seconds is not None
        except DeviceError:
            self._alive = False
        return self._alive


class CiscoSnmpDriver(HuaweiSnmpDriver):
    """Same IF-MIB polling; Cisco CPU/memory/environment MIBs for entity sensors."""

    def entity(self) -> EntitySnapshot:
        from app.network.snmp.collector import collect_entity_cisco

        return self._run(collect_entity_cisco)

    def capabilities(self) -> DeviceFacts:
        snap = self.system()
        return DeviceFacts(
            family=DeviceFamily.UNKNOWN,
            vrp_version=None,
            software_version=None,
            model=None,
            hostname=snap.sys_name,
            uptime_seconds=snap.uptime_seconds,
            capabilities={"snmp": True, "transport": "snmp"},
        )


class TplinkSnmpDriver(CiscoSnmpDriver):
    """IF-MIB polling plus the TP-Link system monitor MIB."""

    def entity(self) -> EntitySnapshot:
        from app.network.snmp.collector import collect_entity_tplink

        return self._run(collect_entity_tplink)
