"""Typed CLI builders for L2 features (VLANs, ports, MAC, STP, DHCP snooping).

All inputs are validated models/values; operator text only appears via ``quote_free_text``.
Every command below is VRP-VERSION-DEPENDENT; spellings target S (V200R019) and CE (V8).
"""

from __future__ import annotations

from typing import Literal

from app.domain.commands.sanitize import quote_free_text
from app.domain.interfaces import normalize_interface
from app.domain.mac import to_huawei
from app.domain.vlans import to_huawei_ranges, validate_vlan_id
from app.network.builders.plan import ConfigPlan, Verifier
from app.network.parsers.interface_config import InterfaceConfig
from app.network.parsers.vlans import parse_vlans
from app.network.ports import DeviceDriver

PortMode = Literal["access", "trunk", "hybrid"]


def _vlan_verifier(expected_present: set[int], expected_absent: set[int]) -> Verifier:
    def verify(driver: DeviceDriver) -> bool:
        output = driver.run_show("display vlan").output
        present = {v.vlan_id for v in parse_vlans(output)}
        return expected_present <= present and not (expected_absent & present)

    return verify


# --- VLANs ----------------------------------------------------------------------------------
def create_vlans(wanted: dict[int, str | None], existing: dict[int, str | None]) -> ConfigPlan:
    """Create missing VLANs and (re)name ones whose name differs. Idempotent."""
    missing = sorted(v for v in wanted if v not in existing)
    lines: list[str] = []
    if missing:
        lines.append(f"vlan batch {to_huawei_ranges(missing)}")
    for vid in sorted(wanted):
        validate_vlan_id(vid, allow_reserved=False)
        name = wanted[vid]
        if name and existing.get(vid) != name:
            lines += [f"vlan {vid}", f"name {quote_free_text(name, max_length=31)}", "quit"]
    rollback = (f"undo vlan batch {to_huawei_ranges(missing)}",) if missing else ()
    return ConfigPlan(
        "vlan.create",
        tuple(lines),
        rollback,
        _vlan_verifier(set(wanted), set()),
        summary=f"create {len(missing)} VLAN(s), ensure {len(wanted)}",
    )


def delete_vlans(vlans: list[int], existing: dict[int, str | None]) -> ConfigPlan:
    targets = sorted(v for v in set(vlans) if v in existing)
    for vid in targets:
        validate_vlan_id(vid, allow_reserved=False)
    if not targets:
        return ConfigPlan("vlan.delete", (), summary="nothing to delete")
    line = (
        f"undo vlan {targets[0]}"
        if len(targets) == 1
        else f"undo vlan batch {to_huawei_ranges(targets)}"
    )
    restore: list[str] = [f"vlan batch {to_huawei_ranges(targets)}"]
    for vid in targets:
        if existing.get(vid):
            restore += [
                f"vlan {vid}",
                f"name {quote_free_text(existing[vid] or '', max_length=31)}",
                "quit",
            ]
    return ConfigPlan(
        "vlan.delete",
        (line,),
        tuple(restore),
        _vlan_verifier(set(), set(targets)),
        summary=f"delete VLAN(s) {to_huawei_ranges(targets)}",
    )


def rename_vlan(vlan_id: int, name: str) -> ConfigPlan:
    validate_vlan_id(vlan_id)
    return ConfigPlan(
        "vlan.rename",
        (f"vlan {vlan_id}", f"name {quote_free_text(name, max_length=31)}", "quit"),
        summary=f"rename VLAN {vlan_id}",
    )


# --- ports ----------------------------------------------------------------------------------
def _reset_l2(current: InterfaceConfig) -> list[str]:
    """Huawei refuses a link-type change while VLAN settings exist; clear them first."""
    lines: list[str] = []
    if current.default_vlan is not None:
        lines.append("undo port default vlan")
    if current.trunk_allowed:
        lines.append("undo port trunk allow-pass vlan all")
    if current.trunk_pvid is not None:
        lines.append("undo port trunk pvid vlan")
    if current.hybrid_tagged or current.hybrid_untagged:
        lines.append("undo port hybrid vlan all")
    if current.hybrid_pvid is not None:
        lines.append("undo port hybrid pvid vlan")
    return lines


