"""Application settings loaded from environment variables / .env (pydantic-settings)."""

from __future__ import annotations

import ipaddress
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment. Anything except DEV/TEST enforces production secrets."""

    DEV = "dev"
    TEST = "test"
    STAGING = "staging"
    PROD = "prod"


_DEV_PLACEHOLDER = "dev-only-insecure-change-me"


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    """All runtime configuration. Secrets are SecretStr and never logged."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_name: str = "Huawei NAC Platform"
    environment: Environment = Environment.DEV
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = True

    # --- persistence -----------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://nac:nac@localhost:5432/nac"
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # --- auth --------------------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr(_DEV_PLACEHOLDER)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "huawei-nac"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 7 * 24 * 3600
    login_max_failures: int = 5
    login_lockout_seconds: int = 900
    ws_ticket_ttl_seconds: int = 30
    confirmation_token_ttl_seconds: int = 300

    # --- credential vault (AES-256-GCM, key id versioned) ------------------------------
    # Format: "kid1:<base64 32 bytes>,kid2:<base64 32 bytes>"; the active key encrypts.
    credential_keys: SecretStr = SecretStr("")
    credential_active_key_id: str = "k1"

    # --- http hardening -------------------------------------------------------------------
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    trusted_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    max_request_body_bytes: int = 1_048_576
    rate_limit_default: str = "120/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_execute: str = "20/minute"
    rate_limit_internal: str = "600/minute"

    # --- internal endpoints (RADIUS callbacks, metrics) ---------------------------------
    internal_api_token: SecretStr = SecretStr(_DEV_PLACEHOLDER)
    internal_allowed_networks: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )
    metrics_enabled: bool = True

    # --- network -----------------------------------------------------------------------
    device_max_sessions: int = 2
    device_command_timeout: int = 30
    device_connect_retries: int = 2
    device_lock_ttl_seconds: int = 600
    ssh_strict_host_key: bool = True
    controller_ips: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- CLI -------------------------------------------------------------------------------
    cli_username: str = "svc-cli"

    # --- jobs ----------------------------------------------------------------------------
    # "celery" in deployments; "inline" runs jobs as asyncio tasks in the API process (tests/dev).
    job_dispatch: str = "celery"

    # --- discovery ---------------------------------------------------------------------
    discovery_max_prefix: int = 22
    discovery_allowed_subnets: Annotated[list[str], NoDecode] = Field(default_factory=list)
    discovery_concurrency: int = 16
    discovery_rate_per_second: int = 20

    # --- NAC -----------------------------------------------------------------------------
    nac_fail_mode: str = "closed"
    nac_quarantine_vlan: int = 999
    nac_guest_vlan: int = 20
    radius_reference_server_enabled: bool = False

    # --- alerting channels -------------------------------------------------------------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    telegram_bot_token: SecretStr | None = None
    telegram_api_base: str = "https://api.telegram.org"
    webhook_signing_secret: SecretStr | None = None

    # --- monitoring --------------------------------------------------------------------
    metric_retention_days: int = 90

    @field_validator(
        "cors_origins",
        "trusted_hosts",
        "internal_allowed_networks",
        "controller_ips",
        "discovery_allowed_subnets",
        mode="before",
    )
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("internal_allowed_networks", "discovery_allowed_subnets")
    @classmethod
    def _valid_networks(cls, value: list[str]) -> list[str]:
        for net in value:
            ipaddress.ip_network(net, strict=False)
        return value

    @field_validator("nac_fail_mode")
    @classmethod
    def _fail_mode(cls, value: str) -> str:
        if value not in {"closed", "open"}:
            raise ValueError("nac_fail_mode must be 'closed' or 'open'")
        return value

    @property
    def is_production_like(self) -> bool:
        """True for staging/prod, where insecure defaults are refused."""
        return self.environment in {Environment.STAGING, Environment.PROD}

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @model_validator(mode="after")
    def _fail_fast_on_insecure_secrets(self) -> Self:
        if not self.is_production_like:
            return self
        problems: list[str] = []
        for name in ("jwt_secret", "internal_api_token"):
            secret: SecretStr = getattr(self, name)
            raw = secret.get_secret_value()
            if not raw or raw == _DEV_PLACEHOLDER or len(raw) < 32:
                problems.append(f"{name} must be set to a random value of >= 32 chars")
        if not self.credential_keys.get_secret_value():
            problems.append("credential_keys must be configured")
        if "*" in self.trusted_hosts:
            problems.append("trusted_hosts must not contain '*' in production")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings (immutable after load)."""
    return Settings()
