"""Shared helpers for Huawei VRP ``display`` output parsers.

Every parser first runs :func:`clean_output`, which removes the noise a CLI session leaves in
captured text (ANSI cursor sequences, ``---- More ----`` paging prompts, command echo and the
trailing ``<SW-01>`` / ``[~CE-01]`` prompt). The remaining helpers are tolerant converters that
return ``None`` instead of raising, because parsers must never fail on unexpected lines.
"""

from __future__ import annotations

import re

from app.domain.interfaces import InvalidInterfaceName, normalize_interface
from app.domain.mac import InvalidMac, normalize_mac

# "  ---- More ----" followed by the erase sequence ESC[42D <spaces> ESC[42D.
_ERASE_RE = re.compile(r"\x1b\[\d*D[ ]*\x1b\[\d*D")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]")
_MORE_RE = re.compile(r"[ ]*-{2,}[ ]*More[ ]*-{2,}", re.IGNORECASE)
_PROMPT_RE = re.compile(r"^\s*(?:<[\w.\-/:@]+>|\[[~*]?[\w.\-/:@]+\])")
_ECHO_RE = re.compile(r"^\s*dis(?:p|pl|pla|play)?\s+\S", re.IGNORECASE)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ERROR_RE = re.compile(r"^\s*Error\s*:\s*(?P<msg>.+?)\s*$", re.MULTILINE)
_STATE_RE = re.compile(r"(up|down)", re.IGNORECASE)
_SIZE_UNITS: dict[str, int] = {
    "": 1,
    "b": 1,
    "byte": 1,
    "bytes": 1,
    "k": 1024,
    "kb": 1024,
    "kbyte": 1024,
    "kbytes": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "mbyte": 1024**2,
    "mbytes": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "gbyte": 1024**3,
    "gbytes": 1024**3,
}

IPV4_PATTERN = r"\d{1,3}(?:\.\d{1,3}){3}"
MAC_PATTERN = r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"


def clean_output(text: str | None) -> str:
    """Strip paging residue, ANSI sequences, command echo and prompt lines from CLI output."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ERASE_RE.sub("", text)
    text = _ANSI_RE.sub("", text)
    text = _MORE_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    kept = [
        line.rstrip()
        for line in text.split("\n")
        if not _PROMPT_RE.match(line) and not _ECHO_RE.match(line)
    ]
    return "\n".join(kept).strip("\n")


def clean_lines(text: str | None) -> list[str]:
    """Return the cleaned output split into lines (blank lines preserved)."""
    cleaned = clean_output(text)
    return cleaned.split("\n") if cleaned else []


def find_cli_error(text: str | None) -> str | None:
    """Return the message of a VRP ``Error: ...`` response, or ``None`` if there is none."""
    match = _ERROR_RE.search(clean_output(text))
    return match.group("msg") if match else None


def to_int(value: str | None) -> int | None:
    """Parse an integer, tolerating thousands separators; ``None`` for ``--``/garbage."""
    if value is None:
        return None
    raw = value.strip().replace(",", "")
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return None


def to_float(value: str | None) -> float | None:
    """Parse a float, tolerating a trailing ``%``; ``None`` for ``--``/garbage."""
    if value is None:
        return None
    raw = value.strip().rstrip("%").strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        return float(raw)
    return None


def to_bytes(value: str | None, unit: str | None = None) -> int | None:
    """Convert ``value`` expressed in ``unit`` (bytes, Kbytes, MB, ...) to bytes (1024 base)."""
    number = to_float(value)
    if number is None:
        return None
    factor = _SIZE_UNITS.get((unit or "").strip().lower())
    if factor is None:
        return None
    return int(number * factor)


def safe_interface(name: str | None) -> str | None:
    """Canonical interface name, the stripped raw name if unknown, ``None`` for ``-``/empty."""
    if name is None:
        return None
    raw = name.strip()
    if not raw or raw in {"-", "--", "N/A"}:
        return None
    try:
        return normalize_interface(raw)
    except InvalidInterfaceName:
        return raw


def safe_mac(value: str | None) -> str | None:
    """Canonical ``aa:bb:cc:dd:ee:ff`` MAC or ``None`` when the value is not a MAC."""
    if not value:
        return None
    try:
        return normalize_mac(value)
    except InvalidMac:
        return None


def strip_state(value: str) -> str:
    """Reduce a VRP state cell (``*down``, ``^down``, ``(l)up``, ``up(s)``) to ``up``/``down``."""
    match = _STATE_RE.search(value)
    return match.group(1).lower() if match else value.strip().lower()


def none_if_dash(value: str | None) -> str | None:
    """Return ``None`` for empty or placeholder (``-``, ``--``) cells, else the stripped value."""
    if value is None:
        return None
    raw = value.strip()
    return None if raw in {"", "-", "--", "N/A"} else raw
