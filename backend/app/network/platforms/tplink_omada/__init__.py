"""TP-Link switches managed by an Omada Controller, driven through its Open API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.platforms import CommitModel, Platform, RollbackTimer
from app.network.platforms.base import PlatformAdapter, Q
from app.network.platforms.tplink_omada import builders, config_text, parsers
from app.network.platforms.tplink_omada.dsl import port_number
from app.network.platforms.tplink_omada.rules import OmadaClassifier, OmadaLockoutGuard

if TYPE_CHECKING:
    from app.network.ports import ConnectionParams, DeviceDriver

COMMANDS: dict[Q, str] = {
    Q.VERSION: "GET switch",
    Q.RUNNING_CONFIG: "GET config",
    Q.VLANS: "GET networks",
    Q.INTERFACES_BRIEF: "GET ports",
    Q.INTERFACES: "GET ports",
    Q.INTERFACE: "GET port {name}",
    Q.INTERFACE_CONFIG: "GET port {name}",
    Q.MAC_TABLE: "GET clients",
    Q.ARP: "GET clients",
    Q.CPU: "GET switch",
    Q.MEMORY: "GET switch",
}


def _api(params: ConnectionParams) -> DeviceDriver:
    from app.network.drivers.omada_api import OmadaApiDriver

    return OmadaApiDriver(params)


TPLINK_OMADA = PlatformAdapter(
    platform=Platform.TPLINK_OMADA,
    commit_model=CommitModel.API,
    rollback_timer=RollbackTimer.NONE,
    commands=COMMANDS,
    parsers=parsers,
    l2=builders,
    acl=builders,
    routing=builders,
    nac=builders,
    config=config_text,
    classifier=OmadaClassifier(),
    lockout=OmadaLockoutGuard(),
    drivers={"api": _api},
    routing_probes={},
    notes=(
        "Managed through the Omada Controller Open API: register the switch's own IP and an "
        "'omada_api' credential (controller URL, Omada ID, client id/secret, site).",
        "VLANs are site-wide LAN networks; ports take one native VLAN plus tagged VLANs.",
        "ACLs, routing, 802.1X, STP and DHCP snooping stay in the controller (refused here).",
        "Port/network endpoints follow the Open API reference but are not yet lab-verified.",
    ),
    interface_cli=lambda name: str(port_number(name)),
)
