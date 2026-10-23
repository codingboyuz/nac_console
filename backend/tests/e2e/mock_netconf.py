"""Minimal NETCONF 1.0 server (asyncssh 'netconf' subsystem) emulating a Huawei CE device.

Supports hello, get, get-config, edit-config (merge/delete on huawei-vlan), validate, lock,
unlock, commit (incl. confirmed + confirming), cancel-commit, discard-changes, close-session.
Datastores hold a VLAN dict so tests can verify candidate vs running semantics and rollback.
"""

from __future__ import annotations

import asyncio
import copy
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

import asyncssh

EOM = "]]>]]>"
NC = "urn:ietf:params:xml:ns:netconf:base:1.0"
VLAN_NS = "urn:huawei:yang:huawei-vlan"
DEFAULT_CAPS = [
    "urn:ietf:params:netconf:base:1.0",
    "urn:ietf:params:netconf:capability:candidate:1.0",
    "urn:ietf:params:netconf:capability:confirmed-commit:1.0",
    "urn:ietf:params:netconf:capability:validate:1.0",
    "urn:ietf:params:netconf:capability:rollback-on-error:1.0",
    f"{VLAN_NS}?module=huawei-vlan&revision=2019-04-24",
    "urn:huawei:yang:huawei-ifm?module=huawei-ifm&revision=2019-04-10",
]


@dataclass
class NetconfState:
    running: dict[int, str] = field(default_factory=lambda: {1: "default"})
    candidate: dict[int, str] = field(default_factory=lambda: {1: "default"})
    pending_confirm: dict[int, str] | None = None
    operations: list[str] = field(default_factory=list)
    fail_validate: bool = False
    capabilities: list[str] = field(default_factory=lambda: list(DEFAULT_CAPS))


def _vlans_xml(vlans: dict[int, str]) -> str:
    rows = "".join(f"<vlan><id>{v}</id><name>{n}</name></vlan>" for v, n in sorted(vlans.items()))
    return f'<vlan xmlns="{VLAN_NS}"><vlans>{rows}</vlans></vlan>'


def _reply(message_id: str, body: str = "<ok/>") -> str:
    return f'<rpc-reply xmlns="{NC}" message-id="{message_id}">{body}</rpc-reply>{EOM}'


def _error(message_id: str, text: str) -> str:
    return _reply(
        message_id,
        "<rpc-error><error-type>application</error-type><error-tag>operation-failed</error-tag>"
        f"<error-severity>error</error-severity><error-message>{text}</error-message></rpc-error>",
    )


class NetconfHandler:
    def __init__(self, state: NetconfState) -> None:
        self.state = state

    def handle(self, xml_text: str) -> str | None:
        root = ET.fromstring(xml_text)  # noqa: S314 - trusted test input
        if root.tag.endswith("hello"):
            return None
        mid = root.attrib.get("message-id", "0")
        op = next(iter(root))
        name = op.tag.split("}")[-1]
        self.state.operations.append(name)
        st = self.state
        if name in {"lock", "unlock"}:
            return _reply(mid)
        if name == "close-session":
            return _reply(mid)
        if name in {"get", "get-config"}:
            source = op.find(f"{{{NC}}}source")
            store = (
                st.candidate
                if source is not None and source.find(f"{{{NC}}}candidate") is not None
                else st.running
            )
            return _reply(mid, f"<data>{_vlans_xml(store)}</data>")
        if name == "edit-config":
            for vlan in op.iter(f"{{{VLAN_NS}}}vlan"):
                vid = vlan.find(f"{{{VLAN_NS}}}id")
                if vid is None:
                    continue
                vname = vlan.find(f"{{{VLAN_NS}}}name")
                if vlan.attrib.get(f"{{{NC}}}operation") in {"delete", "remove"}:
                    st.candidate.pop(int(vid.text or 0), None)
                else:
                    st.candidate[int(vid.text or 0)] = (
                        vname.text if vname is not None and vname.text else ""
                    )
            return _reply(mid)
        if name == "validate":
            return _error(mid, "validation failed") if st.fail_validate else _reply(mid)
        if name == "commit":
            if op.find(f"{{{NC}}}confirmed") is not None:
                st.pending_confirm = copy.deepcopy(st.running)
            else:
                st.pending_confirm = None
            st.running = copy.deepcopy(st.candidate)
            return _reply(mid)
        if name == "cancel-commit":
            if st.pending_confirm is not None:
                st.running = st.pending_confirm
                st.candidate = copy.deepcopy(st.running)
                st.pending_confirm = None
            return _reply(mid)
        if name == "discard-changes":
            st.candidate = copy.deepcopy(st.running)
            return _reply(mid)
        return _error(mid, f"unsupported operation {name}")


class MockNetconfServer:
    def __init__(self, *, username: str = "netconf", password: str = "Fake-Passw0rd!") -> None:
        self.username = username
        self.password = password
        self.state = NetconfState()
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._server: asyncssh.SSHAcceptor | None = None

    @property
    def public_key_b64(self) -> str:
        return self.host_key.export_public_key("openssh").decode().split()[1]

    async def _session(self, process: asyncssh.SSHServerProcess[str]) -> None:
        caps = "".join(f"<capability>{escape(c)}</capability>" for c in self.state.capabilities)
        process.stdout.write(
            f'<hello xmlns="{NC}"><capabilities>{caps}</capabilities><session-id>1</session-id></hello>{EOM}'
        )
        handler = NetconfHandler(self.state)
        buffer = ""
        try:
            while True:
                chunk = await process.stdin.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while EOM in buffer:
                    message, buffer = buffer.split(EOM, 1)
                    if not message.strip():
                        continue
                    reply = handler.handle(message.strip())
                    if reply:
                        process.stdout.write(reply)
                    if "close-session" in message:
                        process.exit(0)
                        return
        except (asyncssh.BreakReceived, ConnectionError):
            pass
        process.exit(0)

    async def _start(self) -> None:
        server = self

        class _Auth(asyncssh.SSHServer):
            def begin_auth(self, username: str) -> bool:
                return True

            def password_auth_supported(self) -> bool:
                return True

            def validate_password(self, username: str, password: str) -> bool:
                return username == server.username and password == server.password

        self._server = await asyncssh.create_server(
            _Auth,
            "127.0.0.1",
            0,
            server_host_keys=[self.host_key],
            process_factory=self._session,
            encoding="utf-8",
            line_editor=False,
            allow_scp=False,
        )
        self.port = self._server.sockets[0].getsockname()[1]

    def __enter__(self) -> MockNetconfServer:
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._start(), self._loop).result(timeout=10)
        return self

    def __exit__(self, *exc: object) -> None:
        async def stop() -> None:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()

        asyncio.run_coroutine_threadsafe(stop(), self._loop).result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
