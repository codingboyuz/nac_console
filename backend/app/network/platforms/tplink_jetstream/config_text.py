"""TP-Link running-config text: '#' separators and a '!<hostname>' first line, otherwise the
indented IOS-like structure; restore planning reuses the IOS-style merge."""

from __future__ import annotations

import re

from app.network.platforms.cisco_ios import config_text as ios

RestorePlan = ios.RestorePlan
_NOISE = re.compile(r"^(#\s*$|![^\s].*$)")


def _strip(text: str) -> str:
    return "\n".join(
        line for line in (text or "").replace("\r\n", "\n").split("\n") if not _NOISE.match(line)
    )


def normalize(text: str) -> str:
    return ios.normalize(_strip(text))


def config_hash(text: str) -> str:
    return ios.config_hash(_strip(text))


def sanitize(text: str) -> str:
    return ios.sanitize(_strip(text))


def unified_diff(
    old: str, new: str, *, old_name: str = "a", new_name: str = "b", context: int = 3
) -> str:
    return ios.unified_diff(
        _strip(old), _strip(new), old_name=old_name, new_name=new_name, context=context
    )


def restore_plan(running: str, backup: str) -> ios.RestorePlan:
    return ios.restore_plan(_strip(running), _strip(backup))
