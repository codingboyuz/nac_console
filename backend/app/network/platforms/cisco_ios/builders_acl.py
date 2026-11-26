"""ACL plans in Cisco IOS syntax: named standard/extended/MAC lists with sequence numbers.

The shared ``AclDefinition`` maps to: basic -> 'ip access-list standard NAME', advanced ->
'ip access-list extended NAME', layer2 -> 'mac access-list extended NAME' (IOS MAC lists have
no sequence numbers, so they are replaced as a whole). Cisco lists are named; a definition
without a name is rejected. Applied with 'ip access-group NAME in|out' on an interface; IOS has
no global filter equivalent of Huawei's system-view traffic-filter.
"""

from __future__ import annotations

import re

from app.domain.acl import AclKind, AclRuleSpec, AclValidationError
from app.domain.commands.categories import CommandCategory
from app.domain.devices import DeviceFamily
from app.domain.interfaces import normalize_interface
from app.domain.mac import to_cisco
from app.network.builders.cli_acl import AclDefinition, Direction
from app.network.builders.plan import ConfigPlan
from app.network.parsers.acl import ParsedAcl

D = CommandCategory.DANGEROUS

# Well-known port names IOS prints in 'show access-lists' -> numbers (canonical form).
_PORT_NAMES = {
    "ftp-data": 20,
    "ftp": 21,
    "ssh": 22,
    "telnet": 23,
    "smtp": 25,
    "domain": 53,
    "bootps": 67,
    "bootpc": 68,
    "tftp": 69,
    "www": 80,
    "pop3": 110,
    "ntp": 123,
    "snmp": 161,
    "snmptrap": 162,
    "bgp": 179,
    "ldap": 389,
    "syslog": 514,
}
_WILDCARD_BITS = re.compile(r"(\S+), wildcard bits (\S+)")
_MATCHES = re.compile(r"\s*\(\d+ matches?\)")
_HOST_WILDCARD = "0.0.0.0"  # noqa: S104 - ACL host wildcard, not a bind address
_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def canonical_entry(text: str) -> str:
    """Normalize an IOS ACL entry so device output and generated text compare equal."""
    text = _MATCHES.sub("", " ".join(text.split()))
    text = _WILDCARD_BITS.sub(r"\1 \2", text)
    words = text.split(" ")
    out: list[str] = []
    for i, word in enumerate(words):
        if (i and words[i - 1] in {"eq", "gt", "lt", "neq", "range"} and word in _PORT_NAMES) or (
            i > 1 and words[i - 2] == "range" and word in _PORT_NAMES
        ):
            out.append(str(_PORT_NAMES[word]))
        else:
            out.append(word)
    # 'host A' and a bare standard-ACL host 'A' both mean 'A 0.0.0.0'.
    canon: list[str] = []
    i = 0
    while i < len(out):
        if out[i] == "host" and i + 1 < len(out):
            canon += [out[i + 1], _HOST_WILDCARD]
            i += 2
            continue
        canon.append(out[i])
        i += 1
    bare_host = len(canon) >= 3 and canon[1] in {"permit", "deny"} and _IPV4.match(canon[2])
    if bare_host and (len(canon) == 3 or not _IPV4.match(canon[3])):
        canon.insert(3, _HOST_WILDCARD)
    return " ".join(canon)


def _addr(cidr: str | None) -> str:
    if cidr is None:
        return "any"
    import ipaddress

    net = ipaddress.ip_network(cidr, strict=False)
    if net.version == 6:
        return "any" if net.prefixlen == 0 else str(net)
    if net.prefixlen == 32:
        return f"host {net.network_address}"
    if net.prefixlen == 0:
        return "any"
    return f"{net.network_address} {net.hostmask}"


def render_rule(rule: AclRuleSpec, kind: AclKind, *, ipv6: bool = False) -> str:
    rule.validate(kind)
    if kind is AclKind.LAYER2:
        src = f"host {to_cisco(rule.source_mac)}" if rule.source_mac else "any"
        dst = f"host {to_cisco(rule.destination_mac)}" if rule.destination_mac else "any"
        parts = [rule.action, src, dst]
        if rule.vlan is not None:
            parts.append(f"vlan {rule.vlan}")
        return " ".join(parts)
    if kind is AclKind.BASIC:
        parts = [str(rule.rule_id), rule.action, _addr(rule.source)]
    else:
        proto = rule.protocol or "ip"
        if ipv6 and proto == "ip":
            proto = "ipv6"
        parts = [str(rule.rule_id), rule.action, proto, _addr(rule.source)]
        if rule.source_port:
            parts.append(rule.source_port.render())
        parts.append(_addr(rule.destination))
        if rule.destination_port:
            parts.append(rule.destination_port.render())
    if rule.log:
        parts.append("log")
    if ipv6:
        # IPv6 lists put the sequence at the end: 'permit ipv6 any any sequence 10'.
        seq = parts.pop(0)
        parts += ["sequence", seq]
    return " ".join(parts)


