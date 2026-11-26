"""Central secret redaction used by logging, audit summaries and transport logs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

REDACTED = "***REDACTED***"

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "shared_secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "private_key",
        "community",
        "auth_key",
        "priv_key",
        "enable_password",
        "jwt",
        "cookie",
        "confirmation_token",
        "ticket",
        "bot_token",
        "credential",
        "key",
    }
)
_SENSITIVE_FRAGMENTS = ("password", "secret", "token", "private_key", "community", "passphrase")

# Huawei secret markers that precede the actual secret ("shared-key cipher <x>").
_HUAWEI_MARKERS = r"cipher\b|irreversible-cipher\b|simple\b"

# (pattern, replacement) pairs applied in order. Huawei configuration secrets appear as
# "password cipher <x>", "irreversible-cipher <x>", "shared-key cipher <x>", SNMP communities
# and usm-user keys; generic secrets as key=value, bearer tokens, JWTs and PEM blocks.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        REDACTED,
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+"), REDACTED),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+"), rf"\1 {REDACTED}"),
    (
        re.compile(r"(?i)\b(password|passwd|secret|token|community|key)=([^\s&,;]+)"),
        rf"\1={REDACTED}",
    ),
    # Cisco / TP-Link typed secrets: "secret 9 $9$..", "password 7 0822..", "key 7 ..",
    # "key-string 7 ..". The type digit stays visible, the value is masked.
    (
        re.compile(
            r"(?i)\b(password|secret|key|key-string|server-key|authentication-key|shared-key|"
            r"pre-shared-key|md5)\s+([0-9])\s+(\"[^\"]*\"|\S+)"
        ),
        rf"\1 \2 {REDACTED}",
    ),
    # Cisco SMB / TP-Link: "password encrypted <x>", "key encrypted <x>".
    (
        re.compile(r"(?i)\b(password|secret|key)\s+(encrypted)\s+(\"[^\"]*\"|\S+)"),
        rf"\1 \2 {REDACTED}",
    ),
    # Untyped "secret <x>" / "key-string <x>"; RADIUS/TACACS server keys on one line or as an
    # indented "key <x>" inside a "radius server" block.
    (
        re.compile(r"(?i)\b(secret|key-string|server-key)\s+(?![0-9]\s)(\"[^\"]*\"|\S+)"),
        rf"\1 {REDACTED}",
    ),
    # Huawei "radius-server shared-key cipher <x>" is left to the cipher rule below.
    (
        re.compile(
            r"(?i)\b((?:radius|tacacs)(?:-server)?\b[^\n]*?\bkey)\s+"
            rf"(?![0-9]\s|{_HUAWEI_MARKERS})(\"[^\"]*\"|\S+)"
        ),
        rf"\1 {REDACTED}",
    ),
    (
        # "key <x>" as a whole line (RADIUS server block, generated plans); not "key chain".
        re.compile(rf"(?im)^(\s*key)\s+(?![0-9]\s|chain\b|{_HUAWEI_MARKERS})(\"[^\"]*\"|\S+)\s*$"),
        rf"\1 {REDACTED}",
    ),
    # SNMPv3 users (IOS / NX-OS / TP-Link): "auth sha <key> priv aes-128 <key>".
    (re.compile(r"(?i)\b(auth\s+(?:md5|sha(?:-?\d+)?))\s+\S+"), rf"\1 {REDACTED}"),
    (
        re.compile(r"(?i)\b(priv\s+(?:aes-?(?:128|192|256)|aes|3des|des))\s+\S+"),
        rf"\1 {REDACTED}",
    ),
    (re.compile(r"(?i)\b(priv)\s+(0x[0-9a-f]+)"), rf"\1 {REDACTED}"),
    (re.compile(r"(?i)\b(irreversible-cipher|cipher|simple)\s+\S+"), rf"\1 {REDACTED}"),
    (
        re.compile(r"(?i)\b(authentication-mode|privacy-mode)\s+(\S+)\s+(?!cipher\b)\S+"),
        rf"\1 \2 {REDACTED}",
    ),
    (
        re.compile(
            r"(?i)\b(community(?:\s+(?:read|write))?)\s+(?!cipher\b|irreversible-cipher\b)\S+"
        ),
        rf"\1 {REDACTED}",
    ),
    (
        re.compile(
            r"(?i)\b(password|shared-key|authentication-key|pre-shared-key)"
            r"\s+(?!cipher\b|irreversible-cipher\b|simple\b|\*\*\*|[0-9]\s+\*\*\*|encrypted\s+\*\*\*)\S+"
        ),
        rf"\1 {REDACTED}",
    ),
)


def redact_text(text: str) -> str:
    """Mask secrets embedded in free text (CLI transcripts, error messages, configs)."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def is_sensitive_key(key: str) -> bool:
    """Return True when a mapping key name denotes a secret."""
    lowered = key.lower()
    return lowered in SENSITIVE_KEYS or any(f in lowered for f in _SENSITIVE_FRAGMENTS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact mappings/sequences; strings get pattern-based masking."""
    if _depth > 8:
        return value
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            k: (
                REDACTED
                if isinstance(k, str) and is_sensitive_key(k) and v not in (None, "")
                else redact(v, _depth=_depth + 1)
            )
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact(v, _depth=_depth + 1) for v in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
