"""Command classifier for Cisco IOS / IOS-XE (deny by default, view and context aware).

Rules see lower-cased, single-spaced text with 'sh' / unambiguous 'show' topics expanded.
DANGEROUS first, then SAFE, then WARNING; anything unmatched is DANGEROUS.
"""

from __future__ import annotations

import re

from app.domain.commands.categories import CommandCategory, LineClassification
from app.domain.commands.classifier import CommandClassifier, DeviceContext, canonical_interface
from app.domain.commands.rules import Rule
from app.domain.vlans import parse_vlan_list


def _r(pattern: str, category: CommandCategory, reason: str) -> Rule:
    return Rule(re.compile(pattern), category, reason)


S, W, D = CommandCategory.SAFE, CommandCategory.WARNING, CommandCategory.DANGEROUS

_SHOW_TOPICS = (
    "version|running-config|startup-config|interfaces?|ip interface|ipv6 interface|vlan|"
    "mac address-table|mac-address-table|ip arp|arp|ip route|ipv6 route|spanning-tree|"
    "processes|memory|inventory|environment|power|logging|clock|cdp|lldp|etherchannel|"
    "port-channel|access-lists|ip access-lists|ipv6 access-lists|ip dhcp|dot1x|authentication|"
    "access-session|mab|radius|aaa|users|ssh|snmp|ntp|ip ospf|ip bgp|bgp|ip protocols|ip rip|"
    "vrrp|standby|route-map|ip prefix-list|platform|switch|module|license|boot|archive|"
    "ip igmp|storm-control|errdisable|policy-map|class-map|redundancy|stack|vtp|udld|"
    "controllers|hardware|system|vrf|ip vrf|port-security|monitor|flash|file|privilege|"
    "line|terminal|history|sessions|tech-support|ip arp inspection|ip verify|device-tracking|"
    "ip ssh|crypto pki certificates|counters|transceiver|idprom|diagnostic"
)
SHOW_WORDS: frozenset[str] = frozenset(t.split(" ")[0] for t in _SHOW_TOPICS.split("|"))

DANGEROUS_RULES: tuple[Rule, ...] = (
    _r(r"^reload\b", D, "reloads the device"),
    _r(r"^(write erase|erase)\b", D, "erases the startup configuration / storage"),
    _r(r"^(delete|format|squeeze|fsck|mkdir|rmdir|rename|more|verify)\b", D, "file system"),
    _r(
        r"^copy (running-config|system:running-config) (startup-config|nvram:startup-config)$",
        W,
        "saves the running configuration",
    ),
    _r(r"^copy\b", D, "file transfer / configuration replacement"),
    _r(
        r"^(configure (replace|memory|network|overwrite-network)|archive|install|request|"
        r"software|upgrade)\b",
        D,
        "replaces configuration or software",
    ),
    _r(r"^(no )?(boot|config-register|license)\b", D, "boot / licence change"),
    _r(
        r"^(no )?(username|enable (secret|password)|aaa|line|tacacs|tacacs-server|"
        r"radius-server|radius server|radius|privilege|login|security passwords)\b",
        D,
        "AAA / user / VTY change",
    ),
    _r(
        r"^(no )?(snmp-server|ip ssh|ip http|ip scp|ip ftp|ip tftp|crypto|service|" r"transport)\b",
        D,
        "management-plane service change",
    ),
    _r(
        r"^(no )?(hostname|ntp|clock|logging|ip domain|ip name-server|banner|vtp|"
        r"system mtu|switch \d+|power|ip routing|ipv6 unicast-routing|platform)\b",
        D,
        "system-wide change",
    ),
    _r(r"^(no )?(debug|undebug|test|clear|terminal monitor)\b", D, "debug / clear state"),
    _r(r"^no interface\b", D, "removes an interface"),
    _r(r"^default interface\b", D, "resets an interface to factory defaults"),
    _r(r"^no router\b", D, "removes a routing process"),
    _r(r"^do\b", D, "exec command inside configuration mode"),
)