def _name(definition: AclDefinition) -> str:
    if not definition.name:
        raise AclValidationError("Cisco ACLs are named: set 'name'")
    definition.header()  # name validation shared with Huawei
    return definition.name


def _header(definition: AclDefinition) -> str:
    name = _name(definition)
    if definition.kind is AclKind.LAYER2:
        return f"mac access-list extended {name}"
    if definition.ipv6:
        return f"ipv6 access-list {name}"
    kind = "standard" if definition.kind is AclKind.BASIC else "extended"
    return f"ip access-list {kind} {name}"


def plan_acl(
    desired: AclDefinition, current: ParsedAcl | None, mgmt_acls: frozenset[str] = frozenset()
) -> ConfigPlan:
    header = _header(desired)
    if desired.kind is AclKind.LAYER2:
        return _replace_mac_acl(desired, header, mgmt_acls)
    rendered = {r.rule_id: render_rule(r, desired.kind, ipv6=desired.ipv6) for r in desired.rules}
    if len(rendered) != len(desired.rules):
        raise AclValidationError("duplicate rule ids")
    existing = {rid: canonical_entry(t) for rid, t in (current.rules if current else {}).items()}
    body: list[str] = []
    undo: list[str] = []
    for rid, text in sorted(rendered.items()):
        if existing.get(rid) == canonical_entry(text):
            continue
        if rid in existing:
            body.append(f"no {rid}")
            undo += [f"no {rid}", existing[rid]]
        else:
            undo.append(f"no {rid}")
        body.append(text)
    for rid in sorted(set(existing) - set(rendered)):
        body.append(f"no {rid}")
        undo.append(existing[rid])
    if desired.description:
        from app.domain.commands.sanitize import quote_free_text

        body.append(f"remark {quote_free_text(desired.description, max_length=100)}")
    if not body:
        return ConfigPlan("acl.deploy", (), summary="no change")
    category, reason = _category(desired, existing, mgmt_acls)
    rollback = (header, *undo, "exit") if current else (f"no {header}",)
    return ConfigPlan(
        "acl.deploy",
        (header, *body, "exit"),
        rollback,
        summary=f"ACL {desired.identifier}: {len(body)} change(s)",
        min_category=category,
        min_category_reason=reason,
        mgmt_acls=mgmt_acls,
    )


def _category(
    desired: AclDefinition, existing: dict[int, str], mgmt_acls: frozenset[str]
) -> tuple[CommandCategory, str]:
    had_permit = any(" permit " in f" {t} " for t in existing.values())
    keeps_permit = any(r.action == "permit" for r in desired.rules)
    if desired.identifier in mgmt_acls:
        return D, f"ACL {desired.identifier} protects the management plane (VTY)"
    if had_permit and not keeps_permit:
        return D, "removes the last permit entry of the ACL"
    return CommandCategory.WARNING, ""


def _replace_mac_acl(desired: AclDefinition, header: str, mgmt_acls: frozenset[str]) -> ConfigPlan:
    entries = [
        render_rule(r, AclKind.LAYER2) for r in sorted(desired.rules, key=lambda r: r.rule_id)
    ]
    return ConfigPlan(
        "acl.deploy",
        (f"no {header}", header, *entries, "exit"),
        (f"no {header}",),
        summary=f"MAC ACL {desired.identifier}: replaced ({len(entries)} entries)",
        min_category=CommandCategory.WARNING,
        notes=("IOS MAC access lists have no sequence numbers; the list is rebuilt.",),
        mgmt_acls=mgmt_acls,
    )


def delete_acl(definition: AclDefinition, mgmt_acls: frozenset[str]) -> ConfigPlan:
    header = _header(definition)
    category = D if definition.identifier in mgmt_acls else CommandCategory.WARNING
    return ConfigPlan(
        "acl.delete",
        (f"no {header}",),
        summary=f"delete ACL {definition.identifier}",
        min_category=category,
        min_category_reason="ACL is bound to VTY lines" if category is D else "",
        mgmt_acls=mgmt_acls,
    )


def apply_acl(
    family: DeviceFamily,
    acl: str,
    direction: Direction,
    *,
    interface: str | None,
    remove: bool = False,
    ipv6: bool = False,
    mgmt_acls: frozenset[str] = frozenset(),
) -> ConfigPlan:
    if not interface:
        raise AclValidationError("IOS applies ACLs per interface (or VTY); give an interface")
    word = "in" if direction == "inbound" else "out"
    line = f"ipv6 traffic-filter {acl} {word}" if ipv6 else f"ip access-group {acl} {word}"
    apply, undo = (f"no {line}", line) if remove else (line, f"no {line}")
    name = normalize_interface(interface)
    return ConfigPlan(
        "acl.apply.remove" if remove else "acl.apply",
        (f"interface {name}", apply, "exit"),
        (f"interface {name}", undo, "exit"),
        summary=f"{'remove' if remove else 'apply'} ACL {acl} {direction} on {name}",
        mgmt_acls=mgmt_acls,
    )
