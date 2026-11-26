"""Text parsers for Cisco IOS / IOS-XE 'show' output; return the shared parser value objects.

Fixtures: tests/fixtures/cisco/c9300_iosxe17 (Catalyst 9300, IOS-XE 17.9) and c2960x_ios15.
Parsers are tolerant: unknown lines are skipped, never raised on.
"""

from __future__ import annotations

import re

from app.domain.devices import DeviceFamily
from app.domain.interfaces import InvalidInterfaceName, normalize_interface
from app.domain.vlans import parse_vlan_list
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
from app.network.parsers.version import uptime_to_seconds
from app.network.platforms.cisco_ios.builders_acl import canonical_entry

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_MORE = re.compile(r"\s*--More--\s*", re.IGNORECASE)
_PROMPT = re.compile(r"^[\w.\-()/:]{1,63}[#>]")  # 'HOST#', 'HOST(config-if)#', 'HOST>'
_ALL_VLANS = set(range(1, 4095))


def clean_lines(text: str | None) -> list[str]:
    """Drop ANSI codes, '--More--' paging residue, prompt and command-echo lines."""
    if not text:
        return []
    text = _ANSI.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    out: list[str] = []
    for raw in text.split("\n"):
        line = _MORE.sub("", raw).rstrip()
        if _PROMPT.match(line):
            continue
        out.append(line)
    return out


# --- version ---------------------------------------------------------------------------------
_OS_VERSION = re.compile(r"Cisco IOS XE Software, Version (\S+)|, Version ([^\s,]+)")
_MODEL = (
    re.compile(r"^Model Number\s*:\s*(\S+)", re.M),
    re.compile(r"^cisco (\S+) \(.+\) processor", re.M | re.I),
)
_UPTIME = re.compile(r"^\S+ uptime is (.+)$", re.M)
_HOSTNAME = re.compile(r"^(\S+) uptime is", re.M)


def parse_version(text: str | None) -> VersionInfo:
    body = "\n".join(clean_lines(text))
    os_version = None
    if match := _OS_VERSION.search(body):
        os_version = match.group(1) or match.group(2)
    model = None
    for pattern in _MODEL:
        if match := pattern.search(body):
            model = match.group(1)
            break
    uptime = _UPTIME.search(body)
    uptime_text = uptime.group(1).strip() if uptime else None
    image = re.search(r"\(([A-Z0-9_]+-[A-Z0-9_-]+|CAT9K_IOSXE)\)", body)
    return VersionInfo(
        family=DeviceFamily.UNKNOWN,
        vrp_version=os_version,
        software_version=(f"{image.group(1)} {os_version}" if image and os_version else os_version),
        model=model,
        uptime=uptime_text,
        uptime_seconds=_uptime_seconds(uptime_text),
    )


def _uptime_seconds(text: str | None) -> int | None:
    if not text:
        return None
    total = 0
    units = {"year": 31536000, "week": 604800, "day": 86400, "hour": 3600, "minute": 60}
    found = False
    for amount, unit in re.findall(r"(\d+)\s+(year|week|day|hour|minute)s?", text):
        total += int(amount) * units[unit]
        found = True
    return total if found else uptime_to_seconds(text)


# --- VLANs -----------------------------------------------------------------------------------
_VLAN_ROW = re.compile(
    r"^(\d{1,4})\s+(\S+)\s+(active|suspended|act/unsup|act/lshut|sus/lshut)\s*(.*)$"
)


