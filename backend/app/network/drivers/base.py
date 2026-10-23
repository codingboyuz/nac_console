"""Shared driver plumbing: bounded retries with backoff+jitter, timing, context manager."""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from types import TracebackType
from typing import Self

from app.core.exceptions import DeviceTimeout, DeviceUnreachable
from app.core.logging import get_logger
from app.network.errors import map_exception
from app.network.ports import ConnectionParams

log = get_logger(__name__)
_RETRYABLE = (DeviceUnreachable, DeviceTimeout)


def retry_connect[T](
    params: ConnectionParams,
    attempt: Callable[[], T],
    *,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``attempt``; retry only transport failures (never auth/host-key failures)."""
    retries = max(0, params.connect_retries)
    for number in range(retries + 1):
        try:
            return attempt()
        except Exception as exc:
            error = map_exception(exc, params.host)
            if not isinstance(error, _RETRYABLE) or number >= retries:
                raise error from exc
            delay = min(max_delay, base_delay * (2**number)) * (0.5 + random.random())  # noqa: S311
            log.warning(
                "device_connect_retry",
                device_id=params.device_id,
                attempt=number + 1,
                error=error.code,
                delay=round(delay, 2),
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


class DriverBase(ABC):
    """Context-manager support; subclasses implement connect/disconnect."""

    params: ConnectionParams

    @abstractmethod
    def connect(self) -> None:
        """Open the transport session."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the transport session (idempotent)."""

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.disconnect()
