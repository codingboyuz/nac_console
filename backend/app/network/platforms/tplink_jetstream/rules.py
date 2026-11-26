"""TP-Link JetStream classifier and lockout guard."""

from __future__ import annotations

import ipaddress
import re

from app.domain.commands.categories import CommandCategory, LineClassification
from app.domain.commands.classifier import DeviceContext, canonical_interface
from app.domain.commands.lockout import (
    ANY_ADDRESS,
    LockoutContext,
    LockoutFinding,
    LockoutGuard,
    _wildcard_covers,
)
from app.domain.commands.rules import Rule
from app.domain.vlans import parse_vlan_list
from app.network.platforms.cisco_ios.rules import CiscoIosClassifier, normalize_ios

S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS


def _r(pattern: str, category: CommandCategory, reason: str) -> Rule:
    return Rule(re.compile(pattern), category, reason)


_SHOW = (
    "system-info|image-info|boot|system-time|vlan|interface|mac address-table|arp|ip arp|"
    "ip route|ip interface|ip source binding|ip dhcp snooping|spanning-tree|cpu-utilization|"
    "memory-utilization|temperature|voltage|access-list|running-config|startup-config|dot1x|"
    "lldp|etherchannel|lacp|radius-server|aaa|user|logging|snmp-server|reboot-schedule|"
    "checkpoint|ip routing|ip ospf|version"
)


class TplinkClassifier(CiscoIosClassifier):
    dangerous_rules = (
        _r(
            r"^(reboot|reset|firmware|boot application|boot config|rollback|checkpoint)(?=\s|$)",
            D,
            "reboot / reset / firmware / config replacement",
        ),
        _r(r"^copy (?!running-config startup-config$)", D, "file transfer / config replacement"),
        _r(
            r"^(no )?(user name|enable password|enable secret|enable-admin|line|ip ssh|ip http|"
            r"telnet|snmp-server|radius-server|tacacs-server|aaa|hostname|system-time|"
            r"ip management-vlan|sdm|clock)\b",
            D,
            "AAA / management-plane / system change",
        ),
        _r(r"^(no )?(debug|clear)\b", D, "debug / clear state"),
        _r(r"^no interface\b", D, "removes an interface"),
        _r(r"^no access-list \d+$", D, "deletes a whole ACL"),
        _r(r"^do\b", D, "exec command inside configuration mode"),
    )
    safe_rules = (_r(rf"^show ({_SHOW})\b", S, "read-only show command"),)
    warning_rules = (
        _r(r"^(configure|end|exit|enable)$", W, "mode navigation"),
        _r(r"^copy running-config startup-config$", W, "saves the running configuration"),
        _r(r"^reboot-schedule (in \d+|cancel)$", W, "rollback timer (reboot into saved config)"),
        _r(r"^(ping|tracert|traceroute)\b", W, "active probe generates traffic"),
        _r(r"^interface (range )?\S+", W, "enters an interface view"),
        _r(r"^(no )?shutdown$", W, "interface admin state change"),
        _r(r"^(no )?description\b", W, "description change"),
        _r(r"^(no )?switchport (general|pvid|acceptable|check)\b", W, "port VLAN change"),
        _r(r"^(no )?(speed|duplex|flow-control|jumbo-size)\b", W, "PHY setting"),
        _r(r"^(no )?vlan \d", W, "VLAN create/delete"),
        _r(r"^(no )?name\b", W, "VLAN name change"),
        _r(r"^(no )?spanning-tree\b", W, "STP setting"),
        _r(r"^(no )?ip dhcp snooping\b", W, "DHCP snooping setting"),
        _r(r"^(no )?access-list (create|ip|bind|mac)\b", W, "ACL change"),
        _r(r"^(no )?mac address-table (static|filtering|aging-time)\b", W, "static MAC change"),
        _r(r"^(no )?ip (route|routing|address)\b", W, "routing / interface IP change"),
        _r(r"^(no )?(router ospf|network|router-id|passive-interface)\b", W, "OSPF setting"),
        _r(r"^(no )?dot1x\b", W, "NAC port setting"),
        _r(r"^(no )?(channel-group|lacp|lldp)\b", W, "LAG / LLDP setting"),
        _r(r"^server \S+$", W, "AAA group member"),
    )
    exec_probe_prefixes = ("ping", "tracert", "traceroute", "copy running-config startup-config")

    def _escalate(
        self, item: LineClassification, text: str, view: str | None, ctx: DeviceContext
    ) -> LineClassification:
        item = super()._escalate(item, text, view, ctx)
        if item.category is D:
            return item
        iface = view.split(":", 1)[1] if view and view.startswith("interface:") else None
        reason = None
        if (
            iface
            and ctx.is_protected(iface)
            and text.startswith(("no switchport general allowed vlan", "switchport pvid"))
        ):
            reason = f"changes the VLAN membership of protected interface {iface}"
        if re.match(r"^access-list ip \S+ rule \d+ deny logging \w+$", text):
            reason = "ACL rule denies all traffic"
        return item if reason is None else LineClassification(item.line, D, reason, item.view)


