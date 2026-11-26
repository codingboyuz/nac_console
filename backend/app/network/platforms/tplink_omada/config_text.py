"""Backups of Omada-managed switches are JSON snapshots built by the driver ('GET config'):

    {"switch": {...}, "networks": [{"vlan", "name"}], "ports": [{"port", "name", "disable",
     "nativeVlan", "taggedVlans"}]}

Restore re-applies port settings and missing networks in the Omada change language.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from typing import Any

from app.network.platforms.cisco_ios.config_text import RestorePlan
from app.network.platforms.tplink_omada.dsl import port_name

__all__ = ["RestorePlan", "config_hash", "normalize", "restore_plan", "sanitize", "unified_diff"]


def _load(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def normalize(text: str) -> str:
    data = _load(text)
    if not data:
        return (text or "").strip()
    data["networks"] = sorted(data.get("networks") or [], key=lambda n: n.get("vlan") or 0)
    data["ports"] = sorted(data.get("ports") or [], key=lambda p: p.get("port") or 0)
    return json.dumps(data, indent=2, sort_keys=True)


def config_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def sanitize(text: str) -> str:
    return normalize(text)  # snapshots hold no secrets


def unified_diff(
    old: str, new: str, *, old_name: str = "a", new_name: str = "b", context: int = 3
) -> str:
    return "\n".join(
        difflib.unified_diff(
            normalize(old).splitlines(),
            normalize(new).splitlines(),
            old_name,
            new_name,
            n=context,
            lineterm="",
        )
    )


def _tagged(vlans: list[int] | None) -> str:
    return ",".join(str(v) for v in sorted(vlans or [])) or "none"


def restore_plan(running: str, backup: str) -> RestorePlan:
    now, then = _load(running), _load(backup)
    lines: list[str] = []
    notes: list[str] = []
    have = {n.get("vlan"): n.get("name") for n in now.get("networks") or []}
    want = {n.get("vlan"): n.get("name") for n in then.get("networks") or []}
    for vid, name in sorted(want.items(), key=lambda kv: kv[0] or 0):
        if vid not in have:
            lines.append(f"vlan create {vid} name {name or f'VLAN{vid:04d}'}")
        elif name and have[vid] != name:
            lines.append(f"vlan rename {vid} name {name}")
    for vid in sorted(set(have) - set(want), key=lambda v: v or 0):
        notes.append(f"VLAN {vid} exists only on the controller; not removed automatically")
    ports = {p.get("port"): p for p in now.get("ports") or []}
    for old in sorted(then.get("ports") or [], key=lambda p: p.get("port") or 0):
        number = old.get("port")
        cur = ports.get(number)
        if cur is None:
            notes.append(f"{port_name(int(number or 0))} is not present on the switch")
            continue
        if old.get("nativeVlan") is not None and old.get("nativeVlan") != cur.get("nativeVlan"):
            lines.append(f"port {number} native {old['nativeVlan']}")
        if _tagged(old.get("taggedVlans")) != _tagged(cur.get("taggedVlans")):
            lines.append(f"port {number} tagged {_tagged(old.get('taggedVlans'))}")
        if (old.get("name") or None) != (cur.get("name") or None):
            lines.append(f"port {number} name {old.get('name') or 'none'}")
        if bool(old.get("disable")) != bool(cur.get("disable")):
            lines.append(f"port {number} {'disable' if old.get('disable') else 'enable'}")
    return RestorePlan(tuple(lines), tuple(notes))