def set_port_mode(
    interface: str,
    mode: PortMode,
    current: InterfaceConfig,
    *,
    access_vlan: int | None = None,
    trunk_vlans: list[int] | None = None,
    pvid: int | None = None,
    tagged: list[int] | None = None,
    untagged: list[int] | None = None,
) -> ConfigPlan:
    name = normalize_interface(interface)
    body: list[str] = []
    if current.link_type != mode:
        body += _reset_l2(current)
        body.append(f"port link-type {mode}")
    if (
        mode == "access"
        and access_vlan is not None
        and (current.default_vlan != validate_vlan_id(access_vlan) or current.link_type != mode)
    ):
        body.append(f"port default vlan {access_vlan}")
    if mode == "trunk":
        add = sorted(
            set(trunk_vlans or []) - (current.trunk_allowed if current.link_type == mode else set())
        )
        if add:
            body.append(f"port trunk allow-pass vlan {to_huawei_ranges(add)}")
        if pvid is not None and current.trunk_pvid != validate_vlan_id(pvid):
            body.append(f"port trunk pvid vlan {pvid}")
    if mode == "hybrid":
        if pvid is not None and current.hybrid_pvid != validate_vlan_id(pvid):
            body.append(f"port hybrid pvid vlan {pvid}")
        if tagged:
            body.append(f"port hybrid tagged vlan {to_huawei_ranges(tagged)}")
        if untagged:
            body.append(f"port hybrid untagged vlan {to_huawei_ranges(untagged)}")
    lines = (f"interface {name}", *body, "quit") if body else ()
    rollback = (f"interface {name}", *_restore_port(current), "quit") if body else ()
    return ConfigPlan("interface.port_mode", tuple(lines), rollback, summary=f"{name} -> {mode}")


def _restore_port(cfg: InterfaceConfig) -> list[str]:
    lines = ["undo port default vlan", "undo port trunk allow-pass vlan all"]
    if cfg.link_type:
        lines.append(f"port link-type {cfg.link_type}")
    if cfg.default_vlan is not None:
        lines.append(f"port default vlan {cfg.default_vlan}")
    if cfg.trunk_allowed:
        lines.append(f"port trunk allow-pass vlan {to_huawei_ranges(sorted(cfg.trunk_allowed))}")
    return lines


def trunk_vlans(
    interface: str, add: list[int], remove: list[int], current: InterfaceConfig
) -> ConfigPlan:
    name = normalize_interface(interface)
    to_add = sorted(set(add) - current.trunk_allowed)
    to_remove = sorted(set(remove) & current.trunk_allowed)
    body = []
    if to_add:
        body.append(f"port trunk allow-pass vlan {to_huawei_ranges(to_add)}")
    if to_remove:
        body.append(f"undo port trunk allow-pass vlan {to_huawei_ranges(to_remove)}")
    rollback: list[str] = []
    if to_add:
        rollback.append(f"undo port trunk allow-pass vlan {to_huawei_ranges(to_add)}")
    if to_remove:
        rollback.append(f"port trunk allow-pass vlan {to_huawei_ranges(to_remove)}")

    def wrap(lines: list[str]) -> tuple[str, ...]:
        return (f"interface {name}", *lines, "quit") if lines else ()

    return ConfigPlan("interface.trunk_vlans", wrap(body), wrap(rollback), summary=f"{name} trunk")


