"""Typed L2 builders in Cisco IOS / IOS-XE syntax (VLANs, ports, MAC, STP, DHCP snooping).

Lines run inside 'configure terminal'; interface bodies end with 'exit'. IOS has no hybrid
port mode, so 'hybrid' is rejected. Spellings target Catalyst IOS 15.2 / IOS-XE 16-17.
"""

from __future__ import annotations

from app.domain.commands.sanitize import quote_free_text
from app.domain.interfaces import normalize_interface
from app.domain.mac import to_cisco
from app.domain.vlans import to_cisco_ranges, validate_vlan_id
from app.network.builders.plan import ConfigPlan, Verifier
from app.network.parsers.interface_config import InterfaceConfig
from app.network.platforms.contracts import PortMode
from app.network.ports import DeviceDriver

ALL_VLANS = frozenset(range(1, 4095))
SHOW_VLANS = "show vlan brief"


def _vlan_verifier(expected_present: set[int], expected_absent: set[int]) -> Verifier:
    def verify(driver: DeviceDriver) -> bool:
        from app.network.platforms.cisco_ios.parsers import parse_vlans

        present = {v.vlan_id for v in parse_vlans(driver.run_show(SHOW_VLANS).output)}
        return expected_present <= present and not (expected_absent & present)

    return verify


def _iface(name: str, body: list[str]) -> tuple[str, ...]:
    return (f"interface {name}", *body, "exit") if body else ()


# --- VLANs ----------------------------------------------------------------------------------
def create_vlans(wanted: dict[int, str | None], existing: dict[int, str | None]) -> ConfigPlan:
    lines: list[str] = []
    missing = sorted(v for v in wanted if v not in existing)
    for vid in sorted(wanted):
        validate_vlan_id(vid, allow_reserved=False)
        name = wanted[vid]
        if vid in existing and (not name or existing.get(vid) == name):
            continue
        lines.append(f"vlan {vid}")
        if name:
            lines.append(f"name {quote_free_text(name, max_length=32)}")
        lines.append("exit")
    rollback = (f"no vlan {to_cisco_ranges(missing)}",) if missing else ()
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
    restore: list[str] = []
    for vid in targets:
        restore.append(f"vlan {vid}")
        if existing.get(vid):
            restore.append(f"name {quote_free_text(existing[vid] or '', max_length=32)}")
        restore.append("exit")
    return ConfigPlan(
        "vlan.delete",
        (f"no vlan {to_cisco_ranges(targets)}",),
        tuple(restore),
        _vlan_verifier(set(), set(targets)),
        summary=f"delete VLAN(s) {to_cisco_ranges(targets)}",
    )


def rename_vlan(vlan_id: int, name: str) -> ConfigPlan:
    validate_vlan_id(vlan_id)
    return ConfigPlan(
        "vlan.rename",
        (f"vlan {vlan_id}", f"name {quote_free_text(name, max_length=32)}", "exit"),
        summary=f"rename VLAN {vlan_id}",
    )


# --- ports ----------------------------------------------------------------------------------
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
    if mode == "hybrid":
        raise ValueError("Cisco IOS has no hybrid port mode; use access or trunk")
    name = normalize_interface(interface)
    body: list[str] = []
    if current.link_type != mode:
        body.append(f"switchport mode {mode}")
    if (
        mode == "access"
        and access_vlan is not None
        and (current.default_vlan != validate_vlan_id(access_vlan) or current.link_type != mode)
    ):
        body.append(f"switchport access vlan {access_vlan}")
    if mode == "trunk":
        if trunk_vlans:
            wanted = set(trunk_vlans)
            for vid in wanted:
                validate_vlan_id(vid)
            if current.link_type != mode or current.trunk_allowed != wanted:
                body.append(f"switchport trunk allowed vlan {to_cisco_ranges(sorted(wanted))}")
        if pvid is not None and current.trunk_pvid != validate_vlan_id(pvid):
            body.append(f"switchport trunk native vlan {pvid}")
    return ConfigPlan(
        "interface.port_mode",
        _iface(name, body),
        _iface(name, _restore_port(current)) if body else (),
        summary=f"{name} -> {mode}",
    )


def _restore_port(cfg: InterfaceConfig) -> list[str]:
    lines: list[str] = []
    if cfg.link_type in {"access", "trunk"}:
        lines.append(f"switchport mode {cfg.link_type}")
    if cfg.default_vlan is not None:
        lines.append(f"switchport access vlan {cfg.default_vlan}")
    if cfg.trunk_allowed and cfg.trunk_allowed != ALL_VLANS:
        lines.append(f"switchport trunk allowed vlan {to_cisco_ranges(sorted(cfg.trunk_allowed))}")
    elif cfg.link_type == "trunk":
        lines.append("switchport trunk allowed vlan all")
    if cfg.trunk_pvid is not None:
        lines.append(f"switchport trunk native vlan {cfg.trunk_pvid}")
    return lines


