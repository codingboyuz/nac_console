"""In-memory Omada Controller speaking the Open API shapes the driver uses (httpx transport)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

OMADAC = "a1b2c3d4e5f6"
SITE_ID = "6512aa00bb11cc22dd33ee44"
SWITCH_MAC = "50-C7-BF-12-34-00"
TOKEN = "AT-0123456789"


def _ok(result: Any = None) -> httpx.Response:
    return httpx.Response(200, json={"errorCode": 0, "msg": "Success.", "result": result})


def _err(code: int, msg: str) -> httpx.Response:
    return httpx.Response(200, json={"errorCode": code, "msg": msg})


@dataclass
class OmadaState:
    client_id: str = "nac-client"
    client_secret: str = "nac-secret"
    switch_ip: str = "10.20.0.11"
    networks: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": "net-1", "name": "Default", "vlan": 1, "purpose": "interface"},
            {"id": "net-10", "name": "Users", "vlan": 10, "purpose": "vlan"},
            {"id": "net-100", "name": "MGMT", "vlan": 100, "purpose": "vlan"},
        ]
    )
    ports: list[dict[str, Any]] = field(default_factory=list)
    calls: list[tuple[str, str, Any]] = field(default_factory=list)
    _next: int = 1000

    def __post_init__(self) -> None:
        if not self.ports:
            for n in range(1, 29):
                uplink = n == 25
                self.ports.append(
                    {
                        "port": n,
                        "name": "UPLINK" if uplink else f"Port{n}",
                        "disable": n == 4,
                        "linkStatus": 1 if n in {1, 3, 25} else 0,
                        "linkSpeed": 3 if n in {1, 25} else (2 if n == 3 else 0),
                        "duplex": 2,
                        "profileOverrideEnable": uplink,
                        "nativeNetworkId": "net-100" if uplink else "net-1",
                        "tagNetworkIds": ["net-10", "net-100"] if uplink else [],
                        "rx": 987654321 if n == 1 else 0,
                        "tx": 1876543210 if n == 1 else 0,
                    }
                )

    def port(self, number: int) -> dict[str, Any]:
        return next(p for p in self.ports if p["port"] == number)

    def vlan(self, network_id: str) -> int | None:
        return next((n["vlan"] for n in self.networks if n["id"] == network_id), None)

    # --- request handling --------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if path == "/openapi/authorize/token":
            if request.url.params.get("grant_type") != "client_credentials":
                return _err(-44106, "unsupported grant type")
            if (body or {}).get("client_secret") != self.client_secret or (body or {}).get(
                "omadacId"
            ) != OMADAC:
                return _err(-44111, "Invalid client credentials")
            return _ok({"accessToken": TOKEN, "tokenType": "bearer", "expiresIn": 7200})
        if request.headers.get("Authorization") != f"AccessToken={TOKEN}":
            return _err(-44112, "The access token has expired")
        prefix = f"/openapi/v1/{OMADAC}/sites"
        if path == prefix:
            return _ok({"totalRows": 1, "data": [{"siteId": SITE_ID, "name": "HQ"}]})
        site = f"{prefix}/{SITE_ID}"
        if not path.startswith(site):
            return _err(-7131, "Controller ID not exist")
        rest = path[len(site) :]
        if rest == "/devices":
            return _ok(
                {
                    "totalRows": 2,
                    "data": [
                        {"mac": "AA-BB-CC-00-00-01", "ip": "10.20.0.2", "type": "ap"},
                        {
                            "mac": SWITCH_MAC,
                            "ip": self.switch_ip,
                            "type": "switch",
                            "name": "ACC-OMADA-01",
                            "model": "SG3428",
                            "firmwareVersion": "2.0.4 Build 20230616 Rel.47261",
                            "uptimeLong": 268066,
                            "cpuUtil": 9,
                            "memUtil": 63,
                        },
                    ],
                }
            )
        switch = f"/switches/{SWITCH_MAC}"
        if rest == switch:
            return _ok({"mac": SWITCH_MAC, "name": "ACC-OMADA-01", "model": "SG3428"})
        if rest == f"{switch}/ports":
            return _ok(self.ports)
        if match := re.fullmatch(rf"{switch}/ports/(\d+)", rest):
            if request.method != "PATCH":
                return _err(-1, "method")
            port = self.port(int(match.group(1)))
            for net in [body.get("nativeNetworkId"), *body.get("tagNetworkIds", [])]:
                if net is not None and self.vlan(net) is None:
                    return _err(-33000, "network not found")
            port.update(body)
            return _ok()
        if rest == "/lan-networks":
            if request.method == "POST":
                if any(n["vlan"] == body["vlan"] for n in self.networks):
                    return _err(-33001, "VLAN already used")
                self._next += 1
                self.networks.append({"id": f"net-{self._next}", **body})
                return _ok({"id": f"net-{self._next}"})
            return _ok({"totalRows": len(self.networks), "data": self.networks})
        if match := re.fullmatch(r"/lan-networks/([\w-]+)", rest):
            net = next((n for n in self.networks if n["id"] == match.group(1)), None)
            if net is None:
                return _err(-33000, "network not found")
            if request.method == "DELETE":
                self.networks.remove(net)
            else:
                net.update(body)
            return _ok()
        if rest == "/clients":
            return _ok(
                {
                    "totalRows": 2,
                    "data": [
                        {
                            "mac": "00-E0-FC-12-34-56",
                            "ip": "10.10.10.21",
                            "vid": 10,
                            "switchMac": SWITCH_MAC,
                            "port": 1,
                        },
                        {
                            "mac": "3C-52-82-AA-01-01",
                            "ip": "10.10.10.99",
                            "vid": 10,
                            "switchMac": "AA-BB-CC-99-99-99",
                            "port": 7,
                        },
                    ],
                }
            )
        return _err(-1, f"no route {rest}")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)
