"""Cisco NX-OS 'show' parsers (Nexus 9000, NX-OS 9.3 / 10.x). Fixtures: n9k_nxos93.

Identical formats are reused from the IOS parsers (VLAN brief, interface status, interface
config, OSPF/BGP neighbours, spanning tree); NX-OS-specific ones are implemented here.
"""

from __future__ import annotations

import re

from app.domain.devices import DeviceFamily
from app.network.parsers import (
    ArpEntry,
    CpuUsage,
    DhcpBindingEntry,
    InterfaceDetail,
    MacEntry,
    MemoryUsage,
    RouteEntry,
    VersionInfo,
)
from app.network.parsers.acl import ParsedAcl
from app.network.parsers.common import safe_interface, safe_mac, to_int
from app.network.parsers.interface_config import InterfaceConfig
from app.network.platforms.cisco_ios import parsers as ios
from app.network.platforms.cisco_ios.builders_acl import canonical_entry
from app.network.platforms.cisco_ios.parsers import clean_lines

# Same output format as IOS / IOS-XE.
parse_vlans = ios.parse_vlans
parse_interface_brief = ios.parse_interface_brief
parse_ospf_peers = ios.parse_ospf_peers
parse_bgp_peers = ios.parse_bgp_peers
parse_stp_global = ios.parse_stp_global
parse_stp_brief = ios.parse_stp_brief
management_acls = ios.management_acls

_NO_NEXTHOP = "0.0.0.0"  # noqa: S104 - connected route next hop, not a bind address


def parse_version(text: str | None) -> VersionInfo:
    body = "\n".join(clean_lines(text))
    version = re.search(r"(?:NXOS|system):\s+version\s+(\S+)", body)
    model = re.search(r"cisco Nexus\S*\s+(\S+)\s+[Cc]hassis", body)
    uptime = re.search(r"Kernel uptime is (.+)$", body, re.M)
    seconds = None
    if uptime:
        units = {"day": 86400, "hour": 3600, "minute": 60, "second": 1}
        seconds = sum(
            int(n) * units[u]
            for n, u in re.findall(r"(\d+) (day|hour|minute|second)", uptime.group(1))
        )
    return VersionInfo(
        family=DeviceFamily.UNKNOWN,
        vrp_version=version.group(1) if version else None,
        software_version=f"NX-OS {version.group(1)}" if version else None,
        model=model.group(1) if model else None,
        uptime=uptime.group(1).strip() if uptime else None,
        uptime_seconds=seconds,
    )


# --- interfaces ------------------------------------------------------------------------------
_HEAD = re.compile(r"^(\S+) is (up|down)(?: \((.+)\))?")
_SPEED = re.compile(r"(full|half|auto)-duplex, (\d+) ([GM])b/s")


def parse_interface_detail(text: str | None) -> list[InterfaceDetail]:
    blocks: list[list[str]] = []
    for line in clean_lines(text):
        if _HEAD.match(line) and not line.startswith(" "):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return [_detail(b) for b in blocks]


def _num(pattern: str, body: str) -> int | None:
    match = re.search(pattern, body)
    return int(match.group(1)) if match else None


def _detail(block: list[str]) -> InterfaceDetail:
    head = _HEAD.match(block[0])
    assert head is not None  # noqa: S101 - guaranteed by caller
    body = "\n".join(block)
    admin = re.search(r"admin state is (up|down)", body)
    desc = re.search(r"^\s*Description: (.+)$", body, re.M)
    mac = re.search(r"address: ([0-9a-f.]+)", body)
    mode = re.search(r"Port mode is (\w+)", body)
    speed = duplex = None
    if match := _SPEED.search(body):
        speed = int(match.group(2)) * (1000 if match.group(3) == "G" else 1)
        duplex = match.group(1) if match.group(1) in {"full", "half"} else None
    rates = dict(re.findall(r"(?:seconds|minute) (input|output) rate (\d+) bits/sec", body))
    return InterfaceDetail(
        name=safe_interface(head.group(1)) or head.group(1),
        admin_up=(admin.group(1) == "up") if admin else "Administratively" not in body,
        oper_up=head.group(2) == "up",
        description=desc.group(1).strip() if desc else None,
        speed_mbps=speed,
        duplex=duplex,
        mtu=_num(r"MTU (\d+) bytes", body),
        mac=safe_mac(mac.group(1)) if mac else None,
        pvid=None,
        link_type=mode.group(1) if mode and mode.group(1) in {"access", "trunk"} else None,
        input_packets=_num(r"(\d+) input packets", body),
        input_bytes=_bytes(body, "input"),
        output_packets=_num(r"(\d+) output packets", body),
        output_bytes=_bytes(body, "output"),
        crc_errors=_num(r"(\d+) CRC", body),
        input_errors=_num(r"(\d+) input error", body),
        output_errors=_num(r"(\d+) output error", body),
        input_discards=_num(r"(\d+) input discard", body),
        output_discards=_num(r"(\d+) output discard", body),
        input_rate_bps=to_int(rates.get("input")),
        output_rate_bps=to_int(rates.get("output")),
    )


