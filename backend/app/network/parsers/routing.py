"""Parser for ``display ip routing-table`` (VRP5 and VRP8 share the column layout)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.network.parsers.common import IPV4_PATTERN, clean_lines, safe_interface

_ROW_RE = re.compile(
    rf"^\s*(?:(?P<prefix>{IPV4_PATTERN}/\d{{1,2}})\s+)?(?P<proto>[A-Za-z][\w-]*)\s+"
    rf"(?P<pre>\d+)\s+(?P<cost>\d+)\s+(?:(?P<flags>[A-Za-z]+)\s+)?(?P<nh>{IPV4_PATTERN})"
    r"(?:\s+(?P<intf>\S+))?\s*$"
)


@dataclass(frozen=True, slots=True)
class RouteEntry:
    """One next-hop of a route; ECMP routes produce one entry per next-hop."""

    prefix: str
    protocol: str
    preference: int
    cost: int
    flags: str
    nexthop: str
    interface: str | None


def parse_routing_table(text: str | None) -> list[RouteEntry]:
    """Parse ``display ip routing-table``; continuation rows inherit the previous prefix."""
    routes: list[RouteEntry] = []
    last_prefix: str | None = None
    for line in clean_lines(text):
        match = _ROW_RE.match(line)
        if not match:
            continue
        prefix = match.group("prefix") or last_prefix
        if prefix is None:
            continue
        last_prefix = prefix
        routes.append(
            RouteEntry(
                prefix=prefix,
                protocol=match.group("proto"),
                preference=int(match.group("pre")),
                cost=int(match.group("cost")),
                flags=match.group("flags") or "",
                nexthop=match.group("nh"),
                interface=safe_interface(match.group("intf")),
            )
        )
    return routes
