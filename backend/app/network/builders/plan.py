"""ConfigPlan: what a typed builder wants applied, how to undo it and how to verify it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain.commands.categories import CommandCategory
from app.network.ports import DeviceDriver

Verifier = Callable[[DeviceDriver], bool]


@dataclass(frozen=True, slots=True)
class ConfigPlan:
    """Generated (never user-typed) configuration for one device.

    ``lines`` run in system-view. ``rollback`` is the best-effort inverse used when
    post-verification fails. ``noop`` plans (desired state already present) are skipped.
    """

    operation: str
    lines: tuple[str, ...]
    rollback: tuple[str, ...] = ()
    verify: Verifier | None = None
    summary: str = ""
    netconf_xml: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    # Feature-level escalation (e.g. removing the last permit of an ACL) and facts the lockout
    # guard needs (ACLs already bound to VTY / management interfaces).
    min_category: CommandCategory = CommandCategory.SAFE
    min_category_reason: str = ""
    mgmt_acls: frozenset[str] = field(default_factory=frozenset)
    # Arm the device-side rollback timer (CE commit trial / S schedule reboot) even without
    # lockout findings, e.g. for restores.
    wants_rollback_timer: bool = False

    @property
    def noop(self) -> bool:
        return not self.lines and self.netconf_xml is None

    def preview(self) -> list[str]:
        return list(self.lines) if self.lines else ([self.netconf_xml] if self.netconf_xml else [])
