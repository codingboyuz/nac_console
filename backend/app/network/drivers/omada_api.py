"""TP-Link Omada Controller Open API driver for Omada-managed switches.

The device row is the switch (mgmt_ip = the switch's own IP); the credential ('omada_api')
holds the controller URL, Omada ID (omadacId), client_id / client_secret and the site name.

Verified against TP-Link documentation (support doc 139218) and community reports:
  * POST /openapi/authorize/token?grant_type=client_credentials
    body {"omadacId", "client_id", "client_secret"} -> result.accessToken (valid 2 hours)
  * header 'Authorization: AccessToken=<token>'
  * GET /openapi/v1/{omadacId}/sites
  * envelope {"errorCode": 0, "msg": ..., "result": ...}, lists as result.data / totalRows

NOT verified (from the Open API reference as recalled; confirm in the controller's Online API
Document and adjust ``PATHS`` / bodies here only): device list, switch detail, port list and
port modify, LAN networks, clients. See docs/HARDWARE_VALIDATION.md.
"""

from __future__ import annotations

import json
import time
from types import TracebackType
from typing import Any, ClassVar, Self

import httpx

from app.core.exceptions import (
    CommandRejected,
    DeviceAuthFailed,
    DeviceError,
    DeviceTimeout,
    DeviceUnreachable,
    UnsupportedCapability,
)
from app.domain.devices import DeviceFamily, DeviceProtocol
from app.domain.mac import normalize_mac
from app.network.platforms.tplink_omada.dsl import PortOp, VlanOp, parse_line
from app.network.platforms.tplink_omada.parsers import network_vlans, port_vlans
from app.network.ports import CommandOutput, ConfigResult, ConnectionParams, DeviceFacts

PATHS = {
    "token": "/openapi/authorize/token",
    "sites": "/openapi/v1/{omadac}/sites",
    "devices": "/openapi/v1/{omadac}/sites/{site}/devices",
    "switch": "/openapi/v1/{omadac}/sites/{site}/switches/{mac}",
    "ports": "/openapi/v1/{omadac}/sites/{site}/switches/{mac}/ports",
    "port": "/openapi/v1/{omadac}/sites/{site}/switches/{mac}/ports/{port}",
    "networks": "/openapi/v1/{omadac}/sites/{site}/lan-networks",
    "network": "/openapi/v1/{omadac}/sites/{site}/lan-networks/{network}",
    "clients": "/openapi/v1/{omadac}/sites/{site}/clients",
}
_PAGE = 1000
QUERIES = "GET switch | GET ports | GET port <n> | GET networks | GET clients | GET config"


