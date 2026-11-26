"""Parsers for the JSON the Omada driver returns (controller objects, see drivers/omada_api).

Field names follow the Omada Open API as far as they could be checked; every lookup accepts a
few spellings and missing fields become None, so a controller version with different names
degrades to empty values rather than wrong ones.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.domain.devices import DeviceFamily
from app.network.parsers import (
    ArpEntry,
    CpuUsage,
    DhcpBindingEntry,
    InterfaceBrief,
    InterfaceDetail,
    MacEntry,
    MemoryUsage,
    RouteEntry,
    StpGlobal,
    StpPortBrief,
    VersionInfo,
    VlanInfo,
)
from app.network.parsers.acl import ParsedAcl
from app.network.parsers.common import safe_mac
from app.network.parsers.interface_config import InterfaceConfig
from app.network.parsers.routing_protocols import BgpPeer, OspfNeighbor
from app.network.platforms.tplink_omada.dsl import port_name

# Omada linkSpeed enum (0 auto/unknown) - unverified for every model; strings like "1000M" too.
_SPEED_CODES = {1: 10, 2: 100, 3: 1000, 4: 2500, 5: 10000, 6: 5000, 7: 25000, 8: 40000}


def _load(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _get(obj: Any, *keys: str) -> Any:
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if obj.get(key) is not None:
            return obj[key]
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _items(data: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get(key, data.get("data"))
    return [d for d in data or [] if isinstance(d, dict)] if isinstance(data, list) else []


def network_vlans(networks: Any) -> dict[str, int]:
    """networkId -> VLAN id."""
    out: dict[str, int] = {}
    for net in _items(networks, "networks"):
        ident, vlan = _get(net, "id", "networkId"), _int(_get(net, "vlan", "vlanId", "vid"))
        if ident is not None and vlan is not None:
            out[str(ident)] = vlan
    return out


def _uptime(value: Any) -> int | None:
    if (seconds := _int(value)) is not None:
        return seconds
    if not isinstance(value, str):
        return None
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    parts = re.findall(r"(\d+)\s*(d|h|m|s)", value.lower())
    return sum(int(n) * units[u] for n, u in parts) if parts else None


# --- device ----------------------------------------------------------------------------------
def parse_version(text: str | None) -> VersionInfo:
    sw = _load(text) or {}
    firmware = _get(sw, "firmwareVersion", "version")
    uptime = _get(sw, "uptimeLong", "uptime")
    return VersionInfo(
        family=DeviceFamily.UNKNOWN,
        vrp_version=str(firmware).split()[0] if firmware else None,
        software_version=str(firmware) if firmware else None,
        model=_get(sw, "model", "showModel"),
        uptime=str(_get(sw, "uptime")) if _get(sw, "uptime") is not None else None,
        uptime_seconds=_uptime(uptime),
    )


def parse_cpu_usage(text: str | None) -> CpuUsage:
    cpu = _int(_get(_load(text), "cpuUtil", "cpu"))
    value = float(cpu) if cpu is not None else None
    return CpuUsage(value, None, value, None)


def parse_memory_usage(text: str | None) -> MemoryUsage:
    mem = _int(_get(_load(text), "memUtil", "mem"))
    return MemoryUsage(None, None, None, float(mem) if mem is not None else None)


# --- VLANs (LAN networks) --------------------------------------------------------------------
def parse_vlans(text: str | None) -> list[VlanInfo]:
    out: list[VlanInfo] = []
    for net in _items(_load(text), "networks"):
        vlan = _int(_get(net, "vlan", "vlanId", "vid"))
        if vlan is None:
            continue
        out.append(
            VlanInfo(
                vlan_id=vlan,
                name=str(_get(net, "name") or ""),
                status="enable",
                vlan_type="common",
                tagged_ports=(),
                untagged_ports=(),
            )
        )
    return sorted(out, key=lambda v: v.vlan_id)


# --- ports -----------------------------------------------------------------------------------
def _ports(text: str | None) -> list[dict[str, Any]]:
    data = _load(text)
    if isinstance(data, dict) and "port" in data and isinstance(data["port"], dict):
        return [data["port"]]
    return [p for p in _items(data, "ports") if _int(_get(p, "port")) is not None]


def _up(port: dict[str, Any]) -> bool:
    status = _get(port, "linkStatus", "status")
    return status in (1, True, "up", "UP", "connected")


def _speed(port: dict[str, Any]) -> int | None:
    raw = _get(port, "linkSpeed", "speed")
    if isinstance(raw, str) and (match := re.match(r"(\d+)\s*([MG])", raw)):
        return int(match.group(1)) * (1000 if match.group(2) == "G" else 1)
    code = _int(raw)
    return _SPEED_CODES.get(code) if code is not None else None


def parse_interface_brief(text: str | None) -> list[InterfaceBrief]:
    out: list[InterfaceBrief] = []
    for port in _ports(text):
        up = _up(port)
        out.append(
            InterfaceBrief(
                name=port_name(int(port["port"])),
                phy="up" if up else "down",
                protocol="up" if up else "down",
                in_util=None,
                out_util=None,
                in_errors=None,
                out_errors=None,
                admin_down=bool(_get(port, "disable")),
            )
        )
    return out


def parse_interface_detail(text: str | None) -> list[InterfaceDetail]:
    out: list[InterfaceDetail] = []
    for port in _ports(text):
        duplex = _get(port, "duplex")
        duplex_text = {1: "half", 2: "full"}.get(duplex) if isinstance(duplex, int) else duplex
        out.append(
            InterfaceDetail(
                name=port_name(int(port["port"])),
                admin_up=not bool(_get(port, "disable")),
                oper_up=_up(port),
                description=_get(port, "name"),
                speed_mbps=_speed(port),
                duplex=str(duplex_text).lower() if duplex_text else None,
                mtu=None,
                mac=None,
                pvid=None,
                link_type=None,
                input_packets=_int(_get(port, "rxPkts", "rxPackets")),
                input_bytes=_int(_get(port, "rx", "rxBytes")),
                output_packets=_int(_get(port, "txPkts", "txPackets")),
                output_bytes=_int(_get(port, "tx", "txBytes")),
                crc_errors=None,
                input_errors=_int(_get(port, "rxErrPkts", "rxErrors")),
                output_errors=_int(_get(port, "txErrPkts", "txErrors")),
                input_discards=_int(_get(port, "rxDropPkts")),
                output_discards=_int(_get(port, "txDropPkts")),
                input_rate_bps=None,
                output_rate_bps=None,
            )
        )
    return out


def port_vlans(port: dict[str, Any], networks: Any) -> tuple[int | None, set[int]]:
    """(native/untagged VLAN, tagged VLANs) of one port, resolved through the LAN networks.

    The driver adds 'nativeVlan' / 'taggedVlans' when it already resolved them."""
    if "nativeVlan" in port or "taggedVlans" in port:
        return _int(port.get("nativeVlan")), {int(v) for v in port.get("taggedVlans") or []}
    ids = network_vlans(networks)
    native_id = _get(port, "nativeNetworkId")
    native = ids.get(str(native_id)) if native_id is not None else None
    tagged = {ids[str(i)] for i in _get(port, "tagNetworkIds", "tagIds") or [] if str(i) in ids}
    return native, tagged


def parse_interface_config(text: str | None) -> InterfaceConfig:
    data = _load(text) or {}
    port = data.get("port") if isinstance(data, dict) else None
    cfg = InterfaceConfig()
    if not isinstance(port, dict) or _int(port.get("port")) is None:
        return cfg
    number = int(port["port"])
    native, tagged = port_vlans(port, data.get("networks"))
    cfg.name = port_name(number)
    cfg.description = _get(port, "name")
    cfg.shutdown = bool(_get(port, "disable"))
    cfg.hybrid_pvid = native
    cfg.hybrid_untagged = {native} if native is not None else set()
    cfg.hybrid_tagged = set(tagged)
    if tagged:
        cfg.link_type, cfg.trunk_allowed, cfg.trunk_pvid = "trunk", set(tagged), native
    elif native is not None:
        cfg.link_type, cfg.default_vlan = "access", native
    cfg.lines = [
        f"port {number} {'disable' if cfg.shutdown else 'enable'}",
        *([f"port {number} name {cfg.description}"] if cfg.description else []),
        *([f"port {number} native {native}"] if native is not None else []),
        f"port {number} tagged {','.join(map(str, sorted(tagged))) or 'none'}",
    ]
    return cfg


# --- clients: MAC / ARP ----------------------------------------------------------------------
def _clients(text: str | None) -> list[dict[str, Any]]:
    return _items(_load(text), "clients")


def parse_mac_table(text: str | None) -> list[MacEntry]:
    out: list[MacEntry] = []
    for client in _clients(text):
        mac = safe_mac(str(_get(client, "mac") or ""))
        vlan = _int(_get(client, "vid", "vlanId", "vlan"))
        port = _int(_get(client, "port"))
        if mac and vlan is not None:
            out.append(MacEntry(mac, vlan, port_name(port) if port else None, "dynamic", None))
    return out


def parse_arp(text: str | None) -> list[ArpEntry]:
    out: list[ArpEntry] = []
    for client in _clients(text):
        mac = safe_mac(str(_get(client, "mac") or ""))
        ip = _get(client, "ip")
        if not mac or not ip:
            continue
        vlan = _int(_get(client, "vid", "vlanId", "vlan"))
        out.append(
            ArpEntry(
                ip=str(ip),
                mac=mac,
                expire_minutes=None,
                entry_type="dynamic",
                vlan=vlan,
                interface=f"Vlan{vlan}" if vlan is not None else None,
                vpn=None,
            )
        )
    return out


# --- not exposed through this platform: empty results ----------------------------------------
def parse_mac_aging(text: str | None) -> int | None:
    return None


def parse_dhcp_snooping_bindings(text: str | None) -> list[DhcpBindingEntry]:
    return []


def parse_routing_table(text: str | None) -> list[RouteEntry]:
    return []


def parse_static_route_lines(text: str | None) -> set[str]:
    return set()


def parse_ospf_peers(text: str | None) -> list[OspfNeighbor]:
    return []


def parse_bgp_peers(text: str | None) -> list[BgpPeer]:
    return []


def parse_stp_global(text: str | None) -> StpGlobal:
    return StpGlobal(None, None, None, None, None, None, None, None)


def parse_stp_brief(text: str | None) -> list[StpPortBrief]:
    return []


def parse_acls(text: str | None) -> list[ParsedAcl]:
    return []


def management_acls(running_config: str | None) -> frozenset[str]:
    return frozenset()
