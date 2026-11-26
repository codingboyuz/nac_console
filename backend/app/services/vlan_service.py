"""VLAN and port-VLAN features: live reads, idempotent plans, guarded apply."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.exceptions import UnsupportedCapability, ValidationFailed
from app.domain.devices import DeviceProtocol
from app.domain.identity import Principal, RequestContext
from app.domain.interfaces import normalize_interface
from app.models.device import Device
from app.network.builders import cli_l2
from app.network.builders.netconf_vlan import vlan_create_xml, vlan_delete_xml
from app.network.builders.plan import ConfigPlan
from app.network.parsers import VlanInfo
from app.network.parsers.interface_config import InterfaceConfig
from app.network.platforms import Q, adapter_of
from app.services.change_service import ChangeOptions, ChangeResult, ChangeService
from app.services.collection_service import CollectionService
from app.services.device_service import DeviceService


@dataclass(frozen=True, slots=True)
class PortVlanRequest:
    interface: str
    mode: cli_l2.PortMode
    access_vlan: int | None = None
    trunk_vlans: list[int] | None = None
    pvid: int | None = None
    tagged: list[int] | None = None
    untagged: list[int] | None = None


class L2Base:
    def __init__(
        self, devices: DeviceService, collection: CollectionService, changes: ChangeService
    ):
        self._devices = devices
        self._collection = collection
        self._changes = changes

    async def _device(self, principal: Principal, device_id: uuid.UUID) -> Device:
        return await self._devices.get(principal, device_id)

    async def interface_config(
        self, principal: Principal, device: Device, interface: str
    ) -> InterfaceConfig:
        if device.protocol in {DeviceProtocol.NETCONF, DeviceProtocol.SNMP}:
            raise UnsupportedCapability("port configuration needs a CLI (SSH/Telnet) device")
        name = normalize_interface(interface)
        out = await self._collection.query(device, Q.INTERFACE_CONFIG, principal, name=name)
        return adapter_of(device).parsers.parse_interface_config(out)

    async def _apply(
        self,
        principal: Principal,
        ctx: RequestContext,
        device: Device,
        plan: ConfigPlan,
        options: ChangeOptions,
    ) -> ChangeResult:
        return await self._changes.apply(principal, ctx, device.id, plan, options)


class VlanService(L2Base):
    async def list_live(self, principal: Principal, device_id: uuid.UUID) -> list[VlanInfo]:
        device = await self._device(principal, device_id)
        await self._collection.collect_vlans(device, principal)
        return adapter_of(device).parsers.parse_vlans(
            await self._collection.query(device, Q.VLANS, principal)
        )

    async def _existing(self, principal: Principal, device: Device) -> dict[int, str | None]:
        if device.protocol is DeviceProtocol.NETCONF:
            return {}
        out = await self._collection.query(device, Q.VLANS, principal)
        return {v.vlan_id: v.name for v in adapter_of(device).parsers.parse_vlans(out)}

    async def plan_create(
        self, principal: Principal, device: Device, vlans: dict[int, str | None]
    ) -> ConfigPlan:
        if device.protocol is DeviceProtocol.NETCONF:
            self._require_netconf_vlan(device)
            return ConfigPlan(
                "vlan.create", (), netconf_xml=vlan_create_xml(vlans), summary="netconf"
            )
        return adapter_of(device).l2.create_vlans(vlans, await self._existing(principal, device))

    async def plan_delete(
        self, principal: Principal, device: Device, vlans: list[int]
    ) -> ConfigPlan:
        if device.mgmt_vlan is not None and device.mgmt_vlan in vlans:
            # Lockout guard also catches this; fail early with a clear message.
            raise ValidationFailed(f"VLAN {device.mgmt_vlan} is the management VLAN of this device")
        if device.protocol is DeviceProtocol.NETCONF:
            self._require_netconf_vlan(device)
            return ConfigPlan(
                "vlan.delete", (), netconf_xml=vlan_delete_xml(vlans), summary="netconf"
            )
        return adapter_of(device).l2.delete_vlans(vlans, await self._existing(principal, device))

    @staticmethod
    def _require_netconf_vlan(device: Device) -> None:
        modules = ((device.capabilities or {}).get("netconf") or {}).get("feature_modules") or {}
        if not modules.get("vlan"):
            raise UnsupportedCapability(
                "device does not advertise a supported VLAN YANG module (run test-connection)"
            )

    async def create(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        vlans: dict[int, str | None],
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        return await self._apply(
            principal, ctx, device, await self.plan_create(principal, device, vlans), options
        )

    async def delete(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        vlans: list[int],
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        return await self._apply(
            principal, ctx, device, await self.plan_delete(principal, device, vlans), options
        )

    async def rename(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        vlan_id: int,
        name: str,
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        return await self._apply(
            principal, ctx, device, adapter_of(device).l2.rename_vlan(vlan_id, name), options
        )

    async def set_port_vlan(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        req: PortVlanRequest,
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        current = await self.interface_config(principal, device, req.interface)
        plan = adapter_of(device).l2.set_port_mode(
            req.interface,
            req.mode,
            current,
            access_vlan=req.access_vlan,
            trunk_vlans=req.trunk_vlans,
            pvid=req.pvid,
            tagged=req.tagged,
            untagged=req.untagged,
        )
        return await self._apply(principal, ctx, device, plan, options)

    async def trunk_vlans(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        interface: str,
        add: list[int],
        remove: list[int],
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        current = await self.interface_config(principal, device, interface)
        return await self._apply(
            principal,
            ctx,
            device,
            adapter_of(device).l2.trunk_vlans(interface, add, remove, current),
            options,
        )
