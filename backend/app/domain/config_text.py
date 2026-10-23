"""Huawei configuration text helpers: hashing, sanitizing, diffing, restore planning (pure).

VRP prints configuration as top-level commands (column 0) with view bodies indented by one
space, sections separated by '#'. Volatile header lines ("!Last configuration was updated at",
"!Time:") are excluded from hashing so unchanged configs deduplicate.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field

from app.core.redaction import redact_text
from app.domain.vlans import parse_vlan_list, to_huawei_ranges

_VOLATILE_PREFIXES = ("!Last configuration was", "!Time:", "!Software Version", "!Config-version")
_NOT_RESTORABLE = ("sysname ", "return", "!")


def normalize(text: str) -> str:
    lines = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        is_prompt = line.startswith(("<", "[")) and line.endswith((">", "]"))
        if line.startswith(_VOLATILE_PREFIXES) or is_prompt:
            continue
        lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines) + "\n"


def config_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def sanitize(text: str) -> str:
    """Mask cipher strings, communities, keys and passwords (stored/displayed copy)."""
    return redact_text(normalize(text))


def unified_diff(
    old: str, new: str, *, old_name: str = "a", new_name: str = "b", context: int = 3
) -> str:
    return "".join(
        difflib.unified_diff(
            normalize(old).splitlines(keepends=True),
            normalize(new).splitlines(keepends=True),
            fromfile=old_name,
            tofile=new_name,
            n=context,
        )
    )


@dataclass(slots=True)
class Section:
    header: str
    body: list[str] = field(default_factory=list)


def sections(text: str) -> dict[str, Section]:
    """Group top-level commands with their indented body; key = header line."""
    out: dict[str, Section] = {}
    current: Section | None = None
    for line in normalize(text).split("\n"):
        if not line.strip() or line.strip() == "#":
            current = None
            continue
        if line.startswith(" "):
            if current is not None:
                current.body.append(line.strip())
            continue
        current = Section(line.strip())
        out.setdefault(current.header, current)
        current = out[current.header]
    return out


@dataclass(frozen=True, slots=True)
class RestorePlan:
    lines: tuple[str, ...]
    notes: tuple[str, ...]


def restore_plan(running: str, backup: str) -> RestorePlan:  # noqa: PLR0912 - linear merge
    """Best-effort merge restore (VRP-VERSION-DEPENDENT): re-add missing/changed lines from the
    backup and 'undo' leaf lines present only in running. Never touches sysname/headers."""
    current, target = sections(running), sections(backup)
    lines: list[str] = []
    notes: list[str] = []
    # 'vlan batch' is a set, not a leaf: restore it as a set difference (never 'undo' the
    # whole running batch line, which would delete every VLAN).
    have_vlans, want_vlans = _batch_vlans(current), _batch_vlans(target)
    for header in [h for h in current if h.startswith("vlan batch ")]:
        current.pop(header)
    for header in [h for h in target if h.startswith("vlan batch ")]:
        target.pop(header)
    if want_vlans - have_vlans:
        lines.append(f"vlan batch {to_huawei_ranges(sorted(want_vlans - have_vlans))}")
    remove_vlans = sorted((have_vlans - want_vlans) - {1})  # removed last, after port changes
    for header, section in target.items():
        if header.startswith(_NOT_RESTORABLE):
            continue
        have = current.get(header)
        if have is None:
            lines += [header, *section.body]
            if section.body:
                lines.append("quit")
            continue
        missing = [b for b in section.body if b not in have.body]
        extra = [b for b in have.body if b not in section.body]
        if missing or extra:
            lines.append(header)
            lines += [f"undo {b}" for b in extra if not b.startswith("undo ")]
            lines += missing
            lines.append("quit")
    for header in current:
        if header not in target and not header.startswith(_NOT_RESTORABLE):
            if current[header].body:
                notes.append(f"section '{header}' exists only on the device; review manually")
            else:
                lines.append(f"undo {header}")
    if remove_vlans:
        lines.append(f"undo vlan batch {to_huawei_ranges(remove_vlans)}")
    return RestorePlan(tuple(lines), tuple(notes))


def _batch_vlans(secs: dict[str, Section]) -> set[int]:
    vlans: set[int] = set()
    for header in secs:
        if header.startswith("vlan batch "):
            vlans |= set(parse_vlan_list(header.removeprefix("vlan batch "), strict=False))
    return vlans
