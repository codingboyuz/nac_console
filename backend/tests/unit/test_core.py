"""Unit tests: settings fail-fast, redaction, rate limit parsing."""

from __future__ import annotations

import pytest
from fakeredis import FakeAsyncRedis
from pydantic import SecretStr, ValidationError

from app.core.config import Environment, Settings
from app.core.exceptions import RateLimited
from app.core.rate_limit import RateLimit, RateLimiter
from app.core.redaction import REDACTED, redact, redact_text


def test_prod_refuses_insecure_defaults() -> None:
    with pytest.raises(ValidationError) as err:
        Settings(_env_file=None, environment=Environment.PROD)  # type: ignore[call-arg]
    msg = str(err.value)
    assert "jwt_secret" in msg
    assert "credential_keys" in msg


def test_dev_allows_defaults_and_parses_csv() -> None:
    s = Settings(  # type: ignore[call-arg]
        _env_file=None, cors_origins="http://a, http://b", discovery_allowed_subnets="10.0.0.0/8"
    )
    assert s.cors_origins == ["http://a", "http://b"]
    assert s.discovery_allowed_subnets == ["10.0.0.0/8"]


def test_invalid_subnet_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, discovery_allowed_subnets="not-a-net")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("raw", "leak"),
    [
        ("local-user admin password irreversible-cipher $1a$abcdEFGH$", "$1a$abcdEFGH$"),
        ("radius-server shared-key cipher %^%#Secret123%^%#", "%^%#Secret123%^%#"),
        ("snmp-agent community read Pub1icStr", "Pub1icStr"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("url?password=hunter2&x=1", "hunter2"),
        ("token eyJhbGciOiJI.eyJzdWIiOiIxIn0.sig", "eyJhbGciOiJI.eyJzdWIiOiIxIn0.sig"),
        (
            "snmp-agent usm-user v3 u1 authentication-mode sha AuthKey1 privacy-mode aes128 PrivK",
            "AuthKey1",
        ),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "MIIabc"),
    ],
)
def test_redact_text_masks_secrets(raw: str, leak: str) -> None:
    out = redact_text(raw)
    assert leak not in out
    assert REDACTED in out


def test_redact_mapping_by_key_and_secretstr() -> None:
    data = {
        "username": "admin",
        "password": "p@ss",
        "nested": {"snmp_community": "public", "list": [{"api_key": "k"}]},
        "s": SecretStr("hidden"),
    }
    out = redact(data)
    assert out["username"] == "admin"
    assert out["password"] == REDACTED
    assert out["nested"]["snmp_community"] == REDACTED
    assert out["nested"]["list"][0]["api_key"] == REDACTED
    assert out["s"] == REDACTED


def test_rate_limit_parse() -> None:
    assert RateLimit.parse("10/minute") == RateLimit(10, 60)
    assert RateLimit.parse("5/seconds") == RateLimit(5, 1)
    with pytest.raises(ValueError, match="invalid"):
        RateLimit.parse("ten/minute")


async def test_rate_limiter_blocks_after_budget() -> None:
    limiter = RateLimiter(FakeAsyncRedis(decode_responses=True))
    rule = RateLimit(2, 60)
    await limiter.hit("auth", "1.2.3.4", rule)
    await limiter.hit("auth", "1.2.3.4", rule)
    with pytest.raises(RateLimited) as err:
        await limiter.hit("auth", "1.2.3.4", rule)
    assert err.value.retry_after >= 1
    await limiter.hit("auth", "5.6.7.8", rule)