_RULE = re.compile(r"^access-list ip (\S+) rule \d+ (deny|permit) (.*)$")
_BIND = re.compile(r"^access-list bind (\S+) interface (.+)$", re.I)
_CREATE = re.compile(r'^access-list create (\d+) name "?([^"\s]+)"?', re.I)
_REMOVE_VLAN = re.compile(r"^no switchport general allowed vlan (\S+)$")


def _sip(rest: str) -> str:
    match = re.search(r"\bsip (\S+) sip-mask (\S+)", rest)
    if not match:
        return "any"
    try:
        net = ipaddress.ip_network(f"{match.group(1)}/{match.group(2)}", strict=False)
    except ValueError:
        return "unparseable"
    return f"{net.network_address} {net.hostmask}"


class TplinkLockoutGuard(LockoutGuard):
    def check(  # noqa: PLR0912 - one linear pass over a CLI view state machine
        self, lines: list[str], ctx: LockoutContext
    ) -> list[LockoutFinding]:
        dev = ctx.device
        findings: list[LockoutFinding] = []
        iface: str | None = None
        denying: dict[str, str] = {}
        applied: set[str] = set(ctx.mgmt_acls)
        names: dict[str, str] = {}
        controllers = ctx.controller_ips or (ANY_ADDRESS,)
        for raw in lines:
            text = normalize_ios(raw)
            exact = " ".join(raw.split())
            if text.startswith("interface "):
                iface = canonical_interface(text.removeprefix("interface ").replace(" ", ""))
                continue
            if text in {"exit", "end"}:
                iface = None
                continue
            if match := _CREATE.match(exact):
                names[match.group(1)] = match.group(2)
            rule = _RULE.match(text)
            if (
                rule
                and rule.group(2) == "deny"
                and _wildcard_covers(_sip(rule.group(3)), controllers)
            ):
                denying.setdefault(rule.group(1), raw)
            if bind := _BIND.match(exact):
                # 'access-list bind 1 interface vlan 4 gigabitEthernet 1/0/3': keyword/value pairs
                tokens = bind.group(2).split()
                targets = [
                    canonical_interface(tokens[i] + tokens[i + 1])
                    for i in range(0, len(tokens) - 1, 2)
                ]
                if any(dev.is_protected(t) or t.startswith("Vlan") for t in targets):
                    applied.add(bind.group(1))
            if iface is None:
                continue
            is_mgmt = iface == dev.mgmt_interface or dev.is_mgmt_vlan_interface(iface)
            is_uplink = iface in dev.uplink_interfaces
            if text == "shutdown" and (is_mgmt or is_uplink):
                findings.append(
                    LockoutFinding(raw, f"shuts down management path interface {iface}")
                )
            if text.startswith("no ip address") and is_mgmt:
                findings.append(LockoutFinding(raw, f"removes the management IP on {iface}"))
            removed = _REMOVE_VLAN.match(text) if is_uplink else None
            if removed and dev.mgmt_vlan in parse_vlan_list(removed.group(1), strict=False):
                findings.append(LockoutFinding(raw, f"removes management VLAN from uplink {iface}"))
        for acl, line in denying.items():
            if acl in applied or names.get(acl) in applied:
                findings.append(
                    LockoutFinding(line, f"ACL {acl} on the management path denies the controller")
                )
        for raw in lines:
            text = normalize_ios(raw)
            spec = text.removeprefix("no vlan ") if text.startswith("no vlan ") else ""
            if spec and dev.mgmt_vlan in parse_vlan_list(spec, strict=False):
                findings.append(LockoutFinding(raw, "deletes the management VLAN"))
        return findings
