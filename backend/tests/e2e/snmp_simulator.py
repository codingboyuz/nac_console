"""snmpsim-style SNMPv2c agent on UDP 127.0.0.1 serving a static, mutable MIB view."""

from __future__ import annotations

import bisect
import socket
import threading
from typing import Any

from pyasn1.codec.ber import decoder, encoder
from pysnmp.proto import api

P = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
OidKey = tuple[int, ...]


def _key(oid: str) -> OidKey:
    return tuple(int(x) for x in oid.strip(".").split("."))


def huawei_switch_mib(sample: int = 0) -> dict[str, Any]:
    """A small S-series view: system, 3 interfaces (64-bit counters), entity sensors."""
    base_in = 1_000_000 + sample * 125_000_000  # 1 Gbit/s over 1 s per sample step
    mib: dict[str, Any] = {
        "1.3.6.1.2.1.1.1.0": P.OctetString(
            "Huawei Versatile Routing Platform Software\r\nVRP (R) software, Version 5.170 "
            "(S5735 V200R019C10SPC500)\r\nCopyright (C) 2000-2020 HUAWEI TECH Co., Ltd.\r\nHUAWEI S5735-L48T4X-A1"
        ),
        "1.3.6.1.2.1.1.2.0": P.ObjectIdentifier("1.3.6.1.4.1.2011.2.23.629"),
        "1.3.6.1.2.1.1.3.0": P.TimeTicks(123456 + sample * 100),
        "1.3.6.1.2.1.1.5.0": P.OctetString("SW-SNMP-01"),
    }
    interfaces = {
        6: ("GigabitEthernet0/0/1", 1, 1, 1000),
        7: ("GigabitEthernet0/0/2", 1, 2, 1000),
        30: ("XGigabitEthernet0/0/1", 1, 1, 10000),
    }
    for idx, (name, admin, oper, speed) in interfaces.items():
        mib[f"1.3.6.1.2.1.2.2.1.2.{idx}"] = P.OctetString(name)
        mib[f"1.3.6.1.2.1.2.2.1.7.{idx}"] = P.Integer(admin)
        mib[f"1.3.6.1.2.1.2.2.1.8.{idx}"] = P.Integer(oper)
        mib[f"1.3.6.1.2.1.2.2.1.10.{idx}"] = P.Counter32(base_in % 2**32)
        mib[f"1.3.6.1.2.1.2.2.1.14.{idx}"] = P.Counter32(sample * 3 if idx == 7 else 0)
        mib[f"1.3.6.1.2.1.2.2.1.13.{idx}"] = P.Counter32(0)
        mib[f"1.3.6.1.2.1.2.2.1.16.{idx}"] = P.Counter32(base_in // 2 % 2**32)
        mib[f"1.3.6.1.2.1.2.2.1.19.{idx}"] = P.Counter32(0)
        mib[f"1.3.6.1.2.1.2.2.1.20.{idx}"] = P.Counter32(0)
        mib[f"1.3.6.1.2.1.31.1.1.1.1.{idx}"] = P.OctetString(name.replace("GigabitEthernet", "GE"))
        mib[f"1.3.6.1.2.1.31.1.1.1.6.{idx}"] = P.Counter64(base_in)
        mib[f"1.3.6.1.2.1.31.1.1.1.10.{idx}"] = P.Counter64(base_in // 2)
        mib[f"1.3.6.1.2.1.31.1.1.1.15.{idx}"] = P.Gauge32(speed)
        mib[f"1.3.6.1.2.1.31.1.1.1.18.{idx}"] = P.OctetString("uplink" if idx == 30 else "")
    mib["1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5.67108873"] = P.Integer(23)
    mib["1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5.67108874"] = P.Integer(0)
    mib["1.3.6.1.4.1.2011.5.25.31.1.1.1.1.7.67108873"] = P.Integer(47)
    mib["1.3.6.1.4.1.2011.5.25.31.1.1.1.1.11.67108873"] = P.Integer(41)
    mib["1.3.6.1.4.1.2011.5.25.31.1.1.10.1.7.0.0"] = P.Integer(1)
    mib["1.3.6.1.4.1.2011.5.25.31.1.1.10.1.7.0.1"] = P.Integer(2)
    mib["1.3.6.1.4.1.2011.5.25.31.1.1.18.1.6.0.0"] = P.Integer(1)
    return mib


class SnmpSimulator:
    def __init__(self, mib: dict[str, Any], community: str = "sim-community") -> None:
        self.community = community
        self.requests = 0
        self._lock = threading.Lock()
        self.load(mib)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def load(self, mib: dict[str, Any]) -> None:
        with self._lock:
            self._values = {_key(k): v for k, v in mib.items()}
            self._keys = sorted(self._values)

    def _next(self, key: OidKey) -> tuple[OidKey, Any] | None:
        pos = bisect.bisect_right(self._keys, key)
        if pos >= len(self._keys):
            return None
        nxt = self._keys[pos]
        return nxt, self._values[nxt]

    def _respond(self, data: bytes) -> bytes | None:
        msg, _ = decoder.decode(data, asn1Spec=P.Message())
        if str(P.apiMessage.get_community(msg)) != self.community:
            return None  # wrong community: silently drop like a real agent
        req = P.apiMessage.get_pdu(msg)
        rsp_msg = P.apiMessage.get_response(msg)
        rsp = P.apiMessage.get_pdu(rsp_msg)
        oids = [tuple(o) for o, _ in P.apiPDU.get_varbinds(req)]
        out: list[tuple[Any, Any]] = []
        with self._lock:
            if req.isSameTypeWith(P.GetRequestPDU()):
                for key in oids:
                    out.append((key, self._values.get(key, P.NoSuchInstance(""))))
            elif req.isSameTypeWith(P.GetNextRequestPDU()):
                for key in oids:
                    item = self._next(key)
                    out.append(item if item else (key, P.EndOfMibView("")))
            elif req.isSameTypeWith(P.GetBulkRequestPDU()):
                non_rep = int(P.apiBulkPDU.get_non_repeaters(req))
                max_rep = int(P.apiBulkPDU.get_max_repetitions(req))
                for key in oids[:non_rep]:
                    item = self._next(key)
                    out.append(item if item else (key, P.EndOfMibView("")))
                cursors = list(oids[non_rep:])
                for _ in range(max_rep):
                    for i, key in enumerate(cursors):
                        item = self._next(key)
                        if item is None:
                            out.append((key, P.EndOfMibView("")))
                        else:
                            out.append(item)
                            cursors[i] = item[0]
            else:
                return None
        P.apiPDU.set_varbinds(rsp, [(P.ObjectIdentifier(k), v) for k, v in out])
        return bytes(encoder.encode(rsp_msg))

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                return
            self.requests += 1
            try:
                reply = self._respond(data)
            except Exception:
                continue
            if reply:
                self._sock.sendto(reply, addr)

    def __enter__(self) -> SnmpSimulator:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close()