def _bytes(body: str, direction: str) -> int | None:
    """'123566 input packets  98765432 bytes' (RX/TX blocks, also split over lines)."""
    match = re.search(rf"\d+ {direction} packets\s+(\d+) bytes", body)
    if match:
        return int(match.group(1))
    section = "Rx" if direction == "input" else "Tx"
    block = re.search(rf"^\s*{section}\n(.*?)(?=^\s*(?:Rx|Tx)\s*$|\Z)", body, re.M | re.S | re.I)
    found = re.search(r"(\d+) bytes", block.group(1)) if block else None
    return int(found.group(1)) if found else None


def parse_interface_config(text: str | None) -> InterfaceConfig:
    cfg = ios.parse_interface_config(text)
    if any(line == "spanning-tree port type edge" for line in cfg.lines):
        cfg.stp_edged = True
    if "switchport mode trunk" not in cfg.lines and cfg.default_vlan is not None:
        cfg.link_type = cfg.link_type or "access"  # NX-OS default switchport mode is access
    return cfg


# --- MAC / ARP / DHCP ------------------------------------------------------------------------
_MAC = re.compile(
    r"^[*+GOC ]\s+(\d+|-)\s+([0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\s+(\w+)"  # flag vlan mac type
    r"\s+\S+\s+\S+\s+\S+\s+(.+?)\s*$"  # age secure ntfy ports
)


def parse_mac_table(text: str | None) -> list[MacEntry]:
    out: list[MacEntry] = []
    for line in clean_lines(text):
        match = _MAC.match(line)
        if not match or match.group(1) == "-":
            continue
        port = match.group(4)
        entry_type = match.group(3).lower()
        if port.lower() == "drop":
            entry_type, interface = "blackhole", None
        elif port.startswith(("sup-", "vPC Peer-Link")):
            continue
        else:
            interface = safe_interface(port.split()[0])
        mac = safe_mac(match.group(2))
        if mac:
            out.append(MacEntry(mac, int(match.group(1)), interface, entry_type, None))
    return out


def parse_mac_aging(text: str | None) -> int | None:
    match = re.search(r"(?:Aging Time|aging time)\D*(\d+)", "\n".join(clean_lines(text)))
    return int(match.group(1)) if match else None


_ARP = re.compile(
    r"^(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+([0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\s+(\S+)"
)


def parse_arp(text: str | None) -> list[ArpEntry]:
    out: list[ArpEntry] = []
    for line in clean_lines(text):
        match = _ARP.match(line)
        if not match:
            continue
        mac = safe_mac(match.group(3))
        iface = safe_interface(match.group(4))
        if not mac:
            continue
        vlan = (
            int(iface[4:]) if iface and iface.startswith("Vlan") and iface[4:].isdigit() else None
        )
        out.append(
            ArpEntry(
                ip=match.group(1),
                mac=mac,
                expire_minutes=None,
                entry_type="static" if match.group(2) == "-" else "dynamic",
                vlan=vlan,
                interface=iface,
                vpn=None,
            )
        )
    return out


_BINDING = re.compile(r"^([0-9a-fA-F:.]{14,17})\s+(\S+)\s+(\d+|infinite)\s+\S+\s+(\d+)\s+(\S+)")


