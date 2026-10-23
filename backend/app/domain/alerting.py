"""Threshold evaluation with for-duration and hysteresis, flap detection, maintenance windows."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class Transition(StrEnum):
    NONE = "none"
    PENDING = "pending"  # breached, waiting for for_seconds
    FIRE = "fire"
    STILL_FIRING = "still_firing"
    RESOLVE = "resolve"


@dataclass(frozen=True, slots=True)
class ThresholdRule:
    operator: str  # gt | lt
    threshold: float
    clear_threshold: float | None = None
    for_seconds: int = 0

    def breached(self, value: float) -> bool:
        return value > self.threshold if self.operator == "gt" else value < self.threshold

    def cleared(self, value: float) -> bool:
        """Hysteresis: must cross the clear threshold (defaults to the threshold)."""
        clear = self.threshold if self.clear_threshold is None else self.clear_threshold
        return value <= clear if self.operator == "gt" else value >= clear


@dataclass(frozen=True, slots=True)
class EvalState:
    firing: bool
    pending_since: datetime | None


def evaluate(
    rule: ThresholdRule, value: float | None, state: EvalState, now: datetime
) -> tuple[Transition, EvalState]:
    """Pure state machine for one (rule, device, interface) series."""
    if value is None:
        return Transition.NONE, state
    if state.firing:
        if rule.cleared(value):
            return Transition.RESOLVE, EvalState(False, None)
        return Transition.STILL_FIRING, state
    if not rule.breached(value):
        return Transition.NONE, EvalState(False, None)
    since = state.pending_since or now
    if now - since >= timedelta(seconds=rule.for_seconds):
        return Transition.FIRE, EvalState(True, None)
    return Transition.PENDING, EvalState(False, since)


def is_flapping(
    transitions: Iterable[datetime], now: datetime, window_seconds: int, max_transitions: int
) -> bool:
    recent = [t for t in transitions if now - t <= timedelta(seconds=window_seconds)]
    return len(recent) >= max_transitions


def in_scope(scope: dict[str, Any], device: dict[str, Any]) -> bool:
    """Empty scope matches every device; otherwise every given key must match."""
    return all(
        str(device.get(key)) == str(value) for key, value in scope.items() if value is not None
    )


def in_maintenance(
    windows: Iterable[tuple[dict[str, Any], datetime, datetime]],
    device: dict[str, Any],
    now: datetime,
) -> bool:
    return any(start <= now <= end and in_scope(scope, device) for scope, start, end in windows)


DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "name": "high-cpu",
        "alert_type": "high_cpu",
        "metric": "cpu",
        "operator": "gt",
        "threshold": 85,
        "clear_threshold": 70,
        "for_seconds": 120,
        "severity": "warning",
    },
    {
        "name": "high-memory",
        "alert_type": "high_memory",
        "metric": "memory",
        "operator": "gt",
        "threshold": 90,
        "clear_threshold": 80,
        "for_seconds": 300,
        "severity": "warning",
    },
    {
        "name": "crc-errors",
        "alert_type": "crc_errors",
        "metric": "if_in_errors_ps",
        "operator": "gt",
        "threshold": 1,
        "clear_threshold": 0,
        "for_seconds": 60,
        "severity": "warning",
    },
    {
        "name": "high-traffic",
        "alert_type": "high_traffic",
        "metric": "if_util_pct",
        "operator": "gt",
        "threshold": 90,
        "clear_threshold": 75,
        "for_seconds": 300,
        "severity": "info",
    },
    {
        "name": "interface-down",
        "alert_type": "interface_down",
        "metric": "if_oper",
        "operator": "lt",
        "threshold": 1,
        "clear_threshold": 1,
        "for_seconds": 0,
        "severity": "critical",
    },
]
