"""Parsers for ``display stp brief`` and the CIST global section of ``display stp``."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.network.parsers.common import clean_lines, safe_interface, to_int

_BRIEF_RE = re.compile(
    r"^\s*(?P<inst>\d+)\s+(?P<port>\S+)\s+(?P<role>[A-Za-z]+)\s+(?P<state>[A-Za-z]+)"
    r"\s+(?P<prot>\S+)\s*$"
)
_GLOBAL_START_RE = re.compile(r"\[\s*CIST Global Info\s*\]", re.IGNORECASE)
_MODE_TAG_RE = re.compile(r"\[\s*Mode\s+(?P<mode>[A-Za-z]+)\s*\]", re.IGNORECASE)
_SECTION_RE = re.compile(r"^\s*-+\s*\[")
_KV_RE = re.compile(r"^\s*(?P<key>[^:]+?)\s*:\s*(?P<val>.*?)\s*$")
_MODE_LINE_RE = re.compile(r"^\s*Mode\s+(?P<mode>[A-Za-z]+)\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class StpPortBrief:
    """One row of ``display stp brief``."""

    instance: int
    interface: str
    role: str
    state: str
    protection: str


@dataclass(frozen=True, slots=True)
class StpGlobal:
    """CIST global information from ``display stp``."""

    mode: str | None
    bridge_id: str | None
    root_id: str | None
    root_path_cost: int | None
    root_port: str | None
    tc_count: int | None
    time_since_last_tc: str | None
    bpdu_protection: bool | None


def parse_stp_brief(text: str | None) -> list[StpPortBrief]:
    """Parse ``display stp brief`` rows (header lines are skipped)."""
    rows: list[StpPortBrief] = []
    for line in clean_lines(text):
        match = _BRIEF_RE.match(line)
        if not match:
            continue
        port = safe_interface(match.group("port"))
        if port is None:
            continue
        rows.append(
            StpPortBrief(
                instance=int(match.group("inst")),
                interface=port,
                role=match.group("role").upper(),
                state=match.group("state").upper(),
                protection=match.group("prot").upper(),
            )
        )
    return rows


def _bridge(value: str) -> str | None:
    compact = "".join(value.split())
    return compact or None


def _global_lines(text: str | None) -> list[str]:
    lines = clean_lines(text)
    out: list[str] = []
    started = not any(_GLOBAL_START_RE.search(line) for line in lines)
    for line in lines:
        if _GLOBAL_START_RE.search(line):
            started = True
            out.append(line)
            continue
        if started and _SECTION_RE.match(line):
            break
        if started:
            out.append(line)
    return out


def parse_stp_global(text: str | None) -> StpGlobal:
    """Parse the CIST global section of ``display stp``; per-port sections are ignored."""
    values: dict[str, str] = {}
    mode: str | None = None
    for line in _global_lines(text):
        if tag := _MODE_TAG_RE.search(line):
            mode = tag.group("mode").upper()
            continue
        if mline := _MODE_LINE_RE.match(line):
            mode = mode or mline.group("mode").upper()
            continue
        if kv := _KV_RE.match(line):
            key = " ".join(kv.group("key").lower().split())
            values.setdefault(key, kv.group("val"))
    if mode is None and values.get("mode"):
        mode = values["mode"].split()[0].upper()

    root_id: str | None = None
    root_cost: int | None = None
    root_raw = values.get("cist root/erpc")
    if root_raw:
        bridge, _, cost = root_raw.partition("/")
        root_id = _bridge(bridge)
        root_cost = to_int(cost)
    bpdu_raw = values.get("bpdu-protection", "").lower()
    bpdu: bool | None = None
    if bpdu_raw.startswith("enable"):
        bpdu = True
    elif bpdu_raw.startswith("disable"):
        bpdu = False
    tc_raw = values.get("tc or tcn received")
    return StpGlobal(
        mode=mode,
        bridge_id=_bridge(values.get("cist bridge", "")),
        root_id=root_id,
        root_path_cost=root_cost,
        root_port=values.get("cist rootportid") or None,
        tc_count=to_int(tc_raw.split()[0]) if tc_raw else None,
        time_since_last_tc=values.get("time since last tc") or None,
        bpdu_protection=bpdu,
    )
