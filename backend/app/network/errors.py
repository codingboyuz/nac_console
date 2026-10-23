"""Huawei CLI error markers and exception mapping into domain errors."""

from __future__ import annotations

import re
import socket

from app.core.exceptions import (
    AppError,
    CommandRejected,
    DeviceAuthFailed,
    DeviceError,
    DeviceTimeout,
    DeviceUnreachable,
    HostKeyMismatch,
)
from app.core.redaction import redact_text

# VRP prints errors as "Error: ..." (both generations); some builds print "% ..." or
# "Unrecognized command" without the prefix. VRP-VERSION-DEPENDENT wording.
_ERROR_LINE = re.compile(
    r"^\s*(Error:.*|%\s*(Unrecognized|Incomplete|Wrong|Ambiguous|Too many).*|"
    r"Unrecognized command.*|Wrong parameter.*|Incomplete command.*|Too many parameters.*|"
    r"Ambiguous command.*)$",
    re.IGNORECASE | re.MULTILINE,
)


def detect_cli_error(output: str) -> str | None:
    """Return the first error line found in device output, redacted, or None."""
    match = _ERROR_LINE.search(output or "")
    return redact_text(match.group(1).strip())[:300] if match else None


def raise_on_cli_error(command: str, output: str) -> None:
    error = detect_cli_error(output)
    if error:
        raise CommandRejected(
            f"device rejected command: {error}", details={"command": redact_text(command)}
        )


def map_exception(exc: BaseException, host: str) -> AppError:  # noqa: PLR0911 - mapping table
    """Translate library exceptions into domain errors without leaking credentials."""
    if isinstance(exc, AppError):
        return exc
    name = type(exc).__name__
    text = str(exc).lower()
    chain: list[BaseException] = []
    cursor: BaseException | None = exc
    while cursor is not None and len(chain) < 8:
        chain.append(cursor)
        cursor = cursor.__cause__ or cursor.__context__
    if any(type(e).__name__ == "BadHostKeyException" for e in chain) or (
        "not found in known_hosts" in text or "host key" in text
    ):
        return HostKeyMismatch(f"SSH host key of {host} does not match the pinned key")
    if "authentication" in name.lower() or ("auth" in text and "fail" in text):
        return DeviceAuthFailed(f"authentication failed for {host}")
    if "tcp connection to device failed" in text or any(
        isinstance(e, ConnectionRefusedError) for e in chain
    ):
        return DeviceUnreachable(f"cannot reach {host}: connection refused or no route")
    if isinstance(exc, socket.timeout | TimeoutError) or "timeout" in name.lower():
        return DeviceTimeout(f"timed out talking to {host}")
    if isinstance(exc, ConnectionError | OSError | EOFError) or name in {
        "NoValidConnectionsError",
        "SSHException",
    }:
        return DeviceUnreachable(f"cannot reach {host}: {name}")
    return DeviceError(f"device error on {host}: {name}")
