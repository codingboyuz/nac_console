"""802.1X / MAB provisioning for NX-OS ('feature dot1x', RADIUS host, AAA, port control).

Nexus platforms support a subset of identity features (no multi-auth on every model, no
guest VLAN); this template configures 802.1X with MAC authentication bypass where available.
"""

from __future__ import annotations

import ipaddress

from app.domain.commands.categories import CommandCategory
from app.domain.commands.sanitize import validate_cli_line
from app.domain.devices import DeviceFamily
from app.domain.interfaces import normalize_interface
from app.network.builders.cli_nac import NacProvisionSpec
from app.network.builders.plan import ConfigPlan
from app.network.platforms.cisco_ios.builders_nac import related_config

__all__ = ["provision", "related_config"]

GROUP = "NAC-GROUP"


def _secret(value: str) -> str:
    if not value or any(c.isspace() for c in value) or len(value) > 63 or '"' in value:
        raise ValueError("RADIUS shared secret must be 1-63 non-space characters without quotes")
    validate_cli_line(value)
    return value


def provision(family: DeviceFamily, spec: NacProvisionSpec) -> ConfigPlan:
    host = str(ipaddress.ip_address(spec.radius_host))
    secret = _secret(spec.shared_secret)
    lines = [
        "feature dot1x",
        f"radius-server host {host} key {secret} auth-port {spec.auth_port} "
        f"acct-port {spec.acct_port} authentication accounting",
        f"radius-server timeout {spec.timeout}",
        f"radius-server retransmit {spec.retransmit}",
        f"aaa group server radius {GROUP}",
        f"server {host}",
        "exit",
        f"aaa authentication dot1x default group {GROUP}",
        f"aaa accounting dot1x default group {GROUP}",
    ]
    for iface in spec.interfaces:
        lines += [f"interface {normalize_interface(iface)}", "dot1x port-control auto"]
        if spec.mode in {"mab", "both"}:
            lines.append("dot1x mac-auth-bypass")
        lines.append("exit")
    undo: list[str] = []
    for iface in spec.interfaces:
        undo += [
            f"interface {normalize_interface(iface)}",
            "no dot1x port-control auto",
            "no dot1x mac-auth-bypass",
            "exit",
        ]
    notes = ["NX-OS identity features vary by Nexus model; guest/quarantine VLANs are not set."]
    if spec.mode == "mab":
        notes.append("MAB-only is not available on NX-OS; 802.1X with MAC bypass is configured.")
    return ConfigPlan(
        "nac.provision",
        tuple(lines),
        tuple(undo),
        summary=f"NAC {spec.mode} via RADIUS {host} on {len(spec.interfaces)} port(s)",
        notes=tuple(notes),
        min_category=CommandCategory.DANGEROUS,
        min_category_reason="AAA/RADIUS provisioning",
    )
