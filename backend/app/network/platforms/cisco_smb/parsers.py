"""Cisco Small Business (CBS350 / SG350 / SG550X, firmware 2.5+ / 3.x) 'show' parsers.

Formats follow the Sx350X 2.5.10 and CBS350 CLI guides (docs/vendors/cisco_smb_and_tplink.md).
Fixtures: tests/fixtures/cisco/cbs350_3x. SG300 (1.x, flat port names 'gi1') is not covered.
"""

from __future__ import annotations

import re

from app.domain.devices import DeviceFamily
from app.domain.interfaces import InvalidInterfaceName, normalize_interface
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
from app.network.parsers.common import safe_interface, safe_mac, to_int
from app.network.parsers.interface_config import InterfaceConfig
from app.network.parsers.routing_protocols import BgpPeer, OspfNeighbor
from app.network.platforms.cisco_ios import parsers as ios
from app.network.platforms.cisco_ios.parsers import clean_lines

_NO_NEXTHOP = "0.0.0.0"  # noqa: S104 - connected route next hop, not a bind address


def expand_ports(cell: str) -> list[str]:
    """'gi1/0/5-8, te1/0/2' -> canonical names, ranges expanded on the last number."""
    out: list[str] = []
    for token in (t.strip() for t in cell.replace(",", " ").split()):
        match = re.match(r"^([a-zA-Z-]+)(\d+/\d+/)(\d+)-(\d+)$", token)
        if match:
            prefix, slot, start, end = match.groups()
            for n in range(int(start), int(end) + 1):
                name = safe_interface(f"{prefix}{slot}{n}")
                if name:
                    out.append(name)
            continue
        name = safe_interface(token)
        if name and token:
            out.append(name)
    return out


# --- version ---------------------------------------------------------------------------------
def parse_version(text: str | None) -> VersionInfo:
    """'show version' (= show bootvar) and/or 'show system' output (the driver sends both)."""
    body = "\n".join(clean_lines(text))
    version = re.search(r"Active-image:.*?\n\s*Version:\s*(\S+)", body, re.S)
    sw = re.search(r"SW version\s+(\S+)", body, re.I)
    desc = re.search(r"System Description:\s*(\S+)", body)
    unit = re.search(r"^\s*1\s+(\S+-\S+)\s*$", body, re.M)
    uptime = re.search(r"System Up Time \(days,hour:min:sec\):\s*(\d+),(\d+):(\d+):(\d+)", body)
    seconds = None
    if uptime:
        d, h, m, s = (int(g) for g in uptime.groups())
        seconds = d * 86400 + h * 3600 + m * 60 + s
    os_version = version.group(1) if version else (sw.group(1) if sw else None)
    return VersionInfo(
        family=DeviceFamily.UNKNOWN,
        vrp_version=os_version,
        software_version=f"Cisco SMB {os_version}" if os_version else None,
        model=(unit.group(1) if unit else None) or (desc.group(1) if desc else None),
        uptime=uptime.group(0).rsplit("):", 1)[1].strip() if uptime else None,
        uptime_seconds=seconds,
    )


# --- VLANs -----------------------------------------------------------------------------------
def parse_vlans(text: str | None) -> list[VlanInfo]:
    """'show vlan': column positions come from the header (Tagged / UnTagged / Created by)."""
    lines = clean_lines(text)
    cols: dict[str, int] | None = None
    rows: list[dict[str, object]] = []
    for line in lines:
        if line.startswith("VLAN") and "Tagged Ports" in line:
            cols = {
                "name": line.index("Name") - 6,
                "tagged": line.index("Tagged Ports"),
                "untagged": line.index("UnTagged Ports"),
                "created": line.index("Created by"),
            }
            continue
        if cols is None or not line.strip() or line.lstrip().startswith("-"):
            continue
        tagged = line[cols["tagged"] : cols["untagged"]]
        untagged = line[cols["untagged"] : cols["created"]]
        head = line[: cols["tagged"]].split()
        if head and head[0].isdigit():
            rows.append(
                {
                    "id": int(head[0]),
                    "name": " ".join(head[1:]) or head[0],
                    "tagged": expand_ports(tagged),
                    "untagged": expand_ports(untagged),
                }
            )
        elif rows:  # continuation line
            rows[-1]["tagged"] = [*rows[-1]["tagged"], *expand_ports(tagged)]  # type: ignore[misc]
            rows[-1]["untagged"] = [*rows[-1]["untagged"], *expand_ports(untagged)]  # type: ignore[misc]
    return [
        VlanInfo(
            vlan_id=int(r["id"]),  # type: ignore[call-overload]
            name=str(r["name"]),
            status="enable",
            vlan_type="common",
            tagged_ports=tuple(r["tagged"]),  # type: ignore[arg-type]
            untagged_ports=tuple(r["untagged"]),  # type: ignore[arg-type]
        )
        for r in rows
    ]


