"""Stateful Huawei VRP CLI emulator used by the SSH/Telnet mock servers.

Tracks the CLI view, a VLAN table (rendered for 'display vlan'), per-interface configuration
(rendered for 'display current-configuration interface X'), S-series 'save' and
'schedule reboot' confirmations, and CE two-stage candidate/commit semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.vlans import parse_vlan_list
from app.network.parsers import parse_vlans

CRLF = "\r\n"
UNRECOGNIZED = CRLF + "          ^" + CRLF + "Error: Unrecognized command found at '^' position."
WRONG_PARAMETER = CRLF + "          ^" + CRLF + "Error: Wrong parameter found at '^' position."
_CONFIRM = {
    "save": "Warning: The current configuration will be written to the device. Continue? [Y/N]:",
    "schedule": "Warning: The device will reboot at the scheduled time. Continue? [Y/N]:",
    "unschedule": "Warning: This will cancel the scheduled reboot. Continue? [Y/N]:",
}


_NESTED_VIEWS = (
    "radius-server template",
    "aaa",
    "authentication-scheme",
    "accounting-scheme",
    "domain ",
    "dot1x-access-profile name",
    "mac-access-profile name",
    "authentication-profile name",
    "ospf",
    "bgp",
    "rip",
    "user-interface",
    "route-policy",
    "area ",
    "ipv4-family",
)
_PROFILE_REFS = ("dot1x-access-profile ", "mac-access-profile ")


@dataclass
class HuaweiCliEmulator:
    hostname: str = "SW-01"
    family: str = "S"  # "S" or "CE"
    outputs: dict[str, str] = field(default_factory=dict)
    reject: set[str] = field(default_factory=set)
    received: list[str] = field(default_factory=list)
    running_config: list[str] = field(default_factory=list)
    candidate: list[tuple[str, str]] = field(default_factory=list)
    vlans: dict[int, str] = field(default_factory=dict)
    interfaces: dict[str, list[str]] = field(default_factory=dict)
    acls: dict[str, dict[int, str]] = field(default_factory=dict)
    vty_acl: str | None = None
    extra_config: list[str] = field(default_factory=list)
    saved: bool = False
    commits: int = 0
    reboot_scheduled: bool = False
    view: str = "user"
    pending_confirm: str | None = None
    parents: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.vlans and "display vlan" in self.outputs:
            parsed = parse_vlans(self.outputs["display vlan"])
            self.vlans = {v.vlan_id: v.name or "" for v in parsed}

    def prompt(self) -> str:
        if self.view == "user":
            return f"<{self.hostname}>"
        mark = ("*" if self.candidate else "~") if self.family == "CE" else ""
        suffix = "" if self.view == "system" else f"-{self.view}"
        return f"[{mark}{self.hostname}{suffix}]"

    def banner(self) -> str:
        info = (
            "Info: The max number of VTY users is 5, the number of current VTY users online is 1."
        )
        return CRLF + info + CRLF

    def render_vlans(self) -> str:
        rows = [f"{vid:<5}common" for vid in sorted(self.vlans)]
        desc = [
            f"{vid:<5}enable  default       enable  disable    {self.vlans[vid] or f'VLAN {vid:04d}'}"
            for vid in sorted(self.vlans)
        ]
        lines = [
            f"The total number of vlans is : {len(self.vlans)}",
            "-" * 80,
            "U: Up;         D: Down;         TG: Tagged;         UT: Untagged;",
            "-" * 80,
            "",
            "VID  Type    Ports",
            "-" * 80,
            *rows,
            "",
            "VID  Status  Property      MAC-LRN Statistics Description",
            "-" * 80,
            *desc,
        ]
        return "\n".join(lines)

    def handle(self, line: str) -> str:
        """Process one input line and return the text printed before the next prompt."""
        cmd = line.strip()
        if not cmd:
            return ""
        self.received.append(cmd)
        if self.pending_confirm is not None:
            return self._confirmed(cmd)
        if cmd in self.reject:
            return WRONG_PARAMETER
        if cmd in {"screen-length 0 temporary", "undo screen-length temporary"}:
            info = (
                "Info: The configuration takes effect on the current user terminal interface only."
            )
            return CRLF + info
        if cmd.startswith(("display", "dis ")):
            return self._display(" ".join(cmd.split()))
        return self._config(cmd)

    def _confirmed(self, cmd: str) -> str:
        pending, self.pending_confirm = self.pending_confirm, None
        if cmd.lower() != "y":
            return ""
        if pending == "save":
            self.saved = True
            return CRLF + "Info: Save the configuration successfully."
        self.reboot_scheduled = pending == "schedule"
        return CRLF + "Info: Succeeded in setting the schedule."

    def render_running(self) -> str:
        lines = ["!Software Version V200R019C10SPC500", "#", f"sysname {self.hostname}"]
        if self.vlans:
            lines += ["#", "vlan batch " + " ".join(str(v) for v in sorted(self.vlans))]
        lines += self.extra_config
        for name, body in sorted(self.interfaces.items()):
            lines += ["#", f"interface {name}", *[f" {b}" for b in body]]
        for ident, rules in sorted(self.acls.items()):
            lines += ["#", f"acl number {ident}", *[f" {r}" for _, r in sorted(rules.items())]]
        lines += ["#", *[c for c in self.running_config if c.startswith("ip route-static")]]
        lines += ["#", "user-interface vty 0 4"]
        if self.vty_acl:
            lines.append(f" acl {self.vty_acl} inbound")
        return "\n".join([*lines, "#", "return"])

    def render_acls(self, which: str) -> str:
        blocks = []
        for ident, rules in sorted(self.acls.items()):
            if which not in {"all", ident}:
                continue
            kind = "Basic" if ident.startswith("2") else "Advanced"
            blocks += [f"{kind} ACL {ident}, {len(rules)} rules", "Acl's step is 5"]
            blocks += [f" {text} (0 matches)" for _, text in sorted(rules.items())]
        return "\n".join(blocks)

    def _display(self, cmd: str) -> str:
        text: str | None
        if "|" in cmd:
            base, _, flt = cmd.partition("|")
            body = self._display(base.strip())
            needle = flt.strip().removeprefix("include").strip()
            return CRLF.join(line for line in body.split(CRLF) if needle in line)
        if cmd == "display current-configuration":
            text = self.render_running()
        elif cmd.startswith("display acl "):
            text = self.render_acls(cmd.removeprefix("display acl ").strip())
        elif cmd == "display vlan":
            text = self.render_vlans()
        elif cmd.startswith("display current-configuration interface "):
            name = cmd.rsplit(" ", 1)[1]
            body = [f" {b}" for b in self.interfaces.get(name, [])]
            text = "\n".join(["#", f"interface {name}", *body, "#", "return"])
        else:
            text = self.outputs.get(cmd)
        if text is None:
            return UNRECOGNIZED
        return CRLF + text.replace("\n", CRLF)

    def _user_view(self, cmd: str) -> str:
        key = {"save": "save", "undo schedule reboot": "unschedule"}.get(cmd)
        if key is None and cmd.startswith("schedule reboot delay"):
            key = "schedule"
        if key is None:
            return UNRECOGNIZED
        self.pending_confirm = key
        return CRLF + _CONFIRM[key]

    def _config(self, cmd: str) -> str:
        if cmd == "system-view":
            self.view = "system"
            return CRLF + "Enter system view, return user view with return command."
        if cmd == "return":
            self.view = "user"
            self.parents.clear()
            return ""
        if cmd == "quit":
            if self.parents:
                self.view = self.parents.pop()
            else:
                self.view = "system" if self.view not in {"system", "user"} else "user"
            return ""
        if self.view == "user":
            return self._user_view(cmd)
        if self.family == "CE" and cmd.startswith("commit"):
            for queued_view, queued in self.candidate:
                self._apply(queued_view, queued)
            self.candidate.clear()
            self.commits += 1
            return ""
        if self.family == "CE" and cmd == "abort":
            self.candidate.clear()
            self.view = "user"
            return ""
        view = self.view
        if cmd.startswith("interface "):
            self.view = cmd.split(" ", 1)[1].replace(" ", "")
        elif cmd.startswith("vlan ") and not cmd.startswith("vlan batch"):
            self.view = "vlan" + cmd.split(" ", 1)[1]
        elif cmd.startswith("acl "):
            self.view = "acl-" + cmd.split()[2]
        elif cmd.startswith(_NESTED_VIEWS) and not (
            view.startswith("auth") and cmd.startswith(_PROFILE_REFS)
        ):
            # Generic sub-view (aaa, radius template, profiles, routing processes...).
            if view not in {"system", "user"}:
                self.parents.append(view)
            self.view = cmd.replace(" ", "_")[:40]
        if self.family == "CE":
            self.candidate.append((view, cmd))
        else:
            self._apply(view, cmd)
        return ""

    def _apply(self, view: str, cmd: str) -> None:
        self.running_config.append(cmd)
        if cmd.startswith("acl "):
            self.acls.setdefault(cmd.split()[2], {})
            return
        if cmd.startswith("undo acl "):
            self.acls.pop(cmd.split()[3], None)
            return
        if cmd.startswith("undo ip route-static "):
            line = cmd.removeprefix("undo ")
            self.running_config[:] = [c for c in self.running_config if c not in (line, cmd)]
            return
        if view.startswith("acl-"):
            rules = self.acls.setdefault(view.removeprefix("acl-"), {})
            if cmd.startswith("undo rule "):
                rules.pop(int(cmd.split()[2]), None)
            elif cmd.startswith("rule "):
                rules[int(cmd.split()[1])] = cmd
            return
        if cmd.startswith("vlan batch "):
            for vid in parse_vlan_list(cmd.removeprefix("vlan batch ")):
                self.vlans.setdefault(vid, "")
        elif cmd.startswith("undo vlan batch "):
            for vid in parse_vlan_list(cmd.removeprefix("undo vlan batch ")):
                self.vlans.pop(vid, None)
        elif cmd.startswith("undo vlan "):
            self.vlans.pop(int(cmd.rsplit(" ", 1)[1]), None)
        elif cmd.startswith("vlan ") and cmd.split(" ", 1)[1].isdigit():
            self.vlans.setdefault(int(cmd.split(" ", 1)[1]), "")
        elif cmd.startswith("name ") and view.startswith("vlan"):
            self.vlans[int(view.removeprefix("vlan"))] = cmd.split(" ", 1)[1]
        elif view not in {"system", "user"} and not view.startswith("vlan"):
            if cmd.startswith("interface "):
                return
            body = self.interfaces.setdefault(view, [])
            if cmd.startswith("undo "):
                prefix = cmd.removeprefix("undo ").split(" vlan")[0]
                body[:] = [b for b in body if not b.startswith(prefix)]
            else:
                body.append(cmd)
