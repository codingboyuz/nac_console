"""Phase 5: NETCONF driver vs mock server, SNMP collector vs simulator, model registry."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from app.core.exceptions import CommandRejected, DeviceError, UnsupportedCapability
from app.domain.devices import CredentialKind, DeviceFamily, DeviceProtocol
from app.network.builders.netconf_vlan import vlan_create_xml, vlan_delete_xml, vlan_filter_xml
from app.network.drivers.huawei_netconf import HuaweiNetconfDriver
from app.network.drivers.huawei_snmp import HuaweiSnmpDriver, family_from_descr
from app.network.hostkeys import fingerprint_sha256
from app.network.ports import ConnectionParams, HostKey
from app.services.credential_service import ResolvedSecret
from tests.e2e.mock_netconf import MockNetconfServer
from tests.e2e.snmp_simulator import SnmpSimulator, huawei_switch_mib

pytestmark = pytest.mark.e2e


@pytest.fixture
def nc_server() -> Iterator[MockNetconfServer]:
    with MockNetconfServer() as server:
        yield server


def _nc_params(server: MockNetconfServer) -> ConnectionParams:
    b64 = server.public_key_b64
    return ConnectionParams(
        device_id="nc-1",
        host="127.0.0.1",
        port=server.port,
        protocol=DeviceProtocol.NETCONF,
        family=DeviceFamily.CE,
        username=server.username,
        password=SecretStr(server.password),
        timeout=10,
        connect_retries=0,
        pinned_host_key=HostKey("ssh-ed25519", b64, fingerprint_sha256(b64)),
    )


def test_capability_discovery(nc_server: MockNetconfServer) -> None:
    with HuaweiNetconfDriver(_nc_params(nc_server)) as drv:
        facts = drv.capabilities()
        with pytest.raises(UnsupportedCapability):
            drv.run_show("display version")
    caps = facts.capabilities["netconf"]
    assert caps["features"]["candidate"] is True
    assert caps["features"]["confirmed_commit"] is True
    assert caps["edit_target"] == "candidate"
    assert caps["feature_modules"]["vlan"] == "huawei-vlan"
    assert caps["feature_modules"]["acl"] is None


def test_apply_edit_candidate_confirmed_commit(nc_server: MockNetconfServer) -> None:
    with HuaweiNetconfDriver(_nc_params(nc_server)) as drv:
        drv.apply_edit(vlan_create_xml({100: "users", 200: "voice"}), filter_xml=vlan_filter_xml())
        running = drv.get_config("running", vlan_filter_xml())
    assert "<id>100</id>" in running
    assert set(nc_server.state.running) == {1, 100, 200}
    ops = nc_server.state.operations
    assert (
        ops.index("lock") < ops.index("edit-config") < ops.index("validate") < ops.index("commit")
    )
    assert ops.count("commit") == 2  # confirmed + confirming
    assert nc_server.state.pending_confirm is None


def test_failed_verification_rolls_back(nc_server: MockNetconfServer) -> None:
    with HuaweiNetconfDriver(_nc_params(nc_server)) as drv:
        with pytest.raises(DeviceError, match="verification"):
            drv.apply_edit(vlan_create_xml({300: "x"}), verify=lambda: False)
    assert 300 not in nc_server.state.running
    assert "cancel-commit" in nc_server.state.operations
    assert "unlock" in nc_server.state.operations


def test_validate_failure_discards_candidate(nc_server: MockNetconfServer) -> None:
    nc_server.state.fail_validate = True
    with HuaweiNetconfDriver(_nc_params(nc_server)) as drv, pytest.raises(CommandRejected):
        drv.apply_edit(vlan_create_xml({400: "y"}))
    assert 400 not in nc_server.state.candidate
    assert "discard-changes" in nc_server.state.operations


def test_delete_vlan_payload(nc_server: MockNetconfServer) -> None:
    nc_server.state.running[50] = nc_server.state.candidate[50] = "old"
    with HuaweiNetconfDriver(_nc_params(nc_server)) as drv:
        drv.apply_edit(vlan_delete_xml([50]))
    assert 50 not in nc_server.state.running


def test_running_only_device_without_writable_running_refused(nc_server: MockNetconfServer) -> None:
    nc_server.state.capabilities = ["urn:ietf:params:netconf:base:1.0"]
    with HuaweiNetconfDriver(_nc_params(nc_server)) as drv:
        with pytest.raises(UnsupportedCapability):
            drv.apply_edit(vlan_create_xml({10: "a"}))


def test_vlan_xml_escapes_names() -> None:
    payload = vlan_create_xml({10: "a<b>&c"})
    assert "a&lt;b&gt;&amp;c" in payload


def _snmp_params(sim: SnmpSimulator, community: str | None = None) -> ConnectionParams:
    secret = ResolvedSecret(
        kind=CredentialKind.SNMP_V2C,
        username=None,
        community=SecretStr(community or sim.community),
    )
    return ConnectionParams(
        device_id="snmp-1",
        host="127.0.0.1",
        port=sim.port,
        protocol=DeviceProtocol.SNMP,
        timeout=2,
        snmp={"kind": "snmp_v2c", "secret": secret},
    )


def test_snmp_collects_system_interfaces_entity() -> None:
    with SnmpSimulator(huawei_switch_mib()) as sim, HuaweiSnmpDriver(_snmp_params(sim)) as drv:
        facts = drv.capabilities()
        ifaces = drv.interfaces()
        entity = drv.entity()
        assert drv.health()
    assert facts.family is DeviceFamily.S
    assert facts.hostname == "SW-SNMP-01"
    assert facts.capabilities["huawei_enterprise"] is True
    by_name = {i.name: i for i in ifaces}
    assert set(by_name) == {"GigabitEthernet0/0/1", "GigabitEthernet0/0/2", "XGigabitEthernet0/0/1"}
    assert by_name["GigabitEthernet0/0/2"].oper_up is False
    assert by_name["XGigabitEthernet0/0/1"].counters.counter_bits == 64
    assert by_name["XGigabitEthernet0/0/1"].speed_mbps == 10000
    assert entity.cpu_percent == 23.0
    assert entity.memory_percent == 47.0
    assert entity.temperatures_c == [41.0]
    assert entity.fans_ok == {"0.0": True, "0.1": False}
    assert entity.power_ok == {"0.0": True}


def test_snmp_wrong_community_times_out() -> None:
    with SnmpSimulator(huawei_switch_mib()) as sim:
        drv = HuaweiSnmpDriver(_snmp_params(sim, community="wrong"))
        assert drv.health() is False


@pytest.mark.parametrize(
    ("descr", "family"),
    [
        ("VRP (R) software, Version 8.180 (CE6881 V200R005C10SPC800)", DeviceFamily.CE),
        ("Huawei AR6120 Router", DeviceFamily.AR),
        ("HUAWEI S5735-L48T4X-A1", DeviceFamily.S),
        ("something else", DeviceFamily.UNKNOWN),
    ],
)
def test_family_from_descr(descr: str, family: DeviceFamily) -> None:
    assert family_from_descr(descr) is family
