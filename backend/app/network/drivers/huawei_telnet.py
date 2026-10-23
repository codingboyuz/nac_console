"""Telnet variant of the Huawei CLI driver (Netmiko ``huawei_telnet``).

Telnet is insecure: every result carries ``security_warning``. Paramiko is not involved, so
host-key pinning does not apply; passwords never reach logs because Netmiko session logging is
disabled and the central redaction filter masks the transport logger.
"""

from __future__ import annotations

from typing import Any

from app.domain.devices import TELNET_SECURITY_WARNING, DeviceProtocol
from app.network.drivers.huawei_ssh import HuaweiCliDriver


class HuaweiTelnetDriver(HuaweiCliDriver):
    protocol = DeviceProtocol.TELNET
    security_warning = TELNET_SECURITY_WARNING

    def device_type(self) -> str:
        # Netmiko has no VRP V8 telnet class; CE commit is sent explicitly by _commit().
        return "huawei_telnet"

    def _netmiko_args(self) -> dict[str, Any]:
        args = super()._netmiko_args()
        for key in (
            "use_keys",
            "pkey",
            "allow_agent",
            "system_host_keys",
            "ssh_strict",
            "alt_host_keys",
            "alt_key_file",
            "banner_timeout",
            "auth_timeout",
        ):
            args.pop(key, None)
        return args

    def _write_known_hosts(self) -> str:
        return ""  # not applicable to telnet
