"""Cisco SMB / TP-Link configuration text: block structure ends with 'exit' (bodies may be
unindented); the 'config-file-header ... ssd-control-end' preamble is excluded from hashing.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field

from app.core.redaction import redact_text

_PROMPT = re.compile(r"^[\w.\-()/:]{1,63}[#>]")
_BLOCK_START = re.compile(
    r"^(interface |vlan database|management access-list|ip access-list|mac access-list|"
    r"router |line |spanning-tree mst configuration|aaa group|policy-map|class-map)"
)
_VOLATILE = re.compile(r"^(!\s*$|#\s*$|Building configuration|Current configuration|!System )")
_NOT_RESTORABLE = ("hostname ", "username ", "end", "config-file-header", "ssd")


def normalize(text: str) -> str:
    out: list[str] = []
    skip_until: str | None = None  # header ends at '@', the SSD block at 'ssd-control-end'
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if skip_until is not None:
            if line.startswith(skip_until):
                skip_until = None
            continue
        if line.startswith("config-file-header"):
            skip_until = "@"
            continue
        if line.startswith("ssd-control-start"):
            skip_until = "ssd-control-end"
            continue
        if _PROMPT.match(line) or _VOLATILE.match(line):
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    while out and not out[0].strip():
        out.pop(0)
    return "\n".join(out) + "\n"


def config_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def sanitize(text: str) -> str:
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
    block: bool = False


def sections(text: str) -> dict[str, Section]:
    out: dict[str, Section] = {}
    current: Section | None = None
    for raw in normalize(text).split("\n"):
        line = raw.strip()
        if not line:
            continue
        if current is not None:
            if line == "exit":
                current = None
            else:
                current.body.append(line)
            continue
        if _BLOCK_START.match(line):
            current = out.setdefault(line, Section(line, block=True))
            continue
        out.setdefault(line, Section(line))
    return out


@dataclass(frozen=True, slots=True)
class RestorePlan:
    lines: tuple[str, ...]
    notes: tuple[str, ...]


def restore_plan(running: str, backup: str) -> RestorePlan:
    current, target = sections(running), sections(backup)
    lines: list[str] = []
    notes: list[str] = []
    for header, section in target.items():
        if header.startswith(_NOT_RESTORABLE):
            continue
        have = current.get(header)
        if have is None:
            lines += [header, *section.body] + (["exit"] if section.block else [])
            continue
        missing = [b for b in section.body if b not in have.body]
        extra = [b for b in have.body if b not in section.body]
        if missing or extra:
            lines += [
                header,
                *[f"no {b}" for b in extra if not b.startswith("no ")],
                *missing,
                "exit",
            ]
    for header, section in current.items():
        if header in target or header.startswith(_NOT_RESTORABLE):
            continue
        if section.block:
            notes.append(f"block '{header}' exists only on the device; review manually")
        elif not header.startswith("no "):
            lines.append(f"no {header}")
    return RestorePlan(tuple(lines), tuple(notes))
