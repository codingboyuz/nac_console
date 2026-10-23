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
            r"\s+(?!cipher\b|irreversible-cipher\b|simple\b|\*\*\*)\S+"
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
