"""MAC address normalization: one canonical form (lower-case, colon separated) at every boundary.

Accepted inputs: Huawei ``00e0-fc12-3456``, ``00:E0:FC:12:34:56``, ``00-e0-fc-12-34-56``,
``00e0.fc12.3456`` and bare ``00e0fc123456``.
"""

from __future__ import annotations

import re

_HEX = re.compile(r"^[0-9a-f]{12}$")
_SEPARATORS = re.compile(r"[\s:.\-]")


class InvalidMac(ValueError):
    pass


def normalize_mac(value: str) -> str:
    """Return ``aa:bb:cc:dd:ee:ff`` or raise InvalidMac."""
    raw = _SEPARATORS.sub("", (value or "").strip().lower())
    if not _HEX.match(raw):
        raise InvalidMac(f"invalid MAC address: {value!r}")
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def to_huawei(mac: str) -> str:
    """Canonical -> Huawei CLI format ``aabb-ccdd-eeff``."""
    raw = normalize_mac(mac).replace(":", "")
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


def oui(mac: str) -> str:
    """First three octets, ``AA:BB:CC`` upper-case (IEEE registry style)."""
    return normalize_mac(mac)[:8].upper()


def is_multicast(mac: str) -> bool:
    return bool(int(normalize_mac(mac)[:2], 16) & 1)


def is_locally_administered(mac: str) -> bool:
    """Randomized/private MACs (common on phones) set the U/L bit."""
    return bool(int(normalize_mac(mac)[:2], 16) & 2)


def to_cisco(mac: str) -> str:
    """Canonical -> Cisco / TP-Link CLI format ``aabb.ccdd.eeff``."""
    raw = normalize_mac(mac).replace(":", "")
    return f"{raw[0:4]}.{raw[4:8]}.{raw[8:12]}"


def to_ietf(mac: str) -> str:
    """Canonical -> RFC 3580 / IETF Calling-Station-Id ``AA-BB-CC-DD-EE-FF``."""
    return normalize_mac(mac).replace(":", "-").upper()