def parse_vlans(text: str | None) -> list[VlanInfo]:
    """'show vlan brief'. Ports listed there are access (untagged) members; trunks are not."""
    vlans: list[VlanInfo] = []
    current: dict[str, object] | None = None

    def flush() -> None:
        if current is not None:
            vlans.append(
                VlanInfo(
                    vlan_id=int(current["id"]),  # type: ignore[call-overload]
                    name=str(current["name"]),
                    status=str(current["status"]),
                    vlan_type="common",
                    tagged_ports=(),
                    untagged_ports=tuple(current["ports"]),  # type: ignore[arg-type]
                )
            )

    for line in clean_lines(text):
        if match := _VLAN_ROW.match(line):
            flush()
            vid = int(match.group(1))
            if vid in {1002, 1003, 1004, 1005}:
                current = None
                continue
            current = {
                "id": vid,
                "name": match.group(2),
                "status": "enable" if match.group(3).startswith("act") else "disable",
                "ports": _ports(match.group(4)),
            }
        elif current is not None and line.startswith(" ") and line.strip():
            current["ports"] = [*current["ports"], *_ports(line)]  # type: ignore[misc]
    flush()
    return vlans


def _ports(cell: str) -> list[str]:
    return [p for p in (safe_interface(x) for x in cell.replace(",", " ").split()) if p]


# --- interfaces ------------------------------------------------------------------------------
_STATUS_HEADER = re.compile(r"^Port\s+Name\s+Status\s+Vlan", re.I)


def parse_interface_brief(text: str | None) -> list[InterfaceBrief]:
    """'show interfaces status' (fixed columns located from the header line)."""
    lines = clean_lines(text)
    rows: list[InterfaceBrief] = []
    cols: dict[str, int] | None = None
    for line in lines:
        if _STATUS_HEADER.match(line):
            cols = {k: line.index(k) for k in ("Name", "Status", "Vlan", "Duplex")}
            continue
        if cols is None or not line.strip() or len(line) < cols["Status"]:
            continue
        raw_name = line[: cols["Name"]].strip()
        try:  # skip separator lines ('------') and anything that is not an interface
            name: str | None = normalize_interface(raw_name)
        except InvalidInterfaceName:
            name = None
        status = line[cols["Status"] : cols["Vlan"]].strip().lower()
        if not name or not status:
            continue
        up = status == "connected"
        rows.append(
            InterfaceBrief(
                name=name,
                phy="up" if up else "down",
                protocol="up" if up else "down",
                in_util=None,
                out_util=None,
                in_errors=None,
                out_errors=None,
                admin_down=status == "disabled",
            )
        )
    return rows


_IF_HEAD = re.compile(
    r"^(\S+) is (administratively down|up|down|deleted)(?:, line protocol is (\w+))?"
)
_RATE = re.compile(r"(\d+) (?:minute|second) (input|output) rate (\d+) bits/sec")
_PKTS = re.compile(r"(\d+) packets (input|output), (\d+) bytes")
_ERRS = re.compile(r"(\d+) (input|output) errors(?:, (\d+) CRC)?")
_SPEED = re.compile(r"(Full|Half|Auto)-duplex, (\d+)(Mb|Gb)/s|(Full|Half|Auto)-duplex, Auto-speed")
_DROPS = re.compile(r"Total output drops: (\d+)")
_INPUT_DROPS = re.compile(r"Input queue: \d+/\d+/(\d+)/")


def parse_interface_detail(text: str | None) -> list[InterfaceDetail]:
    """'show interfaces' (all or one)."""
    blocks: list[list[str]] = []
    for line in clean_lines(text):
        if _IF_HEAD.match(line):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return [_detail(block) for block in blocks]


