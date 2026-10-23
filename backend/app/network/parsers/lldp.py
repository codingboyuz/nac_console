"""Parser for ``display lldp neighbor brief`` (column order differs between VRP5 and VRP8)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.interfaces import InvalidInterfaceName, normalize_interface
from app.network.parsers.common import clean_lines, safe_interface, to_int

_COLUMNS: dict[str, re.Pattern[str]] = {
    "local": re.compile(r"Local\s+Int\w*", re.IGNORECASE),
    "device": re.compile(r"Neighbor\s+Dev\w*", re.IGNORECASE),
    "remote": re.compile(r"Neighbor\s+Int\w*", re.IGNORECASE),
    "exptime": re.compile(r"Exptime", re.IGNORECASE),
}


@dataclass(frozen=True, slots=True)
class LldpNeighbor:
    """One LLDP neighbor seen on a local port."""

    local_interface: str
    neighbor_device: str
    neighbor_interface: str
    exptime: int | None


def _header_columns(line: str) -> list[tuple[int, str]] | None:
    found: list[tuple[int, str]] = []
    for key, pattern in _COLUMNS.items():
        match = pattern.search(line)
        if match is None:
            return None
        found.append((match.start(), key))
    return sorted(found)


def _split_row(line: str, columns: list[tuple[int, str]]) -> dict[str, str]:
    tokens = line.split()
    if len(tokens) == len(columns):
        return {key: tok for (_, key), tok in zip(columns, tokens, strict=True)}
    cells: dict[str, str] = {}
    for idx, (start, key) in enumerate(columns):
        end = columns[idx + 1][0] if idx + 1 < len(columns) else len(line)
        cells[key] = line[start:end].strip()
    return cells


def parse_lldp_neighbors_brief(text: str | None) -> list[LldpNeighbor]:
    """Parse ``display lldp neighbor brief`` using the header to locate columns."""
    neighbors: list[LldpNeighbor] = []
    columns: list[tuple[int, str]] | None = None
    for line in clean_lines(text):
        if not line.strip() or set(line.strip()) <= {"-"}:
            continue
        header = _header_columns(line)
        if header is not None:
            columns = header
            continue
        if columns is None:
            continue
        cells = _split_row(line, columns)
        try:
            local = normalize_interface(cells.get("local", ""))
        except InvalidInterfaceName:
            continue
        device = cells.get("device", "").strip()
        remote = cells.get("remote", "").strip()
        if not device or not remote:
            continue
        neighbors.append(
            LldpNeighbor(
                local_interface=local,
                neighbor_device=device,
                neighbor_interface=safe_interface(remote) or remote,
                exptime=to_int(cells.get("exptime")),
            )
        )
    return neighbors
