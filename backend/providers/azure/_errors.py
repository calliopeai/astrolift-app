"""Azure error mapping.

azure-* SDKs raise azure.core.exceptions.HttpResponseError + a
handful of typed subclasses (ResourceNotFoundError, ResourceExistsError).
This module maps them to the same typed exceptions AWS / GCP use so
workflow code can pattern-match once across clouds.
"""

from __future__ import annotations

from typing import Any


class ProviderError(Exception):
    """Base for all Azure-driver errors."""


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


# Exception class-name → typed error. Reading the class name avoids
# importing the full azure.core.exceptions hierarchy at driver load.
_NAME_MAP: dict[str, type[ProviderError]] = {
    "ResourceNotFoundError": NotFoundError,
    "ResourceExistsError": ConflictError,
    "HttpResponseError": ProviderError,
    "ClientAuthenticationError": PermissionError,
    "ServiceRequestError": TransientError,
    "ServiceResponseError": TransientError,
    "AzureError": ProviderError,
}


def map_api_error(exc: Any) -> ProviderError:
    name = type(exc).__name__
    cls = _NAME_MAP.get(name, ProviderError)

    # HttpResponseError carries .status_code; refine via that
    status = getattr(exc, "status_code", None)
    if status == 404:
        cls = NotFoundError
    elif status == 409:
        cls = ConflictError
    elif status in (401, 403):
        cls = PermissionError
    elif status == 429:
        cls = ThrottledError
    elif status in (500, 502, 503, 504):
        cls = TransientError

    message = getattr(exc, "message", None) or str(exc)
    return cls(f"{name}: {message}" if name else message)
