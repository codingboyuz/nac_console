"""Parse ``display acl <n|all>`` output into ACL headers and raw rule lines.

S: "Basic ACL 2000, 2 rules" / "Advanced ACL 3000, 3 rules"; CE: "Basic ACL 2000, 2 rules" too,
with "Acl's step is 5". Rules: " rule 5 permit source 10.0.0.0 0.0.0.255 (12 matches)".
VRP-VERSION-DEPENDENT: named ACL headers read "Advanced ACL WEB 3999, 1 rule".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.network.parsers.common import clean_lines

_HEADER = re.compile(
    r"^(?P<kind>Basic|Advanced|L2|Layer2|Link|User)\s+(?:IPv6\s+)?ACL\s+(?:(?P<name>[A-Za-z]\S*)\s+)?"
    r"(?P<number>\d+)?,?\s*(?P<count>\d+)?\s*rules?",
    re.I,
)
_RULE = re.compile(
    r"^rule (?P<id>\d+) (?P<body>.+?)(?:\s+\((?P<matches>\d+) (?:matches|times matched)\))?$"
)


@dataclass(slots=True)
class ParsedAcl:
    kind: str
    number: int | None
    name: str | None
    rules: dict[int, str] = field(default_factory=dict)  # id -> normalized rule text
    matches: dict[int, int] = field(default_factory=dict)


def parse_acls(text: str | None) -> list[ParsedAcl]:
    acls: list[ParsedAcl] = []
    current: ParsedAcl | None = None
    for raw in clean_lines(text):
        line = " ".join(raw.split())
        if match := _HEADER.match(line):
            number = match.group("number")
            current = ParsedAcl(
                kind=match.group("kind").lower(),
                number=int(number) if number else None,
                name=match.group("name"),
            )
            acls.append(current)
            continue
        if current is not None and (rule := _RULE.match(line)):
            rid = int(rule.group("id"))
            current.rules[rid] = f"rule {rid} {rule.group('body').strip()}"
            if rule.group("matches"):
                current.matches[rid] = int(rule.group("matches"))
    return acls


def management_acls(running_config: str | None) -> frozenset[str]:
    """ACL numbers/names bound to VTY lines ('acl 2000 inbound' under user-interface)."""
    names: set[str] = set()
    in_vty = False
    for raw in clean_lines(running_config):
        line = raw.strip()
        if raw and not raw.startswith(" "):
            in_vty = line.startswith("user-interface")
            continue
        if in_vty and (m := re.match(r"^acl (?:ipv6 )?(?:name )?(\S+) inbound$", line)):
            names.add(m.group(1))
    return frozenset(names)
