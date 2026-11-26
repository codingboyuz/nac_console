"""Vendor platforms (framework-free).

A platform is the unit every vendor-specific behaviour hangs off: CLI dialect, transport driver,
commit model, device-side rollback timer, parsers, builders and command-safety rules.
``DeviceFamily`` stays the Huawei hardware family (S / CE / AR) used by Huawei builders.
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.devices import DeviceFamily


class Vendor(StrEnum):
    HUAWEI = "Huawei"
    CISCO = "Cisco"
    TPLINK = "TP-Link"


class CommitModel(StrEnum):
    """How configuration takes effect and is persisted."""

    IMMEDIATE = "immediate"  # applies line by line; persisted by an explicit save
    TWO_STAGE = "two-stage"  # candidate configuration + commit (persisted by commit)
    API = "api"  # changes go through a controller API, no CLI session


class RollbackTimer(StrEnum):
    """Device-side safety net that survives losing the management path."""

    # Changes roll back unless confirmed: CE 'commit trial <s>' + 'commit',
    # IOS/IOS-XE 'configure terminal revert timer <min>' + 'configure confirm' (needs archive).
    COMMIT_TRIAL = "commit-trial"
    # Reboot into the saved configuration unless cancelled (S/AR 'schedule reboot delay').
    SCHEDULED_RELOAD = "scheduled-reload"
    NONE = "none"  # no device-side timer: lockout overrides are refused


class Platform(StrEnum):
    HUAWEI_VRP = "huawei_vrp"  # S-series switches, AR routers (VRP V5)
    HUAWEI_VRPV8 = "huawei_vrpv8"  # CloudEngine (VRP V8)
    CISCO_IOS = "cisco_ios"  # Catalyst IOS / IOS-XE
    CISCO_NXOS = "cisco_nxos"  # Nexus NX-OS
    CISCO_SMB = "cisco_smb"  # Small Business CBS / SG series
    TPLINK_JETSTREAM = "tplink_jetstream"  # JetStream / Omada managed switches over CLI
    TPLINK_OMADA = "tplink_omada"  # switches managed through an Omada controller API

    @property
    def vendor(self) -> Vendor:
        if self.value.startswith("huawei"):
            return Vendor.HUAWEI
        if self.value.startswith("cisco"):
            return Vendor.CISCO
        return Vendor.TPLINK

    @property
    def is_huawei(self) -> bool:
        return self.vendor is Vendor.HUAWEI

    @property
    def label(self) -> str:
        return _LABELS[self]

    @classmethod
    def for_huawei_family(cls, family: DeviceFamily) -> Platform:
        return cls.HUAWEI_VRPV8 if family is DeviceFamily.CE else cls.HUAWEI_VRP


_LABELS: dict[Platform, str] = {
    Platform.HUAWEI_VRP: "Huawei VRP (S / AR)",
    Platform.HUAWEI_VRPV8: "Huawei VRP V8 (CloudEngine)",
    Platform.CISCO_IOS: "Cisco IOS / IOS-XE (Catalyst)",
    Platform.CISCO_NXOS: "Cisco NX-OS (Nexus)",
    Platform.CISCO_SMB: "Cisco Small Business (CBS / SG)",
    Platform.TPLINK_JETSTREAM: "TP-Link JetStream / Omada (CLI)",
    Platform.TPLINK_OMADA: "TP-Link Omada Controller (API)",
}
