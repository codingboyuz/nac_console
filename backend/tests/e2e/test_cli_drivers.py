"""Netmiko SSH/Telnet drivers against the fake Huawei CLI servers (no real device)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from app.core.exceptions import (
    CommandRejected,
    DeviceAuthFailed,
    DeviceUnreachable,
    HostKeyMismatch,
)
from app.domain.devices import DeviceFamily, DeviceProtocol
from app.network.drivers.huawei_ssh import HuaweiCliDriver
from app.network.drivers.huawei_telnet import HuaweiTelnetDriver
from app.network.hostkeys import fetch_host_key, fingerprint_sha256
from app.network.ports import ConnectionParams, HostKey
from tests.e2e.mock_huawei import HuaweiCliEmulator, MockHuaweiSSH, MockHuaweiTelnet, load_outputs

pytestmark = pytest.mark.e2e


def _s_series() -> HuaweiCliEmulator:
    return HuaweiCliEmulator(
        "SW-01", "S", load_outputs("s5735_v200r019"), reject={"port default vlan 4000"}
    )


def _ce() -> HuaweiCliEmulator:
    return HuaweiCliEmulator("CE-01", "CE", load_outputs("ce6881_v200r005"))


@pytest.fixture
def s_server() -> Iterator[MockHuaweiSSH]:
    with MockHuaweiSSH(_s_series) as server:
        yield server


@pytest.fixture
def ce_server() -> Iterator[MockHuaweiSSH]:
    with MockHuaweiSSH(_ce) as server:
        yield server


def _params(server: MockHuaweiSSH, family: DeviceFamily, **kw: object) -> ConnectionParams:
    key = HostKey("ssh-ed25519", server.public_key_b64, fingerprint_sha256(server.public_key_b64))
    base: dict[str, object] = {
        "device_id": "dev-1",
        "host": "127.0.0.1",
        "port": server.port,
        "protocol": DeviceProtocol.SSH,
        "family": family,
        "username": server.username,
        "password": SecretStr(server.password),
        "timeout": 10,
        "connect_retries": 0,
        "pinned_host_key": key,
    }
    base.update(kw)
    return ConnectionParams(**base)  # type: ignore[arg-type]


def test_fetch_host_key(s_server: MockHuaweiSSH) -> None:
    key = fetch_host_key("127.0.0.1", s_server.port, 5)
    assert key.key_type == "ssh-ed25519"
    assert key.public_key_b64 == s_server.public_key_b64
    assert key.fingerprint.startswith("SHA256:")


def test_show_and_capabilities_s_series(s_server: MockHuaweiSSH) -> None:
    with HuaweiCliDriver(_params(s_server, DeviceFamily.S)) as drv:
        out = drv.run_show("display vlan")
        assert "VID" in out.output
        facts = drv.capabilities()
        assert drv.health()
    assert facts.family is DeviceFamily.S
    assert facts.vrp_version == "5.170"
    assert facts.capabilities["commit_model"] == "immediate"
    session = s_server.sessions[-1]
    assert "screen-length 0 temporary" in session.received


def test_push_config_and_save_s_series(s_server: MockHuaweiSSH) -> None:
    with HuaweiCliDriver(_params(s_server, DeviceFamily.S)) as drv:
        result = drv.push_config(
            ["vlan batch 30", "interface GE0/0/3", "port default vlan 30"], save=True
        )
    assert result.saved
    assert not result.committed
    session = s_server.sessions[-1]
    assert "vlan batch 30" in session.running_config
    assert session.saved


def test_device_error_marker_fails_operation(s_server: MockHuaweiSSH) -> None:
    with HuaweiCliDriver(_params(s_server, DeviceFamily.S)) as drv:
        with pytest.raises(CommandRejected, match="Wrong parameter"):
            drv.push_config(["interface GE0/0/3", "port default vlan 4000"])
        with pytest.raises(CommandRejected, match="Unrecognized"):
            drv.run_show("display nonsense-topic")


def test_ce_two_stage_commit(ce_server: MockHuaweiSSH) -> None:
    with HuaweiCliDriver(_params(ce_server, DeviceFamily.CE)) as drv:
        facts = drv.capabilities()
        result = drv.push_config(["vlan batch 40", "interface 10GE1/0/2", "port default vlan 40"])
    assert facts.family is DeviceFamily.CE
    assert facts.vrp_version == "8.180"
    assert result.committed
    session = ce_server.sessions[-1]
    assert session.commits == 1
    assert "vlan batch 40" in session.running_config
    assert session.candidate == []


def test_wrong_password_maps_to_auth_failed(s_server: MockHuaweiSSH) -> None:
    params = _params(s_server, DeviceFamily.S, password=SecretStr("nope"))
    with pytest.raises(DeviceAuthFailed) as err:
        HuaweiCliDriver(params).connect()
    assert "nope" not in str(err.value)


def test_pinned_key_mismatch_refuses_connection(s_server: MockHuaweiSSH) -> None:
    import asyncssh

    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key("openssh").decode()
    b64 = other.split()[1]
    params = _params(
        s_server,
        DeviceFamily.S,
        pinned_host_key=HostKey("ssh-ed25519", b64, fingerprint_sha256(b64)),
    )
    with pytest.raises((HostKeyMismatch, DeviceUnreachable)):
        HuaweiCliDriver(params).connect()


def test_unreachable() -> None:
    params = ConnectionParams(
        device_id="x",
        host="127.0.0.1",
        port=1,
        protocol=DeviceProtocol.SSH,
        username="u",
        password=SecretStr("p"),
        timeout=5,
        connect_retries=0,
        strict_host_key=False,
    )
    with pytest.raises(DeviceUnreachable):
        HuaweiCliDriver(params).connect()


def test_telnet_driver_returns_security_warning() -> None:
    with MockHuaweiTelnet(_s_series) as server:
        params = ConnectionParams(
            device_id="t",
            host="127.0.0.1",
            port=server.port,
            protocol=DeviceProtocol.TELNET,
            family=DeviceFamily.S,
            username=server.username,
            password=SecretStr(server.password),
            timeout=10,
            connect_retries=0,
        )
        with HuaweiTelnetDriver(params) as drv:
            out = drv.run_show("display version")
    assert "VRP" in out.output
    assert out.security_warning is not None
    assert "clear text" in out.security_warning
