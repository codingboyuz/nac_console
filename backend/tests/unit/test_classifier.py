"""Command classifier, injection guard and self-lockout guard."""

from __future__ import annotations

import pytest

from app.core.exceptions import UnsafeCommand
from app.domain.commands.categories import CommandCategory
from app.domain.commands.classifier import CommandClassifier, DeviceContext
from app.domain.commands.lockout import LockoutContext, LockoutGuard
from app.domain.commands.sanitize import quote_free_text, validate_cli_line

S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS
clf = CommandClassifier()
CTX = DeviceContext(
    mgmt_interface="Vlanif100",
    mgmt_vlan=100,
    uplink_interfaces=frozenset({"GigabitEthernet0/0/24", "XGigabitEthernet0/0/1"}),
)


@pytest.mark.parametrize(
    "command",
    [
        "display version",
        "display current-configuration",
        "display ip interface brief",
        "display interface GigabitEthernet0/0/1",
        "display vlan",
        "display mac-address",
        "display arp",
        "display ip routing-table",
        "display stp brief",
        "display cpu-usage",
        "display memory-usage",
        "dis ver",
        "DISPLAY   VERSION",
        "display current-configuration | include vlan",
    ],
)
def test_safe_display_commands(command: str) -> None:
    assert clf.classify_exec(command).category is S


@pytest.mark.parametrize(
    "command",
    [
        "reboot",
        "reset saved-configuration",
        "reset counters interface",
        "format flash:",
        "delete flash:/vrpcfg.zip",
        "startup system-software s5735.cc",
        "undo vlan batch 10 to 20",
        "local-user admin password irreversible-cipher x",
        "system-view",  # config commands in user view are refused
        "interface GigabitEthernet0/0/1",
        "foobar",  # unknown -> deny by default
        "displayx version",
    ],
)
def test_dangerous_exec_commands(command: str) -> None:
    assert clf.classify_exec(command).category is D


def test_ping_is_warning() -> None:
    assert clf.classify_exec("ping 192.0.2.1").category is W


@pytest.mark.parametrize(
    "payload",
    [
        "display version\nreboot",
        "display version\rreboot",
        "display version; reboot",
        "display version && reboot",
        "display version | reboot",
        "display\x00version",
        "display vеrsion",  # Cyrillic 'е'
        "x" * 600,
        "",
        "reboot | include x",
    ],
)
def test_injection_rejected(payload: str) -> None:
    with pytest.raises(UnsafeCommand):
        clf.classify_exec(payload)


def test_config_block_warning_and_views() -> None:
    result = clf.classify_config(
        [
            "vlan batch 10 20",
            "interface GigabitEthernet0/0/1",
            "port link-type access",
            "port default vlan 10",
            "description user-port",
            "quit",
        ],
        CTX,
    )
    assert result.category is W
    assert all(line.category is W for line in result.lines)


@pytest.mark.parametrize(
    ("lines", "reason"),
    [
        (["interface GE0/0/24", "shutdown"], "protected interface"),
        (["interface Eth-Trunk1", "shutdown"], "protected interface"),
        (["interface MEth0/0/1", "shutdown"], "protected interface"),
        (["interface Vlanif100", "undo ip address"], "management address"),
        (["undo vlan 100"], "management VLAN"),
        (["interface XGE0/0/1", "undo port trunk allow-pass vlan 10"], "uplink"),
        (["acl number 3000", "rule 5 deny ip"], "denies all"),
        (["interface GE0/0/24", "traffic-filter inbound acl 3000"], "ACL applied"),
        (["aaa", "local-user x password irreversible-cipher y"], "AAA"),
    ],
)
def test_context_escalation(lines: list[str], reason: str) -> None:
    result = clf.classify_config(lines, CTX)
    assert result.category is D
    assert reason.lower() in result.reason.lower()


def test_shutdown_on_access_port_is_only_warning() -> None:
    assert clf.classify_config(["interface GE0/0/5", "shutdown"], CTX).category is W


def test_unknown_config_line_is_dangerous() -> None:
    assert clf.classify_config(["frobnicate all"], CTX).category is D


GUARD = LockoutGuard()
LCTX = LockoutContext(device=CTX, controller_ips=("10.0.0.5",))


@pytest.mark.parametrize(
    "lines",
    [
        ["interface Vlanif100", "shutdown"],
        ["interface GigabitEthernet0/0/24", "shutdown"],
        ["interface Vlanif100", "undo ip address"],
        ["undo vlan 100"],
        ["undo vlan batch 90 to 110"],
        ["undo interface Vlanif100"],
        ["interface XGE0/0/1", "undo port trunk allow-pass vlan 50 to 150"],
        [
            "acl number 2000",
            "rule 5 deny source 10.0.0.0 0.0.0.255",
            "quit",
            "user-interface vty 0 4",
            "acl 2000 inbound",
        ],
        [
            "acl number 3001",
            "rule deny ip",
            "quit",
            "interface GE0/0/24",
            "traffic-filter inbound acl 3001",
        ],
    ],
)
def test_lockout_detected(lines: list[str]) -> None:
    assert GUARD.check(lines, LCTX)


@pytest.mark.parametrize(
    "lines",
    [
        ["interface GE0/0/5", "shutdown"],
        ["undo vlan 200"],
        [
            "acl number 2000",
            "rule 5 deny source 192.0.2.0 0.0.0.255",
            "quit",
            "user-interface vty 0 4",
            "acl 2000 inbound",
        ],
        ["acl number 3001", "rule deny ip"],  # not applied to the management plane
    ],
)
def test_lockout_not_triggered(lines: list[str]) -> None:
    assert GUARD.check(lines, LCTX) == []


def test_existing_mgmt_acl_deny_detected() -> None:
    ctx = LockoutContext(device=CTX, controller_ips=("10.0.0.5",), mgmt_acls=frozenset({"2001"}))
    assert GUARD.check(["acl number 2001", "rule 10 deny source any"], ctx)


def test_quote_free_text() -> None:
    assert quote_free_text("Uplink  to core") == "Uplink to core"
    with pytest.raises(UnsafeCommand):
        quote_free_text("x\nreboot")
    with pytest.raises(UnsafeCommand):
        quote_free_text("undo something")
    with pytest.raises(UnsafeCommand):
        validate_cli_line("x" * 513)
