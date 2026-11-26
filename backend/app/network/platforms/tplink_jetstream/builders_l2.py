"""L2 builders for TP-Link JetStream / Omada switches (general-port VLAN model).

There is no 'switchport mode': access = one untagged VLAN + PVID, trunk = tagged VLANs
(+ PVID as native), hybrid = both. VLAN names are max 16 characters, descriptions 16.
Interfaces are written 'interface gigabitEthernet 1/0/1'.
"""

from __future__ import annotations

from app.domain.commands.sanitize import quote_free_text
from app.domain.mac import normalize_mac
from app.domain.vlans import to_cisco_ranges, validate_vlan_id
from app.network.builders.plan import ConfigPlan, Verifier
from app.network.parsers.interface_config import InterfaceConfig
from app.network.platforms.contracts import PortMode
from app.network.platforms.tplink_jetstream.naming import tp_iface
from app.network.ports import DeviceDriver


def _verifier(present: set[int], absent: set[int]) -> Verifier:
    def verify(driver: DeviceDriver) -> bool:
        from app.network.platforms.tplink_jetstream.parsers import parse_vlans

        have = {v.vlan_id for v in parse_vlans(driver.run_show("show vlan").output)}
        return present <= have and not (absent & have)

    return verify


def _iface(name: str, body: list[str]) -> tuple[str, ...]:
    return (f"interface {tp_iface(name)}", *body, "exit") if body else ()


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
            lines.append(f"name {quote_free_text(name, max_length=16)}")
        lines.append("exit")
    return ConfigPlan(
        "vlan.create",
        tuple(lines),
        (f"no vlan {to_cisco_ranges(missing)}",) if missing else (),
        _verifier(set(wanted), set()),
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
            restore.append(f"name {quote_free_text(existing[vid] or '', max_length=16)}")
        restore.append("exit")
    return ConfigPlan(
        "vlan.delete",
        (f"no vlan {to_cisco_ranges(targets)}",),
        tuple(restore),
        _verifier(set(), set(targets)),
        summary=f"delete VLAN(s) {to_cisco_ranges(targets)}",
    )


def rename_vlan(vlan_id: int, name: str) -> ConfigPlan:
    validate_vlan_id(vlan_id)
    return ConfigPlan(
        "vlan.rename",
        (f"vlan {vlan_id}", f"name {quote_free_text(name, max_length=16)}", "exit"),
        summary=f"rename VLAN {vlan_id}",
    )


def _members(current: InterfaceConfig) -> set[int]:
    return set(current.hybrid_tagged) | set(current.hybrid_untagged)


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
    want_tagged: set[int] = set()
    want_untagged: set[int] = set()
    want_pvid: int | None = pvid
    if mode == "access":
        if access_vlan is None:
            raise ValueError("access mode needs access_vlan")
        want_untagged, want_pvid = {validate_vlan_id(access_vlan)}, access_vlan
    elif mode == "trunk":
        want_tagged = {validate_vlan_id(v) for v in trunk_vlans or []}
    else:
        want_tagged = {validate_vlan_id(v) for v in tagged or []}
        want_untagged = {validate_vlan_id(v) for v in untagged or []}
    have = _members(current)
    body: list[str] = []
    if remove := sorted(have - want_tagged - want_untagged):
        body.append(f"no switchport general allowed vlan {to_cisco_ranges(remove)}")
    if add_t := sorted(want_tagged - set(current.hybrid_tagged)):
        body.append(f"switchport general allowed vlan {to_cisco_ranges(add_t)} tagged")
    if add_u := sorted(want_untagged - set(current.hybrid_untagged)):
        body.append(f"switchport general allowed vlan {to_cisco_ranges(add_u)} untagged")
    if want_pvid is not None and current.hybrid_pvid != want_pvid:
        body.append(f"switchport pvid {validate_vlan_id(want_pvid)}")
    restore: list[str] = []
    if wanted_all := sorted(want_tagged | want_untagged):
        restore.append(f"no switchport general allowed vlan {to_cisco_ranges(wanted_all)}")
    if current.hybrid_tagged:
        tagged_now = to_cisco_ranges(sorted(current.hybrid_tagged))
        restore.append(f"switchport general allowed vlan {tagged_now} tagged")
    if current.hybrid_untagged:
        restore.append(
            "switchport general allowed vlan "
            f"{to_cisco_ranges(sorted(current.hybrid_untagged))} untagged"
        )
    if current.hybrid_pvid is not None:
        restore.append(f"switchport pvid {current.hybrid_pvid}")
    return ConfigPlan(
        "interface.port_mode",
        _iface(interface, body),
        _iface(interface, restore) if body else (),
        summary=f"{interface} -> {mode}",
    )


def trunk_vlans(
    interface: str, add: list[int], remove: list[int], current: InterfaceConfig
) -> ConfigPlan:
    have = _members(current)
    to_add = sorted(set(add) - have)
    to_remove = sorted(set(remove) & have)
    body: list[str] = []
    rollback: list[str] = []
    if to_add:
        body.append(f"switchport general allowed vlan {to_cisco_ranges(to_add)} tagged")
        rollback.append(f"no switchport general allowed vlan {to_cisco_ranges(to_add)}")
    if to_remove:
        body.append(f"no switchport general allowed vlan {to_cisco_ranges(to_remove)}")
        rollback.append(f"switchport general allowed vlan {to_cisco_ranges(to_remove)} tagged")
    return ConfigPlan(
        "interface.trunk_vlans",
        _iface(interface, body),
        _iface(interface, rollback),
        summary=f"{interface} trunk",
    )


def set_admin_state(interface: str, enabled: bool, current: InterfaceConfig) -> ConfigPlan:
    if enabled != current.shutdown:
        return ConfigPlan("interface.admin_state", (), summary="no change")
    line, undo = ("no shutdown", "shutdown") if enabled else ("shutdown", "no shutdown")
    return ConfigPlan(
        "interface.admin_state",
        _iface(interface, [line]),
        _iface(interface, [undo]),
        summary=f"{interface} {'enable' if enabled else 'shutdown'}",
    )


def set_description(
    interface: str, description: str | None, current: InterfaceConfig
) -> ConfigPlan:
    if (description or None) == current.description:
        return ConfigPlan("interface.description", (), summary="no change")
    line = (
        f"description {quote_free_text(description, max_length=16)}"
        if description
        else "no description"
    )
    undo = f"description {current.description}" if current.description else "no description"
    return ConfigPlan("interface.description", _iface(interface, [line]), _iface(interface, [undo]))


def set_speed_duplex(
    interface: str, speed: int | None, duplex: str | None, auto: bool
) -> ConfigPlan:
    if auto:
        body = ["speed auto", "duplex auto"]
    else:
        body = []
        if speed:
            if speed not in {10, 100, 1000, 10000}:
                raise ValueError("unsupported speed")
            body.append(f"speed {speed}")
        if duplex:
            if duplex not in {"full", "half"}:
                raise ValueError("duplex must be full or half")
            body.append(f"duplex {duplex}")
    return ConfigPlan("interface.speed_duplex", _iface(interface, body), summary=interface)


def static_mac(
    mac: str, vlan: int, interface: str | None, *, remove: bool = False, blackhole: bool = False
) -> ConfigPlan:
    hw = normalize_mac(mac)
    validate_vlan_id(vlan)
    if blackhole:
        line = f"mac address-table filtering {hw} vid {vlan}"
        no_line = f"no {line}"
    else:
        if interface is None:
            raise ValueError("static MAC needs an interface")
        line = f"mac address-table static {hw} vid {vlan} interface {tp_iface(interface)}"
        no_line = f"no mac address-table static {hw} vid {vlan}"
    apply, undo = (no_line, line) if remove else (line, no_line)
    op = "mac.blackhole" if blackhole else "mac.static"
    return ConfigPlan(f"{op}.{'remove' if remove else 'add'}", (apply,), (undo,), summary=op)


def stp_settings(mode: str | None, priority: int | None, instance: int = 0) -> ConfigPlan:
    lines: list[str] = []
    if mode:
        if mode not in {"stp", "rstp", "mstp"}:
            raise ValueError("mode must be stp, rstp or mstp")
        lines += ["spanning-tree", f"spanning-tree mode {mode}"]
    if priority is not None:
        if priority % 4096 or not 0 <= priority <= 61440:
            raise ValueError("priority must be a multiple of 4096 in 0-61440")
        lines.append(f"spanning-tree priority {priority}")
    return ConfigPlan("stp.settings", tuple(lines), summary="stp")


def stp_edge_port(interface: str, enabled: bool) -> ConfigPlan:
    state = "enable" if enabled else "disable"
    return ConfigPlan(
        "stp.edge_port", _iface(interface, [f"spanning-tree common-config portfast {state}"])
    )


def dhcp_snooping(vlans: list[int], trusted: list[str]) -> ConfigPlan:
    lines = ["ip dhcp snooping"]
    if vlans:
        lines.append(f"ip dhcp snooping vlan {to_cisco_ranges(vlans)}")
    for iface in trusted:
        lines += list(_iface(iface, ["ip dhcp snooping trust"]))
    return ConfigPlan("dhcp.snooping", tuple(lines), summary="dhcp snooping")
