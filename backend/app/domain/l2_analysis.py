"""Pure L2/L3 anomaly heuristics over collected tables (no I/O, table-driven tests)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    key: str
    detail: str
    severity: str = "warning"


@dataclass(frozen=True, slots=True)
class MacObservation:
    device_id: str
    mac: str
    vlan: int | None
    interface: str | None
    is_edge: bool = True


@dataclass(frozen=True, slots=True)
class ArpObservation:
    device_id: str
    ip: str
    mac: str
    entry_type: str = "dynamic"


@dataclass(frozen=True, slots=True)
class BindingObservation:
    device_id: str
    ip: str
    mac: str
    interface: str | None = None


@dataclass(frozen=True, slots=True)
class MoveObservation:
    mac: str
    timestamp: datetime
    from_interface: str | None
    to_interface: str | None


def mac_flapping(
    moves: Iterable[MoveObservation],
    *,
    window: timedelta = timedelta(minutes=10),
    threshold: int = 3,
) -> list[Finding]:
    """A MAC that moved ``threshold`` times inside ``window`` is flapping (loop or spoofing)."""
    by_mac: dict[str, list[MoveObservation]] = defaultdict(list)
    for move in moves:
        by_mac[move.mac].append(move)
    findings: list[Finding] = []
    for mac, items in by_mac.items():
        items.sort(key=lambda m: m.timestamp)
        for i in range(len(items) - threshold + 1):
            if items[i + threshold - 1].timestamp - items[i].timestamp <= window:
                ports = sorted({p for m in items for p in (m.from_interface, m.to_interface) if p})
                findings.append(
                    Finding("mac_flapping", mac, f"{len(items)} moves between {', '.join(ports)}")
                )
                break
    return findings


def duplicate_ips(arp: Iterable[ArpObservation]) -> list[Finding]:
    """Same IP answered by more than one MAC (excluding local interface entries)."""
    macs: dict[str, set[str]] = defaultdict(set)
    for entry in arp:
        if entry.entry_type != "interface":
            macs[entry.ip].add(entry.mac)
    return [
        Finding("duplicate_ip", ip, f"IP claimed by {', '.join(sorted(m))}", "critical")
        for ip, m in sorted(macs.items())
        if len(m) > 1
    ]


def arp_anomalies(
    arp: Iterable[ArpObservation],
    *,
    max_ips_per_mac: int = 16,
    router_macs: frozenset[str] = frozenset(),
) -> list[Finding]:
    """One MAC answering for many IPs (proxy ARP / spoofing), excluding known routers."""
    ips: dict[str, set[str]] = defaultdict(set)
    for entry in arp:
        if entry.entry_type != "interface" and entry.mac not in router_macs:
            ips[entry.mac].add(entry.ip)
    return [
        Finding("arp_many_ips", mac, f"{len(v)} IPs resolve to this MAC")
        for mac, v in sorted(ips.items())
        if len(v) > max_ips_per_mac
    ]


def binding_mismatches(
    bindings: Iterable[BindingObservation], arp: Iterable[ArpObservation]
) -> list[Finding]:
    """DHCP snooping says IP X belongs to MAC A, but ARP resolves X to MAC B (spoof/static IP)."""
    by_ip = {b.ip: b for b in bindings}
    findings = []
    for entry in arp:
        binding = by_ip.get(entry.ip)
        if binding and binding.mac != entry.mac and entry.entry_type != "interface":
            findings.append(
                Finding(
                    "binding_arp_mismatch",
                    entry.ip,
                    f"binding {binding.mac} but ARP {entry.mac} on device {entry.device_id}",
                    "critical",
                )
            )
    return findings


def duplicate_bindings(bindings: Iterable[BindingObservation]) -> list[Finding]:
    macs: dict[str, set[str]] = defaultdict(set)
    for b in bindings:
        macs[b.ip].add(b.mac)
    return [
        Finding("dhcp_duplicate_ip", ip, f"leased to {', '.join(sorted(m))}", "critical")
        for ip, m in sorted(macs.items())
        if len(m) > 1
    ]


def unknown_macs(observations: Iterable[MacObservation], known: set[str]) -> list[Finding]:
    """Edge-port MACs not registered as endpoints (input for NAC / unknown_mac alerts)."""
    seen: dict[str, MacObservation] = {}
    for obs in observations:
        if obs.is_edge and obs.mac not in known:
            seen.setdefault(obs.mac, obs)
    return [
        Finding("unknown_mac", mac, f"on {o.interface} vlan {o.vlan} device {o.device_id}")
        for mac, o in sorted(seen.items())
    ]


def untrusted_dhcp_ports(trusted_ports: Iterable[str], uplinks: Iterable[str]) -> list[Finding]:
    """'dhcp snooping trusted' on a non-uplink port lets a rogue DHCP server answer clients."""
    allowed = set(uplinks)
    return [
        Finding(
            "unexpected_dhcp_server_port", port, "DHCP-trusted port is not an uplink", "critical"
        )
        for port in sorted(set(trusted_ports) - allowed)
    ]
