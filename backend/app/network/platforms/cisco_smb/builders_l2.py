"""L2 builders for Cisco SMB (CBS350 / SG350 / SG550X).

VLANs are created inside 'vlan database' (valid on every firmware) and named under
'interface vlan N'. Trunk membership is changed with 'allowed vlan add/remove' (the subset
SG300-era firmware also accepts). The shared 'hybrid' mode maps to SMB 'general' mode.
"""

from __future__ import annotations

from app.domain.commands.sanitize import quote_free_text
from app.domain.interfaces import normalize_interface
from app.domain.mac import normalize_mac
from app.domain.vlans import to_cisco_ranges, validate_vlan_id
from app.network.builders.plan import ConfigPlan, Verifier
from app.network.parsers.interface_config import InterfaceConfig
from app.network.platforms.cisco_ios.builders_l2 import (
    ALL_VLANS,
    dhcp_snooping,
    set_admin_state,
    set_description,
)
from app.network.platforms.contracts import PortMode
from app.network.ports import DeviceDriver

__all__ = [
    "ALL_VLANS",
    "create_vlans",
    "delete_vlans",
    "dhcp_snooping",
    "rename_vlan",
    "set_admin_state",
    "set_description",
    "set_port_mode",
    "set_speed_duplex",
    "static_mac",
    "stp_edge_port",
    "stp_settings",
    "trunk_vlans",
]


def _verifier(present: set[int], absent: set[int]) -> Verifier:
    def verify(driver: DeviceDriver) -> bool:
        from app.network.platforms.cisco_smb.parsers import parse_vlans

        have = {v.vlan_id for v in parse_vlans(driver.run_show("show vlan").output)}
        return present <= have and not (absent & have)

    return verify


def _name_lines(vid: int, name: str) -> list[str]:
    return [f"interface vlan {vid}", f"name {quote_free_text(name, max_length=32)}", "exit"]


def create_vlans(wanted: dict[int, str | None], existing: dict[int, str | None]) -> ConfigPlan:
    for vid in wanted:
        validate_vlan_id(vid, allow_reserved=False)
    missing = sorted(v for v in wanted if v not in existing)
    lines: list[str] = []
    if missing:
        lines += ["vlan database", f"vlan {to_cisco_ranges(missing)}", "exit"]
    for vid in sorted(wanted):
        name = wanted[vid]
        if name and existing.get(vid) != name:
            lines += _name_lines(vid, name)
    rollback = ("vlan database", f"no vlan {to_cisco_ranges(missing)}", "exit") if missing else ()
    return ConfigPlan(
        "vlan.create",
        tuple(lines),
        rollback,
        _verifier(set(wanted), set()),
        summary=f"create {len(missing)} VLAN(s), ensure {len(wanted)}",
    )


def delete_vlans(vlans: list[int], existing: dict[int, str | None]) -> ConfigPlan:
    targets = sorted(v for v in set(vlans) if v in existing)
    for vid in targets:
        validate_vlan_id(vid, allow_reserved=False)
    if not targets:
        return ConfigPlan("vlan.delete", (), summary="nothing to delete")
    restore = ["vlan database", f"vlan {to_cisco_ranges(targets)}", "exit"]
    for vid in targets:
        if existing.get(vid) and existing[vid] != str(vid):
            restore += _name_lines(vid, existing[vid] or "")
    return ConfigPlan(
        "vlan.delete",
        ("vlan database", f"no vlan {to_cisco_ranges(targets)}", "exit"),
        tuple(restore),
        _verifier(set(), set(targets)),
        summary=f"delete VLAN(s) {to_cisco_ranges(targets)}",
    )


def rename_vlan(vlan_id: int, name: str) -> ConfigPlan:
    validate_vlan_id(vlan_id)
    return ConfigPlan("vlan.rename", tuple(_name_lines(vlan_id, name)), summary=f"rename {vlan_id}")


def _iface(name: str, body: list[str]) -> tuple[str, ...]:
    return (f"interface {name}", *body, "exit") if body else ()