def parse_dhcp_snooping_bindings(text: str | None) -> list[DhcpBindingEntry]:
    out: list[DhcpBindingEntry] = []
    for line in clean_lines(text):
        if (match := _BINDING.match(line.strip())) and (mac := safe_mac(match.group(1))):
            out.append(
                DhcpBindingEntry(
                    ip=match.group(2),
                    mac=mac,
                    vlan=int(match.group(4)),
                    interface=safe_interface(match.group(5)),
                    lease_expire=f"{match.group(3)}s",
                    vsi=None,
                )
            )
    return out


# --- routing ---------------------------------------------------------------------------------
_PREFIX = re.compile(r"^(\d+\.\d+\.\d+\.\d+/\d+), ubest/mbest")
_VIA = re.compile(r"^\s+\*?via ([\d.]+)(?:%\S+)?, (?:(\S+), )?\[(\d+)/(\d+)\], [^,]+, ([\w-]+)")
_PROTOCOL = {
    "static": "Static",
    "direct": "Direct",
    "local": "Direct",
    "ospf": "OSPF",
    "bgp": "BGP",
    "rip": "RIP",
    "eigrp": "EIGRP",
    "isis": "ISIS",
    "hsrp": "Direct",
}


def parse_routing_table(text: str | None) -> list[RouteEntry]:
    out: list[RouteEntry] = []
    prefix: str | None = None
    for line in clean_lines(text):
        if match := _PREFIX.match(line):
            prefix = match.group(1)
            continue
        if prefix and (via := _VIA.match(line)):
            proto_raw = via.group(5).split("-")[0].lower()
            protocol = _PROTOCOL.get(proto_raw, proto_raw)
            iface = safe_interface(via.group(2)) if via.group(2) else None
            nexthop = _NO_NEXTHOP if protocol == "Direct" else via.group(1)
            out.append(
                RouteEntry(
                    prefix,
                    protocol,
                    int(via.group(3)),
                    int(via.group(4)),
                    "D" + ("*" if prefix == "0.0.0.0/0" else ""),
                    nexthop,
                    iface,
                )
            )
    return out


def parse_static_route_lines(text: str | None) -> set[str]:
    return {" ".join(line.split()) for line in clean_lines(text) if line.startswith("ip route ")}


# --- CPU / memory ----------------------------------------------------------------------------
def parse_cpu_usage(text: str | None) -> CpuUsage:
    match = re.search(
        r"CPU states\s*:\s*([\d.]+)% user,\s*([\d.]+)% kernel", "\n".join(clean_lines(text))
    )
    if not match:
        return CpuUsage(None, None, None, None)
    busy = round(float(match.group(1)) + float(match.group(2)), 2)
    return CpuUsage(busy, busy, None, None)


def parse_memory_usage(text: str | None) -> MemoryUsage:
    match = re.search(
        r"Memory usage:\s*(\d+)K total,\s*(\d+)K used,\s*(\d+)K free", "\n".join(clean_lines(text))
    )
    if not match:
        return MemoryUsage(None, None, None, None)
    total, used, free = (int(g) * 1024 for g in match.groups())
    return MemoryUsage(total, used, free, round(used / total * 100, 1) if total else None)


# --- ACLs ------------------------------------------------------------------------------------
_ACL_HEAD = re.compile(r"^(IP|IPv6) access list (\S+)$")
_ENTRY = re.compile(r"^\s+(\d+) (permit|deny|remark)\b(.*)$")


def parse_acls(text: str | None) -> list[ParsedAcl]:
    acls: list[ParsedAcl] = []
    current: ParsedAcl | None = None
    for line in clean_lines(text):
        if match := _ACL_HEAD.match(line.strip()):
            current = ParsedAcl(kind="advanced", number=None, name=match.group(2))
            acls.append(current)
            continue
        if current is None or not (entry := _ENTRY.match(line)):
            continue
        seq = int(entry.group(1))
        body = f"{entry.group(2)}{entry.group(3)}"
        if hits := re.search(r"\[match=(\d+)\]", body):
            current.matches[seq] = int(hits.group(1))
            body = body.replace(hits.group(0), "")
        current.rules[seq] = canonical_entry(f"{seq} {body}")
    return acls
