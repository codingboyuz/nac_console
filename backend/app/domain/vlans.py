"""VLAN id/range parsing and validation (API "10,20-30" and Huawei "10 20 to 30" syntax)."""

from __future__ import annotations

import re

MIN_VLAN, MAX_VLAN = 1, 4094
# VLAN 1 is the default VLAN: it can be neither created nor deleted on VRP.
RESERVED_VLANS = frozenset({1})
MAX_VLANS_PER_REQUEST = 4094

_TOKEN = re.compile(r"^\d+$")


class InvalidVlan(ValueError):
    pass


def validate_vlan_id(vlan_id: int, *, allow_reserved: bool = True) -> int:
    if not MIN_VLAN <= vlan_id <= MAX_VLAN:
        raise InvalidVlan(f"VLAN {vlan_id} outside {MIN_VLAN}-{MAX_VLAN}")
    if not allow_reserved and vlan_id in RESERVED_VLANS:
        raise InvalidVlan(f"VLAN {vlan_id} is reserved")
    return vlan_id


def parse_vlan_list(spec: str, *, strict: bool = True, allow_reserved: bool = True) -> list[int]:
    """Parse '10,20-30', '10 20 to 30' or '10 20-30' into a sorted unique list.

    ``strict=False`` ignores malformed tokens instead of raising (used when scanning CLI text).
    """
    text = (spec or "").lower().replace(",", " ").replace(" to ", "-")
    text = re.sub(r"\s*-\s*", "-", text)
    result: set[int] = set()
    for token in text.split():
        try:
            if "-" in token:
                start_s, end_s = token.split("-", 1)
                if not (_TOKEN.match(start_s) and _TOKEN.match(end_s)):
                    raise InvalidVlan(f"invalid VLAN range {token!r}")
                start, end = int(start_s), int(end_s)
                if start > end:
                    raise InvalidVlan(f"invalid VLAN range {token!r}")
                for vid in (start, end):
                    validate_vlan_id(vid, allow_reserved=allow_reserved)
                result.update(range(start, end + 1))
            else:
                if not _TOKEN.match(token):
                    raise InvalidVlan(f"invalid VLAN id {token!r}")
                result.add(validate_vlan_id(int(token), allow_reserved=allow_reserved))
        except InvalidVlan:
            if strict:
                raise
    if strict and not result:
        raise InvalidVlan("no VLANs given")
    if len(result) > MAX_VLANS_PER_REQUEST:
        raise InvalidVlan("too many VLANs")
    if not allow_reserved and result & RESERVED_VLANS:
        raise InvalidVlan("reserved VLAN in range")
    return sorted(result)


def to_huawei_ranges(vlans: list[int]) -> str:
    """[10, 11, 12, 20] -> '10 to 12 20' (Huawei 'vlan batch' / 'allow-pass vlan' syntax)."""
    if not vlans:
        raise InvalidVlan("no VLANs given")
    ordered = sorted(set(vlans))
    parts: list[str] = []
    start = prev = ordered[0]
    for vid in ordered[1:]:
        if vid == prev + 1:
            prev = vid
            continue
        parts.append(str(start) if start == prev else f"{start} to {prev}")
        start = prev = vid
    parts.append(str(start) if start == prev else f"{start} to {prev}")
    return " ".join(parts)


def to_cisco_ranges(vlans: list[int]) -> str:
    """[10, 11, 12, 20] -> '10-12,20' (Cisco / TP-Link VLAN list syntax)."""
    return to_huawei_ranges(vlans).replace(" to ", "-").replace(" ", ",")
