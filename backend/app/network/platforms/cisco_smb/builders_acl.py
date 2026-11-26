"""ACL plans for Cisco SMB: 'ip access-list extended NAME' with 'ace-priority' ordering.

Entries are 'permit|deny <proto> <src> <wc> [<sport>] <dst> <wc> [<dport>] ace-priority N';
TCP/UDP always carry a source and destination port slot ('any' when unset). There is no
'host' keyword (wildcard 0.0.0.0). An entry is removed by repeating it with 'no' (without the
priority). Bound with 'service-acl input NAME' (one ACL per port, ingress or egress).
"""

from __future__ import annotations

import ipaddress

from app.domain.acl import AclKind, AclRuleSpec, AclValidationError, PortMatch
from app.domain.commands.categories import CommandCategory
from app.domain.devices import DeviceFamily
from app.domain.interfaces import normalize_interface
from app.network.builders.cli_acl import AclDefinition, Direction
from app.network.builders.plan import ConfigPlan
from app.network.parsers.acl import ParsedAcl
from app.network.platforms.cisco_smb.parsers import canonical_smb_entry

D = CommandCategory.DANGEROUS


def _addr(cidr: str | None) -> str:
    if cidr is None:
        return "any"
    net = ipaddress.ip_network(cidr, strict=False)
    if net.version == 6:
        raise AclValidationError("IPv6 entries are not generated for Cisco SMB")
    return "any" if net.prefixlen == 0 else f"{net.network_address} {net.hostmask}"


def _port(match: PortMatch | None) -> str:
    if match is None:
        return "any"
    if match.op == "eq":
        return str(match.start)
    if match.op == "range":
        return f"{match.start}-{match.end}"
    raise AclValidationError("Cisco SMB ports support 'eq' and 'range' only")


def render_entry(rule: AclRuleSpec, kind: AclKind) -> str:
    """Entry text without the priority (the form used for 'no <entry>' and comparisons)."""
    if kind is AclKind.LAYER2:
        raise AclValidationError("MAC ACLs are not generated for Cisco SMB")
    rule.validate(kind)
    proto = rule.protocol or "ip"
    parts = [rule.action, proto, _addr(rule.source)]
    if proto in {"tcp", "udp"}:
        parts.append(_port(rule.source_port))
    parts.append(_addr(rule.destination))
    if proto in {"tcp", "udp"}:
        parts.append(_port(rule.destination_port))
    return " ".join(parts)


def _header(definition: AclDefinition) -> str:
    if definition.kind is AclKind.LAYER2:
        raise AclValidationError("MAC ACLs are not generated for Cisco SMB")
    if not definition.name:
        raise AclValidationError("Cisco SMB ACLs are named: set 'name'")
    if definition.ipv6:
        raise AclValidationError("IPv6 ACLs are not generated for Cisco SMB")
    definition.header()
    return f"ip access-list extended {definition.name}"


def plan_acl(
    desired: AclDefinition, current: ParsedAcl | None, mgmt_acls: frozenset[str] = frozenset()
) -> ConfigPlan:
    header = _header(desired)
    wanted = {r.rule_id: render_entry(r, desired.kind) for r in desired.rules}
    if len(wanted) != len(desired.rules):
        raise AclValidationError("duplicate rule ids")
    for rid in wanted:
        if not 1 <= rid <= 2147483647:
            raise AclValidationError("ace-priority must be 1-2147483647")
    existing = {k: canonical_smb_entry(v) for k, v in (current.rules if current else {}).items()}
    body: list[str] = []
    undo: list[str] = []
    for prio, text in sorted(existing.items()):
        if wanted.get(prio) != text:
            body.append(f"no {text}")
            undo.append(f"{text} ace-priority {prio}")
    for prio, text in sorted(wanted.items()):
        if existing.get(prio) != text:
            body.append(f"{text} ace-priority {prio}")
            undo.insert(0, f"no {text}")
    if not body:
        return ConfigPlan("acl.deploy", (), summary="no change")
    category, reason = CommandCategory.WARNING, ""
    had_permit = any(t.startswith("permit") for t in existing.values())
    if had_permit and not any(r.action == "permit" for r in desired.rules):
        category, reason = D, "removes the last permit entry of the ACL"
    if desired.identifier in mgmt_acls:
        category, reason = D, f"ACL {desired.identifier} protects the management plane"
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
        min_category_reason="ACL protects the management plane" if category is D else "",
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
        raise AclValidationError("Cisco SMB binds ACLs per interface; give an interface")
    word = "input" if direction == "inbound" else "output"
    name = normalize_interface(interface)
    line = f"service-acl {word} {acl}"
    apply, undo = (f"no service-acl {word}", line) if remove else (line, f"no service-acl {word}")
    return ConfigPlan(
        "acl.apply.remove" if remove else "acl.apply",
        (f"interface {name}", apply, "exit"),
        (f"interface {name}", undo, "exit"),
        summary=f"{'remove' if remove else 'apply'} ACL {acl} {direction} on {name}",
        notes=("A port takes one ACL per direction: remove the current binding first.",),
        mgmt_acls=mgmt_acls,
    )
