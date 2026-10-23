"""CommandClassifier: SAFE / WARNING / DANGEROUS, deny-by-default, view and context aware."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.commands.categories import (
    Classification,
    CommandCategory,
    LineClassification,
)
from app.domain.commands.rules import (
    DANGEROUS_RULES,
    DISPLAY_WORDS,
    SAFE_RULES,
    WARNING_RULES,
    Rule,
)
from app.domain.commands.sanitize import validate_cli_line
from app.domain.interfaces import InvalidInterfaceName, normalize_interface

D, W = CommandCategory.DANGEROUS, CommandCategory.WARNING
_ABBREV_DISPLAY = re.compile(r"^(dis|disp|displ|displa)( |$)")
_INTERFACE_VIEW = re.compile(r"^interface (\S+)$")
_UNDO_VLAN = re.compile(r"^undo vlan (\d+)$")
_DENY_ANY = re.compile(r"^rule( \d+)? deny( ip| any)?( source any)?( destination any)?$")
_ALLOW_PASS = re.compile(r"^undo port (trunk|hybrid) (allow-pass|tagged|untagged) vlan\b")


@dataclass(frozen=True, slots=True)
class DeviceContext:
    """Per-device facts that make otherwise-WARNING commands DANGEROUS."""

    mgmt_interface: str | None = None
    mgmt_vlan: int | None = None
    uplink_interfaces: frozenset[str] = field(default_factory=frozenset)

    def is_protected(self, interface: str) -> bool:
        if interface.startswith(("MEth", "Eth-Trunk")):
            return True
        if interface == self.mgmt_interface or interface in self.uplink_interfaces:
            return True
        return self.mgmt_vlan is not None and interface == f"Vlanif{self.mgmt_vlan}"


def canonical_interface(name: str) -> str:
    try:
        return normalize_interface(name)
    except InvalidInterfaceName:
        return name


def normalize_command(line: str) -> str:
    """Lower-case, single-space, expand 'dis' and unambiguous display-topic abbreviations."""
    text = _ABBREV_DISPLAY.sub("display ", " ".join(line.lower().split())).strip()
    words = text.split(" ")
    if len(words) > 1 and words[0] == "display" and words[1] not in DISPLAY_WORDS:
        candidates = [w for w in DISPLAY_WORDS if w.startswith(words[1])]
        if len(words[1]) >= 2 and len(candidates) == 1:
            words[1] = candidates[0]
    return " ".join(words)


def _match(rules: tuple[Rule, ...], text: str) -> Rule | None:
    return next((rule for rule in rules if rule.pattern.search(text)), None)


class CommandClassifier:
    """Stateless; ``classify_config`` tracks the CLI view across a configuration block."""

    def classify_line(self, line: str, *, view: str | None = None) -> LineClassification:
        text = normalize_command(line.split("|", 1)[0])
        for rules in (DANGEROUS_RULES, SAFE_RULES, WARNING_RULES):
            rule = _match(rules, text)
            if rule is not None:
                return LineClassification(line, rule.category, rule.reason, view)
        return LineClassification(line, D, "unknown command (deny by default)", view)

    def classify_exec(self, command: str) -> Classification:
        """Classify one command entered in user view (execute-command endpoint)."""
        clean = validate_cli_line(command, allow_pipe=True)
        result = self.classify_line(clean)
        if result.category is W and not normalize_command(clean).startswith(("ping", "tracert")):
            # Configuration commands make no sense in user view; treat them as unknown.
            result = LineClassification(clean, D, "configuration command outside system-view")
        return Classification(result.category, result.reason, (result,))

    def classify_config(
        self, lines: list[str], context: DeviceContext | None = None
    ) -> Classification:
        """Classify a configuration block executed from system-view."""
        ctx = context or DeviceContext()
        view: str | None = None
        results: list[LineClassification] = []
        for raw in lines:
            clean = validate_cli_line(raw)
            text = normalize_command(clean)
            item = self.classify_line(clean, view=view)
            item = self._escalate(item, text, view, ctx)
            results.append(item)
            view = self._next_view(text, view)
        return Classification.of_lines(results)

    @staticmethod
    def _next_view(text: str, view: str | None) -> str | None:
        match = _INTERFACE_VIEW.match(text)
        if match:
            return f"interface:{canonical_interface(match.group(1))}"
        if text.startswith(("vlan ", "acl ", "ospf", "bgp", "rip", "user-interface", "aaa")):
            return text.split(" ", 1)[0]
        if text in {"quit", "return"}:
            return None
        return view

    @staticmethod
    def _escalate(
        item: LineClassification, text: str, view: str | None, ctx: DeviceContext
    ) -> LineClassification:
        if item.category is D:
            return item
        iface = view.split(":", 1)[1] if view and view.startswith("interface:") else None
        reason: str | None = None
        if iface and ctx.is_protected(iface):
            if text == "shutdown":
                reason = f"shutdown on protected interface {iface} (uplink/LAG/management)"
            elif text.startswith("undo ip address") and (
                iface == ctx.mgmt_interface or iface.startswith(("MEth", "Vlanif"))
            ):
                reason = f"removes the management address on {iface}"
            elif _ALLOW_PASS.match(text):
                reason = f"removes VLANs from uplink {iface}"
            elif text.startswith(("port link-type", "eth-trunk", "undo eth-trunk")):
                reason = f"changes port mode/LAG membership of uplink {iface}"
            elif text.startswith("traffic-filter"):
                reason = f"ACL applied to management/uplink interface {iface}"
        undo_vlan = _UNDO_VLAN.match(text)
        if undo_vlan and ctx.mgmt_vlan is not None and int(undo_vlan.group(1)) == ctx.mgmt_vlan:
            reason = "removes the management VLAN"
        if _DENY_ANY.match(text):
            reason = "ACL rule denies all traffic"
        if reason is None:
            return item
        return LineClassification(item.line, D, reason, item.view)