def _detail(block: list[str]) -> InterfaceDetail:
    head = _IF_HEAD.match(block[0])
    assert head is not None  # noqa: S101 - guaranteed by caller
    body = "\n".join(block)
    desc = re.search(r"^\s*Description: (.+)$", body, re.M)
    mac = re.search(r"address is ([0-9a-f.]+)", body)
    mtu = re.search(r"MTU (\d+) bytes", body)
    speed_mbps = duplex = None
    if match := _SPEED.search(body):
        if match.group(2):
            speed_mbps = int(match.group(2)) * (1000 if match.group(3) == "Gb" else 1)
        duplex = (match.group(1) or match.group(4) or "").lower() or None
    rates = {d: int(v) for _, d, v in _RATE.findall(body)}
    pkts = {d: (int(p), int(b)) for p, d, b in _PKTS.findall(body)}
    errors: dict[str, int] = {}
    crc = None
    for count, direction, crc_count in _ERRS.findall(body):
        errors[direction] = int(count)
        if crc_count:
            crc = int(crc_count)
    out_drops = _DROPS.search(body)
    in_drops = _INPUT_DROPS.search(body)
    return InterfaceDetail(
        name=safe_interface(head.group(1)) or head.group(1),
        admin_up=head.group(2) != "administratively down",
        oper_up=head.group(2) == "up" and (head.group(3) in {None, "up"}),
        description=desc.group(1).strip() if desc else None,
        speed_mbps=speed_mbps,
        duplex=duplex if duplex in {"full", "half"} else None,
        mtu=int(mtu.group(1)) if mtu else None,
        mac=safe_mac(mac.group(1)) if mac else None,
        pvid=None,
        link_type=None,
        input_packets=pkts.get("input", (None, None))[0],
        input_bytes=pkts.get("input", (None, None))[1],
        output_packets=pkts.get("output", (None, None))[0],
        output_bytes=pkts.get("output", (None, None))[1],
        crc_errors=crc,
        input_errors=errors.get("input"),
        output_errors=errors.get("output"),
        input_discards=int(in_drops.group(1)) if in_drops else None,
        output_discards=int(out_drops.group(1)) if out_drops else None,
        input_rate_bps=rates.get("input"),
        output_rate_bps=rates.get("output"),
    )


def parse_interface_config(text: str | None) -> InterfaceConfig:  # noqa: PLR0912 - flat dispatch
    """'show running-config interface X' -> shared InterfaceConfig.

    Mapping: switchport mode -> link_type, access vlan -> default_vlan, native vlan ->
    trunk_pvid, allowed vlans -> trunk_allowed (no allowed line on a trunk = all VLANs).
    """
    cfg = InterfaceConfig()
    allowed: set[int] | None = None
    for raw in clean_lines(text):
        line = " ".join(raw.split())
        if line.startswith("interface "):
            cfg.name = safe_interface(line.removeprefix("interface "))
            continue
        if not raw.startswith(" "):
            continue
        cfg.lines.append(line)
        if line.startswith("switchport mode "):
            mode = line.removeprefix("switchport mode ")
            cfg.link_type = mode if mode in {"access", "trunk"} else None
        elif line.startswith("switchport access vlan "):
            cfg.default_vlan = to_int(line.split()[-1])
        elif line.startswith("switchport trunk native vlan "):
            cfg.trunk_pvid = to_int(line.split()[-1])
        elif line.startswith("switchport trunk allowed vlan add "):
            allowed = (allowed or set()) | set(parse_vlan_list(line.split()[-1], strict=False))
        elif line.startswith("switchport trunk allowed vlan "):
            value = line.removeprefix("switchport trunk allowed vlan ")
            allowed = (
                set()
                if value == "none"
                else (_ALL_VLANS if value == "all" else set(parse_vlan_list(value, strict=False)))
            )
        elif line.startswith("description "):
            cfg.description = line.removeprefix("description ")
        elif line == "shutdown":
            cfg.shutdown = True
        elif line in {"spanning-tree portfast", "spanning-tree portfast edge"}:
            cfg.stp_edged = True
        elif line == "ip dhcp snooping trust":
            cfg.dhcp_trusted = True
    if cfg.link_type == "trunk":
        cfg.trunk_allowed = _ALL_VLANS if allowed is None else allowed
    return cfg


# --- MAC / ARP / DHCP ------------------------------------------------------------------------
_MAC_ROW = re.compile(
    r"^\s*(\d+|All)\s+([0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\s+(\S+)\s+(.+?)\s*$", re.I
)


