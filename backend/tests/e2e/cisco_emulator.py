"""Stateful Cisco IOS-XE CLI emulator for the SSH mock server (Netmiko 'cisco_ios').

Device state (VLANs, interface bodies, saved flag, revert timer) lives in ``CiscoState`` and is
shared by every session, like a real switch. Supports user/privileged exec, 'enable' with a
secret, 'configure terminal' (+ 'revert timer N' when the archive is configured), interface /
VLAN / ACL / router sub-modes, 'end' / 'exit', 'write memory', 'configure confirm' and
'% Invalid input' rejection of non-IOS syntax.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.domain.interfaces import InvalidInterfaceName, normalize_interface
from app.domain.vlans import parse_vlan_list
from app.network.platforms.cisco_ios.parsers import parse_vlans
from app.network.platforms.cisco_ios.rules import normalize_ios

CRLF = "\r\n"
INVALID = CRLF + "                ^" + CRLF + "% Invalid input detected at '^' marker." + CRLF
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cisco"

_FIXTURE_COMMANDS = {
    "show version": "show_version.txt",
    "show interfaces status": "show_interfaces_status.txt",
    "show interfaces": "show_interfaces.txt",
    "show mac address-table": "show_mac_address-table.txt",
    "show ip arp": "show_ip_arp.txt",
    "show ip dhcp snooping binding": "show_ip_dhcp_snooping_binding.txt",
    "show ip route": "show_ip_route.txt",
    "show ip route static": "show_ip_route.txt",
    "show ip ospf neighbor": "show_ip_ospf_neighbor.txt",
    "show ip bgp summary": "show_ip_bgp_summary.txt",
    "show spanning-tree": "show_spanning-tree.txt",
    "show ip access-lists": "show_ip_access-lists.txt",
}
_CONFIG_KEYWORDS = (
    "vlan",
    "no",
    "interface",
    "name",
    "description",
    "switchport",
    "shutdown",
    "speed",
    "duplex",
    "spanning-tree",
    "ip",
    "mac",
    "access-list",
    "permit",
    "deny",
    "remark",
    "router",
    "network",
    "neighbor",
    "passive-interface",
    "address-family",
    "exit-address-family",
    "bgp",
    "version",
    "vrrp",
    "route-map",
    "match",
    "set",
    "aaa",
    "radius",
    "radius-server",
    "dot1x",
    "authentication",
    "mab",
    "server",
    "key",
    "address",
    "timeout",
    "retransmit",
    "client",
    "channel-group",
    "line",
    "access-class",
    "transport",
    "username",
    "hostname",
    "snmp-server",
    "ntp",
    "service",
    "enable",
    "default",
    "service-acl",
    "negotiation",
    "management",
    "reboot-schedule",
)
_SUBMODES = {
    "interface": "config-if",
    "vlan": "config-vlan",
    "ip access-list standard": "config-std-nacl",
    "ip access-list extended": "config-ext-nacl",
    "router": "config-router",
    "line": "config-line",
    "radius server": "config-radius-server",
    "aaa group server radius": "config-sg-radius",
    "aaa server radius dynamic-author": "config-locsvr-da-radius",
    "route-map": "config-route-map",
}


_NXOS_COMMANDS = {
    "show version": "show_version.txt",
    "show interface status": "show_interface_status.txt",
    "show interface": "show_interface.txt",
    "show mac address-table": "show_mac_address-table.txt",
    "show ip arp": "show_ip_arp.txt",
    "show ip dhcp snooping binding": "show_ip_dhcp_snooping_binding.txt",
    "show ip route": "show_ip_route.txt",
    "show ip route static": "show_ip_route.txt",
    "show system resources": "show_system_resources.txt",
    "show ip access-lists": "show_ip_access-lists.txt",
}


_SMB_COMMANDS = {
    "show version": "show_version.txt",
    "show system": "show_system.txt",
    "show interfaces status": "show_interfaces_status.txt",
    "show mac address-table": "show_mac_address-table.txt",
    "show arp": "show_arp.txt",
    "show ip route": "show_ip_route.txt",
    "show spanning-tree": "show_spanning-tree.txt",
    "show cpu utilization": "show_cpu_utilization.txt",
    "show access-lists": "show_access-lists.txt",
}


_TPLINK_COMMANDS = {
    "show system-info": "show_system-info.txt",
    "show interface status": "show_interface_status.txt",
    "show mac address-table": "show_mac_address-table.txt",
    "show arp": "show_arp.txt",
    "show ip route": "show_ip_route.txt",
    "show spanning-tree bridge": "show_spanning-tree_bridge.txt",
    "show spanning-tree interface": "show_spanning-tree_interface.txt",
    "show cpu-utilization": "show_cpu-utilization.txt",
    "show memory-utilization": "show_memory-utilization.txt",
}


def load_tplink_outputs() -> dict[str, str]:
    folder = FIXTURES.parent / "tplink" / "t2600g_v3"
    return {c: (folder / n).read_text(encoding="utf-8") for c, n in _TPLINK_COMMANDS.items()}


def load_outputs(generation: str = "c9300_iosxe17") -> dict[str, str]:
    folder = FIXTURES / generation
    commands = (
        _NXOS_COMMANDS
        if generation.startswith("n9k")
        else _SMB_COMMANDS if generation.startswith("cbs") else _FIXTURE_COMMANDS
    )
    return {cmd: (folder / name).read_text(encoding="utf-8") for cmd, name in commands.items()}


@dataclass
class CiscoState:
    hostname: str = "ACC-SW-01"
    enable_secret: str = "Fake-Enable-Secret1!"
    outputs: dict[str, str] = field(default_factory=load_outputs)
    vlans: dict[int, str] = field(default_factory=dict)
    interfaces: dict[str, list[str]] = field(default_factory=dict)
    global_lines: list[str] = field(default_factory=list)
    archive: bool = True
    revert_pending: bool = False
    confirmed: int = 0
    saved: int = 0
    received: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.vlans:
            brief = (FIXTURES / "c9300_iosxe17" / "show_vlan_brief.txt").read_text(encoding="utf-8")
            self.vlans = {v.vlan_id: v.name or "" for v in parse_vlans(brief)}
        if not self.interfaces:
            self.interfaces = {
                "GigabitEthernet1/0/1": [
                    "description PC-101",
                    "switchport access vlan 10",
                    "switchport mode access",
                ],
                "GigabitEthernet1/0/5": [],
                "TenGigabitEthernet1/1/1": [
                    "description UPLINK-CORE",
                    "switchport trunk native vlan 100",
                    "switchport trunk allowed vlan 10,20,100,999",
                    "switchport mode trunk",
                ],
                "Vlan100": ["description MGMT", "ip address 192.0.2.10 255.255.255.0"],
            }
        if not self.global_lines:
            self.global_lines = [
                "enable secret 9 $9$abcdefghijklmn$opqrstuvwxyz0123456789ABCDEFGHIJK",
                "ip route 0.0.0.0 0.0.0.0 192.0.2.1",
                "snmp-server community S3cretRO RO",
            ]

    # --- rendering -----------------------------------------------------------------------
    def render_vlan_brief(self) -> str:
        rows = [
            "",
            "VLAN Name                             Status    Ports",
            "---- -------------------------------- --------- -------------------------------",
        ]
        for vid in sorted(self.vlans):
            ports = [
                n.replace("GigabitEthernet", "Gi")
                for n, body in self.interfaces.items()
                if f"switchport access vlan {vid}" in body
            ]
            name = self.vlans[vid] or f"VLAN{vid:04d}"
            rows.append(f"{vid:<4} {name:<32} active    {', '.join(ports)}")
        return CRLF.join(rows)

    def render_vlan_smb(self) -> str:
        rows = [
            "Created by: S-Static, G-GVRP, R-Radius Assigned VLAN, V-Voice VLAN",
            "",
            "VLAN        Name    Tagged Ports    UnTagged Ports  Created by",
            "-----  -----------  --------------  --------------  ----------",
        ]
        for vid in sorted(self.vlans):
            ports = [
                n.replace("GigabitEthernet", "gi")
                for n, body in self.interfaces.items()
                if f"switchport access vlan {vid}" in body
            ]
            name = (self.vlans[vid] or str(vid))[:11]
            rows.append(f"{vid:<6} {name:<11}  {'':<14}  {','.join(ports):<14}  S")
        return CRLF.join(rows)

    def render_interface(self, name: str) -> str:
        body = self.interfaces.get(name, [])
        lines = [
            "Building configuration...",
            "",
            "Current configuration : 120 bytes",
            "!",
            f"interface {name}",
            *[f" {line}" for line in body],
            "end",
        ]
        return CRLF.join(lines)

    def render_running(self) -> str:
        lines = [
            "Building configuration...",
            "",
            "Current configuration : 2048 bytes",
            "!",
            "! Last configuration change at 10:44:12 UTC Mon Nov 24 2026",
            "!",
            "version 17.9",
            f"hostname {self.hostname}",
            "!",
        ]
        lines += [g for g in self.global_lines if not g.startswith("ip route")]
        for vid in sorted(self.vlans):
            if vid == 1:
                continue
            lines += [
                f"vlan {vid}",
                *([f" name {self.vlans[vid]}"] if self.vlans[vid] else []),
                "!",
            ]
        for name, body in sorted(self.interfaces.items()):
            lines += [f"interface {name}", *[f" {b}" for b in body], "!"]
        lines += [g for g in self.global_lines if g.startswith("ip route")]
        lines += ["!", "end"]
        return CRLF.join(lines)


@dataclass
class CiscoIosEmulator:
    state: CiscoState
    mode: str = "user"
    nxos: bool = False
    smb: bool = False
    tplink: bool = False
    pending_save: bool = False
    stack: list[tuple[str, str | None]] = field(default_factory=list)  # (mode, context)
    awaiting_enable: bool = False
    view: str = "user"  # consumed by the mock server ('quit' handling)

    def __post_init__(self) -> None:
        if self.nxos or self.smb or self.tplink:
            self.mode = "priv"  # NX-OS / SMB level-15 logins land in privileged exec

    def banner(self) -> str:
        return CRLF

    def prompt(self) -> str:
        if self.awaiting_enable:
            return ""
        host = self.state.hostname
        if self.mode == "user":
            return f"{host}>"
        if self.mode == "priv":
            return f"{host}#"
        return f"{host}({self.mode})#"

    def handle(self, line: str) -> str:
        cmd = " ".join(line.split())
        if self.awaiting_enable:
            self.awaiting_enable = False
            if cmd == self.state.enable_secret:
                self.mode = "priv"
                return ""
            return CRLF + "% Access denied"
        if self.pending_save:
            self.pending_save = False
            if cmd.upper().startswith("Y"):
                self.state.saved += 1
                return "Copy succeeded"
            return ""
        if not cmd:
            return ""
        self.state.received.append(cmd)
        if cmd.startswith("terminal "):
            return ""
        if self.mode == "user":
            if cmd == "enable":
                self.awaiting_enable = True
                return "Password: "
            if cmd.startswith("show "):
                return self._show(cmd)
            return INVALID
        if self.mode == "priv":
            return self._exec(cmd)
        return self._config(cmd)

    # --- exec ----------------------------------------------------------------------------
    def _exec(self, cmd: str) -> str:
        if cmd.startswith(("show ", "sh ")):
            return self._show(cmd)
        if self.tplink and cmd == "configure":
            self.mode = "config"
            return ""
        if self.tplink and cmd == "copy running-config startup-config":
            self.state.saved += 1
            return "Saving user config OK!"
        if cmd == "configure terminal":
            self.mode = "config"
            return "Enter configuration commands, one per line.  End with CNTL/Z."
        if cmd.startswith("configure terminal revert timer "):
            if not self.state.archive:
                return CRLF + "% Rollback Confirmed Change: Archive feature not configured"
            self.state.revert_pending = True
            self.mode = "config"
            return "Rollback Confirmed Change: Rollback will begin after the timer expires"
        if cmd == "configure confirm":
            self.state.revert_pending = False
            self.state.confirmed += 1
            return ""
        if self.smb and cmd in {"write memory", "write", "copy running-config startup-config"}:
            self.pending_save = True
            return "Overwrite file [startup-config] ?[Yes/press any key for no]...."
        if cmd in {"write memory", "write", "copy running-config startup-config"}:
            self.state.saved += 1
            return "Building configuration..." + CRLF + "[OK]"
        if cmd in {"exit", "logout"}:
            return ""
        if cmd.startswith(("ping", "reload")):
            return "Type escape sequence to abort." if cmd.startswith("ping") else INVALID
        return INVALID

    def _show(self, cmd: str) -> str:
        if self.tplink and cmd == "show vlan":
            rows = [
                "VLAN  Name                 Status    Ports",
                "----- -------------------- --------- -------------",
            ]
            for vid in sorted(self.state.vlans):
                rows.append(f"{vid:<5} {(self.state.vlans[vid] or f'VLAN{vid:04d}'):<20} active")
            return CRLF.join(rows)
        if self.tplink and cmd.startswith("show running-config interface "):
            try:
                name = normalize_interface(cmd.removeprefix("show running-config interface "))
            except InvalidInterfaceName:
                return INVALID
            body = self.state.interfaces.get(name, [])
            return CRLF.join(["#", f"interface {name}", *[f"  {b}" for b in body], "#"])
        if self.smb and cmd == "show vlan":
            return self.state.render_vlan_smb()
        if self.smb and cmd.startswith("show running-config interface "):
            try:
                name = normalize_interface(cmd.removeprefix("show running-config interface "))
            except InvalidInterfaceName:
                return INVALID
            body = self.state.interfaces.get(name, [])
            return CRLF.join([f"interface {name}", *[f" {b}" for b in body], "exit"])
        if not cmd.startswith("show running-config") and not (self.nxos or self.smb or self.tplink):
            cmd = normalize_ios(cmd).replace("show interface ", "show interfaces ")
            cmd = "show interfaces" if cmd == "show interface" else cmd
        if cmd == "show vlan brief":
            return self.state.render_vlan_brief()
        if cmd.startswith("show running-config interface "):
            try:
                name = normalize_interface(cmd.removeprefix("show running-config interface "))
            except InvalidInterfaceName:
                return INVALID
            return self.state.render_interface(name)
        if cmd == "show running-config":
            return self.state.render_running()
        if cmd.startswith("show running-config | include"):
            needle = cmd.split("include", 1)[1].strip().lstrip("^")
            return CRLF.join(
                line for line in self.state.render_running().split(CRLF) if line.startswith(needle)
            )
        if cmd == "show startup-config":
            return self.state.render_running()
        if cmd in self.state.outputs:
            return self.state.outputs[cmd]
        if cmd.startswith(("show processes cpu", "show processes memory")):
            return "CPU utilization for five seconds: 7%/1%; one minute: 6%; five minutes: 5%"
        return INVALID

    # --- configuration -------------------------------------------------------------------
    def _config(self, cmd: str) -> str:
        if cmd == "end":
            self.mode, self.stack = "priv", []
            return ""
        if cmd == "exit":
            if self.stack:
                self.mode, _ = self.stack.pop()
            else:
                self.mode = "priv"
            return ""
        if cmd.split(" ", 1)[0] not in _CONFIG_KEYWORDS:
            return INVALID
        context = self._context()
        if self.mode == "config":
            return self._global(cmd)
        if (
            self.mode == "config-if"
            and context
            and context.startswith("Vlan")
            and cmd.startswith("name ")
        ):
            self.state.vlans[int(context[4:])] = cmd.removeprefix("name ")
            return ""
        if self.mode == "config-if" and context is not None:
            self._apply_body(self.state.interfaces.setdefault(context, []), cmd)
            return ""
        if self.mode == "config-vlan" and context is None:  # SMB 'vlan database'
            spec = cmd.removeprefix("no vlan ").removeprefix("vlan ")
            for vid in parse_vlan_list(spec, strict=False):
                if cmd.startswith("no "):
                    self.state.vlans.pop(vid, None)
                else:
                    self.state.vlans.setdefault(vid, "")
            return ""
        if self.mode == "config-vlan" and context is not None:
            if cmd.startswith("name "):
                self.state.vlans[int(context)] = cmd.removeprefix("name ")
            return ""
        if cmd.startswith(("interface ", "vlan ")) and self.mode.startswith("config-"):
            # IOS lets you jump straight from one sub-mode into another.
            self.mode, self.stack = "config", []
            return self._global(cmd)
        return ""

    def _context(self) -> str | None:
        return self.stack[-1][1] if self.stack else None

    def _enter(self, mode: str, context: str | None) -> None:
        """Every sub-mode is entered from global configuration; 'exit' returns there."""
        self.stack = [("config", context)]
        self.mode = mode

    def _global(self, cmd: str) -> str:
        if cmd.startswith("interface "):
            try:
                name = normalize_interface(cmd.removeprefix("interface "))
            except InvalidInterfaceName:
                return INVALID
            self.state.interfaces.setdefault(name, [])
            self._enter("config-if", name)
            return ""
        if cmd == "vlan database":
            self._enter("config-vlan", None)
            return ""
        if cmd.startswith("vlan "):
            spec = cmd.removeprefix("vlan ")
            ids = parse_vlan_list(spec, strict=False)
            if not ids:
                return INVALID
            for vid in ids:
                self.state.vlans.setdefault(vid, "")
            self._enter("config-vlan", str(ids[-1]))
            return ""
        if cmd.startswith("no vlan "):
            for vid in parse_vlan_list(cmd.removeprefix("no vlan "), strict=False):
                self.state.vlans.pop(vid, None)
            return ""
        for prefix, mode in _SUBMODES.items():
            if cmd.startswith(prefix):
                self._enter(mode, cmd)
                return ""
        if cmd.startswith("no "):
            target = cmd.removeprefix("no ")
            self.state.global_lines = [g for g in self.state.global_lines if g != target]
        else:
            self.state.global_lines.append(cmd)
        return ""

    @staticmethod
    def _apply_body(body: list[str], cmd: str) -> None:
        if cmd.startswith("no "):
            target = cmd.removeprefix("no ")
            body[:] = [b for b in body if not b.startswith(target)]
            return
        if cmd.startswith("switchport trunk allowed vlan add "):
            return  # summarised: keep the existing allowed line
        key = (
            " ".join(cmd.split()[:3]) if cmd.startswith("switchport") else cmd.split(maxsplit=1)[0]
        )
        body[:] = [b for b in body if not b.startswith(key)]
        body.append(cmd)