# --- interfaces ------------------------------------------------------------------------------
_STATUS = re.compile(
    r"^(\S+)\s+(\S+)\s+(Full|Half|--)\s+(\d+|--)\s+(\S+)\s+(\S+)\s+(Up|Down)(\*)?", re.I
)


def _status_rows(text: str | None) -> list[re.Match[str]]:
    rows: list[re.Match[str]] = []
    for line in clean_lines(text):
        match = _STATUS.match(line.strip())
        if not match:
            continue
        try:
            normalize_interface(match.group(1))
        except InvalidInterfaceName:
            continue
        rows.append(match)
    return rows


def parse_interface_brief(text: str | None) -> list[InterfaceBrief]:
    out: list[InterfaceBrief] = []
    for m in _status_rows(text):
        up = m.group(7).lower() == "up"
        out.append(
            InterfaceBrief(
                name=normalize_interface(m.group(1)),
                phy="up" if up else "down",
                protocol="up" if up else "down",
                in_util=None,
                out_util=None,
                in_errors=None,
                out_errors=None,
                admin_down=bool(m.group(8)),  # '*' = suspended by the system
            )
        )
    return out


def parse_interface_detail(text: str | None) -> list[InterfaceDetail]:
    """'show interfaces status' rows and/or 'show interfaces counters <if>' error lines."""
    details = [
        InterfaceDetail(
            name=normalize_interface(m.group(1)),
            admin_up=not m.group(8),
            oper_up=m.group(7).lower() == "up",
            description=None,
            speed_mbps=to_int(m.group(4)),
            duplex=m.group(3).lower() if m.group(3) != "--" else None,
            mtu=None,
            mac=None,
            pvid=None,
            link_type=None,
            input_packets=None,
            input_bytes=None,
            output_packets=None,
            output_bytes=None,
            crc_errors=None,
            input_errors=None,
            output_errors=None,
            input_discards=None,
            output_discards=None,
            input_rate_bps=None,
            output_rate_bps=None,
        )
        for m in _status_rows(text)
    ]
    if details:
        return details
    body = "\n".join(clean_lines(text))
    port = re.search(r"^(\S+/\d+/\d+)\s+\d+", body, re.M)
    if not port:
        return []
    ins = re.search(rf"^{re.escape(port.group(1))}\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", body, re.M)
    outs = list(
        re.finditer(rf"^{re.escape(port.group(1))}\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", body, re.M)
    )
    fcs = re.search(r"FCS Errors:\s*(\d+)", body)
    mac_rx = re.search(r"Internal MAC Rx Errors:\s*(\d+)", body)
    in_pkts = sum(int(g) for g in ins.groups()[:3]) if ins else None
    out_m = outs[1] if len(outs) > 1 else None
    return [
        InterfaceDetail(
            name=normalize_interface(port.group(1)),
            admin_up=True,
            oper_up=True,
            description=None,
            speed_mbps=None,
            duplex=None,
            mtu=None,
            mac=None,
            pvid=None,
            link_type=None,
            input_packets=in_pkts,
            input_bytes=int(ins.group(4)) if ins else None,
            output_packets=sum(int(g) for g in out_m.groups()[:3]) if out_m else None,
            output_bytes=int(out_m.group(4)) if out_m else None,
            crc_errors=int(fcs.group(1)) if fcs else None,
            input_errors=(int(fcs.group(1)) + int(mac_rx.group(1))) if fcs and mac_rx else None,
            output_errors=None,
            input_discards=None,
            output_discards=None,
            input_rate_bps=None,
            output_rate_bps=None,
        )
    ]