def parse_mac_table(text: str | None) -> list[MacEntry]:
    entries: list[MacEntry] = []
    for line in clean_lines(text):
        match = _MAC_ROW.match(line)
        if not match or match.group(1) == "All":
            continue
        port = match.group(4).split()[0]
        entry_type = match.group(3).lower()
        if port.lower() == "drop":
            entry_type, interface = "blackhole", None
        elif port.upper() in {"CPU", "ROUTER", "SWITCH"}:
            continue
        else:
            interface = safe_interface(port)
        mac = safe_mac(match.group(2))
        if mac:
            entries.append(MacEntry(mac, int(match.group(1)), interface, entry_type, None))
    return entries


def parse_mac_aging(text: str | None) -> int | None:
    match = re.search(r"Global Aging Time:\s*(\d+)", "\n".join(clean_lines(text)))
    return int(match.group(1)) if match else None


_ARP_ROW = re.compile(
    r"^Internet\s+(\S+)\s+(\S+)\s+([0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\s+\S+\s*(\S*)", re.I
)


def parse_arp(text: str | None) -> list[ArpEntry]:
    entries: list[ArpEntry] = []
    for line in clean_lines(text):
        match = _ARP_ROW.match(line)
        if not match:
            continue
        mac = safe_mac(match.group(3))
        if not mac:
            continue
        age = match.group(2)
        iface = safe_interface(match.group(4)) if match.group(4) else None
        vlan = (
            int(iface[4:]) if iface and iface.startswith("Vlan") and iface[4:].isdigit() else None
        )
        entries.append(
            ArpEntry(
                ip=match.group(1),
                mac=mac,
                expire_minutes=to_int(age),
                entry_type="static" if age == "-" else "dynamic",
                vlan=vlan,
                interface=iface,
                vpn=None,
            )
        )
    return entries


_BINDING = re.compile(r"^([0-9A-Fa-f:]{17})\s+(\S+)\s+(\d+|infinite)\s+\S+\s+(\d+)\s+(\S+)")


def parse_dhcp_snooping_bindings(text: str | None) -> list[DhcpBindingEntry]:
    out: list[DhcpBindingEntry] = []
    for line in clean_lines(text):
        if match := _BINDING.match(line.strip()):
            mac = safe_mac(match.group(1))
            if mac:
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
_PROTOCOLS = {
    "C": "Direct",
    "L": "Direct",
    "S": "Static",
    "O": "OSPF",
    "B": "BGP",
    "R": "RIP",
    "D": "EIGRP",
    "i": "ISIS",
}
_ROUTE = re.compile(
    r"^(?P<code>[A-Za-z]{1,2})(?P<star>\*)?\s+(?:[A-Z0-9]{1,2}\s+)?(?P<prefix>\d+\.\d+\.\d+\.\d+/\d+)"
    r"(?P<rest>.*)$"
)
# "[110/20] via 192.0.2.2, 01:02:03, Vlan100" / "[20/0] via 192.0.2.5, 2d03h"
_VIA = re.compile(r"\[(\d+)/(\d+)\] via ([\d.]+)(.*)$")
_CONT = re.compile(r"^\s+(\[\d+/\d+\] via .*)$")
_NO_NEXTHOP = "0.0.0.0"  # noqa: S104 - connected route next hop, not a bind address
_CONNECTED = re.compile(r"is directly connected, (\S+)")


def parse_routing_table(text: str | None) -> list[RouteEntry]:
    routes: list[RouteEntry] = []
    last: tuple[str, str, str] | None = None  # prefix, protocol, flags
    for line in clean_lines(text):
        if match := _ROUTE.match(line):
            code = match.group("code")
            protocol = _PROTOCOLS.get(code[0], code)
            prefix, rest = match.group("prefix"), match.group("rest")
            flags = "D" + ("*" if match.group("star") else "")
            last = (prefix, protocol, flags)
            if conn := _CONNECTED.search(rest):
                routes.append(
                    RouteEntry(
                        prefix, protocol, 0, 0, flags, _NO_NEXTHOP, safe_interface(conn.group(1))
                    )
                )
            elif via := _VIA.search(rest):
                routes.append(_via(prefix, protocol, flags, via))
            continue
        if last and (cont := _CONT.match(line)) and (via := _VIA.search(cont.group(1))):
            routes.append(_via(*last, via))
    return routes