class OmadaApiDriver:
    protocol = DeviceProtocol.API
    security_warning: str | None = None
    # Tests inject an httpx.MockTransport here; production uses the default transport.
    transport: ClassVar[httpx.BaseTransport | None] = None

    def __init__(self, params: ConnectionParams) -> None:
        self.params = params
        api = params.api or {}
        if not api.get("controller_url") or not api.get("omadac_id"):
            raise DeviceError("device needs an 'omada_api' credential (controller_url, omadac_id)")
        self._base = str(api["controller_url"]).rstrip("/")
        self._omadac = str(api["omadac_id"])
        self._site_name: str | None = api.get("site")
        self._verify = bool(api.get("verify_tls", True))
        self._client: httpx.Client | None = None
        self._token: str | None = None
        self._site: str | None = None
        self._mac: str | None = None
        self._networks: list[dict[str, Any]] | None = None

    # --- plumbing ----------------------------------------------------------------------------
    def _path(self, key: str, **kw: Any) -> str:
        return PATHS[key].format(omadac=self._omadac, site=self._site, mac=self._mac, **kw)

    def _http(self) -> httpx.Client:
        if self._client is None:
            raise DeviceError("not connected")
        return self._client

    def _call(self, method: str, path: str, *, auth: bool = True, **kwargs: Any) -> Any:
        headers = {"Authorization": f"AccessToken={self._token}"} if auth else {}
        try:
            resp = self._http().request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise DeviceTimeout(f"Omada controller timed out: {self._base}") from exc
        except httpx.TransportError as exc:
            raise DeviceUnreachable(f"Omada controller unreachable: {self._base}") from exc
        if resp.status_code in {401, 403}:
            raise DeviceAuthFailed("Omada controller rejected the API client")
        try:
            body = resp.json()
        except ValueError as exc:
            raise DeviceError(f"Omada controller returned HTTP {resp.status_code}") from exc
        code = body.get("errorCode", 0) if isinstance(body, dict) else -1
        if code != 0:
            message = body.get("msg") if isinstance(body, dict) else None
            error = DeviceAuthFailed if not auth else CommandRejected
            raise error(f"Omada API error {code}: {message or 'unknown'}")
        return body.get("result")

    def _list(self, key: str, **kw: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            result = self._call(
                "GET", self._path(key, **kw), params={"page": page, "pageSize": _PAGE}
            )
            rows = result.get("data") if isinstance(result, dict) else result
            rows = [r for r in rows or [] if isinstance(r, dict)]
            out += rows
            total = result.get("totalRows") if isinstance(result, dict) else None
            if not rows or not isinstance(total, int) or len(out) >= total:
                return out
            page += 1

    # --- session -----------------------------------------------------------------------------
    def connect(self) -> None:
        self._client = httpx.Client(
            base_url=self._base,
            verify=self._verify,
            timeout=float(self.params.timeout),
            transport=self.transport,
        )
        secret = self.params.password.get_secret_value() if self.params.password else ""
        token = self._call(
            "POST",
            PATHS["token"],
            auth=False,
            params={"grant_type": "client_credentials"},
            json={
                "omadacId": self._omadac,
                "client_id": self.params.username,
                "client_secret": secret,
            },
        )
        if not isinstance(token, dict) or not token.get("accessToken"):
            raise DeviceAuthFailed("Omada controller returned no access token")
        self._token = str(token["accessToken"])
        self._site = self._find_site()
        self._mac = self._find_switch()["mac"]

    def _find_site(self) -> str:
        sites = self._list("sites")
        if self._site_name:
            sites = [s for s in sites if s.get("name") == self._site_name]
        if len(sites) != 1:
            raise DeviceError("set omada_site on the credential: site not found or ambiguous")
        return str(sites[0].get("siteId") or sites[0].get("id"))

    def _find_switch(self) -> dict[str, Any]:
        for dev in self._list("devices"):
            if dev.get("ip") == self.params.host and str(dev.get("type", "switch")) == "switch":
                return dev
        raise DeviceError(f"no switch with IP {self.params.host} in the Omada site")

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.disconnect()

    # --- reads -------------------------------------------------------------------------------
    def networks(self) -> list[dict[str, Any]]:
        if self._networks is None:
            self._networks = self._list("networks")
        return self._networks

    def _switch(self) -> dict[str, Any]:
        info = dict(self._find_switch())
        try:
            detail = self._call("GET", self._path("switch"))
        except CommandRejected:
            detail = None
        if isinstance(detail, dict):
            info.update({k: v for k, v in detail.items() if v is not None})
        return info

    def _ports(self) -> list[dict[str, Any]]:
        result = self._call("GET", self._path("ports"))
        rows = result.get("data") if isinstance(result, dict) else result
        return [p for p in rows or [] if isinstance(p, dict)]

    def _port(self, number: int) -> dict[str, Any]:
        for port in self._ports():
            if port.get("port") == number:
                return port
        raise CommandRejected(f"switch has no port {number}")

    def _config(self) -> dict[str, Any]:
        sw = self._switch()
        ports = []
        for port in self._ports():
            native, tagged = port_vlans(port, self.networks())
            ports.append(
                {
                    "port": port.get("port"),
                    "name": port.get("name"),
                    "disable": bool(port.get("disable")),
                    "nativeVlan": native,
                    "taggedVlans": sorted(tagged),
                }
            )
        nets = [
            {"vlan": vid, "name": n.get("name")}
            for n in self.networks()
            if (vid := network_vlans([n]).get(str(n.get("id") or n.get("networkId"))))
        ]
        return {
            "switch": {k: sw.get(k) for k in ("name", "model", "mac", "firmwareVersion")},
            "networks": nets,
            "ports": ports,
        }

    def _clients(self) -> list[dict[str, Any]]:
        mine = normalize_mac(self._mac or "")
        out = []
        for client in self._list("clients"):
            switch = client.get("switchMac")
            if switch and normalize_mac(str(switch)) == mine:
                out.append(client)
        return out

    def run_show(self, command: str) -> CommandOutput:
        started = time.monotonic()
        text = " ".join(command.split())
        data: Any
        if text == "GET switch":
            data = self._switch()
        elif text == "GET ports":
            data = {"ports": self._ports(), "networks": self.networks()}
        elif text.startswith("GET port ") and text[9:].isdigit():
            data = {"port": self._port(int(text[9:])), "networks": self.networks()}
        elif text == "GET networks":
            data = self.networks()
        elif text == "GET clients":
            data = self._clients()
        elif text == "GET config":
            data = self._config()
        else:
            raise UnsupportedCapability(f"Omada switches answer: {QUERIES}")
        elapsed = int((time.monotonic() - started) * 1000)
        return CommandOutput(command, json.dumps(data, sort_keys=True), elapsed)

    # --- writes ------------------------------------------------------------------------------
    def _network_id(self, vlan: int) -> str:
        for net in self.networks():
            ident = net.get("id") or net.get("networkId")
            if network_vlans([net]).get(str(ident)) == vlan:
                return str(ident)
        raise CommandRejected(f"VLAN {vlan} has no LAN network on the controller")

    def _apply_vlan(self, op: VlanOp) -> None:
        if op.action == "create":
            body = {"name": op.name, "purpose": "vlan", "vlan": op.vlan}
            self._call("POST", self._path("networks"), json=body)
        elif op.action == "rename":
            body = {"name": op.name}
            self._call("PATCH", self._path("network", network=self._network_id(op.vlan)), json=body)
        else:
            self._call("DELETE", self._path("network", network=self._network_id(op.vlan)))
        self._networks = None

    def _apply_port(self, op: PortOp) -> None:
        port = self._port(op.port)
        native, tagged = port_vlans(port, self.networks())
        body: dict[str, Any] = {}
        if op.field in {"enable", "disable"}:
            body["disable"] = op.field == "disable"
        elif op.field == "name":
            body["name"] = "" if op.value == "none" else op.value
        else:
            if op.field == "native":
                native = int(op.value or 0)
            else:
                tagged = set(op.tagged())
            body |= {
                "profileOverrideEnable": True,
                "nativeNetworkId": self._network_id(native) if native is not None else None,
                "tagNetworkIds": [self._network_id(v) for v in sorted(tagged)],
            }
        self._call("PATCH", self._path("port", port=op.port), json=body)

    def push_config(
        self, commands: list[str], *, save: bool = False, trial_seconds: int | None = None
    ) -> ConfigResult:
        if trial_seconds:
            raise UnsupportedCapability("the Omada API has no trial commit")
        started = time.monotonic()
        done: list[str] = []
        for line in commands:
            try:
                op = parse_line(line)
            except ValueError as exc:
                raise CommandRejected(str(exc)) from exc
            if isinstance(op, VlanOp):
                self._apply_vlan(op)
            else:
                self._apply_port(op)
            done.append(f"ok: {line}")
        return ConfigResult(
            commands=tuple(commands),
            output="\n".join(done),
            committed=True,
            saved=True,  # the controller persists and provisions every accepted change
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    def save(self) -> str:
        return ""

    # --- facts -------------------------------------------------------------------------------
    def capabilities(self) -> DeviceFacts:
        from app.network.platforms.tplink_omada.parsers import parse_version

        sw = self._switch()
        info = parse_version(json.dumps(sw))
        return DeviceFacts(
            family=DeviceFamily.UNKNOWN,
            vrp_version=info.vrp_version,
            software_version=info.software_version,
            model=info.model,
            hostname=sw.get("name"),
            uptime_seconds=info.uptime_seconds,
            capabilities={
                "api": True,
                "commit_model": "api",
                "rollback_timer": None,
                "controller": self._base,
                "switch_mac": self._mac,
                "transport": "api",
            },
        )

    def health(self) -> bool:
        try:
            self._find_switch()
        except DeviceError:
            return False
        return True
