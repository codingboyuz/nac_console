"""NX-OS command classifier and lockout guard: the IOS rule set plus NX-OS specifics."""

from __future__ import annotations

import re

from app.domain.commands.categories import CommandCategory
from app.domain.commands.lockout import LockoutContext, LockoutFinding
from app.domain.commands.rules import Rule
from app.network.platforms.cisco_ios import rules as ios
from app.network.platforms.cisco_ios.lockout import CiscoLockoutGuard
from app.network.platforms.cisco_ios.rules import CiscoIosClassifier, normalize_ios

S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS


def _r(pattern: str, category: CommandCategory, reason: str) -> Rule:
    return Rule(re.compile(pattern), category, reason)


_NXOS_DANGEROUS = (
    _r(r"^no feature\b", D, "disables a feature (removes its configuration)"),
    _r(r"^(rollback|checkpoint|install|reload|write erase)\b", D, "replaces or erases config"),
    _r(r"^(no )?(role|vpc|fabric|nv overlay|system|hardware|vdc)\b", D, "system-wide change"),
)
_NXOS_SAFE = (
    _r(
        r"^show (system resources|feature|vpc|port-channel summary|module|environment|"
        r"interface|hardware|checkpoint|ip interface|nve|bgp|mac address-table)\b",
        S,
        "read-only show command",
    ),
)
_NXOS_WARNING = (
    _r(
        r"^feature (ospf|bgp|rip|vrrp|hsrp|dhcp|interface-vlan|lacp|lldp|dot1x|udld|"
        r"port-security)$",
        W,
        "enables a protocol feature",
    ),
    _r(r"^(no )?ip (router (ospf|rip)|port access-group)\b", W, "per-interface routing / port ACL"),
    _r(r"^(no )?(address|priority|preempt|remote-as)\b", W, "protocol sub-mode setting"),
)


class NxosClassifier(CiscoIosClassifier):
    dangerous_rules = (*_NXOS_DANGEROUS, *ios.DANGEROUS_RULES)
    safe_rules = (*_NXOS_SAFE, *ios.SAFE_RULES)
    warning_rules = (*_NXOS_WARNING, *ios.WARNING_RULES)
    exec_probe_prefixes = ("ping", "traceroute", "copy running-config startup-config")


_PORT_ACL = re.compile(r"^ip port access-group (\S+) in$", re.I)


class NxosLockoutGuard(CiscoLockoutGuard):
    """Adds NX-OS port ACLs ('ip port access-group') on protected interfaces."""

    def check(self, lines: list[str], ctx: LockoutContext) -> list[LockoutFinding]:
        extra: set[str] = set()
        iface: str | None = None
        for raw in lines:
            text = normalize_ios(raw)
            if text.startswith("interface "):
                iface = text.removeprefix("interface ")
            elif text in {"exit", "end"}:
                iface = None
            elif iface and (bound := _PORT_ACL.match(" ".join(raw.split()))):
                from app.domain.commands.classifier import canonical_interface

                if ctx.device.is_protected(canonical_interface(iface.replace(" ", ""))):
                    extra.add(bound.group(1))
        if extra:
            ctx = LockoutContext(ctx.device, ctx.controller_ips, ctx.mgmt_acls | frozenset(extra))
        return super().check(lines, ctx)
