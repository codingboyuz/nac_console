"""Vault crypto (AES-256-GCM, rotation) and interface name normalization."""

from __future__ import annotations

import base64
import os

import pytest
from pydantic import SecretStr

from app.core.crypto import CredentialVault, VaultError, parse_keyring
from app.domain.interfaces import InvalidInterfaceName, normalize_interface, parse_interface


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_encrypt_roundtrip_and_aad_binding() -> None:
    vault = CredentialVault(SecretStr(f"k1:{_key()}"), "k1")
    env = vault.encrypt(b"s3cret", "credential:a")
    assert env.startswith("v1:k1:")
    assert b"s3cret" not in env.encode()
    assert vault.decrypt(env, "credential:a") == b"s3cret"
    with pytest.raises(VaultError):
        vault.decrypt(env, "credential:b")  # swapped row


def test_tamper_detected() -> None:
    vault = CredentialVault(SecretStr(f"k1:{_key()}"), "k1")
    env = vault.encrypt(b"x", "a")
    raw = bytearray(base64.b64decode(env.split(":", 2)[2]))
    raw[-1] ^= 1
    with pytest.raises(VaultError):
        vault.decrypt("v1:k1:" + base64.b64encode(bytes(raw)).decode(), "a")


def test_key_rotation() -> None:
    k1, k2 = _key(), _key()
    old = CredentialVault(SecretStr(f"k1:{k1}"), "k1")
    env = old.encrypt_json({"password": "p"}, "aad")
    new = CredentialVault(SecretStr(f"k1:{k1},k2:{k2}"), "k2")
    assert new.needs_rotation(env)
    rotated = new.rotate(env, "aad")
    assert new.key_id_of(rotated) == "k2"
    assert new.decrypt_json(rotated, "aad") == {"password": "p"}
    only_k2 = CredentialVault(SecretStr(f"k2:{k2}"), "k2")
    assert only_k2.decrypt_json(rotated, "aad") == {"password": "p"}
    with pytest.raises(VaultError, match="unknown key id"):
        only_k2.decrypt(env, "aad")


@pytest.mark.parametrize(
    "spec", ["k1", "k1:notbase64!", f"k1:{base64.b64encode(b'short').decode()}"]
)
def test_bad_keyring(spec: str) -> None:
    with pytest.raises(ValueError, match="credential key"):
        parse_keyring(spec)


def test_active_key_must_exist() -> None:
    with pytest.raises(ValueError, match="active credential key"):
        CredentialVault(SecretStr(f"k1:{_key()}"), "k9")


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("GE0/0/1", "GigabitEthernet0/0/1"),
        ("gigabitethernet 0/0/24", "GigabitEthernet0/0/24"),
        ("XGE0/0/1", "XGigabitEthernet0/0/1"),
        ("10ge1/0/1", "10GE1/0/1"),
        ("25GE1/0/48", "25GE1/0/48"),
        ("100GE1/0/1", "100GE1/0/1"),
        ("eth-trunk1", "Eth-Trunk1"),
        ("Vlanif100", "Vlanif100"),
        ("MEth0/0/1", "MEth0/0/1"),
        ("LoopBack0", "LoopBack0"),
        ("GE1/0/1.100", "GigabitEthernet1/0/1.100"),
    ],
)
def test_normalize_interface(raw: str, canonical: str) -> None:
    assert normalize_interface(raw) == canonical


@pytest.mark.parametrize("bad", ["", "GE", "Foo0/0/1", "Vlanif5000", "GE1", "GE0/0/1; reboot"])
def test_invalid_interface(bad: str) -> None:
    with pytest.raises(InvalidInterfaceName):
        parse_interface(bad)


def test_interface_properties() -> None:
    assert parse_interface("MEth0/0/1").is_management
    assert parse_interface("Eth-Trunk1").is_lag
    assert parse_interface("Vlanif10").vlanif_id == 10
    assert parse_interface("GE0/0/1").is_physical
