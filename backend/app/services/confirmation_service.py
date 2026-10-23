"""Short-lived, single-use confirmation tokens for DANGEROUS operations.

A token is issued by a dry-run call and is bound to (user, device, operation payload hash).
Only its SHA-256 is stored in Redis; consumption is atomic (GETDEL), so replay is impossible.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from redis.asyncio import Redis

from app.core.exceptions import ConfirmationRequired
from app.core.security import new_opaque_token, sha256_hex
from app.domain.identity import Principal

_KEY = "confirm:{}"


def payload_hash(*parts: object) -> str:
    """Stable hash of what is being confirmed (command text, config lines, target ids)."""
    blob = json.dumps([str(p) for p in parts], separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


class ConfirmationService:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self.ttl = ttl_seconds

    async def issue(self, principal: Principal, target_id: uuid.UUID | str, digest: str) -> str:
        token = new_opaque_token("ct")
        record = json.dumps({"user": str(principal.user_id), "target": str(target_id), "h": digest})
        await self._redis.set(_KEY.format(sha256_hex(token)), record, ex=self.ttl)
        return token

    async def consume(
        self, token: str | None, principal: Principal, target_id: uuid.UUID | str, digest: str
    ) -> None:
        """Validate and burn the token; raise ConfirmationRequired on any mismatch."""
        if not token:
            raise ConfirmationRequired(
                "dangerous operation: run a dry-run first and resend with confirm=true, "
                "confirmation_token and reason"
            )
        raw = await self._redis.getdel(_KEY.format(sha256_hex(token)))
        if raw is None:
            raise ConfirmationRequired("confirmation token invalid, expired or already used")
        record = json.loads(raw)
        expected = {"user": str(principal.user_id), "target": str(target_id), "h": digest}
        if record != expected:
            raise ConfirmationRequired("confirmation token does not match this user/target/change")
