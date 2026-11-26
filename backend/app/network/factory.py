"""DeviceConnectionFactory: adapter selection per protocol and bounded per-device sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from app.core.exceptions import UnsupportedCapability
from app.domain.devices import DeviceProtocol
from app.network.ports import ConnectionParams, DeviceDriver

DriverBuilder = Callable[[ConnectionParams], DeviceDriver]


def _by_platform(transport: str) -> DriverBuilder:
    """Builder that picks the platform adapter's driver for one transport."""

    def build(params: ConnectionParams) -> DeviceDriver:
        from app.network.platforms import adapter_for

        driver = adapter_for(params.platform).drivers.get(transport)
        if driver is None:
            raise UnsupportedCapability(
                f"{params.platform.label} cannot be managed over {transport}"
            )
        return driver(params)

    return build


_ssh, _telnet, _netconf, _snmp, _api = (
    _by_platform(t) for t in ("ssh", "telnet", "netconf", "snmp", "api")
)


DEFAULT_BUILDERS: dict[DeviceProtocol, DriverBuilder] = {
    DeviceProtocol.SSH: _ssh,
    DeviceProtocol.TELNET: _telnet,
    DeviceProtocol.NETCONF: _netconf,
    DeviceProtocol.SNMP: _snmp,
    DeviceProtocol.API: _api,
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