def _via(prefix: str, protocol: str, flags: str, match: re.Match[str]) -> RouteEntry:
    # The outgoing interface is the last comma-separated token that starts with a letter
    # (uptimes such as '01:02:03' or '2d03h' start with a digit).
    tail = [t.strip() for t in match.group(4).split(",") if t.strip()]
    iface = tail[-1] if tail and tail[-1][0].isalpha() else None
    return RouteEntry(
        prefix,
        protocol,
        int(match.group(1)),
        int(match.group(2)),
        flags,
        match.group(3),
        safe_interface(iface) if iface else None,
    )


def parse_static_route_lines(text: str | None) -> set[str]:
    return {" ".join(line.split()) for line in clean_lines(text) if line.startswith("ip route ")}


_OSPF = re.compile(r"^(\d+\.\d+\.\d+\.\d+)\s+\d+\s+(\S+)\s+\S+\s+(\S+)\s+(\S+)")


def parse_ospf_peers(text: str | None) -> list[OspfNeighbor]:
    out: list[OspfNeighbor] = []
    for line in clean_lines(text):
        if match := _OSPF.match(line.strip()):
            state = match.group(2).split("/")[0].capitalize()
            out.append(
                OspfNeighbor(
                    area="-",
                    interface=safe_interface(match.group(4)),
                    router_id=match.group(1),
                    state=state,
                )
            )
    return out


_BGP = re.compile(
    r"^(\d+\.\d+\.\d+\.\d+)\s+4\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(\S+)\s+(\S+)"
)


def parse_bgp_peers(text: str | None) -> list[BgpPeer]:
    out: list[BgpPeer] = []
    for line in clean_lines(text):
        if match := _BGP.match(line.strip()):
            state = match.group(4)
            out.append(
                BgpPeer(
                    peer=match.group(1),
                    remote_as=int(match.group(2)),
                    up_down=match.group(3),
                    state="Established" if state.isdigit() else state,
                )
            )
    return out


# --- STP -------------------------------------------------------------------------------------
_STP_MODES = {"rstp": "rstp", "ieee": "stp", "mstp": "mstp"}
_STP_PORT = re.compile(r"^(\S+)\s+(Root|Desg|Altn|Back|Mstr|Disb)\s+(\S+)\s+\d+\s+\S+\s*(.*)$")
_INSTANCE = re.compile(r"^(?:VLAN|MST)(\d+)$")


def parse_stp_global(text: str | None) -> StpGlobal:
    """First instance of 'show spanning-tree' (root, bridge and protocol)."""
    body = "\n".join(clean_lines(text))
    proto = re.search(r"Spanning tree enabled protocol (\S+)", body)
    root = re.search(r"Root ID\s+Priority\s+(\d+)\s+Address\s+(\S+)", body)
    bridge = re.search(r"Bridge ID\s+Priority\s+(\d+).*?Address\s+(\S+)", body, re.S)
    cost = re.search(r"Root ID.*?Cost\s+(\d+)", body.split("Bridge ID")[0], re.S)
    port = re.search(r"Port\s+\d+ \((\S+)\)", body.split("Bridge ID")[0])
    is_root = "This bridge is the root" in body.split("Bridge ID")[0]
    return StpGlobal(
        mode=_STP_MODES.get(proto.group(1), proto.group(1)) if proto else None,
        bridge_id=f"{bridge.group(1)}.{bridge.group(2)}" if bridge else None,
        root_id=f"{root.group(1)}.{root.group(2)}" if root else None,
        root_path_cost=0 if is_root else (int(cost.group(1)) if cost else None),
        root_port=safe_interface(port.group(1)) if port and not is_root else None,
        tc_count=None,
        time_since_last_tc=None,
        bpdu_protection=None,
    )


