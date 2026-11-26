"""Switch-side NAC provisioning for Cisco IOS / IOS-XE (802.1X + MAB, RADIUS, CoA).

Uses the classic authentication-manager syntax accepted by IOS 15.2 and IOS-XE 16/17 (devices
converted to IBNS 2.0 with 'authentication display new-style' need policy-maps instead).
Deploy only through the job engine ('nac.provision') with a DANGEROUS confirmation.
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

SERVER = "NAC"
GROUP = "NAC-GROUP"
NOTES = (
    "'aaa new-model' switches VTY/console login to AAA method lists: make sure "
    "'aaa authentication login default ...' keeps your current login working before deploying.",
    "IBNS 2.0 (new-style) devices need policy-map based configuration instead.",
)


def _ip(value: str) -> str:
    return str(ipaddress.ip_address(value))


def _secret(value: str) -> str:
    if not value or any(c.isspace() for c in value) or len(value) > 63 or '"' in value:
        raise ValueError("RADIUS shared secret must be 1-63 non-space characters without quotes")
    validate_cli_line(value)
    return value


def provision(family: DeviceFamily, spec: NacProvisionSpec) -> ConfigPlan:
    host, controller = _ip(spec.radius_host), _ip(spec.controller_ip)
    secret = _secret(spec.shared_secret)
    q_vlan = validate_vlan_id(spec.quarantine_vlan)
    lines = [
        "aaa new-model",
        f"radius server {SERVER}",
        f"address ipv4 {host} auth-port {spec.auth_port} acct-port {spec.acct_port}",
        f"key {secret}",
        f"timeout {spec.timeout}",
        f"retransmit {spec.retransmit}",
        "exit",
        f"aaa group server radius {GROUP}",
        f"server name {SERVER}",
        "exit",
        f"aaa authentication dot1x default group {GROUP}",
        f"aaa authorization network default group {GROUP}",
        f"aaa accounting dot1x default start-stop group {GROUP}",
        "aaa accounting update newinfo periodic 15",
        "aaa server radius dynamic-author",
        f"client {controller} server-key {secret}",
        "exit",
        "radius-server attribute 6 on-for-login-auth",
        "radius-server attribute 8 include-in-access-req",
        "radius-server attribute 25 access-request include",
        "radius-server vsa send authentication",
        "radius-server vsa send accounting",
        "dot1x system-auth-control",
    ]
    order = {"both": "dot1x mab", "dot1x": "dot1x", "mab": "mab"}[spec.mode]
    for iface in spec.interfaces:
        body = [
            f"interface {normalize_interface(iface)}",
            "switchport mode access",
            "authentication host-mode multi-auth",
            f"authentication order {order}",
            f"authentication priority {order}",
            "authentication port-control auto",
            "authentication periodic",
            "authentication timer reauthenticate server",
            f"authentication event server dead action authorize vlan {q_vlan}",
            "authentication event server alive action reinitialize",
        ]
        if spec.mode in {"dot1x", "both"}:
            body += ["dot1x pae authenticator", "dot1x timeout tx-period 10"]
            body.append(f"authentication event fail action authorize vlan {q_vlan}")
            if spec.guest_vlan is not None:
                body.append(
                    "authentication event no-response action authorize vlan "
                    f"{validate_vlan_id(spec.guest_vlan)}"
                )
        if spec.mode in {"mab", "both"}:
            body.append("mab")
        body += ["spanning-tree portfast", "exit"]
        lines += body
    undo: list[str] = []
    for iface in spec.interfaces:
        undo += [
            f"interface {normalize_interface(iface)}",
            "no authentication port-control auto",
            "no mab",
            "no dot1x pae authenticator",
            "exit",
        ]
    return ConfigPlan(
        "nac.provision",
        tuple(lines),
        tuple(undo),
        summary=f"NAC {spec.mode} via RADIUS {host} on {len(spec.interfaces)} port(s)",
        notes=NOTES,
        min_category=CommandCategory.DANGEROUS,
        min_category_reason="AAA/RADIUS provisioning",
    )


_NAC_WORDS = ("authentication", "dot1x", "mab", "access-session")


def related_config(running: str) -> str:
    """Current AAA/RADIUS/802.1X lines and the NAC lines of each interface."""
    keep: list[str] = []
    header: str | None = None
    include_body = False
    for line in running.splitlines():
        if not line.startswith(" "):
            header, include_body = line, line.startswith(("aaa", "radius", "dot1x"))
            if include_body:
                keep.append(line)
            continue
        if include_body:
            keep.append(line)
        elif header and header.startswith("interface ") and line.strip().startswith(_NAC_WORDS):
            if header not in keep:
                keep.append(header)
            keep.append(line)
    return "\n".join(keep)
