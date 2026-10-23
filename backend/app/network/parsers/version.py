"""Parser for ``display version`` (S-series VRP5, CE VRP8, AR VRP5)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.devices import DeviceFamily
from app.network.parsers.common import clean_output

_VRP_RE = re.compile(
    r"VRP\s*\(R\)\s*software,\s*Version\s+(?P<vrp>\d+\.\d+)(?:\s*\((?P<inner>[^)]*)\))?",
    re.IGNORECASE,
)
_SOFTWARE_RE = re.compile(r"\bV\d{3}R\d{3}(?:C\d+)?(?:SPC\d+|SPH\d+|B\d+)*", re.IGNORECASE)
_MODEL_UPTIME_RE = re.compile(
    r"^\s*HUAWEI\s+(?P<model>[A-Za-z0-9][\w\-+]*)\b.*?\buptime\s+is\s+(?P<uptime>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_UPTIME_PART_RE = re.compile(r"(\d+)\s*(week|day|hour|minute|second)s?", re.IGNORECASE)
_UNIT_SECONDS = {"week": 604800, "day": 86400, "hour": 3600, "minute": 60, "second": 1}


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """Device identity extracted from ``display version``."""

    family: DeviceFamily
    vrp_version: str | None
    software_version: str | None
    model: str | None
    uptime: str | None
    uptime_seconds: int | None


def detect_family(model: str | None, vrp_version: str | None = None) -> DeviceFamily:
    """Map a model string (and VRP major version as fallback) to a :class:`DeviceFamily`."""
    upper = (model or "").upper()
    if upper.startswith("CE"):
        return DeviceFamily.CE
    if upper.startswith("AR"):
        return DeviceFamily.AR
    if re.match(r"^S\d", upper):
        return DeviceFamily.S
    if vrp_version and vrp_version.split(".")[0] == "8":
        return DeviceFamily.CE
    return DeviceFamily.UNKNOWN


def uptime_to_seconds(uptime: str | None) -> int | None:
    """Convert ``12 weeks, 3 days, 4 hours, 25 minutes`` to seconds (``None`` if no parts)."""
    if not uptime:
        return None
    parts = _UPTIME_PART_RE.findall(uptime)
    if not parts:
        return None
    return sum(int(num) * _UNIT_SECONDS[unit.lower()] for num, unit in parts)


def parse_version(text: str | None) -> VersionInfo:
    """Parse ``display version`` output into a :class:`VersionInfo`."""
    cleaned = clean_output(text)
    vrp_version: str | None = None
    software_version: str | None = None
    paren_model: str | None = None
    vrp_match = _VRP_RE.search(cleaned)
    if vrp_match:
        vrp_version = vrp_match.group("vrp")
        inner = vrp_match.group("inner") or ""
        soft = _SOFTWARE_RE.search(inner)
        if soft:
            software_version = soft.group(0).upper()
        head = inner.split()[0] if inner.split() else ""
        if head and not _SOFTWARE_RE.fullmatch(head):
            paren_model = head
    if software_version is None:
        soft = _SOFTWARE_RE.search(cleaned)
        software_version = soft.group(0).upper() if soft else None

    model: str | None = None
    uptime: str | None = None
    mu = _MODEL_UPTIME_RE.search(cleaned)
    if mu:
        model = mu.group("model")
        uptime = mu.group("uptime")
    model = model or paren_model
    return VersionInfo(
        family=detect_family(model, vrp_version),
        vrp_version=vrp_version,
        software_version=software_version,
        model=model,
        uptime=uptime,
        uptime_seconds=uptime_to_seconds(uptime),
    )
