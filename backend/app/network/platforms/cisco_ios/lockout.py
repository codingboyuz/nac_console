"""Self-lockout guard for Cisco IOS-style CLIs (IOS / IOS-XE, reused by SMB and TP-Link).

Flags changes that would cut the controller's management path: shutting the management or
uplink interface, removing the management SVI address or VLAN, pruning the management VLAN
from an uplink trunk, and ACLs that deny the controller once bound to VTY lines
('access-class') or a management/uplink interface ('ip access-group').
"""

from __future__ import annotations

import ipaddress
import re

from app.domain.commands.classifier import DeviceContext, canonical_interface
from app.domain.commands.lockout import (
    ANY_ADDRESS,
    LockoutContext,
    LockoutFinding,
    LockoutGuard,
    _wildcard_covers,
)
from app.domain.vlans import parse_vlan_list
from app.network.platforms.cisco_ios.rules import normalize_ios

_IFACE = re.compile(r"^interface (?!range )(\S+(?: \S+)?)$")
# Name-bearing patterns run on the case-preserved line: Cisco ACL names are case-sensitive.
_ACL_VIEW = re.compile(r"^(?:ip|ipv6) access-list (?:standard |extended )?(\S+)$", re.I)
_NUMBERED = re.compile(r"^access-list (\d+) (deny|permit) (.+)$")
_ENTRY = re.compile(r"^(?:\d+ |sequence \d+ )?(deny|permit) (.+)$")
_PROTOCOLS = {"ip", "ipv6", "tcp", "udp", "icmp", "gre", "esp", "ahp", "ospf", "eigrp", "pim"}
_VTY_ACL = re.compile(r"^access-class (\S+) in\b", re.I)
_IF_ACL = re.compile(r"^(?:ip access-group|ipv6 traffic-filter) (\S+) in$", re.I)
_TRUNK_REMOVE = re.compile(r"^switchport trunk allowed vlan remove (.+)$")
_TRUNK_SET = re.compile(r"^switchport trunk allowed vlan (\d[\d,\-]*)$")
_TRUNK_NONE = re.compile(r"^switchport trunk allowed vlan (none|except .+)$")


def _source_spec(rest: str) -> str:  # noqa: PLR0911 - one return per ACL address form
    """Source of an ACL entry as '<ip> <wildcard>' or 'any' (standard and extended forms)."""
    words = rest.split()
    if words and words[0] in _PROTOCOLS:
        words = words[1:]  # extended entry: protocol first
    if not words:
        return "any"
    if words[0] == "any":
        return "any"
    if words[0] == "host" and len(words) > 1:
        return f"{words[1]} 0"
    if "/" in words[0]:  # NX-OS CIDR form
        try:
            net = ipaddress.ip_network(words[0], strict=False)
        except ValueError:
            return words[0]  # unparseable -> the coverage check assumes the worst
        return f"{net.network_address} {net.hostmask}"
    if len(words) > 1 and words[1].count(".") == 3:
        return f"{words[0]} {words[1]}"
    return f"{words[0]} 0"  # standard ACL single host without 'host'


class CiscoLockoutGuard(LockoutGuard):
    def check(  # noqa: PLR0912 - one linear pass over a CLI view state machine
        self, lines: list[str], ctx: LockoutContext
    ) -> list[LockoutFinding]:
        dev = ctx.device
        findings: list[LockoutFinding] = []
        iface: str | None = None
        acl: str | None = None
        in_vty = False
        denying: dict[str, str] = {}
        applied: set[str] = set(ctx.mgmt_acls)
        controllers = ctx.controller_ips or (ANY_ADDRESS,)
        for raw in lines:
            text = normalize_ios(raw)
            exact = " ".join(raw.split())  # ACL names are case-sensitive on Cisco
            if match := _IFACE.match(text):
                iface, acl, in_vty = (
                    canonical_interface(match.group(1).replace(" ", "")),
                    None,
                    False,
                )
                continue
            if match := _ACL_VIEW.match(exact):
                acl, iface, in_vty = match.group(1), None, False
                continue
            if text.startswith("line vty"):
                in_vty, iface, acl = True, None, None
                continue
            if text in {"exit", "end"}:
                iface, acl, in_vty = None, None, False
                continue
            if in_vty and (vty := _VTY_ACL.match(exact)):
                applied.add(vty.group(1))
                continue
            if match := _NUMBERED.match(text):
                if match.group(2) == "deny" and _wildcard_covers(
                    _source_spec(match.group(3)), controllers
                ):
                    denying.setdefault(match.group(1), raw)
                continue
            if acl and (entry := _ENTRY.match(text)):
                if entry.group(1) == "deny" and _wildcard_covers(
                    _source_spec(entry.group(2)), controllers
                ):
                    denying.setdefault(acl, raw)
                continue
            findings.extend(self._iface_line(raw, text, iface, dev))
            if iface and dev.is_protected(iface) and (bound := _IF_ACL.match(exact)):
                applied.add(bound.group(1))
            if text.startswith("no interface ") and dev.is_mgmt_vlan_interface(
                canonical_interface(text.removeprefix("no interface ").replace(" ", ""))
            ):
                findings.append(LockoutFinding(raw, "removes the management VLAN interface"))
            if text.startswith("no vlan ") and dev.mgmt_vlan is not None:
                spec = text.removeprefix("no vlan ").replace(" ", "")
                if dev.mgmt_vlan in parse_vlan_list(spec, strict=False):
                    findings.append(LockoutFinding(raw, "deletes the management VLAN"))
        for name, line in denying.items():
            if name in applied:
                findings.append(
                    LockoutFinding(
                        line, f"ACL {name} on the management plane denies the controller"
                    )
                )
        return findings

    @staticmethod
    def _iface_line(  # noqa: PLR0911 - one early return per lockout pattern
        raw: str, text: str, iface: str | None, dev: DeviceContext
    ) -> list[LockoutFinding]:
        if iface is None:
            return []
        is_mgmt = iface == dev.mgmt_interface or dev.is_mgmt_vlan_interface(iface)
        is_mgmt = is_mgmt or iface.startswith(("mgmt", "MEth"))
        is_uplink = iface in dev.uplink_interfaces
        if text == "shutdown" and (is_mgmt or is_uplink):
            return [LockoutFinding(raw, f"shuts down management path interface {iface}")]
        if text.startswith("no ip address") and is_mgmt:
            return [LockoutFinding(raw, f"removes the management IP on {iface}")]
        if not is_uplink or dev.mgmt_vlan is None:
            return []
        if (match := _TRUNK_REMOVE.match(text)) and dev.mgmt_vlan in parse_vlan_list(
            match.group(1), strict=False
        ):
            return [LockoutFinding(raw, f"removes management VLAN from uplink {iface}")]
        if (match := _TRUNK_SET.match(text)) and dev.mgmt_vlan not in parse_vlan_list(
            match.group(1), strict=False
        ):
            return [LockoutFinding(raw, f"uplink {iface} trunk no longer carries mgmt VLAN")]
        if _TRUNK_NONE.match(text):
            return [LockoutFinding(raw, f"uplink {iface} trunk allowed list drops mgmt VLAN")]
        if text == "switchport mode access":
            return [LockoutFinding(raw, f"turns uplink {iface} into an access port")]
        return []
