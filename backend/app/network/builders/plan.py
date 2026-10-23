"""ConfigPlan: what a typed builder wants applied, how to undo it and how to verify it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

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

    @property
    def noop(self) -> bool:
        return not self.lines and self.netconf_xml is None

    def preview(self) -> list[str]:
        return list(self.lines) if self.lines else ([self.netconf_xml] if self.netconf_xml else [])
