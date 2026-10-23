"""Per-request marker so the audit middleware does not duplicate service-level audit rows."""

from __future__ import annotations

from contextvars import ContextVar

_marker: ContextVar[dict[str, bool] | None] = ContextVar("audit_marker", default=None)


def begin_request() -> dict[str, bool]:
    marker = {"audited": False}
    _marker.set(marker)
    return marker


def mark_audited() -> None:
    marker = _marker.get()
    if marker is not None:
        marker["audited"] = True
