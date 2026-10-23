"""Switch-side NAC provisioning templates (generated, never pushed blindly).

Every command here is VRP-VERSION-DEPENDENT:
- S-series V200R019 (NAC unified mode): radius-server template, aaa schemes/domain,
  dot1x-access-profile / mac-access-profile / authentication-profile bound to access ports.
- CE (VRP V8): same profile model; RADIUS template and AAA syntax identical for this subset,
  but changes are committed (two-stage) and dot1x must be enabled globally first.
Deploy only through the job engine (`nac.provision`) with a DANGEROUS confirmation.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Literal

from app.domain.commands.categories import CommandCategory
from app.domain.commands.sanitize import validate_cli_line
from app.domain.devices import DeviceFamily
from app.domain.interfaces import normalize_interface
from app.domain.vlans import validate_vlan_id
from app.network.builders.plan import ConfigPlan

Mode = Literal["dot1x", "mab", "both"]
TEMPLATE = "nac"


@dataclass(frozen=True, slots=True)
class NacProvisionSpec:
    radius_host: str
    shared_secret: str
    controller_ip: str  # CoA client (this platform) allowed via 'radius-server authorization'
    auth_port: int = 1812
    acct_port: int = 1813
    interfaces: tuple[str, ...] = field(default_factory=tuple)
    mode: Mode = "both"
    guest_vlan: int | None = None
    quarantine_vlan: int = 999
    retransmit: int = 3
    timeout: int = 5


def _ip(value: str) -> str:
    return str(ipaddress.ip_address(value))


def _secret(value: str) -> str:
    # The secret is placed on the wire as "cipher <secret>"; VRP encrypts it on entry.
    if not value or any(c.isspace() for c in value) or len(value) > 128:
        raise ValueError("RADIUS shared secret must be 1-128 non-space characters")
    validate_cli_line(value)
    return value


def provision(family: DeviceFamily, spec: NacProvisionSpec) -> ConfigPlan:
    host, controller = _ip(spec.radius_host), _ip(spec.controller_ip)
    secret = _secret(spec.shared_secret)
    q_vlan = validate_vlan_id(spec.quarantine_vlan)
    lines: list[str] = []
    if family is DeviceFamily.CE:
        lines.append("dot1x enable")  # VRP-VERSION-DEPENDENT: global enable required on CE
    lines += [
        f"radius-server template {TEMPLATE}",
        f"radius-server shared-key cipher {secret}",
        f"radius-server authentication {host} {spec.auth_port} weight 80",
        f"radius-server accounting {host} {spec.acct_port} weight 80",
        f"radius-server retransmit {spec.retransmit} timeout {spec.timeout}",
        "quit",
        f"radius-server authorization {controller} shared-key cipher {secret}",
        "aaa",
        "authentication-scheme nac-auth",
        "authentication-mode radius",
        "quit",
        "accounting-scheme nac-acct",
        "accounting-mode radius",
        "accounting realtime 15",
        "quit",
        "domain default",
        "authentication-scheme nac-auth",
        "accounting-scheme nac-acct",
        f"radius-server {TEMPLATE}",
        "quit",
        "quit",
    ]
    if spec.mode in {"dot1x", "both"}:
        lines += ["dot1x-access-profile name nac-dot1x", "quit"]
    if spec.mode in {"mab", "both"}:
        lines += ["mac-access-profile name nac-mac", "quit"]
    lines.append("authentication-profile name nac-auth")
    if spec.mode in {"dot1x", "both"}:
        lines.append("dot1x-access-profile nac-dot1x")
    if spec.mode in {"mab", "both"}:
        lines.append("mac-access-profile nac-mac")
    lines.append(f"authentication event authen-server-down action authorize vlan {q_vlan}")
    lines.append("authentication event authen-server-up action re-authen")
    if spec.guest_vlan is not None:
        lines.append(
            "authentication event pre-authen action authorize vlan "
            f"{validate_vlan_id(spec.guest_vlan)}"
        )
    lines.append("quit")
    for iface in spec.interfaces:
        lines += [
            f"interface {normalize_interface(iface)}",
            "authentication-profile nac-auth",
            "quit",
        ]
    rollback = [f"interface {normalize_interface(i)}" for i in spec.interfaces]
    undo: list[str] = []
    for item in rollback:
        undo += [item, "undo authentication-profile", "quit"]
    return ConfigPlan(
        "nac.provision",
        tuple(lines),
        tuple(undo),
        summary=f"NAC {spec.mode} via RADIUS {host} on {len(spec.interfaces)} port(s)",
        min_category=CommandCategory.DANGEROUS,
        min_category_reason="AAA/RADIUS provisioning",
    )


RELATED_PREFIXES = (
    "radius-server",
    "aaa",
    " authentication-scheme",
    " accounting-scheme",
    " domain",
    "dot1x",
    "mac-access-profile",
    "authentication-profile",
    " authentication-profile",
)


def related_config(running: str) -> str:
    """Current AAA/RADIUS/NAC-related lines, for side-by-side review with the generated plan."""
    keep: list[str] = []
    include_body = False
    for line in running.splitlines():
        if not line.startswith(" "):
            include_body = line.startswith(
                ("radius-server", "aaa", "dot1x", "mac-access", "authentication-profile")
            )
            if include_body or line.startswith("interface "):
                keep.append(line)
            continue
        if include_body or "authentication-profile" in line:
            keep.append(line)
    # Drop interface headers without NAC lines.
    out: list[str] = []
    for i, line in enumerate(keep):
        if line.startswith("interface ") and not (
            i + 1 < len(keep) and keep[i + 1].startswith(" ")
        ):
            continue
        out.append(line)
    return "\n".join(out)