def set_port_mode(  # noqa: PLR0912 - one branch per mode option
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
    smb_mode = "general" if mode == "hybrid" else mode
    if current.link_type != mode:
        body.append(f"switchport mode {smb_mode}")
    if mode == "access" and access_vlan is not None:
        body.append(f"switchport access vlan {validate_vlan_id(access_vlan)}")
    if mode == "trunk":
        have = current.trunk_allowed if current.link_type == "trunk" else set()
        if trunk_vlans:
            wanted = {validate_vlan_id(v) for v in trunk_vlans}
            if remove := sorted(have - wanted):
                body.append(f"switchport trunk allowed vlan remove {to_cisco_ranges(remove)}")
            if add := sorted(wanted - have):
                body.append(f"switchport trunk allowed vlan add {to_cisco_ranges(add)}")
        if pvid is not None and current.trunk_pvid != validate_vlan_id(pvid):
            body.append(f"switchport trunk native vlan {pvid}")
    if mode == "hybrid":
        if pvid is not None:
            body.append(f"switchport general pvid {validate_vlan_id(pvid)}")
        if tagged:
            body.append(f"switchport general allowed vlan add {to_cisco_ranges(tagged)} tagged")
        if untagged:
            body.append(f"switchport general allowed vlan add {to_cisco_ranges(untagged)} untagged")
    restore: list[str] = []
    if current.link_type:
        mode_word = "general" if current.link_type == "hybrid" else current.link_type
        restore.append(f"switchport mode {mode_word}")
    if current.default_vlan is not None:
        restore.append(f"switchport access vlan {current.default_vlan}")
    return ConfigPlan(
        "interface.port_mode",
        _iface(name, body),
        _iface(name, restore) if body else (),
        summary=f"{name} -> {smb_mode}",
    )


def trunk_vlans(
    interface: str, add: list[int], remove: list[int], current: InterfaceConfig
) -> ConfigPlan:
    name = normalize_interface(interface)
    allowed = current.trunk_allowed
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


def set_speed_duplex(
    interface: str, speed: int | None, duplex: str | None, auto: bool
) -> ConfigPlan:
    name = normalize_interface(interface)
    if auto:
        body = ["negotiation"]
    else:
        body = ["no negotiation"]
        if speed:
            if speed not in {10, 100, 1000, 2500, 5000, 10000}:
                raise ValueError("unsupported speed")
            body.append(f"speed {speed}")
        if duplex:
            if duplex not in {"full", "half"}:
                raise ValueError("duplex must be full or half")
            body.append(f"duplex {duplex}")
    return ConfigPlan("interface.speed_duplex", _iface(name, body), summary=name)


def static_mac(
    mac: str, vlan: int, interface: str | None, *, remove: bool = False, blackhole: bool = False
) -> ConfigPlan:
    if blackhole:
        raise ValueError("Cisco SMB has no drop MAC entry; block the endpoint through NAC")
    if interface is None:
        raise ValueError("static MAC needs an interface")
    validate_vlan_id(vlan)
    hw = normalize_mac(mac)
    line = f"mac address-table static {hw} vlan {vlan} interface {normalize_interface(interface)}"
    remove_line = f"no mac address-table static {hw} vlan {vlan}"
    apply, undo = (remove_line, line) if remove else (line, remove_line)
    return ConfigPlan(
        f"mac.static.{'remove' if remove else 'add'}", (apply,), (undo,), summary=f"static {mac}"
    )


def stp_settings(mode: str | None, priority: int | None, instance: int = 0) -> ConfigPlan:
    modes = {"stp": "stp", "rstp": "rstp", "mstp": "mst"}
    lines: list[str] = []
    if mode:
        if mode not in modes:
            raise ValueError("mode must be stp, rstp or mstp")
        lines.append(f"spanning-tree mode {modes[mode]}")
    if priority is not None:
        if priority % 4096 or not 0 <= priority <= 61440:
            raise ValueError("priority must be a multiple of 4096 in 0-61440")
        lines.append(
            f"spanning-tree mst {instance} priority {priority}"
            if mode == "mstp" and instance
            else f"spanning-tree priority {priority}"
        )
    return ConfigPlan("stp.settings", tuple(lines), summary="stp")


def stp_edge_port(interface: str, enabled: bool) -> ConfigPlan:
    name = normalize_interface(interface)
    line = "spanning-tree portfast" if enabled else "no spanning-tree portfast"
    return ConfigPlan("stp.edge_port", (f"interface {name}", line, "exit"))
