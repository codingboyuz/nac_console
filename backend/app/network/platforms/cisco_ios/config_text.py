"""Cisco IOS-style configuration text: hashing, sanitizing, diffing, restore planning (pure).

Running configuration is top-level commands with one-space indented bodies; '!' separates
sections. Volatile lines ('Building configuration...', 'Current configuration : N bytes',
'! Last configuration change at ...', 'ntp clock-period') are excluded from hashing.
Shared by IOS / IOS-XE, NX-OS, Cisco SMB and TP-Link JetStream (same structure).
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field

from app.core.redaction import redact_text

_VOLATILE = re.compile(
    r"^(Building configuration\.\.\.|Current configuration\s*:|!\s*Last configuration change|"
    r"!\s*NVRAM config last updated|!\s*No configuration change since|ntp clock-period|"
    r"!Command: show running-config|!Running configuration last done at|!Time:|"
    r"!\s*Time:|System Name\s*:|!\s*$)"
)
_PROMPT = re.compile(r"^[\w.\-()/:]{1,63}[#>]")
# Never pushed back on restore: identity, structural keywords, crypto material.
_NOT_RESTORABLE = (
    "hostname ",
    "version ",
    "end",
    "crypto pki certificate",
    " certificate",
    "boot",
    "license",
    "!",
    "Building",
    "Current configuration",
)


def normalize(text: str) -> str:
    lines: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if _VOLATILE.match(line) or _PROMPT.match(line):
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
    out: dict[str, Section] = {}
    current: Section | None = None
    for line in normalize(text).split("\n"):
        if not line.strip() or line.strip() == "!":
            current = None
            continue
        if line.startswith(" "):
            if current is not None:
                current.body.append(line.strip())
            continue
        current = out.setdefault(line.strip(), Section(line.strip()))
    return out


@dataclass(frozen=True, slots=True)
class RestorePlan:
    lines: tuple[str, ...]
    notes: tuple[str, ...]


def restore_plan(running: str, backup: str) -> RestorePlan:
    """Best-effort merge restore: re-add missing/changed lines, 'no' running-only leaf lines.

    Sections present only on the device are reported, never removed blindly.
    """
    current, target = sections(running), sections(backup)
    lines: list[str] = []
    notes: list[str] = []
    for header, section in target.items():
        if header.startswith(_NOT_RESTORABLE):
            continue
        have = current.get(header)
        if have is None:
            lines += [header, *section.body]
            if section.body:
                lines.append("exit")
            continue
        missing = [b for b in section.body if b not in have.body]
        extra = [b for b in have.body if b not in section.body]
        if missing or extra:
            lines.append(header)
            lines += [f"no {b}" for b in extra if not b.startswith("no ")]
            lines += missing
            lines.append("exit")
    for header, section in current.items():
        if header in target or header.startswith(_NOT_RESTORABLE):
            continue
        if section.body:
            notes.append(f"section '{header}' exists only on the device; review manually")
        elif not header.startswith("no "):
            lines.append(f"no {header}")
    return RestorePlan(tuple(lines), tuple(notes))
