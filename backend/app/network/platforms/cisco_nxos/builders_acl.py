"""ACL plans for NX-OS: 'ip access-list NAME' with sequence numbers and CIDR addresses.

NX-OS has no standard/extended split; basic definitions render as 'permit ip <src> any'.
Applied as a port ACL ('ip port access-group NAME in') on L2 ports (Ethernet / port-channel)
and as a routed ACL ('ip access-group NAME in|out') on SVIs and other L3 interfaces.
MAC ACLs ('mac access-list') are not generated here.
"""

from __future__ import annotations

import ipaddress

from app.domain.acl import AclKind, AclRuleSpec, AclValidationError
from app.domain.commands.categories import CommandCategory
from app.domain.commands.sanitize import quote_free_text
from app.domain.devices import DeviceFamily
from app.domain.interfaces import parse_interface
from app.network.builders.cli_acl import AclDefinition, Direction
from app.network.builders.plan import ConfigPlan
from app.network.parsers.acl import ParsedAcl
from app.network.platforms.cisco_ios.builders_acl import canonical_entry

D = CommandCategory.DANGEROUS


def _addr(cidr: str | None) -> str:
    if cidr is None:
        return "any"
    net = ipaddress.ip_network(cidr, strict=False)
    return "any" if net.prefixlen == 0 else str(net)


def render_rule(rule: AclRuleSpec, kind: AclKind, *, ipv6: bool = False) -> str:
    if kind is AclKind.LAYER2:
        raise AclValidationError("NX-OS MAC ACLs are not generated; use an IP ACL")
    rule.validate(kind)
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
    return " ".join(parts)


def _header(definition: AclDefinition) -> str:
    if not definition.name:
        raise AclValidationError("NX-OS ACLs are named: set 'name'")
    definition.header()
    return f"{'ipv6' if definition.ipv6 else 'ip'} access-list {definition.name}"


def plan_acl(
    desired: AclDefinition, current: ParsedAcl | None, mgmt_acls: frozenset[str] = frozenset()
) -> ConfigPlan:
    header = _header(desired)
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
        body.append(f"remark {quote_free_text(desired.description, max_length=100)}")
    if not body:
        return ConfigPlan("acl.deploy", (), summary="no change")
    category, reason = CommandCategory.WARNING, ""
    had_permit = any(" permit " in f" {t} " for t in existing.values())
    if had_permit and not any(r.action == "permit" for r in desired.rules):
        category, reason = D, "removes the last permit entry of the ACL"
    if desired.identifier in mgmt_acls:
        category, reason = D, f"ACL {desired.identifier} protects the management plane (VTY)"
    return ConfigPlan(
        "acl.deploy",
        (header, *body, "exit"),
        (header, *undo, "exit") if current else (f"no {header}",),
        summary=f"ACL {desired.identifier}: {len(body)} change(s)",
        min_category=category,
        min_category_reason=reason,
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
        raise AclValidationError("NX-OS applies ACLs per interface; give an interface")
    parsed = parse_interface(interface)
    word = "in" if direction == "inbound" else "out"
    family_kw = "ipv6" if ipv6 else "ip"
    if parsed.is_physical or parsed.is_lag:
        if word == "out":
            raise AclValidationError("NX-OS port ACLs are ingress only")
        line = f"{family_kw} port access-group {acl} in"
    else:
        line = f"ipv6 traffic-filter {acl} {word}" if ipv6 else f"ip access-group {acl} {word}"
    apply, undo = (f"no {line}", line) if remove else (line, f"no {line}")
    name = parsed.canonical
    return ConfigPlan(
        "acl.apply.remove" if remove else "acl.apply",
        (f"interface {name}", apply, "exit"),
        (f"interface {name}", undo, "exit"),
        summary=f"{'remove' if remove else 'apply'} ACL {acl} {direction} on {name}",
        mgmt_acls=mgmt_acls,
    )
