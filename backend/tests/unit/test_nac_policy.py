"""Table-driven NAC policy engine tests, validation hardening, fail modes, profiling, templates."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.commands.categories import CommandCategory
from app.domain.devices import DeviceFamily
from app.domain.nac import policy as engine
from app.domain.nac.profiling import ProfileSignals, profile
from app.network.builders import cli_nac

DEFAULTS = [
    (f"id-{p['name']}", engine.PolicySpec.model_validate(p)) for p in engine.DEFAULT_POLICIES
]
MON_10AM = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # Monday
SUN_10PM = datetime(2026, 10, 25, 22, 0, tzinfo=UTC)


def _eval(policies: list[Any], **facts: Any) -> engine.Decision:
    base = {"mac": "00:11:22:33:44:55"}
    base.update(facts)
    return engine.evaluate(
        policies, engine.RequestFacts(**base), guest_vlan=20, quarantine_vlan=999
    )


@pytest.mark.parametrize(
    ("facts", "policy", "result", "vlan", "status"),
    [
        ({"registered": True}, "registered-employee", "accept", 10, "AUTHORIZED"),
        ({"endpoint_group": "guest"}, "guest", "accept", 20, "AUTHORIZED"),
        ({}, "default-quarantine", "accept", 999, "QUARANTINED"),
        ({"status": "BLOCKED", "registered": True}, "blocked", "reject", None, "BLOCKED"),
    ],
)
def test_required_default_policies(
    facts: dict[str, Any], policy: str, result: str, vlan: int | None, status: str
) -> None:
    decision = _eval(DEFAULTS, **facts)
    assert (decision.policy_name, decision.result, decision.vlan, decision.endpoint_status) == (
        policy,
        result,
        vlan,
        status,
    )
    if vlan is not None:
        assert decision.reply["Tunnel-Private-Group-Id"] == str(vlan)
        assert decision.reply["Tunnel-Type"] == "VLAN"
        assert decision.reply["Tunnel-Medium-Type"] == "IEEE-802"


def _p(
    name: str, priority: int, condition: dict[str, Any], action: dict[str, Any]
) -> tuple[str, engine.PolicySpec]:
    return name, engine.PolicySpec.model_validate(
        {"name": name, "priority": priority, "condition": condition, "action": action}
    )


CASES = [
    (
        _p(
            "phones",
            50,
            {"type": "oui_in", "ouis": ["00:04:F2"]},
            {"type": "assign_vlan", "vlan": 30},
        ),
        {"mac": "00:04:f2:aa:bb:cc"},
        30,
    ),
    (
        _p(
            "staff-hours",
            50,
            {
                "type": "all_of",
                "conditions": [
                    {"type": "user_in_group", "group": "staff"},
                    {
                        "type": "time_window",
                        "start": "08:00",
                        "end": "18:00",
                        "days": ["mon", "tue", "wed", "thu", "fri"],
                    },
                ],
            },
            {"type": "assign_vlan", "vlan": 40},
        ),
        {"user_groups": frozenset({"staff"}), "now": MON_10AM},
        40,
    ),
    (
        _p(
            "staff-hours",
            50,
            {
                "type": "all_of",
                "conditions": [
                    {"type": "user_in_group", "group": "staff"},
                    {"type": "time_window", "start": "08:00", "end": "18:00", "days": ["mon"]},
                ],
            },
            {"type": "assign_vlan", "vlan": 40},
        ),
        {"user_groups": frozenset({"staff"}), "now": SUN_10PM},
        999,
    ),
    (
        _p(
            "dot1x-only",
            50,
            {"type": "not", "condition": {"type": "auth_method_is", "method": "mab"}},
            {"type": "assign_vlan", "vlan": 50, "filter_id": "staff-acl", "session_timeout": 3600},
        ),
        {"auth_method": "dot1x"},
        50,
    ),
    (
        _p(
            "printers",
            50,
            {
                "type": "any_of",
                "conditions": [
                    {"type": "device_type_is", "device_type": "printer"},
                    {"type": "site_is", "site": "print-room"},
                ],
            },
            {"type": "assign_vlan", "vlan": 60},
        ),
        {"site": "print-room"},
        60,
    ),
]


@pytest.mark.parametrize(("policy", "facts", "vlan"), CASES)
def test_conditions(
    policy: tuple[str, engine.PolicySpec], facts: dict[str, Any], vlan: int
) -> None:
    assert _eval([*DEFAULTS, policy], **facts).vlan == vlan


def test_first_match_by_priority_and_disabled_skipped() -> None:
    low = _p("a", 1, {"type": "always"}, {"type": "assign_vlan", "vlan": 11})
    high = _p("b", 2, {"type": "always"}, {"type": "assign_vlan", "vlan": 12})
    assert _eval([high, low]).vlan == 11
    disabled = (
        "c",
        engine.PolicySpec.model_validate(
            {
                "name": "c",
                "priority": 0,
                "condition": {"type": "always"},
                "action": {"type": "reject"},
                "enabled": False,
            }
        ),
    )
    assert _eval([disabled, low]).vlan == 11
    assert _eval([]).policy_name == "builtin-default"


def test_filter_id_and_session_timeout_attributes() -> None:
    decision = _eval([CASES[3][0]], auth_method="dot1x")
    assert decision.reply["Filter-Id"] == "staff-acl"
    assert decision.reply["Session-Timeout"] == "3600"


@pytest.mark.parametrize(
    "bad",
    [
        {
            "name": "x",
            "priority": 1,
            "condition": {"type": "eval", "code": "__import__('os')"},
            "action": {"type": "reject"},
        },
        {
            "name": "x",
            "priority": 1,
            "condition": {"type": "always"},
            "action": {"type": "run_cli", "command": "reboot"},
        },
        {
            "name": "x",
            "priority": 1,
            "condition": {"type": "always", "extra": 1},
            "action": {"type": "reject"},
        },
        {
            "name": "x",
            "priority": 1,
            "condition": {"type": "always"},
            "action": {"type": "assign_vlan", "vlan": 5000},
        },
        {
            "name": "x",
            "priority": 1,
            "condition": {"type": "always"},
            "action": {"type": "assign_vlan", "vlan": 10, "filter_id": "a; reboot"},
        },
        {
            "name": "Bad Name",
            "priority": 1,
            "condition": {"type": "always"},
            "action": {"type": "reject"},
        },
        {
            "name": "x",
            "priority": 1,
            "condition": {"type": "time_window", "start": "25:00", "end": "26:00"},
            "action": {"type": "reject"},
        },
    ],
)
def test_validation_rejects_unknown_or_unsafe(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        engine.PolicySpec.model_validate(bad)


def test_fail_modes() -> None:
    closed = engine.fail_mode_decision("closed", 999)
    assert closed.vlan == 999
    assert closed.reply["Tunnel-Private-Group-Id"] == "999"
    opened = engine.fail_mode_decision("open", 999)
    assert opened.vlan is None
    assert opened.result == "accept"
    assert opened.reply == {}


@pytest.mark.parametrize(
    ("signals", "device_type", "os"),
    [
        (ProfileSignals(mac="00:04:f2:00:00:01"), "voip-phone", None),
        (
            ProfileSignals(mac="02:00:00:00:00:01", dhcp_prl="1,3,6,15,26,28,51,58,59"),
            "mobile",
            "Android",
        ),
        (
            ProfileSignals(mac="11:22:33:44:55:66", vendor_class="MSFT 5.0"),
            "workstation",
            "Windows",
        ),
        (
            ProfileSignals(mac="11:22:33:44:55:66", lldp_system="Avaya phone 9608"),
            "voip-phone",
            None,
        ),
    ],
)
def test_profiling(signals: ProfileSignals, device_type: str, os: str | None) -> None:
    result = profile(signals)
    assert result.device_type == device_type
    assert result.os == os
    assert 0 < result.confidence <= 1
    assert result.reasons


SPEC = cli_nac.NacProvisionSpec(
    radius_host="192.0.2.50",
    shared_secret="Lab-Radius-Secret1",
    controller_ip="10.0.0.5",
    interfaces=("GE0/0/1", "GE0/0/2"),
    guest_vlan=20,
    quarantine_vlan=999,
)


def test_provisioning_template_s_series() -> None:
    plan = cli_nac.provision(DeviceFamily.S, SPEC)
    lines = plan.lines
    assert lines[0] == "radius-server template nac"
    assert "radius-server authentication 192.0.2.50 1812 weight 80" in lines
    assert "radius-server authorization 10.0.0.5 shared-key cipher Lab-Radius-Secret1" in lines
    assert "authentication event authen-server-down action authorize vlan 999" in lines
    assert lines.count("authentication-profile nac-auth") == 2
    assert "interface GigabitEthernet0/0/2" in lines
    assert plan.min_category is CommandCategory.DANGEROUS
    assert plan.rollback[1] == "undo authentication-profile"
    assert cli_nac.provision(DeviceFamily.CE, SPEC).lines[0] == "dot1x enable"


@pytest.mark.parametrize("secret", ["", "has space", "x" * 200, "bad\nline"])
def test_provisioning_rejects_bad_secret(secret: str) -> None:
    with pytest.raises(Exception):  # noqa: B017,PT011 - ValueError or UnsafeCommand
        cli_nac.provision(
            DeviceFamily.S, cli_nac.NacProvisionSpec("192.0.2.50", secret, "10.0.0.5")
        )


def test_related_config_extracts_nac_blocks() -> None:
    running = (
        "#\nsysname SW\n#\nradius-server template old\n radius-server shared-key cipher X\n#\n"
        "interface GigabitEthernet0/0/1\n authentication-profile p1\n#\ninterface GigabitEthernet0/0/9\n port default vlan 5\n"
    )
    related = cli_nac.related_config(running)
    assert "radius-server template old" in related
    assert "interface GigabitEthernet0/0/1" in related
    assert "GigabitEthernet0/0/9" not in related
    assert "sysname" not in related
