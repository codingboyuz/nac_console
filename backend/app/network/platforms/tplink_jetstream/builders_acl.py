"""ACL plans for TP-Link: 'access-list create <id> name <n>' + 'access-list ip <id> rule ...'.

IP ACL ids are 500-999 (a definition must carry such a number); rules are
'access-list ip <id> rule <n> permit|deny logging disable [sip A sip-mask M] [dip A dip-mask M]
[protocol P [s-port X s-port-mask 65535] [d-port X d-port-mask 65535]]'. Bindings are global
('access-list bind <id> interface gigabitEthernet 1/0/1'), ingress only.
"""

from __future__ import annotations

import ipaddress

from app.domain.acl import AclKind, AclRuleSpec, AclValidationError, PortMatch
from app.domain.commands.categories import CommandCategory
from app.domain.commands.sanitize import quote_free_text
from app.domain.devices import DeviceFamily
from app.network.builders.cli_acl import AclDefinition, Direction
from app.network.builders.plan import ConfigPlan
from app.network.parsers.acl import ParsedAcl
from app.network.platforms.tplink_jetstream.naming import tp_iface

D = CommandCategory.DANGEROUS
_PROTOCOLS = {"tcp": 6, "udp": 17, "icmp": 1, "gre": 47, "ospf": 89}


def _acl_id(definition: AclDefinition) -> int:
    if definition.kind is not AclKind.ADVANCED and definition.kind is not AclKind.BASIC:
        raise AclValidationError("only IP ACLs are generated for TP-Link")
    if definition.ipv6:
        raise AclValidationError("IPv6 ACLs are not generated for TP-Link")
    if definition.number is None or not 500 <= definition.number <= 999:
        raise AclValidationError("TP-Link IP ACLs need a number in 500-999")
    return definition.number


def _net(prefix: str, cidr: str | None) -> list[str]:
    if cidr is None:
        return []
    net = ipaddress.ip_network(cidr, strict=False)
    return [prefix, str(net.network_address), f"{prefix}-mask", str(net.netmask)]


def _port(prefix: str, match: PortMatch | None) -> list[str]:
    if match is None:
        return []
    if match.op != "eq":
        raise AclValidationError("TP-Link rules match a single port (eq)")
    return [prefix, str(match.start), f"{prefix}-mask", "65535"]


def render_rule(acl_id: int, rule: AclRuleSpec, kind: AclKind) -> str:
    rule.validate(kind)
    parts = [f"access-list ip {acl_id} rule {rule.rule_id} {rule.action}"]
    parts += ["logging", "enable" if rule.log else "disable"]
    parts += _net("sip", rule.source) + _net("dip", rule.destination)
    if rule.protocol and rule.protocol not in {"ip", "ipv6"}:
        parts += ["protocol", str(_PROTOCOLS[rule.protocol])]
        parts += _port("s-port", rule.source_port) + _port("d-port", rule.destination_port)
    return " ".join(parts)


def _body(text: str) -> str:
    """'access-list ip 500 rule 5 deny ...' -> 'rule 5 deny ...' (parsed ACL form)."""
    return text.split(" ", 3)[3]


def plan_acl(
    desired: AclDefinition, current: ParsedAcl | None, mgmt_acls: frozenset[str] = frozenset()
) -> ConfigPlan:
    acl_id = _acl_id(desired)
    rendered = {r.rule_id: render_rule(acl_id, r, desired.kind) for r in desired.rules}
    if len(rendered) != len(desired.rules):
        raise AclValidationError("duplicate rule ids")
    existing = current.rules if current else {}
    lines: list[str] = []
    undo: list[str] = []
    if current is None:
        name = f' name "{quote_free_text(desired.name, max_length=16)}"' if desired.name else ""
        lines.append(f"access-list create {acl_id}{name}")
        undo.append(f"no access-list {acl_id}")
    for rid, text in sorted(rendered.items()):
        if existing.get(rid) == _body(text):
            continue
        if rid in existing:
            lines.append(f"no access-list ip {acl_id} rule {rid}")
            undo += [
                f"no access-list ip {acl_id} rule {rid}",
                f"access-list ip {acl_id} {existing[rid]}",
            ]
        else:
            undo.append(f"no access-list ip {acl_id} rule {rid}")
        lines.append(text)
    for rid in sorted(set(existing) - set(rendered)):
        lines.append(f"no access-list ip {acl_id} rule {rid}")
        undo.append(f"access-list ip {acl_id} {existing[rid]}")
    if not lines:
        return ConfigPlan("acl.deploy", (), summary="no change")
    category, reason = CommandCategory.WARNING, ""
    had_permit = any(" permit " in f" {t} " for t in existing.values())
    if had_permit and not any(r.action == "permit" for r in desired.rules):
        category, reason = D, "removes the last permit rule of the ACL"
    if {str(acl_id), desired.name or ""} & mgmt_acls:
        category, reason = D, f"ACL {acl_id} filters the management VLAN"
    return ConfigPlan(
        "acl.deploy",
        tuple(lines),
        tuple(undo),
        summary=f"ACL {acl_id}: {len(lines)} change(s)",
        min_category=category,
        min_category_reason=reason,
        mgmt_acls=mgmt_acls,
    )


def delete_acl(definition: AclDefinition, mgmt_acls: frozenset[str]) -> ConfigPlan:
    acl_id = _acl_id(definition)
    category = D if {str(acl_id), definition.name or ""} & mgmt_acls else CommandCategory.WARNING
    return ConfigPlan(
        "acl.delete",
        (f"no access-list {acl_id}",),
        summary=f"delete ACL {acl_id}",
        min_category=category,
        min_category_reason="ACL filters the management VLAN" if category is D else "",
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
        raise AclValidationError("give an interface (or 'Vlan<N>') to bind the ACL to")
    if direction != "inbound":
        raise AclValidationError("TP-Link ACL bindings are ingress only")
    line = f"access-list bind {acl} interface {tp_iface(interface)}"
    apply, undo = (f"no {line}", line) if remove else (line, f"no {line}")
    return ConfigPlan(
        "acl.apply.remove" if remove else "acl.apply",
        (apply,),
        (undo,),
        summary=f"{'remove' if remove else 'apply'} ACL {acl} on {interface}",
        mgmt_acls=mgmt_acls,
    )