def set_admin_state(interface: str, enabled: bool, current: InterfaceConfig) -> ConfigPlan:
    name = normalize_interface(interface)
    if enabled != current.shutdown:  # already in the desired state
        return ConfigPlan("interface.admin_state", (), summary="no change")
    line, undo = ("undo shutdown", "shutdown") if enabled else ("shutdown", "undo shutdown")
    return ConfigPlan(
        "interface.admin_state",
        (f"interface {name}", line, "quit"),
        (f"interface {name}", undo, "quit"),
        summary=f"{name} {'enable' if enabled else 'shutdown'}",
    )


def set_description(
    interface: str, description: str | None, current: InterfaceConfig
) -> ConfigPlan:
    name = normalize_interface(interface)
    if (description or None) == current.description:
        return ConfigPlan("interface.description", (), summary="no change")
    line = (
        f"description {quote_free_text(description, max_length=242)}"
        if description
        else "undo description"
    )
    undo = f"description {current.description}" if current.description else "undo description"
    return ConfigPlan(
        "interface.description",
        (f"interface {name}", line, "quit"),
        (f"interface {name}", undo, "quit"),
    )


def set_speed_duplex(
    interface: str, speed: int | None, duplex: str | None, auto: bool
) -> ConfigPlan:
    name = normalize_interface(interface)
    body = ["negotiation auto"] if auto else ["undo negotiation auto"]
    if not auto and speed:
        if speed not in {10, 100, 1000, 10000, 25000, 40000, 100000}:
            raise ValueError("unsupported speed")
        body.append(f"speed {speed}")
    if not auto and duplex:
        if duplex not in {"full", "half"}:
            raise ValueError("duplex must be full or half")
        body.append(f"duplex {duplex}")
    return ConfigPlan("interface.speed_duplex", (f"interface {name}", *body, "quit"), summary=name)


# --- MAC --------------------------------------------------------------------------------------
def static_mac(
    mac: str, vlan: int, interface: str | None, *, remove: bool = False, blackhole: bool = False
) -> ConfigPlan:
    hw = to_huawei(mac)
    validate_vlan_id(vlan)
    if blackhole:
        line = f"mac-address blackhole {hw} vlan {vlan}"
    else:
        if interface is None:
            raise ValueError("static MAC needs an interface")
        line = f"mac-address static {hw} {normalize_interface(interface)} vlan {vlan}"
    apply, undo = (f"undo {line}", line) if remove else (line, f"undo {line}")
    op = "mac.blackhole" if blackhole else "mac.static"
    return ConfigPlan(
        f"{op}.{'remove' if remove else 'add'}", (apply,), (undo,), summary=f"{op} {mac}"
    )


# --- STP / DHCP snooping ---------------------------------------------------------------------
def stp_settings(mode: str | None, priority: int | None, instance: int = 0) -> ConfigPlan:
    lines: list[str] = []
    if mode:
        if mode not in {"stp", "rstp", "mstp"}:
            raise ValueError("mode must be stp, rstp or mstp")
        lines.append(f"stp mode {mode}")
    if priority is not None:
        if priority % 4096 or not 0 <= priority <= 61440:
            raise ValueError("priority must be a multiple of 4096 in 0-61440")
        lines.append(
            f"stp instance {instance} priority {priority}"
            if mode == "mstp" or instance
            else f"stp priority {priority}"
        )
    return ConfigPlan("stp.settings", tuple(lines), summary="stp")


def stp_edge_port(interface: str, enabled: bool) -> ConfigPlan:
    name = normalize_interface(interface)
    line = "stp edged-port enable" if enabled else "undo stp edged-port"
    return ConfigPlan("stp.edge_port", (f"interface {name}", line, "quit"))


def dhcp_snooping(vlans: list[int], trusted: list[str]) -> ConfigPlan:
    lines = ["dhcp enable", "dhcp snooping enable"]
    if vlans:
        lines.append(f"dhcp snooping enable vlan {to_huawei_ranges(vlans)}")
    for iface in trusted:
        lines += [f"interface {normalize_interface(iface)}", "dhcp snooping trusted", "quit"]
    return ConfigPlan("dhcp.snooping", tuple(lines), summary="dhcp snooping")
