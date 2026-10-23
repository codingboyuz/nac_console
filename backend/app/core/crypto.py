"""Credential vault crypto: AES-256-GCM with key ids for rotation.

Envelope format: ``v1:<kid>:<base64(nonce(12) || ciphertext||tag)>``. The associated data binds
a ciphertext to its purpose (e.g. ``credential:<uuid>``) so blobs cannot be swapped between rows.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

_VERSION = "v1"


class VaultError(Exception):
    """Raised when a ciphertext cannot be decrypted (wrong key, tampering, unknown kid)."""


@dataclass(frozen=True, slots=True)
class _Key:
    kid: str
    aead: AESGCM


def parse_keyring(spec: str) -> dict[str, bytes]:
    """Parse ``kid:<b64>,kid2:<b64>`` into raw 32-byte keys."""
    keys: dict[str, bytes] = {}
    for item in filter(None, (part.strip() for part in spec.split(","))):
        kid, sep, b64 = item.partition(":")
        if not sep or not kid.isalnum():
            raise ValueError("credential key entries must look like 'kid:<base64>'")
        try:
            raw = base64.b64decode(b64, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"credential key {kid!r} is not valid base64") from exc
        if len(raw) != 32:
            raise ValueError(f"credential key {kid!r} must be 32 bytes (AES-256)")
        keys[kid] = raw
    return keys


class CredentialVault:
    """Encrypts with the active key; decrypts with any configured key id."""

    def __init__(self, keyring: SecretStr, active_kid: str) -> None:
        raw = parse_keyring(keyring.get_secret_value())
        if not raw:
            # Dev/test convenience: an ephemeral key. Data will not survive restarts.
            raw = {active_kid: AESGCM.generate_key(bit_length=256)}
        if active_kid not in raw:
            raise ValueError(f"active credential key id {active_kid!r} is not configured")
        self._keys = {kid: _Key(kid, AESGCM(key)) for kid, key in raw.items()}
        self.active_kid = active_kid

    def encrypt(self, plaintext: bytes, associated_data: str) -> str:
        key = self._keys[self.active_kid]
        nonce = os.urandom(12)
        sealed = key.aead.encrypt(nonce, plaintext, associated_data.encode())
        return f"{_VERSION}:{key.kid}:{base64.b64encode(nonce + sealed).decode()}"

    def decrypt(self, envelope: str, associated_data: str) -> bytes:
        try:
            version, kid, b64 = envelope.split(":", 2)
        except ValueError as exc:
            raise VaultError("malformed ciphertext envelope") from exc
        if version != _VERSION:
            raise VaultError(f"unsupported envelope version {version!r}")
        key = self._keys.get(kid)
        if key is None:
            raise VaultError(f"unknown key id {kid!r}")
        blob = base64.b64decode(b64)
        try:
            return key.aead.decrypt(blob[:12], blob[12:], associated_data.encode())
        except InvalidTag as exc:
            raise VaultError("ciphertext authentication failed") from exc

    def key_id_of(self, envelope: str) -> str:
        return envelope.split(":", 2)[1]

    def needs_rotation(self, envelope: str) -> bool:
        return self.key_id_of(envelope) != self.active_kid

    def rotate(self, envelope: str, associated_data: str) -> str:
        """Re-encrypt under the active key (no-op result when already current)."""
        if not self.needs_rotation(envelope):
            return envelope
        return self.encrypt(self.decrypt(envelope, associated_data), associated_data)

    # JSON helpers for structured secrets (password + super password, SNMPv3 keys, ...)
    def encrypt_json(self, data: dict[str, Any], associated_data: str) -> str:
        return self.encrypt(json.dumps(data, separators=(",", ":")).encode(), associated_data)

    def decrypt_json(self, envelope: str, associated_data: str) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.decrypt(envelope, associated_data))
        return result
