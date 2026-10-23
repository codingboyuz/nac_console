"""DeviceConnectionFactory: adapter selection per protocol and bounded per-device sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from app.core.exceptions import UnsupportedCapability
from app.domain.devices import DeviceProtocol
from app.network.ports import ConnectionParams, DeviceDriver

DriverBuilder = Callable[[ConnectionParams], DeviceDriver]


def _ssh(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_ssh import HuaweiCliDriver

    return HuaweiCliDriver(params)


def _telnet(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_telnet import HuaweiTelnetDriver

    return HuaweiTelnetDriver(params)


def _netconf(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_netconf import HuaweiNetconfDriver

    return HuaweiNetconfDriver(params)


def _snmp(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.huawei_snmp import HuaweiSnmpDriver

    return HuaweiSnmpDriver(params)


DEFAULT_BUILDERS: dict[DeviceProtocol, DriverBuilder] = {
    DeviceProtocol.SSH: _ssh,
    DeviceProtocol.TELNET: _telnet,
    DeviceProtocol.NETCONF: _netconf,
    DeviceProtocol.SNMP: _snmp,
}


class DeviceConnectionFactory:
    """Creates short-lived drivers and caps concurrent sessions per device (VTY protection).

    The cap is per process; the Redis config lock serializes configuration across processes.
    """

    def __init__(
        self,
        *,
        max_sessions_per_device: int = 2,
        builders: dict[DeviceProtocol, DriverBuilder] | None = None,
    ) -> None:
        self._max = max(1, max_sessions_per_device)
        self._builders = dict(DEFAULT_BUILDERS if builders is None else builders)
        self._slots: dict[str, asyncio.Semaphore] = {}

    def register(self, protocol: DeviceProtocol, builder: DriverBuilder) -> None:
        """Override an adapter (tests use fake drivers; deployments may add NAPALM)."""
        self._builders[protocol] = builder

    def create(self, params: ConnectionParams) -> DeviceDriver:
        builder = self._builders.get(params.protocol)
        if builder is None:
            raise UnsupportedCapability(f"no driver for protocol {params.protocol.value}")
        return builder(params)

    @asynccontextmanager
    async def slot(self, device_id: str) -> AsyncIterator[None]:
        """Bound concurrent sessions to one device so VTY lines are never exhausted."""
        semaphore = self._slots.setdefault(device_id, asyncio.Semaphore(self._max))
        async with semaphore:
            yield
