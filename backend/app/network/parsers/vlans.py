"""Parser for ``display vlan`` (S-series and CloudEngine layouts)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.network.parsers.common import clean_lines, safe_interface

_PORT_ROW_RE = re.compile(r"^\s*[*#]?(?P<vid>\d{1,4})\s+(?P<type>[a-z][\w-]*)\b(?P<rest>.*)$")
_PROP_ROW_RE = re.compile(
    r"^\s*[*#]?(?P<vid>\d{1,4})\s+(?P<status>enable|disable)\b(?P<rest>.*)$", re.IGNORECASE
)
_CONT_RE = re.compile(r"^\s+(?P<rest>\S.*)$")
_PORT_TOKEN_RE = re.compile(r"^(?:(?P<tag>UT|TG|MP|ST):)?(?P<port>[^()\s]+)(?:\([A-Za-z]+\))?$")
_PORTS_HEADER_RE = re.compile(r"^\s*VID\s+(?:Type\s+)?Ports\b", re.IGNORECASE)
_PROPS_HEADER_RE = re.compile(r"^\s*VID\s+Status\s+Property\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class VlanInfo:
    """A VLAN with its member ports and properties."""

    vlan_id: int
    name: str | None
    status: str | None
    vlan_type: str | None
    tagged_ports: tuple[str, ...]
    untagged_ports: tuple[str, ...]


@dataclass(slots=True)
class _Vlan:
    vlan_id: int
    vlan_type: str | None = None
    status: str | None = None
    name: str | None = None
    tagged: list[str] = field(default_factory=list)
    untagged: list[str] = field(default_factory=list)
    mode: str | None = None

    def add_ports(self, rest: str) -> None:
        for token in rest.split():
            match = _PORT_TOKEN_RE.match(token)
            if not match:
                continue
            if match.group("tag"):
                self.mode = match.group("tag")
            port = safe_interface(match.group("port"))
            if port is None:
                continue
            if self.mode == "UT" and port not in self.untagged:
                self.untagged.append(port)
            elif self.mode in {"TG", "MP", "ST"} and port not in self.tagged:
                self.tagged.append(port)

    def freeze(self) -> VlanInfo:
        return VlanInfo(
            vlan_id=self.vlan_id,
            name=self.name,
            status=self.status,
            vlan_type=self.vlan_type,
            tagged_ports=tuple(self.tagged),
            untagged_ports=tuple(self.untagged),
        )


def parse_vlans(text: str | None) -> list[VlanInfo]:
    """Parse ``display vlan``: the ports table and the status/description table are merged."""
    vlans: dict[int, _Vlan] = {}
    mode: str | None = None
    desc_col: int | None = None
    current: _Vlan | None = None

    def get(vid: int) -> _Vlan:
        return vlans.setdefault(vid, _Vlan(vlan_id=vid))

    for line in clean_lines(text):
        if _PROPS_HEADER_RE.match(line):
            mode, current = "props", None
            idx = line.find("Description")
            desc_col = idx if idx >= 0 else None
            continue
        if _PORTS_HEADER_RE.match(line):
            mode, current = "ports", None
            continue
        if not line.strip() or set(line.strip()) <= {"-"}:
            continue
        if mode == "ports":
            row = _PORT_ROW_RE.match(line)
            if row and 1 <= int(row.group("vid")) <= 4094:
                current = get(int(row.group("vid")))
                current.vlan_type = row.group("type")
                current.mode = None
                current.add_ports(row.group("rest"))
            elif current is not None and (cont := _CONT_RE.match(line)):
                current.add_ports(cont.group("rest"))
        elif mode == "props":
            prop = _PROP_ROW_RE.match(line)
            if not prop or not 1 <= int(prop.group("vid")) <= 4094:
                continue
            vlan = get(int(prop.group("vid")))
            vlan.status = prop.group("status").lower()
            if desc_col is not None and len(line) > desc_col:
                vlan.name = line[desc_col:].strip() or None
            else:
                parts = prop.group("rest").split(None, 3)
                vlan.name = parts[3].strip() if len(parts) == 4 else None
    return [vlans[vid].freeze() for vid in sorted(vlans)]
