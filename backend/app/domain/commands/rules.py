"""Deterministic regex rule tables for the command classifier (lower-cased, single-spaced input).

Rules are evaluated DANGEROUS first, then SAFE, then WARNING. Anything unmatched is DANGEROUS
(deny by default). Several commands are VRP-VERSION-DEPENDENT; rules accept both S (V200R) and
CE (V8) spellings where they differ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.commands.categories import CommandCategory


@dataclass(frozen=True, slots=True)
class Rule:
    pattern: re.Pattern[str]
    category: CommandCategory
    reason: str


def _r(pattern: str, category: CommandCategory, reason: str) -> Rule:
    return Rule(re.compile(pattern), category, reason)


S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS

# Read-only display commands (user view or any view). Abbreviated "dis" is accepted.
_DISPLAY_TOPICS = (
    "version|current-configuration|saved-configuration|startup|ip interface|interface|vlan|"
    "mac-address|arp|ip routing-table|ipv6 routing-table|stp|cpu-usage|memory-usage|device|"
    "transceiver|lldp|dhcp|acl|traffic-filter|ospf|bgp|rip|vrrp|route-policy|ip-prefix|"
    "eth-trunk|link-aggregation|port|counters|logbuffer|trapbuffer|alarm|clock|elabel|esn|"
    "patch-information|users|ssh|snmp-agent|ntp|power|fan|temperature|environment|health|"
    "dot1x|mac-authen|access-user|authentication|radius-server|aaa|domain|loopback-detect|"
    "port-security|ip source|ip host|ipv6 interface|cpu-defend|qos|traffic|this|diagnostic|"
    "netconf|license|boot|configuration|debugging|info-center|history-command|"
    "storm-control|igmp-snooping|vxlan|evpn|bfd|nd|dldp|lacp|mpls|isis|static-route|"
    "vlanif|current"
)

# First words of display topics, used to expand unambiguous abbreviations ("dis ver").
DISPLAY_WORDS: frozenset[str] = frozenset(t.split(" ")[0] for t in _DISPLAY_TOPICS.split("|"))

DANGEROUS_RULES: tuple[Rule, ...] = (
    _r(r"^reboot\b", D, "reboots the device"),
    _r(r"^schedule reboot\b", D, "schedules a reboot"),
    _r(r"^reset saved-configuration\b", D, "erases the startup configuration"),
    _r(r"^reset\b", D, "reset commands clear state/counters/sessions"),
    _r(r"^(format|fixdisk)\b", D, "formats storage"),
    _r(r"^(delete|rmdir|move|rename|copy|undelete)\b", D, "file system modification"),
    _r(r"^startup\b", D, "changes startup software/configuration"),
    _r(r"^(upgrade|patch|install|license)\b", D, "software/licence change"),
    _r(
        r"^(aaa|local-user|undo local-user|user-interface|hwtacacs|radius-server|"
        r"undo radius-server|authentication-scheme|authorization-scheme|accounting-scheme|"
        r"domain|undo domain|super password|user privilege)\b",
        D,
        "AAA / user / VTY change",
    ),
    _r(
        r"^(undo )?(stelnet|ssh|telnet|netconf|snetconf|sftp|ftp|http|snmp-agent sys-info|"
        r"snmp-agent usm-user|snmp-agent community)\b",
        D,
        "management-plane service change",
    ),
    _r(r"^(undo )?(cpu-defend|clock|ntp-service|sysname|info-center)\b", D, "system-wide change"),
    _r(r"^undo vlan batch\b", D, "bulk VLAN removal"),
    _r(r"^undo interface\b", D, "removes an interface"),
    _r(r"^undo ip address\b.*\bsub\b", W, "removes a secondary address"),
    _r(r"^(load|configuration replace|rollback configuration)\b", D, "replaces configuration"),
    _r(r"^(set|debugging|terminal debugging)\b", D, "debug/system setting"),
    _r(r"^(save|commit) (force|all)\b", D, "forced persistence"),
    _r(r"^clear\b", D, "clears configuration/state"),
)

SAFE_RULES: tuple[Rule, ...] = (
    _r(rf"^(display|dis) ({_DISPLAY_TOPICS})\b", S, "read-only display command"),
    _r(r"^(display|dis)$", S, "read-only display command"),
    _r(r"^(dir|pwd)\b", S, "read-only file listing"),
    _r(r"^(screen-length 0 temporary|undo screen-length temporary)$", S, "session paging"),
)

WARNING_RULES: tuple[Rule, ...] = (
    _r(r"^(system-view|quit|return|commit|save|y|n)$", W, "view navigation / persistence"),
    _r(r"^commit trial \d+$", W, "trial commit with automatic rollback"),
    _r(r"^(ping|tracert|traceroute)\b", W, "active probe generates traffic"),
    _r(r"^(undo )?interface \S+", W, "enters/creates an interface view"),
    _r(r"^(undo )?shutdown$", W, "interface admin state change"),
    _r(r"^(undo )?description\b", W, "description change"),
    _r(r"^(undo )?port (link-type|default vlan|trunk|hybrid|access)\b", W, "port VLAN change"),
    _r(r"^(undo )?port-isolate\b", W, "port isolation change"),
    _r(r"^(undo )?(speed|duplex|negotiation|mtu|jumboframe|flow-control)\b", W, "PHY setting"),
    _r(r"^(undo )?vlan (batch )?\d", W, "VLAN create/delete"),
    _r(r"^(undo )?name\b", W, "VLAN name change"),
    _r(r"^(undo )?acl (number |name |ipv6 )?\S+", W, "ACL definition"),
    _r(r"^(undo )?rule\b", W, "ACL rule change"),
    _r(r"^(undo )?traffic-filter\b", W, "ACL application change"),
    _r(r"^(undo )?mac-address (static|blackhole|aging-time|sticky)\b", W, "static MAC change"),
    _r(r"^(undo )?stp\b", W, "STP setting"),
    _r(r"^(undo )?ip address\b", W, "interface IP change"),
    _r(r"^(undo )?ip route-static\b", W, "static route change"),
    _r(r"^(undo )?(ospf|bgp|rip|isis)( \d+)?\b", W, "routing protocol process"),
    _r(
        r"^(undo )?(area|network|peer|import-route|default-route-advertise|router-id|"
        r"silent-interface|summary|filter-policy|ipv4-family|preference)\b",
        W,
        "routing setting",
    ),
    _r(r"^(undo )?vrrp vrid\b", W, "VRRP setting"),
    _r(r"^(undo )?(route-policy|ip ip-prefix|if-match|apply)\b", W, "routing policy"),
    _r(r"^(undo )?dhcp (enable|snooping)\b", W, "DHCP snooping setting"),
    _r(r"^(undo )?(lldp|loopback-detect|storm-control)\b", W, "L2 protection setting"),
    _r(
        r"^(undo )?(dot1x|mac-authen|authentication|authentication-profile|access-domain|"
        r"dot1x-access-profile|mac-access-profile|free-rule)\b",
        W,
        "NAC port setting",
    ),
    _r(r"^(undo )?eth-trunk \d+$", W, "LAG membership change"),
    _r(r"^(undo )?(mode lacp|load-balance|max active-linknumber)\b", W, "LAG setting"),
)
