"""Platform registry: ``adapter_for(platform)`` returns the shared adapter for a platform."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import UnsupportedCapability
from app.domain.devices import DeviceFamily
from app.domain.platforms import Platform
from app.network.platforms.base import PlatformAdapter, Q

if TYPE_CHECKING:
    from app.models.device import Device

__all__ = ["PlatformAdapter", "Q", "adapter_for", "adapter_of", "register_adapter"]

_REGISTRY: dict[Platform, PlatformAdapter] = {}
_LOADED = False


def register_adapter(adapter: PlatformAdapter) -> None:
    _REGISTRY[adapter.platform] = adapter


def _load() -> None:
    """Import vendor packages lazily (avoids import cycles; each registers its adapters)."""
    global _LOADED  # noqa: PLW0603 - one-time registry initialisation
    if _LOADED:
        return
    from app.network.platforms import (
        cisco_ios,
        cisco_nxos,
        cisco_smb,
        huawei,
        tplink_jetstream,
        tplink_omada,
    )

    register_adapter(huawei.HUAWEI_VRP)
    register_adapter(huawei.HUAWEI_VRPV8)
    register_adapter(cisco_ios.CISCO_IOS)
    register_adapter(cisco_nxos.CISCO_NXOS)
    register_adapter(cisco_smb.CISCO_SMB)
    register_adapter(tplink_jetstream.TPLINK_JETSTREAM)
    register_adapter(tplink_omada.TPLINK_OMADA)
    _LOADED = True


def adapter_for(platform: Platform) -> PlatformAdapter:
    _load()
    adapter = _REGISTRY.get(platform)
    if adapter is None:
        raise UnsupportedCapability(f"platform {platform.value} is not supported yet")
    return adapter


def adapter_of(device: Device) -> PlatformAdapter:
    """Adapter for a device row. Huawei rows also respect a CE family detected later."""
    platform = device.platform
    if platform.is_huawei and device.family is not DeviceFamily.UNKNOWN:
        platform = Platform.for_huawei_family(device.family)
    return adapter_for(platform)
