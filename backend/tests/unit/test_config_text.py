"""Config hashing (volatile lines ignored), secret masking, diff and restore planning."""

from __future__ import annotations

from app.domain import config_text as ct

BASE = """!Software Version V200R019C10SPC500
!Last configuration was updated at 2026-10-23 09:00:00+00:00
#
sysname SW-01
#
vlan batch 10 20
#
aaa
 local-user admin password irreversible-cipher $1a$SuperSecretHash$
#
snmp-agent community read cipher %^%#CommunitySecret%^%#
#
interface GigabitEthernet0/0/1
 port link-type access
 port default vlan 10
#
return"""


def test_hash_ignores_volatile_headers_and_prompts() -> None:
    later = BASE.replace("09:00:00", "11:30:00") + "\n<SW-01>"
    assert ct.config_hash(BASE) == ct.config_hash(later)
    assert ct.config_hash(BASE) != ct.config_hash(BASE.replace("vlan 10\n#", "vlan 20\n#"))


def test_sanitize_masks_secrets() -> None:
    masked = ct.sanitize(BASE)
    assert "SuperSecretHash" not in masked
    assert "CommunitySecret" not in masked
    assert "port default vlan 10" in masked


def test_unified_diff() -> None:
    diff = ct.unified_diff(BASE, BASE.replace("port default vlan 10", "port default vlan 20"))
    assert "-  port default vlan 10" in diff or "- port default vlan 10" in diff
    assert "+ port default vlan 20" in diff
    assert ct.unified_diff(BASE, BASE) == ""


def test_sections() -> None:
    secs = ct.sections(BASE)
    assert secs["interface GigabitEthernet0/0/1"].body == [
        "port link-type access",
        "port default vlan 10",
    ]
    assert "sysname SW-01" in secs


def test_restore_plan_vlans_ports_and_leaves() -> None:
    running = (
        BASE.replace("vlan batch 10 20", "vlan batch 10 20 300").replace(
            "port default vlan 10", "port default vlan 300\n description temp"
        )
        + "\nip route-static 10.9.0.0 255.255.0.0 192.0.2.9\n#"
    )
    plan = ct.restore_plan(running, BASE)
    assert plan.lines[-1] == "undo vlan batch 300"
    assert "interface GigabitEthernet0/0/1" in plan.lines
    assert "port default vlan 10" in plan.lines
    assert "undo description temp" in plan.lines
    assert "undo ip route-static 10.9.0.0 255.255.0.0 192.0.2.9" in plan.lines
    assert not any(line.startswith("undo vlan batch 10") for line in plan.lines)
    assert not any(line.startswith("sysname") for line in plan.lines)
    assert ct.restore_plan(BASE, BASE).lines == ()


def test_restore_plan_adds_missing_vlans_first() -> None:
    plan = ct.restore_plan(BASE.replace("vlan batch 10 20", "vlan batch 10"), BASE)
    assert plan.lines[0] == "vlan batch 20"
