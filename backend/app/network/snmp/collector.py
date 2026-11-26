"""SNMP collectors: system, IF-MIB interfaces (64-bit preferred) and Huawei entity sensors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import DeviceError
from app.domain.interfaces import InvalidInterfaceName, normalize_interface
from app.domain.metrics import InterfaceCounters
from app.network.snmp import oids
from app.network.snmp.client import SnmpClient


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    sys_name: str | None
    sys_descr: str | None
    sys_object_id: str | None
    uptime_seconds: int | None

    @property
    def is_huawei(self) -> bool:
        return bool(
            self.sys_object_id and self.sys_object_id.startswith(oids.HUAWEI_ENTERPRISE_PREFIX)
        )


@dataclass(frozen=True, slots=True)
class InterfaceSample:
    if_index: int
    name: str
    alias: str | None
    admin_up: bool
    oper_up: bool
    speed_mbps: int | None
    counters: InterfaceCounters


@dataclass(frozen=True, slots=True)
class EntitySnapshot:
    cpu_percent: float | None
    memory_percent: float | None
    temperatures_c: list[float] = field(default_factory=list)
    fans_ok: dict[str, bool] = field(default_factory=dict)
    power_ok: dict[str, bool] = field(default_factory=dict)


def _by_suffix(rows: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Map instance suffix ('6' or multi-part '0.1') -> value for one table column."""
    return {
        oid[len(prefix) + 1 :]: value for oid, value in rows.items() if oid.startswith(prefix + ".")
    }


def _by_index(rows: dict[str, Any], prefix: str) -> dict[int, Any]:
    return {int(k): v for k, v in _by_suffix(rows, prefix).items() if k.isdigit()}


async def collect_system(client: SnmpClient) -> SystemSnapshot:
    values = await client.get(oids.SYS_NAME, oids.SYS_DESCR, oids.SYS_OBJECT_ID, oids.SYS_UPTIME)
    ticks = values.get(oids.SYS_UPTIME)
    return SystemSnapshot(
        sys_name=values.get(oids.SYS_NAME),
        sys_descr=values.get(oids.SYS_DESCR),
        sys_object_id=values.get(oids.SYS_OBJECT_ID),
        uptime_seconds=int(ticks) // 100 if isinstance(ticks, int) else None,
    )


async def _column(client: SnmpClient, oid: str) -> dict[int, Any]:
    return _by_index(await _raw(client, oid), oid)


async def _raw(client: SnmpClient, oid: str) -> dict[str, Any]:
    try:
        return await client.walk(oid)
    except DeviceError:
        return {}


async def collect_interfaces(client: SnmpClient) -> list[InterfaceSample]:
    cols = {key: await _column(client, oid) for key, oid in oids.IF_TABLE_COLUMNS.items()}
    samples: list[InterfaceSample] = []
    for index in sorted(cols["descr"] or cols["name"]):
        raw_name = str(cols["name"].get(index) or cols["descr"].get(index) or f"ifIndex{index}")
        try:
            name = normalize_interface(raw_name)
        except InvalidInterfaceName:
            name = raw_name
        hc = index in cols["hc_in_octets"]
        counters = InterfaceCounters(
            in_octets=cols["hc_in_octets" if hc else "in_octets"].get(index),
            out_octets=cols["hc_out_octets" if hc else "out_octets"].get(index),
            in_errors=cols["in_errors"].get(index),
            out_errors=cols["out_errors"].get(index),
            in_discards=cols["in_discards"].get(index),
            out_discards=cols["out_discards"].get(index),
            counter_bits=64 if hc else 32,
        )
        samples.append(
            InterfaceSample(
                if_index=index,
                name=name,
                alias=cols["alias"].get(index) or None,
                admin_up=cols["admin"].get(index) == 1,
                oper_up=cols["oper"].get(index) == 1,
                speed_mbps=cols["high_speed"].get(index),
                counters=counters,
            )
        )
    return samples


async def collect_entity(client: SnmpClient) -> EntitySnapshot:
    """Huawei entity sensors; values of 0 for non-CPU boards are ignored."""
    cpu = [v for v in (await _column(client, oids.HW_ENTITY_CPU_USAGE)).values() if v]
    mem = [v for v in (await _column(client, oids.HW_ENTITY_MEM_USAGE)).values() if v]
    temps = [float(v) for v in (await _column(client, oids.HW_ENTITY_TEMPERATURE)).values() if v]
    fans = {
        i: v == 1
        for i, v in _by_suffix(await _raw(client, oids.HW_FAN_STATE), oids.HW_FAN_STATE).items()
    }
    power = {
        i: v == 1
        for i, v in _by_suffix(await _raw(client, oids.HW_POWER_STATE), oids.HW_POWER_STATE).items()
    }
    return EntitySnapshot(
        cpu_percent=float(max(cpu)) if cpu else None,
        memory_percent=float(max(mem)) if mem else None,
        temperatures_c=temps,
        fans_ok=fans,
        power_ok=power,
    )


async def collect_entity_cisco(client: SnmpClient) -> EntitySnapshot:
    """Cisco CPU / memory pool / environment sensors (IOS, IOS-XE, NX-OS, SMB)."""
    cpu = list((await _column(client, oids.CISCO_CPU_1MIN)).values())
    used = await _column(client, oids.CISCO_MEM_USED)
    free = await _column(client, oids.CISCO_MEM_FREE)
    pool = 1 if 1 in used and 1 in free else next(iter(used), None)
    memory = None
    if pool is not None and pool in free and (used[pool] + free[pool]):
        memory = round(used[pool] / (used[pool] + free[pool]) * 100, 1)
    temps = [float(v) for v in (await _column(client, oids.CISCO_TEMPERATURE)).values() if v]
    fans = {
        i: v == 1
        for i, v in _by_suffix(
            await _raw(client, oids.CISCO_FAN_STATE), oids.CISCO_FAN_STATE
        ).items()
        if v != 5  # notPresent
    }
    power = {
        i: v == 1
        for i, v in _by_suffix(
            await _raw(client, oids.CISCO_POWER_STATE), oids.CISCO_POWER_STATE
        ).items()
        if v != 5
    }
    return EntitySnapshot(
        cpu_percent=float(max(cpu)) if cpu else None,
        memory_percent=memory,
        temperatures_c=temps,
        fans_ok=fans,
        power_ok=power,
    )


async def collect_entity_tplink(client: SnmpClient) -> EntitySnapshot:
    """TP-Link per-unit CPU / memory utilisation (no fan/power/temperature MIB used)."""
    cpu = list((await _column(client, oids.TPLINK_CPU_1MIN)).values())
    mem = list((await _column(client, oids.TPLINK_MEMORY)).values())
    return EntitySnapshot(
        cpu_percent=float(max(cpu)) if cpu else None,
        memory_percent=float(max(mem)) if mem else None,
        temperatures_c=[],
        fans_ok={},
        power_ok={},
    )
