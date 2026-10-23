"""Domain/application exception hierarchy mapped to RFC 9457 problem+json responses."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base for every expected error. Subclasses fix status code and machine code."""

    status_code: int = 400
    code: str = "bad_request"
    title: str = "Bad Request"

    def __init__(self, message: str | None = None, *, details: Any = None) -> None:
        self.message = message or self.title
        self.details = details
        super().__init__(self.message)


class ValidationFailed(AppError):
    status_code, code, title = 422, "validation_failed", "Validation failed"


class NotFound(AppError):
    status_code, code, title = 404, "not_found", "Resource not found"


class Conflict(AppError):
    status_code, code, title = 409, "conflict", "Conflict"


class Unauthorized(AppError):
    status_code, code, title = 401, "unauthorized", "Authentication required"


class Forbidden(AppError):
    status_code, code, title = 403, "forbidden", "Permission denied"


class Locked(AppError):
    status_code, code, title = 423, "locked", "Resource locked"


class RateLimited(AppError):
    status_code, code, title = 429, "rate_limited", "Too many requests"

    def __init__(self, message: str | None = None, *, retry_after: int = 60) -> None:
        super().__init__(message, details={"retry_after": retry_after})
        self.retry_after = retry_after


class UnsupportedCapability(AppError):
    """Feature not supported by the target device (detected capabilities)."""

    status_code, code, title = 422, "unsupported_capability", "Capability not supported"


class ConfirmationRequired(AppError):
    """Dangerous operation needs confirm=true + a valid confirmation token + reason."""

    status_code, code, title = 409, "confirmation_required", "Confirmation required"


class LockoutRisk(AppError):
    """Change would cut the management path to the device."""

    status_code, code, title = 409, "lockout_risk", "Change would cut management access"


class ServiceUnavailable(AppError):
    status_code, code, title = 503, "service_unavailable", "Dependency unavailable"


# --- network domain errors --------------------------------------------------------------


class DeviceError(AppError):
    status_code, code, title = 502, "device_error", "Device operation failed"


class DeviceUnreachable(DeviceError):
    status_code, code, title = 504, "device_unreachable", "Device unreachable"


class DeviceAuthFailed(DeviceError):
    status_code, code, title = 502, "device_auth_failed", "Device authentication failed"


class DeviceTimeout(DeviceError):
    status_code, code, title = 504, "device_timeout", "Device operation timed out"


class CommandRejected(DeviceError):
    """Device returned an error marker such as 'Error:' or 'Unrecognized command'."""

    status_code, code, title = 502, "command_rejected", "Device rejected the command"


class HostKeyMismatch(DeviceError):
    status_code, code, title = 409, "host_key_mismatch", "SSH host key does not match pinned key"


class UnsafeCommand(AppError):
    """Input contains injection characters or is not permitted for the caller."""

    status_code, code, title = 400, "unsafe_command", "Command rejected by safety policy"
