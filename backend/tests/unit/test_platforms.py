"""Phase 13: platform registry, adapter contracts, rollback-timer gating."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.exceptions import LockoutRisk, UnsupportedCapability
from app.domain.devices import DeviceFamily, DeviceProtocol
from app.domain.platforms import CommitModel, Platform, RollbackTimer, Vendor
from app.network import platforms
from app.network.builders.plan import ConfigPlan
from app.network.platforms import Q, adapter_for, adapter_of
from app.network.platforms.huawei import HUAWEI_VRP
from app.services.change_service import ChangeOptions, ChangeService, _run_plan


def test_platform_vendor_and_labels() -> None:
    assert Platform.CISCO_NXOS.vendor is Vendor.CISCO
    assert Platform.TPLINK_OMADA.vendor is Vendor.TPLINK
    assert Platform.HUAWEI_VRPV8.is_huawei
    assert all(p.label for p in Platform)
    assert Platform.for_huawei_family(DeviceFamily.CE) is Platform.HUAWEI_VRPV8
    assert Platform.for_huawei_family(DeviceFamily.S) is Platform.HUAWEI_VRP


def test_huawei_adapters_keep_commit_semantics() -> None:
    s, ce = adapter_for(Platform.HUAWEI_VRP), adapter_for(Platform.HUAWEI_VRPV8)
    assert s.commit_model is CommitModel.IMMEDIATE
    assert s.rollback_timer is RollbackTimer.SCHEDULED_RELOAD
    assert ce.uses_commit
    assert ce.rollback_timer is RollbackTimer.COMMIT_TRIAL
    assert s.command(Q.VLANS) == "display vlan"
    assert s.command(Q.INTERFACE, name="GigabitEthernet0/0/1") == (
        "display interface GigabitEthernet0/0/1"
    )
    assert set(s.drivers) == {"ssh", "telnet", "netconf", "snmp"}


def test_every_query_has_a_huawei_command() -> None:
    for query in Q:
        assert HUAWEI_VRP.supports(query), query


def test_unsupported_query_is_a_clear_error() -> None:
    adapter = dataclasses.replace(HUAWEI_VRP, commands={})
    with pytest.raises(UnsupportedCapability):
        adapter.command(Q.ARP)


def test_adapter_of_follows_detected_huawei_family() -> None:
    row: Any = SimpleNamespace(platform=Platform.HUAWEI_VRP, family=DeviceFamily.CE)
    assert adapter_of(row).platform is Platform.HUAWEI_VRPV8


@pytest.fixture
def no_timer_platform() -> Any:
    """A registered stand-in platform without any device-side rollback timer."""
    fake = dataclasses.replace(
        HUAWEI_VRP, platform=Platform.CISCO_SMB, rollback_timer=RollbackTimer.NONE
    )
    previous = platforms._REGISTRY.get(Platform.CISCO_SMB)
    platforms.register_adapter(fake)
    yield fake
    if previous is None:
        platforms._REGISTRY.pop(Platform.CISCO_SMB, None)
    else:
        platforms.register_adapter(previous)


def test_lockout_override_refused_without_rollback_timer(no_timer_platform: Any) -> None:
    from app.core.rbac import Role

    device: Any = SimpleNamespace(platform=Platform.CISCO_SMB, family=DeviceFamily.UNKNOWN)
    principal: Any = SimpleNamespace(role=Role.ADMIN)
    options = ChangeOptions(override_lockout=True, rollback_minutes=5)
    with pytest.raises(LockoutRisk, match="no device-side"):
        ChangeService._check_override(principal, options, [], device)


def test_timed_change_refused_without_rollback_timer(no_timer_platform: Any) -> None:
    device: Any = SimpleNamespace(
        platform=Platform.CISCO_SMB, family=DeviceFamily.UNKNOWN, protocol=DeviceProtocol.SSH
    )
    plan = ConfigPlan("test", ("interface X",))
    with pytest.raises(LockoutRisk):
        _run_plan(SimpleNamespace(), device, plan, save=False, timer=5)  # type: ignore[arg-type]


def test_scheduled_reload_platform_arms_and_disarms() -> None:
    calls: list[str] = []

    class Driver:
        def arm_rollback_timer(self, minutes: int) -> str:
            calls.append(f"arm {minutes}")
            return ""

        def disarm_rollback_timer(self) -> str:
            calls.append("disarm")
            return ""

        def push_config(self, lines: list[str], **_: Any) -> Any:
            calls.append("push")
            return SimpleNamespace(output="")

        def save(self) -> str:
            calls.append("save")
            return ""

    device: Any = SimpleNamespace(
        platform=Platform.HUAWEI_VRP, family=DeviceFamily.S, protocol=DeviceProtocol.SSH
    )
    _output, status = _run_plan(Driver(), device, ConfigPlan("t", ("x",)), True, 3)  # type: ignore[arg-type]
    assert status == "applied"
    assert calls == ["arm 3", "push", "disarm", "save"]


def test_unregistered_platform_is_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platforms, "_REGISTRY", {})
    monkeypatch.setattr(platforms, "_LOADED", True)
    with pytest.raises(UnsupportedCapability):
        adapter_for(Platform.TPLINK_OMADA)
