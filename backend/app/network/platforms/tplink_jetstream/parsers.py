"""TP-Link JetStream / Omada switch CLI parsers (T1600G / T2600G / SG3xxx).

The official CLI guides show no command output: the fixtures in tests/fixtures/tplink are
SYNTHETIC. Parsers locate columns from header lines and skip anything they do not recognise,
so they degrade to empty results rather than wrong data until real captures are added.
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
from app.network.parsers.common import safe_mac, to_int
from app.network.parsers.interface_config import InterfaceConfig
from app.network.parsers.routing_protocols import BgpPeer, OspfNeighbor
from app.network.platforms.cisco_ios import parsers as ios
from app.network.platforms.cisco_ios.parsers import clean_lines
from app.network.platforms.cisco_smb.parsers import expand_ports

_NO_NEXTHOP = "0.0.0.0"  # noqa: S104 - connected route next hop, not a bind address


def _iface(raw: str) -> str | None:
    try:
        return normalize_interface(raw.replace(" ", ""))
    except InvalidInterfaceName:
        return None


def _field(body: str, label: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(label)}\s*[-:]\s*(.+?)\s*$", body, re.M)
    return match.group(1) if match else None


# --- version ---------------------------------------------------------------------------------
def parse_version(text: str | None) -> VersionInfo:
    body = "\n".join(clean_lines(text))
    firmware = _field(body, "Firmware Version")
    hardware = _field(body, "Hardware Version")
    running = _field(body, "Running Time")
    seconds = None
    if running:
        units = {"day": 86400, "hour": 3600, "min": 60, "sec": 1}
        seconds = sum(
            int(n) * units[u] for n, u in re.findall(r"(\d+)\s*(day|hour|min|sec)", running)
        )
    return VersionInfo(
        family=DeviceFamily.UNKNOWN,
        vrp_version=firmware.split()[0] if firmware else None,
        software_version=firmware,
        model=hardware.split()[0] if hardware else None,
        uptime=running,
        uptime_seconds=seconds,
    )


# --- VLANs -----------------------------------------------------------------------------------
_VLAN = re.compile(r"^(\d{1,4})\s+(\S+)\s+(active|inactive)\s*(.*)$", re.I)


def parse_vlans(text: str | None) -> list[VlanInfo]:
    """'show vlan' lists member ports only (tagged/untagged detail is per port)."""
    out: list[VlanInfo] = []
    for line in clean_lines(text):
        if match := _VLAN.match(line.strip()):
            out.append(
                VlanInfo(
                    vlan_id=int(match.group(1)),
                    name=match.group(2).strip('"'),
                    status="enable" if match.group(3).lower() == "active" else "disable",
                    vlan_type="common",
                    tagged_ports=(),
                    untagged_ports=tuple(expand_ports(match.group(4))),
                )
            )
    return out


# --- interfaces ------------------------------------------------------------------------------
_STATUS = re.compile(r"^(\S+)\s+(LinkUp|LinkDown|Disable|Disabled)\s+(\S+)\s+(\S+)", re.I)


def _speed(value: str) -> int | None:
    match = re.match(r"(\d+)([MG])", value)
    if not match:
        return None
    return int(match.group(1)) * (1000 if match.group(2) == "G" else 1)


def parse_interface_brief(text: str | None) -> list[InterfaceBrief]:
    out: list[InterfaceBrief] = []
    for line in clean_lines(text):
        match = _STATUS.match(line.strip())
        name = _iface(match.group(1)) if match else None
        if not match or not name:
            continue
        up = match.group(2).lower() == "linkup"
        out.append(
            InterfaceBrief(
                name=name,
                phy="up" if up else "down",
                protocol="up" if up else "down",
                in_util=None,
                out_util=None,
                in_errors=None,
                out_errors=None,
                admin_down=match.group(2).lower().startswith("disable"),
            )
        )
    return out


def _empty_detail(name: str) -> dict[str, object]:
    return {
        "name": name,
        "admin_up": True,
        "oper_up": False,
        "description": None,
        "speed_mbps": None,
        "duplex": None,
        "mtu": None,
        "mac": None,
        "pvid": None,
        "link_type": None,
        "input_packets": None,
        "input_bytes": None,
        "output_packets": None,
        "output_bytes": None,
        "crc_errors": None,
        "input_errors": None,
        "output_errors": None,
        "input_discards": None,
        "output_discards": None,
        "input_rate_bps": None,
        "output_rate_bps": None,
    }


def parse_interface_detail(text: str | None) -> list[InterfaceDetail]:
    """'show interface status' rows, or the label list of 'show interface counters <if>'."""
    lines = clean_lines(text)
    rows: list[InterfaceDetail] = []
    desc_col: int | None = None
    for line in lines:
        if line.startswith("Port") and "Description" in line:
            desc_col = line.index("Description")
            continue
        match = _STATUS.match(line.strip())
        name = _iface(match.group(1)) if match else None
        if not match or not name:
            continue
        values = _empty_detail(name)
        values.update(
            admin_up=not match.group(2).lower().startswith("disable"),
            oper_up=match.group(2).lower() == "linkup",
            speed_mbps=_speed(match.group(3)),
            duplex=match.group(4).lower() if match.group(4).lower() in {"full", "half"} else None,
            description=(
                (line[desc_col:].strip() or None) if desc_col and len(line) > desc_col else None
            ),
        )
        rows.append(InterfaceDetail(**values))  # type: ignore[arg-type]
    if rows:
        return rows
    body = "\n".join(lines)
    head = re.search(r"^\s*(\S+/\d+/\d+):\s*$", body, re.M)
    if not head or not (name := _iface(head.group(1))):
        return []

    def num(label: str) -> int | None:
        value = _field(body, label)
        return to_int(value) if value else None

    rx = [num(f"Rx {k} Pkts") for k in ("Unicast", "Broadcast", "Multicast")]
    tx = [num(f"Tx {k} Pkts") for k in ("Unicast", "Broadcast", "Multicast")]
    values = _empty_detail(name)
    crc = num("CRC Errors")
    values.update(
        oper_up=True,
        input_packets=(
            sum(v for v in rx if v is not None) if any(v is not None for v in rx) else None
        ),
        output_packets=(
            sum(v for v in tx if v is not None) if any(v is not None for v in tx) else None
        ),
        input_bytes=num("Rx Octets"),
        output_bytes=num("Tx Octets"),
        crc_errors=crc,
        input_errors=(crc or 0) + (num("Undersize Pkts") or 0) + (num("Oversize Pkts") or 0),
    )
    return [InterfaceDetail(**values)]  # type: ignore[arg-type]


def parse_interface_config(text: str | None) -> InterfaceConfig:
    """General-mode port config mapped onto the shared model.

    untagged only + pvid -> access (default_vlan); any tagged -> trunk (trunk_allowed = tagged,
    trunk_pvid = pvid); untagged and tagged sets are also kept in hybrid_tagged/untagged.
    """
    cfg = InterfaceConfig()
    for raw in clean_lines(text):
        line = " ".join(raw.split())
        if line.startswith("interface "):
            cfg.name = _iface(line.removeprefix("interface "))
            continue
        if not raw.startswith(" ") or not line:
            continue
        cfg.lines.append(line)
        if match := re.match(r"^switchport general allowed vlan (\S+) (tagged|untagged)$", line):
            vlans = set(parse_vlan_list(match.group(1), strict=False))
            target = cfg.hybrid_tagged if match.group(2) == "tagged" else cfg.hybrid_untagged
            target |= vlans
        elif line.startswith("switchport pvid "):
            cfg.hybrid_pvid = to_int(line.split()[-1])
        elif line.startswith("description "):
            cfg.description = line.removeprefix("description ").strip('"')
        elif line == "shutdown":
            cfg.shutdown = True
        elif line == "spanning-tree common-config portfast enable":
            cfg.stp_edged = True
        elif line == "ip dhcp snooping trust":
            cfg.dhcp_trusted = True
    if cfg.hybrid_tagged:
        cfg.link_type, cfg.trunk_allowed, cfg.trunk_pvid = (
            "trunk",
            set(cfg.hybrid_tagged),
            cfg.hybrid_pvid,
        )
    elif cfg.hybrid_untagged:
        cfg.link_type = "access"
        cfg.default_vlan = cfg.hybrid_pvid or min(cfg.hybrid_untagged)
    return cfg


# --- MAC / ARP / DHCP ------------------------------------------------------------------------
_MAC = re.compile(r"^([0-9a-fA-F:\-]{17})\s+(\d+)\s+(\S+)\s+(\w+)")


def parse_mac_table(text: str | None) -> list[MacEntry]:
    out: list[MacEntry] = []
    for line in clean_lines(text):
        if (match := _MAC.match(line.strip())) and (mac := safe_mac(match.group(1))):
            kind = match.group(4).lower()
            entry_type = {"config": "static", "filtering": "blackhole"}.get(kind, kind)
            out.append(MacEntry(mac, int(match.group(2)), _iface(match.group(3)), entry_type, None))
    return out


def parse_mac_aging(text: str | None) -> int | None:
    match = re.search(r"[Aa]ging[- ][Tt]ime\D*(\d+)", "\n".join(clean_lines(text)))
    return int(match.group(1)) if match else None


_ARP = re.compile(r"^(VLAN\s*\d+|\S+)\s+(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:\-]{17})\s+(\w+)", re.I)


def parse_arp(text: str | None) -> list[ArpEntry]:
    out: list[ArpEntry] = []
    for line in clean_lines(text):
        if (match := _ARP.match(line.strip())) and (mac := safe_mac(match.group(3))):
            iface = _iface(match.group(1))
            vlan = int(iface[4:]) if iface and iface.startswith("Vlan") else None
            out.append(
                ArpEntry(
                    ip=match.group(2),
                    mac=mac,
                    expire_minutes=None,
                    entry_type=match.group(4).lower(),
                    vlan=vlan,
                    interface=iface,
                    vpn=None,
                )
            )
    return out


_BINDING = re.compile(
    r"^\d+\s+\S+\s+(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:\-]{17})\s+(\d+)\s+(\S+)\s+\S+\s+(\S+)"
)


def parse_dhcp_snooping_bindings(text: str | None) -> list[DhcpBindingEntry]:
    """IP-MAC-VID-PORT binding table ('show ip source binding'), DHCP-snooping rows only."""
    out: list[DhcpBindingEntry] = []
    for line in clean_lines(text):
        match = _BINDING.match(line.strip())
        if not match or "snoop" not in match.group(5).lower():
            continue
        if mac := safe_mac(match.group(2)):
            out.append(
                DhcpBindingEntry(
                    ip=match.group(1),
                    mac=mac,
                    vlan=int(match.group(3)),
                    interface=_iface(match.group(4)),
                    lease_expire=None,
                    vsi=None,
                )
            )
    return out


# --- routing ---------------------------------------------------------------------------------
def parse_routing_table(text: str | None) -> list[RouteEntry]:
    """IOS-like route lines with 'VLANnnn' interface names."""
    normalized = re.sub(r"\bVLAN(\d+)\b", r"Vlan\1", text or "")
    return ios.parse_routing_table(normalized)


def parse_static_route_lines(text: str | None) -> set[str]:
    return {" ".join(line.split()) for line in clean_lines(text) if line.startswith("ip route ")}


def parse_ospf_peers(text: str | None) -> list[OspfNeighbor]:
    return ios.parse_ospf_peers(text)


def parse_bgp_peers(text: str | None) -> list[BgpPeer]:
    return []


# --- STP -------------------------------------------------------------------------------------
def parse_stp_global(text: str | None) -> StpGlobal:
    body = "\n".join(clean_lines(text))
    mode = _field(body, "Mode")
    bridge = _field(body, "Bridge ID")
    root = _field(body, "Root ID")
    cost = _field(body, "Root Path Cost")
    port = _field(body, "Root Port")
    is_root = bool(bridge and root and bridge == root)
    return StpGlobal(
        mode=mode.lower() if mode else None,
        bridge_id=bridge.replace("-", ".", 1) if bridge else None,
        root_id=root.replace("-", ".", 1) if root else None,
        root_path_cost=0 if is_root else to_int(cost),
        root_port=None if is_root or not port else _iface(port),
        tc_count=None,
        time_since_last_tc=None,
        bpdu_protection=None,
    )


_STP_PORT = re.compile(
    r"^(\S+)\s+(Enable|Disable)\s+\d+\s+\S+\s+\S+\s+(\w+)\s+(\w+)\s+(Yes|No)\s+(Yes|No)", re.I
)
_STATE = {
    "forwarding": "FORWARDING",
    "blocking": "DISCARDING",
    "discarding": "DISCARDING",
    "learning": "LEARNING",
    "listening": "LISTENING",
    "disabled": "DISABLED",
}
_ROLE = {
    "root": "ROOT",
    "designated": "DESI",
    "alternate": "ALTE",
    "backup": "BACK",
    "disabled": "DISA",
    "master": "MAST",
}


def parse_stp_brief(text: str | None) -> list[StpPortBrief]:
    out: list[StpPortBrief] = []
    for line in clean_lines(text):
        match = _STP_PORT.match(line.strip())
        iface = _iface(match.group(1)) if match else None
        if not match or not iface:
            continue
        out.append(
            StpPortBrief(
                0,
                iface,
                _ROLE.get(match.group(4).lower(), match.group(4).upper()),
                _STATE.get(match.group(3).lower(), match.group(3).upper()),
                "EDGE" if match.group(5).lower() == "yes" else "NONE",
            )
        )
    return out


# --- CPU / memory ----------------------------------------------------------------------------
def parse_cpu_usage(text: str | None) -> CpuUsage:
    match = re.search(r"^\s*1\s*\|\s*(\d+)%\s+(\d+)%\s+(\d+)%", "\n".join(clean_lines(text)), re.M)
    if not match:
        return CpuUsage(None, None, None, None)
    five_s, one_m, five_m = (float(g) for g in match.groups())
    return CpuUsage(five_s, five_s, one_m, five_m)


def parse_memory_usage(text: str | None) -> MemoryUsage:
    match = re.search(r"^\s*1\s*\|\s*(\d+)%", "\n".join(clean_lines(text)), re.M)
    return MemoryUsage(None, None, None, float(match.group(1)) if match else None)


# --- ACLs (from running-config lines, which match the command syntax exactly) -----------------
_CREATE = re.compile(r'^access-list create (\d+)(?: name "?([^"]+)"?)?$')
_RULE = re.compile(r"^access-list (?:ip|mac|combined|ipv6) (\S+) rule (\d+) (.+)$")


def parse_acls(text: str | None) -> list[ParsedAcl]:
    acls: dict[str, ParsedAcl] = {}
    names: dict[str, str] = {}
    for raw in clean_lines(text):
        line = " ".join(raw.split())
        if match := _CREATE.match(line):
            acl = ParsedAcl(kind="advanced", number=int(match.group(1)), name=match.group(2))
            acls[match.group(1)] = acl
            if match.group(2):
                names[match.group(2)] = match.group(1)
        elif match := _RULE.match(line):
            key = names.get(match.group(1), match.group(1))
            acl = acls.setdefault(key, ParsedAcl(kind="advanced", number=to_int(key), name=None))
            acl.rules[int(match.group(2))] = f"rule {match.group(2)} {match.group(3)}"
    return list(acls.values())


def management_acls(running_config: str | None) -> frozenset[str]:
    """ACLs bound to a VLAN interface ('access-list bind <id> interface vlan N ...'), which is
    how management VLAN access is filtered on these switches."""
    acls = {a.number: a for a in parse_acls(running_config) if a.number is not None}
    out: set[str] = set()
    for raw in clean_lines(running_config):
        if match := re.match(r"^access-list bind (\S+) interface vlan ", raw.strip()):
            ident = match.group(1)
            out.add(ident)
            if ident.isdigit() and (acl := acls.get(int(ident))) and acl.name:
                out.add(acl.name)
    return frozenset(out)
