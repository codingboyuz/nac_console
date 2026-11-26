"""Secret masking for Cisco / TP-Link configuration and transcripts (Huawei must not regress)."""

from __future__ import annotations

import pytest

from app.core.redaction import REDACTED, redact_text

SECRETS = [
    ("enable secret 9 $9$abcDEF", "$9$abcDEF"),
    ("username admin privilege 15 secret 5 $1$xyz", "$1$xyz"),
    (" password 7 0822455D0A16", "0822455D0A16"),
    ("snmp-server community PUBLIC RO", "PUBLIC"),
    ("radius-server host 10.0.0.5 key 7 1234ABCD", "1234ABCD"),
    ("radius server R1\n address ipv4 10.0.0.5\n key MyKey1", "MyKey1"),
    ("tacacs-server key s3cr3t", "s3cr3t"),
    ('radius-server host 10.1.1.1 key "quoted key"', "quoted key"),
    ("snmp-server user ops network-admin auth sha 0xabc123 priv aes-128 0xdef456", "0xabc123"),
    ("snmp-server user ops network-admin auth sha 0xabc123 priv aes-128 0xdef456", "0xdef456"),
    ("snmp-server user u2 auth md5 abc priv 0x1234", "0x1234"),
    (" neighbor 1.1.1.1 password 0 bgpPass", "bgpPass"),
    ("username admin password 5 $5$hash role network-admin", "$5$hash"),
    ("user name admin privilege admin secret 5 $1$tp", "$1$tp"),
    ("key-string 7 05080F1C2243", "05080F1C2243"),
    (" ip ospf authentication-key 7 0011", "0011"),
    ("enable password plaintext", "plaintext"),
    ("key MySecret1", "MySecret1"),  # generated IOS 'radius server' body line
    ("client 10.0.0.5 server-key S3cret", "S3cret"),
    ("client 10.0.0.5 server-key 7 0822", "0822"),
    ("username netops password encrypted $15$abc privilege 15", "$15$abc"),  # Cisco SMB
    ("radius-server host 1.1.1.1 key MyRadKey1 usage dot1.x", "MyRadKey1"),
    # Huawei forms keep working.
    ("local-user admin password irreversible-cipher $1a$X", "$1a$X"),
    ("radius-server shared-key cipher %^%#abc", "%^%#abc"),
    ("snmp-agent usm-user v3 u1 authentication-mode sha cipher XX", "XX"),
]


@pytest.mark.parametrize(("text", "secret"), SECRETS)
def test_secret_is_masked(text: str, secret: str) -> None:
    masked = redact_text(text)
    assert secret not in masked
    assert REDACTED in masked


def test_type_digit_stays_visible() -> None:
    assert redact_text(" password 7 0822455D0A16") == f" password 7 {REDACTED}"
    assert redact_text("enable secret 9 $9$x") == f"enable secret 9 {REDACTED}"


@pytest.mark.parametrize(
    "text",
    [
        "crypto key generate rsa modulus 2048",
        "switchport mode trunk",
        "interface GigabitEthernet1/0/1",
        "spanning-tree portfast",
        "key chain KEYS",
    ],
)
def test_ordinary_config_is_untouched(text: str) -> None:
    assert redact_text(text) == text
