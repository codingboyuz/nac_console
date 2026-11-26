"""Cisco SMB classifier and lockout guard: IOS-style rules plus SMB keywords."""

from __future__ import annotations

import re

from app.domain.commands.categories import CommandCategory
from app.domain.commands.classifier import canonical_interface
from app.domain.commands.lockout import LockoutContext, LockoutFinding
from app.domain.commands.rules import Rule
from app.network.platforms.cisco_ios import rules as ios
from app.network.platforms.cisco_ios.lockout import CiscoLockoutGuard
from app.network.platforms.cisco_ios.rules import CiscoIosClassifier, normalize_ios

S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS


def _r(pattern: str, category: CommandCategory, reason: str) -> Rule:
    return Rule(re.compile(pattern), category, reason)


class SmbClassifier(CiscoIosClassifier):
    dangerous_rules = (
        _r(r"^(no )?management (access-list|access-class)\b", D, "management-plane access list"),
        _r(r"^(boot|delete|copy (?!running-config startup-config))", D, "file / boot change"),
        *ios.DANGEROUS_RULES,
    )
    safe_rules = (
        _r(
            r"^show (system|bootvar|cpu utilization|inventory|software versions|interfaces "
            r"(status|counters|configuration|switchport)|access-lists|dot1x)\b",
            S,
            "read-only show command",
        ),
        _r(r"^terminal datadump$", S, "session paging"),
        *ios.SAFE_RULES,
    )
    warning_rules = (
        _r(r"^vlan database$", W, "VLAN database mode"),
        _r(r"^(no )?service-acl (input|output)\b", W, "ACL binding change"),
        _r(r"^(no )?(permit|deny)\b", W, "ACL entry change"),
        _r(r"^(no )?negotiation\b", W, "PHY setting"),
        *ios.WARNING_RULES,
    )


_SERVICE_ACL = re.compile(r"^service-acl input (\S+)", re.I)
_MGMT_CLASS = re.compile(r"^management access-class (\S+)", re.I)


class SmbLockoutGuard(CiscoLockoutGuard):
    """Adds ACLs bound with 'service-acl input' on protected ports and the management
    access-class to the set of ACLs that must never deny the controller."""

    def check(self, lines: list[str], ctx: LockoutContext) -> list[LockoutFinding]:
        extra: set[str] = set()
        iface: str | None = None
        for raw in lines:
            text = normalize_ios(raw)
            exact = " ".join(raw.split())
            if text.startswith("interface "):
                iface = canonical_interface(text.removeprefix("interface ").replace(" ", ""))
            elif text in {"exit", "end"}:
                iface = None
            elif (bound := _SERVICE_ACL.match(exact)) and iface and ctx.device.is_protected(iface):
                extra.add(bound.group(1))
            elif mgmt := _MGMT_CLASS.match(exact):
                extra.add(mgmt.group(1))
        if extra:
            ctx = LockoutContext(ctx.device, ctx.controller_ips, ctx.mgmt_acls | frozenset(extra))
        return super().check(lines, ctx)
