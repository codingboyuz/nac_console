"""Declarative NAC policy engine: whitelisted conditions/actions, priority order, first match.

No eval, no user code, no CLI. Unknown condition/action types fail validation (pydantic
discriminated unions). Lower ``priority`` numbers are evaluated first; a mandatory default
policy (unknown -> quarantine VLAN) always terminates evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.mac import oui

DEFAULT_POLICY_PRIORITY = 2_147_483_647
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _all_days() -> list[Day]:
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- conditions --------------------------------------------------------------------------------
class MacRegistered(_Strict):
    type: Literal["mac_registered"]


class EndpointGroupIs(_Strict):
    type: Literal["endpoint_group_is"]
    group: str = Field(min_length=1, max_length=64)


class UserInGroup(_Strict):
    type: Literal["user_in_group"]
    group: str = Field(min_length=1, max_length=128)


class DeviceTypeIs(_Strict):
    type: Literal["device_type_is"]
    device_type: str = Field(min_length=1, max_length=64)


class OuiIn(_Strict):
    type: Literal["oui_in"]
    ouis: list[str] = Field(min_length=1, max_length=500)


class TimeWindow(_Strict):
    type: Literal["time_window"]
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[Day] = Field(default_factory=_all_days)


class StatusIs(_Strict):
    type: Literal["status_is"]
    status: Literal["AUTHORIZED", "UNKNOWN", "QUARANTINED", "BLOCKED", "PENDING"]


class AuthMethodIs(_Strict):
    type: Literal["auth_method_is"]
    method: Literal["dot1x", "mab"]


class SiteIs(_Strict):
    type: Literal["site_is"]
    site: str = Field(min_length=1, max_length=128)


class AllOf(_Strict):
    type: Literal["all_of"]
    conditions: list[Condition] = Field(min_length=1, max_length=20)


class AnyOf(_Strict):
    type: Literal["any_of"]
    conditions: list[Condition] = Field(min_length=1, max_length=20)


class Not(_Strict):
    type: Literal["not"]
    condition: Condition


class Always(_Strict):
    type: Literal["always"]


Condition = Annotated[
    MacRegistered
    | EndpointGroupIs
    | UserInGroup
    | DeviceTypeIs
    | OuiIn
    | TimeWindow
    | StatusIs
    | AuthMethodIs
    | SiteIs
    | AllOf
    | AnyOf
    | Not
    | Always,
    Field(discriminator="type"),
]
AllOf.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()


# --- actions -------------------------------------------------------------------------------------
class AssignVlan(_Strict):
    type: Literal["assign_vlan"]
    vlan: int = Field(ge=1, le=4094)
    filter_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    session_timeout: int | None = Field(default=None, ge=60, le=86400)


class Guest(_Strict):
    type: Literal["guest"]


class Quarantine(_Strict):
    type: Literal["quarantine"]


class Reject(_Strict):
    type: Literal["reject"]


class Accept(_Strict):
    type: Literal["accept"]
    filter_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")


Action = Annotated[AssignVlan | Guest | Quarantine | Reject | Accept, Field(discriminator="type")]


class PolicySpec(_Strict):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    priority: int = Field(ge=0, le=DEFAULT_POLICY_PRIORITY)
    condition: Condition
    action: Action
    enabled: bool = True


# --- evaluation ----------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RequestFacts:
    mac: str
    username: str | None = None
    auth_method: str = "mab"
    user_groups: frozenset[str] = frozenset()
    site: str | None = None
    now: datetime | None = None
    registered: bool = False
    endpoint_group: str | None = None
    device_type: str | None = None
    status: str = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Decision:
    policy_name: str
    policy_id: str | None
    result: Literal["accept", "reject"]
    vlan: int | None
    endpoint_status: str
    reply: dict[str, str] = field(default_factory=dict)


def _time_ok(cond: TimeWindow, now: datetime | None) -> bool:
    if now is None:
        return False
    day = _DAYS[now.weekday()]
    hhmm = now.strftime("%H:%M")
    inside = (
        cond.start <= hhmm < cond.end
        if cond.start <= cond.end
        else (hhmm >= cond.start or hhmm < cond.end)
    )
    return day in cond.days and inside


def matches(cond: Any, facts: RequestFacts) -> bool:  # noqa: PLR0911,PLR0912 - dispatch table
    match cond:
        case MacRegistered():
            return facts.registered
        case EndpointGroupIs():
            return facts.endpoint_group == cond.group
        case UserInGroup():
            return cond.group in facts.user_groups
        case DeviceTypeIs():
            return (facts.device_type or "").lower() == cond.device_type.lower()
        case OuiIn():
            return oui(facts.mac) in {o.upper().replace("-", ":") for o in cond.ouis}
        case TimeWindow():
            return _time_ok(cond, facts.now)
        case StatusIs():
            return facts.status == cond.status
        case AuthMethodIs():
            return facts.auth_method == cond.method
        case SiteIs():
            return facts.site == cond.site
        case AllOf():
            return all(matches(c, facts) for c in cond.conditions)
        case AnyOf():
            return any(matches(c, facts) for c in cond.conditions)
        case Not():
            return not matches(cond.condition, facts)
        case Always():
            return True
    raise ValueError(f"unsupported condition {cond!r}")


def tunnel_attributes(vlan: int) -> dict[str, str]:
    """RFC 3580 dynamic VLAN assignment attributes."""
    return {
        "Tunnel-Type": "VLAN",
        "Tunnel-Medium-Type": "IEEE-802",
        "Tunnel-Private-Group-Id": str(vlan),
    }


def decide(
    action: Any, *, policy_name: str, policy_id: str | None, guest_vlan: int, quarantine_vlan: int
) -> Decision:
    match action:
        case AssignVlan():
            reply = tunnel_attributes(action.vlan)
            if action.filter_id:
                reply["Filter-Id"] = action.filter_id
            if action.session_timeout:
                reply["Session-Timeout"] = str(action.session_timeout)
            return Decision(policy_name, policy_id, "accept", action.vlan, "AUTHORIZED", reply)
        case Guest():
            return Decision(
                policy_name,
                policy_id,
                "accept",
                guest_vlan,
                "AUTHORIZED",
                tunnel_attributes(guest_vlan),
            )
        case Quarantine():
            return Decision(
                policy_name,
                policy_id,
                "accept",
                quarantine_vlan,
                "QUARANTINED",
                tunnel_attributes(quarantine_vlan),
            )
        case Reject():
            return Decision(
                policy_name,
                policy_id,
                "reject",
                None,
                "BLOCKED",
                {"Reply-Message": "Access denied by policy"},
            )
        case Accept():
            return Decision(
                policy_name,
                policy_id,
                "accept",
                None,
                "AUTHORIZED",
                {"Filter-Id": action.filter_id} if action.filter_id else {},
            )
    raise ValueError(f"unsupported action {action!r}")


def evaluate(
    policies: list[tuple[str | None, PolicySpec]],
    facts: RequestFacts,
    *,
    guest_vlan: int,
    quarantine_vlan: int,
) -> Decision:
    """First enabled match by ascending priority; falls back to the mandatory default."""
    for policy_id, spec in sorted(policies, key=lambda p: (p[1].priority, p[1].name)):
        if spec.enabled and matches(spec.condition, facts):
            return decide(
                spec.action,
                policy_name=spec.name,
                policy_id=policy_id,
                guest_vlan=guest_vlan,
                quarantine_vlan=quarantine_vlan,
            )
    return decide(
        Quarantine(type="quarantine"),
        policy_name="builtin-default",
        policy_id=None,
        guest_vlan=guest_vlan,
        quarantine_vlan=quarantine_vlan,
    )


def fail_mode_decision(mode: str, quarantine_vlan: int) -> Decision:
    """Engine could not decide: fail-closed -> quarantine VLAN, fail-open -> plain accept."""
    if mode == "open":
        return Decision("fail-open", None, "accept", None, "PENDING", {})
    return Decision(
        "fail-closed",
        None,
        "accept",
        quarantine_vlan,
        "QUARANTINED",
        tunnel_attributes(quarantine_vlan),
    )


DEFAULT_POLICIES: list[dict[str, Any]] = [
    {
        "name": "blocked",
        "priority": 10,
        "condition": {"type": "status_is", "status": "BLOCKED"},
        "action": {"type": "reject"},
    },
    {
        "name": "registered-employee",
        "priority": 100,
        "condition": {"type": "mac_registered"},
        "action": {"type": "assign_vlan", "vlan": 10},
    },
    {
        "name": "guest",
        "priority": 200,
        "condition": {"type": "endpoint_group_is", "group": "guest"},
        "action": {"type": "guest"},
    },
    {
        "name": "default-quarantine",
        "priority": DEFAULT_POLICY_PRIORITY,
        "condition": {"type": "always"},
        "action": {"type": "quarantine"},
    },
]