def trunk_vlans(
    interface: str, add: list[int], remove: list[int], current: InterfaceConfig
) -> ConfigPlan:
    name = normalize_interface(interface)
    allowed = current.trunk_allowed or (ALL_VLANS if current.link_type == "trunk" else set())
    to_add = sorted(set(add) - allowed)
    to_remove = sorted(set(remove) & allowed)
    body: list[str] = []
    rollback: list[str] = []
    if to_add:
        body.append(f"switchport trunk allowed vlan add {to_cisco_ranges(to_add)}")
        rollback.append(f"switchport trunk allowed vlan remove {to_cisco_ranges(to_add)}")
    if to_remove:
        body.append(f"switchport trunk allowed vlan remove {to_cisco_ranges(to_remove)}")
        rollback.append(f"switchport trunk allowed vlan add {to_cisco_ranges(to_remove)}")
    return ConfigPlan(
        "interface.trunk_vlans", _iface(name, body), _iface(name, rollback), summary=f"{name} trunk"
    )


def set_admin_state(interface: str, enabled: bool, current: InterfaceConfig) -> ConfigPlan:
    name = normalize_interface(interface)
    if enabled != current.shutdown:
        return ConfigPlan("interface.admin_state", (), summary="no change")
    line, undo = ("no shutdown", "shutdown") if enabled else ("shutdown", "no shutdown")
    return ConfigPlan(
        "interface.admin_state",
        _iface(name, [line]),
        _iface(name, [undo]),
        summary=f"{name} {'enable' if enabled else 'shutdown'}",
    )


def set_description(
    interface: str, description: str | None, current: InterfaceConfig
) -> ConfigPlan:
    name = normalize_interface(interface)
    if (description or None) == current.description:
        return ConfigPlan("interface.description", (), summary="no change")
    line = (
        f"description {quote_free_text(description, max_length=200)}"
        if description
        else "no description"
    )
    undo = f"description {current.description}" if current.description else "no description"
    return ConfigPlan("interface.description", _iface(name, [line]), _iface(name, [undo]))


def set_speed_duplex(
    interface: str, speed: int | None, duplex: str | None, auto: bool
) -> ConfigPlan:
    name = normalize_interface(interface)
    if auto:
        body = ["speed auto", "duplex auto"]
    else:
        body = []
        if speed:
            if speed not in {10, 100, 1000, 2500, 5000, 10000, 25000, 40000, 100000}:
                raise ValueError("unsupported speed")
            body.append(f"speed {speed}")
        if duplex:
            if duplex not in {"full", "half"}:
                raise ValueError("duplex must be full or half")
            body.append(f"duplex {duplex}")
        if not body:
            raise ValueError("give a speed and/or duplex, or auto")
    return ConfigPlan("interface.speed_duplex", _iface(name, body), summary=name)


# --- MAC --------------------------------------------------------------------------------------
def static_mac(
    mac: str, vlan: int, interface: str | None, *, remove: bool = False, blackhole: bool = False
) -> ConfigPlan:
    hw = to_cisco(mac)
    validate_vlan_id(vlan)
    if blackhole:
        line = f"mac address-table static {hw} vlan {vlan} drop"
    else:
        if interface is None:
            raise ValueError("static MAC needs an interface")
        line = (
            f"mac address-table static {hw} vlan {vlan} interface {normalize_interface(interface)}"
        )
    apply, undo = (f"no {line}", line) if remove else (line, f"no {line}")
    op = "mac.blackhole" if blackhole else "mac.static"
    return ConfigPlan(
        f"{op}.{'remove' if remove else 'add'}", (apply,), (undo,), summary=f"{op} {mac}"
    )


# --- STP / DHCP snooping ---------------------------------------------------------------------
_STP_MODES = {"stp": "pvst", "rstp": "rapid-pvst", "mstp": "mst"}


def stp_settings(mode: str | None, priority: int | None, instance: int = 0) -> ConfigPlan:
    """Modes use the platform-neutral names: stp -> pvst, rstp -> rapid-pvst, mstp -> mst."""
    lines: list[str] = []
    if mode:
        if mode not in _STP_MODES:
            raise ValueError("mode must be stp, rstp or mstp")
        lines.append(f"spanning-tree mode {_STP_MODES[mode]}")
    if priority is not None:
        if priority % 4096 or not 0 <= priority <= 61440:
            raise ValueError("priority must be a multiple of 4096 in 0-61440")
        lines.append(
            f"spanning-tree mst {instance} priority {priority}"
            if mode == "mstp"
            else f"spanning-tree vlan 1-4094 priority {priority}"
        )
    return ConfigPlan("stp.settings", tuple(lines), summary="stp")


def stp_edge_port(interface: str, enabled: bool) -> ConfigPlan:
    name = normalize_interface(interface)
    line = "spanning-tree portfast" if enabled else "no spanning-tree portfast"
    return ConfigPlan("stp.edge_port", _iface(name, [line]))


def dhcp_snooping(vlans: list[int], trusted: list[str]) -> ConfigPlan:
    lines = ["ip dhcp snooping"]
    if vlans:
        lines.append(f"ip dhcp snooping vlan {to_cisco_ranges(vlans)}")
    for iface in trusted:
        lines += [*_iface(normalize_interface(iface), ["ip dhcp snooping trust"])]
    return ConfigPlan("dhcp.snooping", tuple(lines), summary="dhcp snooping")
