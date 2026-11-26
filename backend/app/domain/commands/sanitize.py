"""CLI injection guard: every line that may reach a device passes through here first."""

from __future__ import annotations

import re

from app.core.exceptions import UnsafeCommand

MAX_LINE_LENGTH = 512
_PRINTABLE = re.compile(r"^[\x20-\x7e]+$")
# Output filters after a display/show command ("| include vlan"); VRP, IOS, NX-OS, TP-Link.
_PIPE_FILTER = re.compile(r"^\|\s*(include|exclude|begin|count|section|no-more)\b")
_CHAINING = ("&&", "||", ";", "`", "$(", "${")


def normalize_spaces(line: str) -> str:
    return " ".join(line.split())


def validate_cli_line(line: str, *, allow_pipe: bool = False) -> str:
    """Return the whitespace-normalized line or raise UnsafeCommand.

    Rejects newline/CR/control characters, non-ASCII, chaining tokens and over-long input.
    """
    if not isinstance(line, str) or not line.strip():
        raise UnsafeCommand("empty command")
    if len(line) > MAX_LINE_LENGTH:
        raise UnsafeCommand(f"command longer than {MAX_LINE_LENGTH} characters")
    if not _PRINTABLE.match(line):
        raise UnsafeCommand("command contains control, newline or non-ASCII characters")
    if any(token in line for token in _CHAINING):
        raise UnsafeCommand("command chaining is not allowed")
    if "|" in line:
        head, _, tail = line.partition("|")
        if not allow_pipe or not _PIPE_FILTER.match("|" + tail) or "|" in tail:
            raise UnsafeCommand("pipes are only allowed as a display output filter")
        if not head.strip().lower().startswith(("display", "dis ", "show ", "sh ")):
            raise UnsafeCommand("pipes are only allowed after display/show commands")
    return normalize_spaces(line)


def validate_cli_block(lines: list[str]) -> list[str]:
    """Validate a list of configuration lines (no pipes in configuration)."""
    if not lines:
        raise UnsafeCommand("empty configuration")
    if len(lines) > 2000:
        raise UnsafeCommand("configuration block too large")
    return [validate_cli_line(line) for line in lines]


def quote_free_text(value: str, *, max_length: int = 80) -> str:
    """Validate operator free text placed inside a CLI argument (descriptions, names)."""
    text = validate_cli_line(value)
    if len(text) > max_length:
        raise UnsafeCommand(f"text longer than {max_length} characters")
    if text.startswith(("undo", "quit", "return", "system-view")):
        raise UnsafeCommand("text must not start with a CLI keyword")
    return text
