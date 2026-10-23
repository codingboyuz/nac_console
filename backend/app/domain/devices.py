"""Device-related enums and value objects (framework-free)."""

from __future__ import annotations

from enum import StrEnum


class DeviceProtocol(StrEnum):
    SSH = "ssh"
    TELNET = "telnet"
    NETCONF = "netconf"
    SNMP = "snmp"

    @property
    def insecure(self) -> bool:
        return self is DeviceProtocol.TELNET

    @property
    def default_port(self) -> int:
        return {"ssh": 22, "telnet": 23, "netconf": 830, "snmp": 161}[self.value]


class DeviceFamily(StrEnum):
    """S-series campus switch, CloudEngine, AR router. Detected from 'display version'."""

    S = "S"
    CE = "CE"
    AR = "AR"
    UNKNOWN = "UNKNOWN"

    @property
    def uses_commit(self) -> bool:
        """CE (VRP V8) uses two-stage configuration with 'commit'; S/AR apply immediately."""
        return self is DeviceFamily.CE


class DeviceStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    UNREACHABLE = "UNREACHABLE"
    AUTH_FAILED = "AUTH_FAILED"
    MAINTENANCE = "MAINTENANCE"


class CredentialKind(StrEnum):
    CLI = "cli"  # SSH / Telnet / NETCONF: username + password or private key
    SNMP_V2C = "snmp_v2c"
    SNMP_V3 = "snmp_v3"


class HostKeyStatus(StrEnum):
    TRUSTED = "TRUSTED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


TELNET_SECURITY_WARNING = (
    "Telnet transmits credentials and configuration in clear text. Migrate this device to SSH "
    "(stelnet) or NETCONF over SSH as soon as possible."
)
