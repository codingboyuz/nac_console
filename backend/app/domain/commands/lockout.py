"""Self-lockout guard: refuse changes that would cut the controller's management path."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from app.domain.commands.classifier import DeviceContext, canonical_interface, normalize_command
from app.domain.vlans import parse_vlan_list

# Sentinel address used when no controller IP is configured (never bound).
ANY_ADDRESS = "0.0.0.0"  # noqa: S104  # nosec B104

_IFACE = re.compile(r"^interface (\S+)$")
_ACL_VIEW = re.compile(r"^acl (?:number |name )?(\S+)")
_DENY = re.compile(
    r"^rule(?: \d+)? deny(?: (?:ip|tcp|udp|icmp|\d+))?"
    r"(?: source (?P<src>any|\S+ \S+))?(?: destination (?P<dst>any|\S+ \S+))?"
)
_VTY_ACL = re.compile(r"^acl (\S+) inbound$")
# S: "traffic-filter inbound acl 3000"; CE (VRP V8): "traffic-filter acl 3000 inbound".
_TRAFFIC_FILTER = re.compile(
    r"^traffic-filter (?:(?:inbound|outbound) acl (?:ipv6 )?(?:name )?(\S+)"
    r"|acl (?:ipv6 )?(?:name )?(\S+) (?:inbound|outbound))"
)
_ALLOW_PASS_UNDO = re.compile(
    r"^undo port (?:trunk|hybrid) (?:allow-pass|tagged|untagged) vlan (.+)$"
)


@dataclass(frozen=True, slots=True)
class LockoutFinding:
    line: str
    reason: str


@dataclass(frozen=True, slots=True)
class LockoutContext:
    device: DeviceContext
    controller_ips: tuple[str, ...] = ()
    # ACLs already bound to VTY lines or management/uplink interfaces on the device.
    mgmt_acls: frozenset[str] = field(default_factory=frozenset)


def _wildcard_covers(spec: str, ips: tuple[str, ...]) -> bool:
    """Huawei ACL 'source <ip> <wildcard>' or 'any' covers any controller IP?"""
    if spec == "any":
        return True
    try:
        base_s, wildcard_s = spec.split(" ")
        base = int(ipaddress.IPv4Address(base_s))
        wildcard = 0 if wildcard_s == "0" else int(ipaddress.IPv4Address(wildcard_s))
    except ValueError:
        return True  # unparseable -> assume the worst
    for ip in ips:
        try:
            value = int(ipaddress.IPv4Address(ip))
        except ValueError:
            continue
        if (value & ~wildcard) == (base & ~wildcard):
            return True
    return False


class LockoutGuard:
    """Pure check over a configuration block. Returns findings; empty list means safe."""

    def check(  # noqa: PLR0912 - one linear pass over a CLI view state machine
        self, lines: list[str], ctx: LockoutContext
    ) -> list[LockoutFinding]:
        dev = ctx.device
        findings: list[LockoutFinding] = []
        iface: str | None = None
        acl: str | None = None
        in_vty = False
        denying_acls: dict[str, str] = {}
        applied_to_mgmt: set[str] = set(ctx.mgmt_acls)
        for raw in lines:
            text = normalize_command(raw)
            if in_vty and (vty := _VTY_ACL.match(text)):
                applied_to_mgmt.add(vty.group(1))
                continue
            if match := _IFACE.match(text):
                iface, acl, in_vty = canonical_interface(match.group(1)), None, False
                continue
            if match := _ACL_VIEW.match(text):
                acl, iface, in_vty = match.group(1), None, False
                continue
            if text.startswith("user-interface"):
                in_vty, iface, acl = True, None, None
                continue
            if text in {"quit", "return"}:
                iface, acl, in_vty = None, None, False
                continue
            findings.extend(self._line(raw, text, iface, dev))
            if text.startswith("undo interface "):
                target = canonical_interface(text.removeprefix("undo interface "))
                if dev.mgmt_vlan is not None and target == f"Vlanif{dev.mgmt_vlan}":
                    findings.append(LockoutFinding(raw, "removes the management Vlanif"))
            if text.startswith("undo vlan ") and dev.mgmt_vlan is not None:
                spec = text.removeprefix("undo vlan ").removeprefix("batch ")
                if dev.mgmt_vlan in parse_vlan_list(spec, strict=False):
                    findings.append(LockoutFinding(raw, "deletes the management VLAN"))
            if acl and (deny := _DENY.match(text)):
                src = deny.group("src") or "any"
                # No controller configured: any deny rule counts (sentinel address).
                if _wildcard_covers(src, ctx.controller_ips or (ANY_ADDRESS,)):
                    denying_acls[acl] = raw
            if iface and dev.is_protected(iface) and (tf := _TRAFFIC_FILTER.match(text)):
                applied_to_mgmt.add(tf.group(1) or tf.group(2))
        for name, line in denying_acls.items():
            if name in applied_to_mgmt:
                findings.append(
                    LockoutFinding(
                        line, f"ACL {name} on the management plane denies the controller"
                    )
                )
        return findings

    @staticmethod
    def _line(raw: str, text: str, iface: str | None, dev: DeviceContext) -> list[LockoutFinding]:
        if iface is None:
            return []
        is_mgmt = (
            iface == dev.mgmt_interface
            or iface.startswith("MEth")
            or (dev.mgmt_vlan is not None and iface == f"Vlanif{dev.mgmt_vlan}")
        )
        is_uplink = iface in dev.uplink_interfaces
        if text == "shutdown" and (is_mgmt or is_uplink):
            return [LockoutFinding(raw, f"shuts down management path interface {iface}")]
        if text.startswith("undo ip address") and is_mgmt:
            return [LockoutFinding(raw, f"removes the management IP on {iface}")]
        m = _ALLOW_PASS_UNDO.match(text) if is_uplink and dev.mgmt_vlan is not None else None
        if m and dev.mgmt_vlan in parse_vlan_list(m.group(1), strict=False):
            return [LockoutFinding(raw, f"removes management VLAN from uplink {iface}")]
        return []
