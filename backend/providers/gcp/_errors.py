"""GCP error mapping.

google-cloud-* SDKs raise google.api_core.exceptions subclasses
with HTTP-style status codes. This module maps them to the same
typed exceptions AWS uses so workflow code can pattern-match
once across clouds.
"""

from __future__ import annotations

from typing import Any


class ProviderError(Exception):
    """Base for all GCP-driver errors."""


class NotFoundError(ProviderError):
    pass


class ConflictError(ProviderError):
    pass


class ThrottledError(ProviderError):
    pass


class PermissionError(ProviderError):
    pass


class TransientError(ProviderError):
    pass


# Map google-cloud exception class names → typed errors. Real
# SDK calls catch the exception object; we read .__class__.__name__
# rather than importing the full google.api_core hierarchy.
_NAME_MAP: dict[str, type[ProviderError]] = {
    "NotFound": NotFoundError,
    "AlreadyExists": ConflictError,
    "FailedPrecondition": ConflictError,
    "Aborted": ConflictError,
    "PermissionDenied": PermissionError,
    "Unauthenticated": PermissionError,
    "ResourceExhausted": ThrottledError,
    "DeadlineExceeded": TransientError,
    "ServiceUnavailable": TransientError,
    "InternalServerError": TransientError,
}


def map_api_error(exc: Any) -> ProviderError:
    name = type(exc).__name__
    cls = _NAME_MAP.get(name, ProviderError)
    message = getattr(exc, "message", None) or str(exc)
    return cls(f"{name}: {message}" if name else message)
