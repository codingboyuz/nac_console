"""Parsers for routing protocol neighbor tables and static route configuration lines.

``display ospf peer brief``: rows "  0.0.0.0     Vlanif10    192.0.2.2     Full".
``display bgp peer``: rows "  192.0.2.2   4   65002   120   118   0 01:02:03 Established   5".
VRP-VERSION-DEPENDENT column layouts; only the stable leading/trailing columns are used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.network.parsers.common import IPV4_PATTERN, clean_lines, safe_interface

_OSPF = re.compile(
    rf"^\s*(?P<area>{IPV4_PATTERN})\s+(?P<iface>\S+)\s+(?P<rid>{IPV4_PATTERN})\s+(?P<state>\S+)"
)
_BGP = re.compile(
    rf"^\s*(?P<peer>{IPV4_PATTERN})\s+(?P<v>\d)\s+(?P<asn>\d+)\s+\d+\s+\d+\s+\d+\s+(?P<up>\S+)\s+(?P<state>\S+)"
)
_STATIC = re.compile(r"^ip route-static .+$")


@dataclass(frozen=True, slots=True)
class OspfNeighbor:
    area: str
    interface: str | None
    router_id: str
    state: str

    @property
    def is_full(self) -> bool:
        return self.state.lower().startswith("full")


@dataclass(frozen=True, slots=True)
class BgpPeer:
    peer: str
    remote_as: int
    up_down: str
    state: str

    @property
    def established(self) -> bool:
        return self.state.lower() == "established"


def parse_ospf_peers(text: str | None) -> list[OspfNeighbor]:
    out = []
    for line in clean_lines(text):
        if match := _OSPF.match(line):
            out.append(
                OspfNeighbor(
                    match["area"], safe_interface(match["iface"]), match["rid"], match["state"]
                )
            )
    return out


def parse_bgp_peers(text: str | None) -> list[BgpPeer]:
    out = []
    for line in clean_lines(text):
        if match := _BGP.match(line):
            out.append(BgpPeer(match["peer"], int(match["asn"]), match["up"], match["state"]))
    return out


def parse_static_route_lines(text: str | None) -> set[str]:
    """Return 'ip route-static ...' lines from running configuration, whitespace-normalized."""
    return {" ".join(line.split()) for line in clean_lines(text) if _STATIC.match(line.strip())}
