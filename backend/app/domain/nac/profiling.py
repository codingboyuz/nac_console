"""Endpoint profiling from OUI, DHCP fingerprints (option 55 / vendor class) and LLDP hints.

Heuristic and explainable: every signal contributes a weighted vote; the result carries a
confidence in [0, 1]. Tables are intentionally small and data-driven (extend via settings).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.mac import is_locally_administered, oui

# Public IEEE OUI assignments (subset) -> (vendor, likely device type).
OUI_TABLE: dict[str, tuple[str, str | None]] = {
    "00:E0:FC": ("Huawei", "network"),
    "00:18:82": ("Huawei", "network"),
    "48:46:FB": ("Huawei", None),
    "00:50:56": ("VMware", "virtual-machine"),
    "00:0C:29": ("VMware", "virtual-machine"),
    "00:04:F2": ("Polycom", "voip-phone"),
    "64:16:7F": ("Polycom", "voip-phone"),
    "00:40:8C": ("Axis", "camera"),
    "AC:CC:8E": ("Axis", "camera"),
    "44:19:B6": ("Hikvision", "camera"),
    "3C:D9:2B": ("HP", "printer"),
    "00:1B:78": ("HP", None),
    "F4:5C:89": ("Apple", None),
    "00:1E:C2": ("Apple", None),
    "B8:27:EB": ("Raspberry Pi", "iot"),
    "DC:A6:32": ("Raspberry Pi", "iot"),
}

# DHCP option 55 parameter request lists -> OS (well-known fingerprints, normalized).
DHCP_PRL: dict[str, str] = {
    "1,3,6,15,31,33,43,44,46,47,119,121,249,252": "Windows",
    "1,3,6,15,31,33,43,44,46,47,121,249,252": "Windows",
    "1,121,3,6,15,119,252": "macOS",
    "1,121,3,6,15,108,114,119,252": "macOS",
    "1,3,6,15,26,28,51,58,59,43": "Android",
    "1,3,6,15,26,28,51,58,59": "Android",
    "1,121,3,6,15,119,252,95,44,46": "iOS",
    "1,28,2,3,15,6,119,12,44,47,26,121,42": "Linux",
}

VENDOR_CLASS_HINTS: tuple[tuple[str, str, str | None], ...] = (
    ("MSFT 5.0", "Windows", "workstation"),
    ("android-dhcp", "Android", "mobile"),
    ("dhcpcd", "Linux", None),
    ("Polycom", "Polycom UC", "voip-phone"),
    ("Cisco Systems, Inc. IP Phone", "Cisco IP Phone", "voip-phone"),
    ("HP LaserJet", "HP printer firmware", "printer"),
)

LLDP_HINTS: tuple[tuple[str, str], ...] = (
    ("phone", "voip-phone"),
    ("ap", "access-point"),
    ("camera", "camera"),
    ("switch", "network"),
)


@dataclass(frozen=True, slots=True)
class ProfileSignals:
    mac: str
    dhcp_prl: str | None = None
    vendor_class: str | None = None
    hostname: str | None = None
    lldp_system: str | None = None


@dataclass(slots=True)
class Profile:
    vendor: str | None = None
    device_type: str | None = None
    os: str | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)


def profile(signals: ProfileSignals) -> Profile:
    result = Profile()
    score = 0.0
    entry = OUI_TABLE.get(oui(signals.mac))
    if entry:
        result.vendor, result.device_type = entry
        score += 0.3 if entry[1] else 0.15
        result.reasons.append(f"OUI {oui(signals.mac)} -> {entry[0]}")
    elif is_locally_administered(signals.mac):
        result.reasons.append("randomized (locally administered) MAC")
    if signals.dhcp_prl:
        prl = ",".join(p.strip() for p in signals.dhcp_prl.split(",") if p.strip())
        os_name = DHCP_PRL.get(prl)
        if os_name:
            result.os = os_name
            result.device_type = result.device_type or (
                "mobile" if os_name in {"Android", "iOS"} else "workstation"
            )
            score += 0.4
            result.reasons.append(f"DHCP option 55 matches {os_name}")
    if signals.vendor_class:
        for needle, os_name, device_type in VENDOR_CLASS_HINTS:
            if needle.lower() in signals.vendor_class.lower():
                result.os = result.os or os_name
                result.device_type = device_type or result.device_type
                score += 0.3
                result.reasons.append(f"vendor class '{needle}'")
                break
    if signals.lldp_system:
        lowered = signals.lldp_system.lower()
        for needle, device_type in LLDP_HINTS:
            if needle in lowered.split() or lowered.startswith(needle):
                result.device_type = device_type
                score += 0.3
                result.reasons.append(f"LLDP system '{signals.lldp_system}'")
                break
    result.confidence = round(min(score, 1.0), 2)
    return result
