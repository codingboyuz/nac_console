"""802.1X / MAB provisioning for TP-Link JetStream / Omada switches.

RADIUS-assigned VLANs need 'dot1x vlan-assignment'. No RADIUS CoA is documented: sessions
cannot be disconnected from the platform; re-authentication happens on the next period.
"""

from __future__ import annotations

import ipaddress

from app.domain.commands.categories import CommandCategory
from app.domain.commands.sanitize import validate_cli_line
from app.domain.devices import DeviceFamily
from app.domain.vlans import validate_vlan_id
from app.network.builders.cli_nac import NacProvisionSpec
from app.network.builders.plan import ConfigPlan
from app.network.platforms.tplink_jetstream.naming import tp_iface

GROUP = "NAC"


def _secret(value: str) -> str:
    if not value or any(c.isspace() for c in value) or len(value) > 31 or '"' in value:
        raise ValueError("RADIUS shared secret must be 1-31 non-space characters without quotes")
    validate_cli_line(value)
    return value


def provision(family: DeviceFamily, spec: NacProvisionSpec) -> ConfigPlan:
    host = str(ipaddress.ip_address(spec.radius_host))
    secret = _secret(spec.shared_secret)
    lines = [
        f"radius-server host {host} auth-port {spec.auth_port} acct-port {spec.acct_port} "
        f"timeout {spec.timeout} retransmit {spec.retransmit} key {secret}",
        f"aaa group radius {GROUP}",
        f"server {host}",
        "exit",
        f"aaa authentication dot1x default {GROUP}",
        f"aaa accounting dot1x default {GROUP}",
        "dot1x system-auth-control",
        "dot1x vlan-assignment",
        "dot1x accounting",
    ]
    for iface in spec.interfaces:
        lines += [
            f"interface {tp_iface(iface)}",
            "dot1x",
            "dot1x port-control auto",
            "dot1x port-method mac-based",
        ]
        if spec.mode in {"mab", "both"}:
            lines.append("dot1x mab")
        if spec.guest_vlan is not None:
            validate_vlan_id(spec.guest_vlan)
            lines.append("dot1x guest-vlan")
        lines.append("exit")
    undo: list[str] = []
    for iface in spec.interfaces:
        undo += [f"interface {tp_iface(iface)}", "no dot1x", "exit"]
    notes = ["TP-Link switches document no RADIUS CoA: disconnect/reauthenticate is unavailable."]
    if spec.guest_vlan is not None:
        notes.append("Set the guest VLAN id itself on the switch ('dot1x guest-vlan <id>').")
    return ConfigPlan(
        "nac.provision",
        tuple(lines),
        tuple(undo),
        summary=f"NAC {spec.mode} via RADIUS {host} on {len(spec.interfaces)} port(s)",
        notes=tuple(notes),
        min_category=CommandCategory.DANGEROUS,
        min_category_reason="AAA/RADIUS provisioning",
    )


def related_config(running: str) -> str:
    keep: list[str] = []
    header: str | None = None
    for raw in running.splitlines():
        line = raw.strip()
        if not raw.startswith(" "):
            header = line
            if line.startswith(("radius-server", "aaa", "dot1x")):
                keep.append(line)
        elif header and header.startswith("interface ") and line.startswith("dot1x"):
            if header not in keep:
                keep.append(header)
            keep.append(f" {line}")
    return "\n".join(keep)
