"""802.1X / MAB provisioning for Cisco SMB (CBS350 / SG350).

No RADIUS CoA on these switches: sessions are re-authenticated with 'dot1x re-authenticate'
or by bouncing the port. RADIUS-assigned VLANs need 'dot1x radius-attributes vlan'.
"""

from __future__ import annotations

import ipaddress

from app.domain.commands.categories import CommandCategory
from app.domain.commands.sanitize import validate_cli_line
from app.domain.devices import DeviceFamily
from app.domain.interfaces import normalize_interface
from app.domain.vlans import validate_vlan_id
from app.network.builders.cli_nac import NacProvisionSpec
from app.network.builders.plan import ConfigPlan

_NAC_PREFIXES = ("radius-server", "encrypted radius-server", "aaa", "dot1x")


def _secret(value: str) -> str:
    if not value or any(c.isspace() for c in value) or len(value) > 64 or '"' in value:
        raise ValueError("RADIUS shared secret must be 1-64 non-space characters without quotes")
    validate_cli_line(value)
    return value


def provision(family: DeviceFamily, spec: NacProvisionSpec) -> ConfigPlan:
    host = str(ipaddress.ip_address(spec.radius_host))
    secret = _secret(spec.shared_secret)
    methods = {"both": "802.1x mac", "dot1x": "802.1x", "mab": "mac"}[spec.mode]
    lines = [
        f"radius-server host {host} auth-port {spec.auth_port} acct-port {spec.acct_port} "
        f"timeout {spec.timeout} retransmit {spec.retransmit} key {secret} usage dot1.x",
        "aaa authentication dot1x default radius",
        "dot1x system-auth-control",
    ]
    if spec.mode in {"mab", "both"}:
        lines.append("dot1x mac-auth radius username groupsize 2 separator - lowercase")
    if spec.guest_vlan is not None:
        lines += [f"interface vlan {validate_vlan_id(spec.guest_vlan)}", "dot1x guest-vlan", "exit"]
    for iface in spec.interfaces:
        lines += [
            f"interface {normalize_interface(iface)}",
            f"dot1x authentication {methods}",
            "dot1x host-mode multi-sessions",
            "dot1x radius-attributes vlan",
            "dot1x port-control auto",
        ]
        if spec.guest_vlan is not None:
            lines.append("dot1x guest-vlan enable")
        lines.append("exit")
    undo: list[str] = []
    for iface in spec.interfaces:
        undo += [f"interface {normalize_interface(iface)}", "no dot1x port-control", "exit"]
    return ConfigPlan(
        "nac.provision",
        tuple(lines),
        tuple(undo),
        summary=f"NAC {spec.mode} via RADIUS {host} on {len(spec.interfaces)} port(s)",
        notes=(
            "Cisco SMB has no RADIUS CoA: disconnect/reauthenticate from the platform is not "
            "available; quarantine VLAN fallback on server failure is not configurable.",
        ),
        min_category=CommandCategory.DANGEROUS,
        min_category_reason="AAA/RADIUS provisioning",
    )


def related_config(running: str) -> str:
    keep: list[str] = []
    block: list[str] = []
    in_iface = False
    for raw in running.splitlines():
        line = raw.strip()
        if line.startswith(_NAC_PREFIXES):
            keep.append(line)
        elif line.startswith("interface "):
            in_iface, block = True, [line]
        elif line == "exit" and in_iface:
            nac = [b for b in block[1:] if b.startswith("dot1x")]
            if nac:
                keep += [block[0], *[f" {b}" for b in nac]]
            in_iface = False
        elif in_iface:
            block.append(line)
    return "\n".join(keep)