def parse_interface_config(text: str | None) -> InterfaceConfig:
    """Interface block ('interface X' ... 'exit', bodies may be unindented)."""
    indented: list[str] = []
    inside = False
    for raw in clean_lines(text):
        line = raw.strip()
        if line.startswith("interface "):
            inside = True
            indented.append(line)
        elif line == "exit":
            inside = False
        elif inside and line:
            indented.append(f" {line}")
    cfg = ios.parse_interface_config("\n".join(indented))
    if any(line.startswith("switchport mode general") for line in cfg.lines):
        cfg.link_type = "hybrid"
    for line in cfg.lines:
        if line.startswith("switchport general pvid "):
            cfg.hybrid_pvid = to_int(line.split()[-1])
    return cfg


# --- MAC / ARP / DHCP ------------------------------------------------------------------------
_MAC = re.compile(r"^\s*(\d+)\s+([0-9a-fA-F:]{17})\s+(\S+)\s+(\S+)\s*$")


def parse_mac_table(text: str | None) -> list[MacEntry]:
    out: list[MacEntry] = []
    for line in clean_lines(text):
        match = _MAC.match(line)
        if not match or match.group(4).lower() == "self" or match.group(3) == "0":
            continue
        mac = safe_mac(match.group(2))
        if mac:
            out.append(
                MacEntry(
                    mac,
                    int(match.group(1)),
                    safe_interface(match.group(3)),
                    match.group(4).lower(),
                    None,
                )
            )
    return out


def parse_mac_aging(text: str | None) -> int | None:
    match = re.search(r"Aging time is (\d+) sec", "\n".join(clean_lines(text)))
    return int(match.group(1)) if match else None


_ARP = re.compile(r"^VLAN\s+(\d+)\s+(\S+)\s+(\d+\.\d+\.\d+\.\d+)\s+([0-9A-Fa-f:]{17})\s+(\w+)")


def parse_arp(text: str | None) -> list[ArpEntry]:
    out: list[ArpEntry] = []
    for line in clean_lines(text):
        if (match := _ARP.match(line.strip())) and (mac := safe_mac(match.group(4))):
            out.append(
                ArpEntry(
                    ip=match.group(3),
                    mac=mac,
                    expire_minutes=None,
                    entry_type=match.group(5).lower(),
                    vlan=int(match.group(1)),
                    interface=safe_interface(match.group(2)),
                    vpn=None,
                )
            )
    return out


_BINDING = re.compile(r"^([0-9a-fA-F.]{14})\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\S+\s+(\d+)\s+(\S+)")


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
_ROUTE = re.compile(
    r"^\s*([CSI])>?\s+(\d+\.\d+\.\d+\.\d+/\d+)\s+(\d+)/(\d+)\s+(\d+\.\d+\.\d+\.\d+)(.*)$"
)
_OUT_IFACE = re.compile(r"(vlan \d+|[a-zA-Z][\w/.-]*)\s*$")
_ROUTE_PROTO = {"C": "Direct", "S": "Static", "I": "ICMP"}


def parse_routing_table(text: str | None) -> list[RouteEntry]:
    out: list[RouteEntry] = []
    for line in clean_lines(text):
        match = _ROUTE.match(line)
        if not match:
            continue
        out_iface = _OUT_IFACE.search(match.group(6))
        name = safe_interface(out_iface.group(1).replace(" ", "")) if out_iface else None
        protocol = _ROUTE_PROTO[match.group(1)]
        out.append(
            RouteEntry(
                match.group(2),
                protocol,
                int(match.group(3)),
                int(match.group(4)),
                "D" + ("*" if match.group(2) == "0.0.0.0/0" else ""),
                _NO_NEXTHOP if protocol == "Direct" else match.group(5),
                name,
            )
        )
    return out


def parse_static_route_lines(text: str | None) -> set[str]:
    return {" ".join(line.split()) for line in clean_lines(text) if line.startswith("ip route ")}


def parse_ospf_peers(text: str | None) -> list[OspfNeighbor]:
    return []  # no OSPF on Cisco SMB


def parse_bgp_peers(text: str | None) -> list[BgpPeer]:
    return []  # no BGP on Cisco SMB


# --- STP -------------------------------------------------------------------------------------
_STP_PORT = re.compile(r"^(\S+)\s+(enabled|disabled)\s+\S+\s+\d+\s+(\w+)\s+(\S+)\s+(\S+)", re.I)
_STS = {
    "FRW": "FORWARDING",
    "BLK": "DISCARDING",
    "DIS": "DISABLED",
    "LRN": "LEARNING",
    "LIS": "LISTENING",
}
_ROLE = {"Root": "ROOT", "Desg": "DESI", "Altn": "ALTE", "Bkup": "BACK", "-": "DISA"}


