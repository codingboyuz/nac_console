"""Classifier and lockout guard for the Omada change language (see dsl.py)."""

from __future__ import annotations

import re

from app.domain.commands.categories import CommandCategory, LineClassification
from app.domain.commands.classifier import CommandClassifier, DeviceContext
from app.domain.commands.lockout import LockoutContext, LockoutFinding, LockoutGuard
from app.domain.commands.rules import Rule
from app.network.platforms.tplink_omada.dsl import PortOp, VlanOp, parse_line, port_name

S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS


def _r(pattern: str, category: CommandCategory, reason: str) -> Rule:
    return Rule(re.compile(pattern), category, reason)


class OmadaClassifier(CommandClassifier):
    dangerous_rules = (
        _r(r"^vlan delete \d+$", D, "deletes a site-wide LAN network on the controller"),
        _r(r"^(reboot|reset|upgrade|forget|adopt)\b", D, "device lifecycle action"),
    )
    safe_rules = (
        _r(r"^GET (switch|ports|port \d+|networks|clients|config)$", S, "read-only API query"),
    )
    warning_rules = (
        _r(r"^vlan (create|rename) \d+ name \S.*$", W, "LAN network change (whole site)"),
        _r(r"^port \d+ (enable|disable)$", W, "port admin state change"),
        _r(r"^port \d+ name \S.*$", W, "port name change"),
        _r(r"^port \d+ native \d+$", W, "port native VLAN change"),
        _r(r"^port \d+ tagged (none|[\d,\-]+)$", W, "port tagged VLAN change"),
    )
    exec_probe_prefixes: tuple[str, ...] = ()
    config_outside_view_reason = "Omada changes go through the change endpoints, not the console"

    def normalize(self, line: str) -> str:
        return " ".join(line.split())

    def _next_view(self, text: str, view: str | None) -> str | None:
        return None  # API changes have no CLI views

    def _escalate(
        self, item: LineClassification, text: str, view: str | None, ctx: DeviceContext
    ) -> LineClassification:
        if item.category is D:
            return item
        try:
            op = parse_line(text)
        except ValueError:
            return item
        reason = None
        if isinstance(op, PortOp) and ctx.is_protected(port_name(op.port)):
            if op.field == "disable":
                reason = f"disables protected port {port_name(op.port)}"
            elif op.field in {"native", "tagged"}:
                reason = f"changes the VLANs of protected port {port_name(op.port)}"
        if isinstance(op, VlanOp) and op.action == "delete" and op.vlan == ctx.mgmt_vlan:
            reason = "removes the management VLAN"
        return item if reason is None else LineClassification(item.line, D, reason, item.view)


class OmadaLockoutGuard(LockoutGuard):
    def check(self, lines: list[str], ctx: LockoutContext) -> list[LockoutFinding]:
        dev = ctx.device
        findings: list[LockoutFinding] = []
        for raw in lines:
            try:
                op = parse_line(raw)
            except ValueError:
                continue
            if isinstance(op, VlanOp):
                if op.action == "delete" and dev.mgmt_vlan is not None and op.vlan == dev.mgmt_vlan:
                    findings.append(LockoutFinding(raw, "deletes the management VLAN"))
                continue
            name = port_name(op.port)
            path = name == dev.mgmt_interface or name in dev.uplink_interfaces
            if not path:
                continue
            if op.field == "disable":
                findings.append(LockoutFinding(raw, f"disables management path port {name}"))
            elif op.field == "tagged" and dev.mgmt_vlan is not None:
                if dev.mgmt_vlan not in op.tagged():
                    findings.append(
                        LockoutFinding(raw, f"removes management VLAN from uplink {name}")
                    )
            elif (
                op.field == "native"
                and name == dev.mgmt_interface
                and dev.mgmt_vlan is not None
                and op.value != str(dev.mgmt_vlan)
            ):
                findings.append(
                    LockoutFinding(raw, f"moves management port {name} off the mgmt VLAN")
                )
        return findings
