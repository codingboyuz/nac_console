"""Parsers for ``display interface [brief]`` and ``display ip interface brief``.

Handles both the S-series (VRP5, V200R019) and CloudEngine (VRP8, V200R005) layouts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.network.parsers.common import (
    IPV4_PATTERN,
    clean_lines,
    none_if_dash,
    safe_interface,
    safe_mac,
    strip_state,
    to_float,
    to_int,
)

_STATE_CELL = r"[*^]?(?:\([a-zA-Z]+\))*(?:up|down)(?:\([a-zA-Z]+\))*"
_BRIEF_RE = re.compile(
    rf"^\s*(?P<name>\S+)\s+(?P<phy>{_STATE_CELL})\s+(?P<proto>{_STATE_CELL})"
    r"(?:\s+(?P<inu>\S+)\s+(?P<outu>\S+)(?:\s+(?P<ine>\d+)\s+(?P<oute>\d+))?)?\s*$",
    re.IGNORECASE,
)
_IP_BRIEF_RE = re.compile(
    rf"^\s*(?P<name>\S+)\s+(?P<addr>unassigned|{IPV4_PATTERN}(?:/\d{{1,2}})?)\s+"
    rf"(?P<phy>{_STATE_CELL})\s+(?P<proto>{_STATE_CELL})(?:\s+(?P<vpn>\S+))?\s*$",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(
    r"^\s*(?P<name>\S+)\s+current state\s*:\s*(?P<state>.*?)\s*(?:\(ifindex.*)?$"
)
_PROTO_RE = re.compile(r"^\s*Line protocol current state\s*:\s*(?P<state>\S+)", re.IGNORECASE)
_DESC_RE = re.compile(r"^\s*Description\s*:\s*(?P<desc>.*)$", re.IGNORECASE)
_HWADDR_RE = re.compile(r"Hardware address is\s+(?P<mac>[0-9A-Fa-f.:\-]+)", re.IGNORECASE)
_MTU_RE = re.compile(r"Maximum Transmit Unit is\s+(?P<mtu>\d+)", re.IGNORECASE)
_DUPLEX_RE = re.compile(r"\bDuplex\s*:\s*(?P<duplex>[A-Za-z]+)", re.IGNORECASE)
_LINKTYPE_RE = re.compile(r"link-type\s*:\s*(?P<lt>[A-Za-z\-]+)", re.IGNORECASE)
_PORTKIND_RE = re.compile(r"^\s*(?P<kind>Switch|Route) Port\b", re.IGNORECASE)
_RATE_RE = re.compile(
    r"Last\s+\d+\s+seconds\s+(?P<dir>input|output)\s+rate\s*:?\s*(?P<bps>\d+)\s*bits/sec",
    re.IGNORECASE,
)
_TOTALS_RE = re.compile(
    r"^\s*(?P<dir>Input|Output)\s*:\s*(?P<n1>\d+)\s+(?P<u1>packets|bytes)\s*,\s*"
    r"(?P<n2>\d+)\s+(?P<u2>packets|bytes)",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(r"^\s*(?P<dir>Input|Output)\s*:\s*$", re.IGNORECASE)
_PAIR_RE = re.compile(r"^\s*(?P<key>[A-Za-z][A-Za-z \-]*?)\s*:\s*(?P<val>\d+)(?:\s*packets)?\s*$")


@dataclass(frozen=True, slots=True)
class InterfaceBrief:
    """One row of ``display interface brief``."""

    name: str
    phy: str
    protocol: str
    in_util: float | None
    out_util: float | None
    in_errors: int | None
    out_errors: int | None
    admin_down: bool


@dataclass(frozen=True, slots=True)
class InterfaceDetail:
    """One interface block of ``display interface``."""

    name: str
    admin_up: bool
    oper_up: bool
    description: str | None
    speed_mbps: int | None
    duplex: str | None
    mtu: int | None
    mac: str | None
    pvid: int | None
    link_type: str | None
    input_packets: int | None
    input_bytes: int | None
    output_packets: int | None
    output_bytes: int | None
    crc_errors: int | None
    input_errors: int | None
    output_errors: int | None
    input_discards: int | None
    output_discards: int | None
    input_rate_bps: int | None
    output_rate_bps: int | None


@dataclass(frozen=True, slots=True)
class IpInterfaceBrief:
    """One row of ``display ip interface brief``."""

    name: str
    ip: str | None
    prefix_len: int | None
    phy: str
    protocol: str
    vpn: str | None


def parse_interface_brief(text: str | None) -> list[InterfaceBrief]:
    """Parse ``display interface brief`` rows.

    Names are canonicalized; Eth-Trunk member rows that repeat an interface are de-duplicated.
    """
    rows: list[InterfaceBrief] = []
    seen: set[str] = set()
    for line in clean_lines(text):
        match = _BRIEF_RE.match(line)
        if not match:
            continue
        name = safe_interface(match.group("name"))
        if name is None or name in seen:
            continue
        seen.add(name)
        phy_raw = match.group("phy")
        rows.append(
            InterfaceBrief(
                name=name,
                phy=strip_state(phy_raw),
                protocol=strip_state(match.group("proto")),
                in_util=to_float(match.group("inu")),
                out_util=to_float(match.group("outu")),
                in_errors=to_int(match.group("ine")),
                out_errors=to_int(match.group("oute")),
                admin_down=phy_raw.startswith("*"),
            )
        )
    return rows


def parse_ip_interface_brief(text: str | None) -> list[IpInterfaceBrief]:
    """Parse ``display ip interface brief`` rows; ``unassigned`` yields ``ip=None``."""
    rows: list[IpInterfaceBrief] = []
    for line in clean_lines(text):
        match = _IP_BRIEF_RE.match(line)
        if not match:
            continue
        addr = match.group("addr")
        ip: str | None = None
        prefix_len: int | None = None
        if addr.lower() != "unassigned":
            ip, _, plen = addr.partition("/")
            prefix_len = to_int(plen) if plen else None
        rows.append(
            IpInterfaceBrief(
                name=safe_interface(match.group("name")) or match.group("name"),
                ip=ip,
                prefix_len=prefix_len,
                phy=strip_state(match.group("phy")),
                protocol=strip_state(match.group("proto")),
                vpn=none_if_dash(match.group("vpn")),
            )
        )
    return rows


@dataclass(slots=True)
class _Block:
    name: str
    state: str
    protocol: str | None = None
    fields: dict[str, object] = field(default_factory=dict)
    section: str | None = None

    def set_once(self, key: str, value: object) -> None:
        if value is not None and key not in self.fields:
            self.fields[key] = value

    def build(self) -> InterfaceDetail:
        state = self.state.upper()
        admin_up = "ADMINISTRATIVELY" not in state
        oper_up = state.startswith("UP") and (self.protocol is None or self.protocol == "UP")
        f = self.fields

        def num(key: str) -> int | None:
            value = f.get(key)
            return value if isinstance(value, int) else None

        def text(key: str) -> str | None:
            value = f.get(key)
            return value if isinstance(value, str) else None

        return InterfaceDetail(
            name=self.name,
            admin_up=admin_up,
            oper_up=oper_up,
            description=text("description"),
            speed_mbps=num("speed"),
            duplex=text("duplex"),
            mtu=num("mtu"),
            mac=text("mac"),
            pvid=num("pvid"),
            link_type=text("link_type"),
            input_packets=num("input_packets"),
            input_bytes=num("input_bytes"),
            output_packets=num("output_packets"),
            output_bytes=num("output_bytes"),
            crc_errors=num("crc"),
            input_errors=num("input_errors"),
            output_errors=num("output_errors"),
            input_discards=num("input_discards"),
            output_discards=num("output_discards"),
            input_rate_bps=num("input_rate"),
            output_rate_bps=num("output_rate"),
        )


_SECTION_KEYS = {
    "error": "errors",
    "errors": "errors",
    "total error": "errors",
    "discard": "discards",
    "discards": "discards",
}


def _apply_pairs(block: _Block, line: str) -> None:
    for chunk in line.split(","):
        pair = _PAIR_RE.match(chunk)
        if not pair:
            continue
        key = " ".join(pair.group("key").lower().split())
        value = int(pair.group("val"))
        if key == "pvid":
            block.set_once("pvid", value)
        elif key == "speed":
            block.set_once("speed", value)
        elif key == "crc":
            block.set_once("crc", value)
        else:
            direction = block.section
            for prefix in ("input ", "output "):
                if key.startswith(prefix):
                    direction, key = prefix.strip(), key[len(prefix) :]
            kind = _SECTION_KEYS.get(key)
            if kind and direction:
                block.set_once(f"{direction}_{kind}", value)


def _apply_line(block: _Block, line: str) -> None:
    if proto := _PROTO_RE.match(line):
        block.protocol = proto.group("state").upper()
        return
    if desc := _DESC_RE.match(line):
        block.set_once("description", desc.group("desc").strip() or None)
        return
    if section := _SECTION_RE.match(line):
        block.section = section.group("dir").lower()
        return
    if totals := _TOTALS_RE.match(line):
        direction = totals.group("dir").lower()
        block.section = direction
        for num, unit in (("n1", "u1"), ("n2", "u2")):
            block.set_once(f"{direction}_{totals.group(unit).lower()}", int(totals.group(num)))
        return
    if rate := _RATE_RE.search(line):
        block.set_once(f"{rate.group('dir').lower()}_rate", int(rate.group("bps")))
        return
    if hw := _HWADDR_RE.search(line):
        block.set_once("mac", safe_mac(hw.group("mac")))
    if mtu := _MTU_RE.search(line):
        block.set_once("mtu", int(mtu.group("mtu")))
    if duplex := _DUPLEX_RE.search(line):
        block.set_once("duplex", duplex.group("duplex").lower())
    if link := _LINKTYPE_RE.search(line):
        block.fields["link_type"] = link.group("lt").lower()
    elif kind := _PORTKIND_RE.match(line):
        block.set_once("link_type", kind.group("kind").lower())
    _apply_pairs(block, line)


def parse_interface_detail(text: str | None) -> list[InterfaceDetail]:
    """Parse the per-interface blocks of ``display interface``."""
    blocks: list[_Block] = []
    current: _Block | None = None
    for line in clean_lines(text):
        header = _HEADER_RE.match(line)
        if header and not line.lower().lstrip().startswith("line protocol"):
            name = safe_interface(header.group("name")) or header.group("name")
            current = _Block(name=name, state=header.group("state"))
            blocks.append(current)
            continue
        if current is not None and line.strip():
            _apply_line(current, line)
    return [block.build() for block in blocks]