def parse_stp_global(text: str | None) -> StpGlobal:
    body = "\n".join(clean_lines(text))
    mode = re.search(r"Spanning tree (?:enabled|disabled).*?mode (\S+)", body)
    root = re.search(r"Root ID\s+Priority\s+(\d+)\s+Address\s+(\S+)", body)
    bridge = re.search(r"Bridge ID\s+Priority\s+(\d+)\s+Address\s+(\S+)", body)
    cost = re.search(r"Root ID.*?Cost\s+(\d+)", body, re.S)
    port = re.search(r"Root ID.*?Port\s+(\S+)", body, re.S)
    tc = re.search(r"Number of topology changes (\d+) last change occurred (\S+) ago", body)
    is_root = bool(root and bridge and root.groups() == bridge.groups())
    return StpGlobal(
        mode=mode.group(1).lower() if mode else None,
        bridge_id=f"{bridge.group(1)}.{bridge.group(2)}" if bridge else None,
        root_id=f"{root.group(1)}.{root.group(2)}" if root else None,
        root_path_cost=0 if is_root else (int(cost.group(1)) if cost else None),
        root_port=None if is_root or not port else safe_interface(port.group(1)),
        tc_count=int(tc.group(1)) if tc else None,
        time_since_last_tc=tc.group(2) if tc else None,
        bpdu_protection=None,
    )


def parse_stp_brief(text: str | None) -> list[StpPortBrief]:
    out: list[StpPortBrief] = []
    for line in clean_lines(text):
        match = _STP_PORT.match(line.strip())
        if not match:
            continue
        iface = safe_interface(match.group(1))
        if not iface or "/" not in iface:
            continue
        portfast = match.group(5).lower() == "yes"
        out.append(
            StpPortBrief(
                0,
                iface,
                _ROLE.get(match.group(4), match.group(4).upper()),
                _STS.get(match.group(3), match.group(3)),
                "EDGE" if portfast else "NONE",
            )
        )
    return out


# --- CPU / memory ----------------------------------------------------------------------------
def parse_cpu_usage(text: str | None) -> CpuUsage:
    match = re.search(
        r"five seconds:\s*(\d+)%;\s*one minute:\s*(\d+)%;\s*five minutes:\s*(\d+)%",
        "\n".join(clean_lines(text)),
    )
    if not match:
        return CpuUsage(None, None, None, None)
    five_s, one_m, five_m = (float(g) for g in match.groups())
    return CpuUsage(five_s, five_s, one_m, five_m)


def parse_memory_usage(text: str | None) -> MemoryUsage:
    return MemoryUsage(None, None, None, None)  # no CLI memory command; use SNMP


# --- ACLs ------------------------------------------------------------------------------------
_ACL_HEAD = re.compile(r"^(Extended|Standard) (IP|IPv6|MAC) access list (\S+)$")
_PRIORITY = re.compile(r"\s+(?:ace-)?priority (\d+)")


def canonical_smb_entry(text: str) -> str:
    """Entry text without its priority, single-spaced (used for 'no <entry>' and diffs)."""
    return " ".join(_PRIORITY.sub("", text).split())


def parse_acls(text: str | None) -> list[ParsedAcl]:
    acls: list[ParsedAcl] = []
    current: ParsedAcl | None = None
    for line in clean_lines(text):
        if match := _ACL_HEAD.match(line.strip()):
            current = ParsedAcl(kind="advanced", number=None, name=match.group(3))
            acls.append(current)
            continue
        entry = line.strip()
        if current is None or not entry.startswith(("permit", "deny")):
            continue
        prio = _PRIORITY.search(entry)
        key = int(prio.group(1)) if prio else (max(current.rules, default=0) + 20)
        current.rules[key] = canonical_smb_entry(entry)
    return acls


def management_acls(running_config: str | None) -> frozenset[str]:
    """'management access-class NAME' and ACLs bound with 'service-acl input' on VLAN 'mgmt'."""
    names = {
        m.group(1)
        for m in re.finditer(r"^management access-class (\S+)", running_config or "", re.M)
        if m.group(1) != "console-only"
    }
    return frozenset(names)
