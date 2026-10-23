"""Collect L2/L3 tables from devices (display commands) and persist current state + history."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from app.core.database import Database
from app.domain.identity import Principal
from app.models.device import Device
from app.models.network_state import ArpEntry, DhcpBinding, Interface, MacEntry, MacMove, Vlan
from app.network.parsers import (
    parse_arp,
    parse_dhcp_snooping_bindings,
    parse_interface_brief,
    parse_interface_detail,
    parse_mac_table,
    parse_vlans,
)
from app.network.ports import DeviceDriver
from app.repositories.uow import UnitOfWork
from app.services.connection_service import DeviceConnector


def is_edge_port(device: Device, interface: str | None) -> bool:
    """Access-side port: not an uplink, LAG, management or routed (Vlanif) interface."""
    if not interface:
        return False
    if interface in (device.uplink_interfaces or []):
        return False
    return not interface.startswith(("Eth-Trunk", "MEth", "Vlanif", "LoopBack", "NULL"))


class CollectionService:
    def __init__(self, db: Database, connector: DeviceConnector) -> None:
        self._db = db
        self._connector = connector

    async def run[R](
        self, device: Device, operation: Callable[[DeviceDriver], R], principal: Principal | None
    ) -> R:
        """Run an arbitrary read-only driver operation (probes) through the connector."""
        return await self._connector.run(device, operation, principal)

    async def show(self, device: Device, command: str, principal: Principal | None = None) -> str:
        def run(driver: DeviceDriver) -> str:
            return driver.run_show(command).output

        return await self._connector.run(device, run, principal)

    async def _shows(
        self, device: Device, commands: list[str], principal: Principal | None
    ) -> list[str]:
        def run(driver: DeviceDriver) -> list[str]:
            return [driver.run_show(c).output for c in commands]

        return await self._connector.run(device, run, principal)

    # --- VLANs ---------------------------------------------------------------------------
    async def collect_vlans(self, device: Device, principal: Principal | None = None) -> list[Vlan]:
        parsed = parse_vlans(await self.show(device, "display vlan", principal))
        rows = [
            Vlan(
                device_id=device.id,
                vlan_id=v.vlan_id,
                name=v.name,
                status=v.status,
                tagged_ports=",".join(v.tagged_ports)[:4000] or None,
                untagged_ports=",".join(v.untagged_ports)[:4000] or None,
            )
            for v in parsed
        ]
        await self._replace(Vlan, device.id, rows)
        return rows

    # --- interfaces ----------------------------------------------------------------------
    async def collect_interfaces(
        self, device: Device, principal: Principal | None = None
    ) -> list[Interface]:
        brief_out, detail_out = await self._shows(
            device, ["display interface brief", "display interface"], principal
        )
        details = {d.name: d for d in parse_interface_detail(detail_out)}
        uplinks = set(device.uplink_interfaces or [])
        rows: list[Interface] = []
        for b in parse_interface_brief(brief_out):
            d = details.get(b.name)
            rows.append(
                Interface(
                    device_id=device.id,
                    name=b.name,
                    description=d.description if d else None,
                    admin_up=not b.admin_down,
                    oper_up=b.phy == "up",
                    speed_mbps=d.speed_mbps if d else None,
                    duplex=d.duplex if d else None,
                    link_type=d.link_type if d else None,
                    pvid=d.pvid if d else None,
                    mac=d.mac if d else None,
                    crc_errors=d.crc_errors if d else None,
                    input_errors=d.input_errors if d else b.in_errors,
                    output_errors=d.output_errors if d else b.out_errors,
                    is_uplink=b.name in uplinks,
                )
            )
        await self._replace(Interface, device.id, rows)
        return rows

    # --- MAC -----------------------------------------------------------------------------
    async def collect_mac(self, device: Device, principal: Principal | None = None) -> int:
        parsed = parse_mac_table(await self.show(device, "display mac-address", principal))
        now = datetime.now(UTC)
        async with UnitOfWork(self._db) as uow:
            s = uow.session
            current = {
                (r.mac, r.vlan): r
                for r in (await s.scalars(select(MacEntry).where(MacEntry.device_id == device.id)))
            }
            seen: set[tuple[str, int | None]] = set()
            for entry in parsed:
                key = (entry.mac, entry.vlan)
                seen.add(key)
                row = current.get(key)
                if row is None:
                    s.add(
                        MacEntry(
                            device_id=device.id,
                            mac=entry.mac,
                            vlan=entry.vlan,
                            interface=entry.interface,
                            entry_type=entry.entry_type,
                            is_edge=is_edge_port(device, entry.interface),
                            first_seen=now,
                            last_seen=now,
                        )
                    )
                    continue
                if row.interface != entry.interface:
                    s.add(
                        MacMove(
                            device_id=device.id,
                            mac=entry.mac,
                            vlan=entry.vlan,
                            from_interface=row.interface,
                            to_interface=entry.interface,
                            timestamp=now,
                        )
                    )
                    row.moves += 1
                    row.interface = entry.interface
                    row.is_edge = is_edge_port(device, entry.interface)
                row.entry_type = entry.entry_type
                row.last_seen = now
            for key, row in current.items():
                if key not in seen:
                    await s.delete(row)
            await uow.commit()
        return len(parsed)

    # --- ARP / DHCP ----------------------------------------------------------------------
    async def collect_arp(self, device: Device, principal: Principal | None = None) -> int:
        parsed = parse_arp(await self.show(device, "display arp", principal))
        rows = [
            ArpEntry(
                device_id=device.id,
                ip=e.ip,
                mac=e.mac,
                vlan=e.vlan,
                interface=e.interface,
                entry_type=e.entry_type,
                vpn=e.vpn or "",
            )
            for e in parsed
        ]
        await self._replace(ArpEntry, device.id, rows)
        return len(rows)

    async def collect_dhcp(self, device: Device, principal: Principal | None = None) -> int:
        out = await self.show(device, "display dhcp snooping user-bind all", principal)
        rows = [
            DhcpBinding(
                device_id=device.id,
                ip=b.ip,
                mac=b.mac,
                vlan=b.vlan,
                interface=b.interface,
                lease_expire=b.lease_expire,
            )
            for b in parse_dhcp_snooping_bindings(out)
        ]
        await self._replace(DhcpBinding, device.id, rows)
        return len(rows)

    async def _replace(self, model: type[Any], device_id: uuid.UUID, rows: list[Any]) -> None:
        """Replace the device's snapshot rows atomically (dedupe by natural key first)."""
        async with UnitOfWork(self._db) as uow:
            await uow.session.execute(delete(model).where(model.device_id == device_id))
            uow.session.add_all(_dedupe(rows, _natural_key(model)))
            await uow.commit()


def _natural_key(model: type[Any]) -> Callable[[Any], tuple[Any, ...]]:
    if model is Vlan:
        return lambda r: (r.vlan_id,)
    if model is Interface:
        return lambda r: (r.name,)
    if model is ArpEntry:
        return lambda r: (r.ip, r.vpn)
    return lambda r: (r.ip,)


def _dedupe[R](rows: list[R], key: Callable[[R], tuple[Any, ...]]) -> list[R]:
    seen: dict[tuple[Any, ...], R] = {}
    for row in rows:
        seen.setdefault(key(row), row)
    return list(seen.values())
