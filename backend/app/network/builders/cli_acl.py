"""ACL plans: rule-level diff against the device, application to interfaces/globally.

VRP-VERSION-DEPENDENT: S-series applies with ``traffic-filter inbound acl 3000`` (interface or
system view); CE (VRP V8) uses ``traffic-filter acl 3000 inbound`` in interface view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.domain.acl import AclKind, AclRuleSpec, AclValidationError, acl_header
from app.domain.commands.categories import CommandCategory
from app.domain.commands.sanitize import quote_free_text
from app.domain.devices import DeviceFamily
from app.domain.interfaces import normalize_interface
from app.network.builders.plan import ConfigPlan
from app.network.parsers.acl import ParsedAcl

RULE_STEP = 5
Direction = Literal["inbound", "outbound"]
D = CommandCategory.DANGEROUS


@dataclass(frozen=True, slots=True)
class AclDefinition:
    kind: AclKind
    number: int | None = None
    name: str | None = None
    ipv6: bool = False
    description: str | None = None
    rules: tuple[AclRuleSpec, ...] = field(default_factory=tuple)

    @property
    def identifier(self) -> str:
        return self.name or str(self.number)

    def header(self) -> str:
        if self.name:
            quote_free_text(self.name, max_length=32)
            if not self.name.replace("-", "").replace("_", "").isalnum() or self.name[0].isdigit():
                raise AclValidationError("ACL names must start with a letter: [A-Za-z0-9_-]")
        return acl_header(self.number, self.name, self.kind, self.ipv6)


def next_rule_id(existing: set[int]) -> int:
    return (max(existing) // RULE_STEP + 1) * RULE_STEP if existing else RULE_STEP


def plan_acl(
    desired: AclDefinition, current: ParsedAcl | None, mgmt_acls: frozenset[str] = frozenset()
) -> ConfigPlan:
    """Emit only rule changes: undo+re-add modified rules, undo removed ones."""
    rendered = {r.rule_id: r.render(desired.kind) for r in desired.rules}
    if len(rendered) != len(desired.rules):
        raise AclValidationError("duplicate rule ids")
    existing = current.rules if current else {}
    body: list[str] = []
    undo: list[str] = []
    for rid, text in sorted(rendered.items()):
        if existing.get(rid) == text:
            continue
        if rid in existing:
            body.append(f"undo rule {rid}")
            undo += [f"undo rule {rid}", existing[rid]]
        else:
            undo.append(f"undo rule {rid}")
        body.append(text)
    for rid in sorted(set(existing) - set(rendered)):
        body.append(f"undo rule {rid}")
        undo.append(existing[rid])
    if desired.description:
        body.append(f"description {quote_free_text(desired.description, max_length=127)}")
    if not body:
        return ConfigPlan("acl.deploy", (), summary="no change")
    header = desired.header()
    had_permit = any(" permit" in t for t in existing.values())
    keeps_permit = any(r.action == "permit" for r in desired.rules)
    category, reason = CommandCategory.WARNING, ""
    if had_permit and not keeps_permit:
        category, reason = D, "removes the last permit rule of the ACL"
    if desired.identifier in mgmt_acls:
        category, reason = D, f"ACL {desired.identifier} protects the management plane (VTY)"
    rollback = (header, *undo, "quit") if current else (f"undo {header}",)
    return ConfigPlan(
        "acl.deploy",
        (header, *body, "quit"),
        rollback,
        summary=f"ACL {desired.identifier}: {len(body)} change(s)",
        min_category=category,
        min_category_reason=reason,
        mgmt_acls=mgmt_acls,
    )


def delete_acl(definition: AclDefinition, mgmt_acls: frozenset[str]) -> ConfigPlan:
    header = definition.header()
    category = D if definition.identifier in mgmt_acls else CommandCategory.WARNING
    return ConfigPlan(
        "acl.delete",
        (f"undo {header}",),
        summary=f"delete ACL {definition.identifier}",
        min_category=category,
        min_category_reason="ACL is bound to VTY lines" if category is D else "",
        mgmt_acls=mgmt_acls,
    )


def _filter_line(family: DeviceFamily, acl: str, direction: Direction, ipv6: bool) -> str:
    ref = f"ipv6 {acl}" if ipv6 else acl
    if family is DeviceFamily.CE:
        return f"traffic-filter acl {ref} {direction}"
    return f"traffic-filter {direction} acl {ref}"


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
    line = _filter_line(family, acl, direction, ipv6)
    apply, undo = (f"undo {line}", line) if remove else (line, f"undo {line}")
    if interface:
        name = normalize_interface(interface)
        lines: tuple[str, ...] = (f"interface {name}", apply, "quit")
        rollback: tuple[str, ...] = (f"interface {name}", undo, "quit")
    else:
        if family is DeviceFamily.CE:
            raise AclValidationError(
                "global traffic-filter is not supported on CE; use an interface"
            )
        lines, rollback = (apply,), (undo,)
    target = interface or "global"
    return ConfigPlan(
        "acl.apply.remove" if remove else "acl.apply",
        lines,
        rollback,
        summary=f"{'remove' if remove else 'apply'} ACL {acl} {direction} on {target}",
        # A global filter affects management traffic as well.
        min_category=D if interface is None and not remove else CommandCategory.SAFE,
        min_category_reason="global traffic-filter also filters management traffic",
        mgmt_acls=mgmt_acls,
    )