_ROLES = {
    "Root": "ROOT",
    "Desg": "DESI",
    "Altn": "ALTE",
    "Back": "BACK",
    "Mstr": "MAST",
    "Disb": "DISA",
}
_STATES = {
    "FWD": "FORWARDING",
    "BLK": "DISCARDING",
    "LRN": "LEARNING",
    "LIS": "LISTENING",
    "BKN": "BROKEN",
    "DIS": "DISABLED",
}


def parse_stp_brief(text: str | None) -> list[StpPortBrief]:
    out: list[StpPortBrief] = []
    instance = 0
    for line in clean_lines(text):
        if match := _INSTANCE.match(line.strip()):
            instance = int(match.group(1))
            continue
        if match := _STP_PORT.match(line):
            iface = safe_interface(match.group(1))
            if not iface:
                continue
            protection = "EDGE" if "Edge" in match.group(4) else "NONE"
            out.append(
                StpPortBrief(
                    instance,
                    iface,
                    _ROLES.get(match.group(2), match.group(2).upper()),
                    _STATES.get(match.group(3), match.group(3)),
                    protection,
                )
            )
    return out


# --- CPU / memory ----------------------------------------------------------------------------
_CPU = re.compile(
    r"CPU utilization for five seconds: (\d+)%(?:/\d+%)?; one minute: (\d+)%; five minutes: (\d+)%"
)


def parse_cpu_usage(text: str | None) -> CpuUsage:
    match = _CPU.search("\n".join(clean_lines(text)))
    if not match:
        return CpuUsage(None, None, None, None)
    five_s, one_m, five_m = (float(g) for g in match.groups())
    return CpuUsage(five_s, five_s, one_m, five_m)


_MEM = re.compile(r"Processor Pool Total:\s*(\d+)\s+Used:\s*(\d+)\s+Free:\s*(\d+)")


def parse_memory_usage(text: str | None) -> MemoryUsage:
    match = _MEM.search("\n".join(clean_lines(text)))
    if not match:
        return MemoryUsage(None, None, None, None)
    total, used, free = (int(g) for g in match.groups())
    return MemoryUsage(total, used, free, round(used / total * 100, 1) if total else None)


# --- ACLs ------------------------------------------------------------------------------------
_ACL_HEAD = re.compile(r"^(Standard|Extended) IP access list (\S+)$|^IPv6 access list (\S+)")
_ACL_ENTRY = re.compile(r"^\s+(\d+) (.+)$")
_ACL_V6_ENTRY = re.compile(r"^\s+(.+?) sequence (\d+)")


def parse_acls(text: str | None) -> list[ParsedAcl]:
    acls: list[ParsedAcl] = []
    current: ParsedAcl | None = None
    for line in clean_lines(text):
        if match := _ACL_HEAD.match(line.strip()):
            name = match.group(2) or match.group(3)
            kind = "basic" if match.group(1) == "Standard" else "advanced"
            number = int(name) if name.isdigit() else None
            current = ParsedAcl(kind=kind, number=number, name=None if number else name)
            acls.append(current)
            continue
        if current is None:
            continue
        if match := _ACL_V6_ENTRY.match(line):
            seq = int(match.group(2))
            current.rules[seq] = canonical_entry(f"{match.group(1)} sequence {seq}")
        elif match := _ACL_ENTRY.match(line):
            seq = int(match.group(1))
            body = match.group(2)
            hits = re.search(r"\((\d+) match", body)
            if hits:
                current.matches[seq] = int(hits.group(1))
            current.rules[seq] = canonical_entry(f"{seq} {body}")
    return acls


def management_acls(running_config: str | None) -> frozenset[str]:
    """ACLs bound to VTY lines ('access-class NAME in' under 'line vty')."""
    names: set[str] = set()
    in_vty = False
    for raw in clean_lines(running_config):
        if raw and not raw.startswith(" "):
            in_vty = raw.startswith("line vty")
            continue
        if in_vty and (m := re.match(r"^\s*access-class (\S+) in\b", raw)):
            names.add(m.group(1))
    return frozenset(names)
