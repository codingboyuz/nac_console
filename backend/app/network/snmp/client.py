"""Async SNMP client (pysnmp 7, asyncio). SNMPv3 authPriv is the preferred mode.

Only explicitly configured devices are polled; there is no scanning here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from app.core.exceptions import DeviceError, DeviceTimeout, ValidationFailed

_AUTH = {
    "MD5": "usmHMACMD5AuthProtocol",
    "SHA": "usmHMACSHAAuthProtocol",
    "SHA224": "usmHMAC128SHA224AuthProtocol",
    "SHA256": "usmHMAC192SHA256AuthProtocol",
    "SHA384": "usmHMAC256SHA384AuthProtocol",
    "SHA512": "usmHMAC384SHA512AuthProtocol",
}
_PRIV = {
    "DES": "usmDESPrivProtocol",
    "AES128": "usmAesCfb128Protocol",
    "AES192": "usmAesCfb192Protocol",
    "AES256": "usmAesCfb256Protocol",
}


@dataclass(frozen=True, slots=True)
class SnmpAuth:
    version: str  # "v2c" | "v3"
    community: SecretStr | None = None
    username: str | None = None
    auth_protocol: str | None = None
    auth_key: SecretStr | None = None
    priv_protocol: str | None = None
    priv_key: SecretStr | None = None

    def __repr__(self) -> str:
        return f"SnmpAuth(version={self.version!r}, username={self.username!r})"


@dataclass(frozen=True, slots=True)
class SnmpTarget:
    host: str
    port: int = 161
    timeout: float = 2.0
    retries: int = 1


def _value(obj: Any) -> Any:
    """Convert pysnmp values to Python (int / str / bytes)."""
    cls = type(obj).__name__
    if cls in {"NoSuchObject", "NoSuchInstance", "EndOfMibView", "Null"}:
        return None
    if cls in {
        "Integer",
        "Integer32",
        "Counter32",
        "Counter64",
        "Gauge32",
        "TimeTicks",
        "Unsigned32",
    }:
        return int(obj)
    if cls == "OctetString":
        raw = bytes(obj)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw
    if cls in {"ObjectIdentifier", "ObjectIdentity", "ObjectName"} and hasattr(obj, "asTuple"):
        return ".".join(str(part) for part in obj.asTuple())  # numeric, never MIB-resolved
    return obj.prettyPrint() if hasattr(obj, "prettyPrint") else obj


class SnmpClient:
    def __init__(self, target: SnmpTarget, auth: SnmpAuth) -> None:
        self.target = target
        self.auth = auth
        self._engine: Any = None

    def _auth_data(self) -> Any:
        from pysnmp.hlapi.v3arch import asyncio as hl

        if self.auth.version == "v2c":
            if self.auth.community is None:
                raise ValidationFailed("SNMPv2c requires a community")
            # v2c is supported for legacy gear only; credentials default to SNMPv3 authPriv.
            community = self.auth.community.get_secret_value()
            return hl.CommunityData(community, mpModel=1)  # noqa: S508
        if not self.auth.username or self.auth.auth_key is None:
            raise ValidationFailed("SNMPv3 requires username and auth key")
        auth_proto = getattr(hl, _AUTH.get((self.auth.auth_protocol or "SHA").upper(), ""), None)
        priv_proto = (
            getattr(hl, _PRIV.get((self.auth.priv_protocol or "").upper(), ""), None)
            if self.auth.priv_key
            else hl.usmNoPrivProtocol
        )
        if auth_proto is None or priv_proto is None:
            raise ValidationFailed("unsupported SNMPv3 auth/priv protocol")
        return hl.UsmUserData(
            self.auth.username,
            authKey=self.auth.auth_key.get_secret_value(),
            privKey=self.auth.priv_key.get_secret_value() if self.auth.priv_key else None,
            authProtocol=auth_proto,
            privProtocol=priv_proto,
        )

    async def _transport(self) -> Any:
        from pysnmp.hlapi.v3arch import asyncio as hl

        return await hl.UdpTransportTarget.create(
            (self.target.host, self.target.port),
            timeout=self.target.timeout,
            retries=self.target.retries,
        )

    def _snmp_engine(self) -> Any:
        from pysnmp.hlapi.v3arch import asyncio as hl

        if self._engine is None:
            self._engine = hl.SnmpEngine()
        return self._engine

    @staticmethod
    def _check(error_indication: Any, error_status: Any) -> None:
        if error_indication:
            text = str(error_indication)
            if "timeout" in text.lower() or "no snmp response" in text.lower():
                raise DeviceTimeout("SNMP request timed out")
            raise DeviceError(f"SNMP error: {text}")
        if error_status:
            raise DeviceError(f"SNMP error status: {error_status.prettyPrint()}")

    async def get(self, *oids: str) -> dict[str, Any]:
        from pysnmp.hlapi.v3arch import asyncio as hl

        err_ind, err_status, _idx, binds = await hl.get_cmd(
            self._snmp_engine(),
            self._auth_data(),
            await self._transport(),
            hl.ContextData(),
            *(hl.ObjectType(hl.ObjectIdentity(oid)) for oid in oids),
        )
        self._check(err_ind, err_status)
        return {str(name): _value(val) for name, val in binds}

    async def walk(self, oid: str, *, max_rows: int = 10_000) -> dict[str, Any]:
        """GETBULK walk of one subtree (v2c/v3)."""
        from pysnmp.hlapi.v3arch import asyncio as hl

        rows: dict[str, Any] = {}
        async for err_ind, err_status, _idx, binds in hl.bulk_walk_cmd(
            self._snmp_engine(),
            self._auth_data(),
            await self._transport(),
            hl.ContextData(),
            0,
            25,
            hl.ObjectType(hl.ObjectIdentity(oid)),
            lexicographicMode=False,
        ):
            self._check(err_ind, err_status)
            for name, val in binds:
                rows[str(name)] = _value(val)
            if len(rows) >= max_rows:
                break
        return rows

    def close(self) -> None:
        if self._engine is not None:
            self._engine.close_dispatcher()
            self._engine = None