SAFE_RULES: tuple[Rule, ...] = (
    _r(rf"^show ({_SHOW_TOPICS})\b", S, "read-only show command"),
    _r(r"^(dir|pwd)\b", S, "read-only file listing"),
    _r(r"^terminal (length|width) \d+$", S, "session paging"),
)

WARNING_RULES: tuple[Rule, ...] = (
    _r(r"^(configure terminal|end|exit|write memory|write)$", W, "mode navigation / persistence"),
    _r(r"^configure terminal revert timer \d+$", W, "trial configuration with auto revert"),
    _r(r"^configure (confirm|revert now)$", W, "confirm / revert trial configuration"),
    _r(r"^(ping|traceroute)\b", W, "active probe generates traffic"),
    _r(r"^interface (range )?\S+", W, "enters an interface (range) view"),
    _r(r"^(no )?shutdown$", W, "interface admin state change"),
    _r(r"^(no )?description\b", W, "description change"),
    _r(r"^(no )?switchport\b", W, "port VLAN / mode change"),
    _r(r"^(no )?(speed|duplex|mtu|negotiation|flowcontrol|media-type)\b", W, "PHY setting"),
    _r(r"^(no )?vlan \d", W, "VLAN create/delete"),
    _r(r"^(no )?name\b", W, "VLAN name change"),
    _r(r"^(no )?spanning-tree\b", W, "STP setting"),
    _r(r"^(no )?(ip|ipv6|mac) access-list\b", W, "ACL definition"),
    _r(r"^(no )?(\d+ )?(permit|deny|remark)\b", W, "ACL entry change"),
    _r(r"^(no )?sequence \d+\b", W, "ACL entry change"),
    _r(r"^(no )?access-list \d+\b", W, "numbered ACL change"),
    _r(r"^(no )?(ip|ipv6) access-group\b", W, "ACL application change"),
    _r(r"^(no )?(ipv6 traffic-filter|mac access-group)\b", W, "ACL application change"),
    _r(r"^(no )?mac address-table (static|aging-time)\b", W, "static MAC change"),
    _r(r"^(no )?ip address\b", W, "interface IP change"),
    _r(r"^(no )?ip (route|helper-address)\b", W, "static route / relay change"),
    _r(r"^router (ospf|bgp|rip|eigrp)\b", W, "routing protocol process"),
    _r(
        r"^(no )?(network|neighbor|redistribute|default-information|router-id|"
        r"passive-interface|area|address-family|exit-address-family|version|auto-summary|"
        r"timers|maximum-paths|bgp|distance|default-metric|log-adjacency-changes)\b",
        W,
        "routing setting",
    ),
    _r(r"^(no )?(vrrp|standby) \d+\b", W, "first-hop redundancy setting"),
    _r(r"^(no )?(route-map|match|set|ip prefix-list)\b", W, "routing policy"),
    _r(r"^(no )?ip dhcp snooping\b", W, "DHCP snooping setting"),
    _r(r"^(no )?ip arp inspection\b", W, "dynamic ARP inspection setting"),
    _r(r"^(no )?(lldp|cdp|storm-control|udld|errdisable)\b", W, "L2 protection setting"),
    _r(
        r"^(no )?(dot1x|authentication|mab|access-session|service-policy type control)\b",
        W,
        "NAC port setting",
    ),
    _r(r"^(no )?(channel-group|lacp|port-channel)\b", W, "LAG setting"),
)

_ABBREV_SHOW = re.compile(r"^(sh|sho)( |$)")
_INTERFACE_VIEW = re.compile(r"^interface (?!range )(\S+(?: \S+)?)$")
_NO_VLAN = re.compile(r"^no vlan (.+)$")
_DENY_ANY = re.compile(r"^(\d+ )?deny( ip| ipv6)? any( any)?( log)?$|^(\d+ )?deny any$")
_TRUNK_ALLOWED = re.compile(r"^switchport trunk allowed vlan (remove |except |none|\d)")


_TOPIC_WORDS: tuple[tuple[str, ...], ...] = tuple(
    tuple(t.split(" ")) for t in _SHOW_TOPICS.split("|")
)


def _expand_show(words: list[str]) -> list[str]:
    """Expand each abbreviated topic word ('ip int br' -> 'ip interface br') when unique."""
    out = list(words)
    for i in range(1, len(out)):
        prefix = tuple(out[1:i])
        options = {
            t[i - 1].rstrip("?s") if t[i - 1].endswith("s?") else t[i - 1]
            for t in _TOPIC_WORDS
            if len(t) >= i and t[: i - 1] == prefix
        }
        if not options or out[i] in options:
            if not options:
                break
            continue
        matches = [o for o in options if o.startswith(out[i])]
        if len(out[i]) >= 2 and len(matches) == 1:
            out[i] = matches[0]
        else:
            break
    return out


def normalize_ios(line: str) -> str:
    """Lower-case, single-space, expand 'sh' and unambiguous show-topic abbreviations."""
    text = _ABBREV_SHOW.sub("show ", " ".join(line.lower().split())).strip()
    words = text.split(" ")
    if len(words) > 1 and words[0] == "show":
        words = _expand_show(words)
    if text in {"conf t", "config t"}:
        return "configure terminal"
    if text in {"wr", "wr mem"}:
        return "write memory"
    return " ".join(words)


class CiscoIosClassifier(CommandClassifier):
    dangerous_rules = DANGEROUS_RULES
    safe_rules = SAFE_RULES
    warning_rules = WARNING_RULES
    # WARNING commands that are legitimate in exec mode: probes and saving the configuration.
    exec_probe_prefixes: tuple[str, ...] = (
        "ping",
        "traceroute",
        "copy running-config startup-config",
        "write",
    )
    config_outside_view_reason = "configuration command outside configuration mode"

    def normalize(self, line: str) -> str:
        return normalize_ios(line)

    def _next_view(self, text: str, view: str | None) -> str | None:
        match = _INTERFACE_VIEW.match(text)
        if match:
            return f"interface:{canonical_interface(match.group(1).replace(' ', ''))}"
        if text.startswith("interface range "):
            return "interface-range"
        for prefix in (
            "vlan ",
            "ip access-list",
            "ipv6 access-list",
            "mac access-list",
            "router ",
            "line ",
            "route-map ",
            "radius server",
        ):
            if text.startswith(prefix):
                return prefix.strip()
        if text in {"exit", "end"}:
            return None
        return view

    def _escalate(
        self, item: LineClassification, text: str, view: str | None, ctx: DeviceContext
    ) -> LineClassification:
        if item.category is D:
            return item
        iface = view.split(":", 1)[1] if view and view.startswith("interface:") else None
        reason: str | None = None
        if view == "interface-range" and text in {"shutdown", "switchport mode access"}:
            reason = "bulk change on an interface range (may include uplinks)"
        if iface and ctx.is_protected(iface):
            if text == "shutdown":
                reason = f"shutdown on protected interface {iface} (uplink/LAG/management)"
            elif text.startswith("no ip address") and (
                iface == ctx.mgmt_interface or ctx.is_mgmt_vlan_interface(iface)
            ):
                reason = f"removes the management address on {iface}"
            elif _TRUNK_ALLOWED.match(text):
                reason = f"changes the allowed VLANs of uplink {iface}"
            elif text.startswith(
                ("switchport mode", "no switchport", "channel-group", "no channel-group")
            ):
                reason = f"changes port mode/LAG membership of uplink {iface}"
            elif text.startswith(("ip access-group", "ipv6 traffic-filter")):
                reason = f"ACL applied to management/uplink interface {iface}"
        no_vlan = _NO_VLAN.match(text)
        removed = (
            parse_vlan_list(no_vlan.group(1).replace(" ", ""), strict=False) if no_vlan else []
        )
        if ctx.mgmt_vlan is not None and ctx.mgmt_vlan in removed:
            reason = "removes the management VLAN"
        if _DENY_ANY.match(text):
            reason = "ACL entry denies all traffic"
        if reason is None:
            return item
        return LineClassification(item.line, D, reason, item.view)
