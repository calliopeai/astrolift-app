"""AWS error mapping.

boto3/botocore raise ClientError with a structured Error.Code field.
This module maps common codes to typed exceptions the platform's
workflow layer expects (NotFound / Conflict / Throttled / etc.) so
calling code doesn't pattern-match on raw strings.

Spec ref: bootstrap.md "All operations propagate errors as typed
exceptions, not bare strings."
"""

from __future__ import annotations

from typing import Any


class ProviderError(Exception):
    """Base for all AWS-driver errors."""


class NotFoundError(ProviderError):
    """Resource doesn't exist."""


class ConflictError(ProviderError):
    """Resource already exists or invalid state for operation."""


class ThrottledError(ProviderError):
    """Provider rate-limited the call. Caller should back off + retry."""


class PermissionError(ProviderError):
    """IAM denied the operation. Operator must fix the role policy."""


class TransientError(ProviderError):
    """Temporary failure that may succeed on retry."""


# AWS error code → typed exception. Codes not listed fall through to
# ProviderError so the workflow layer surfaces the raw error message.
_CODE_MAP: dict[str, type[ProviderError]] = {
    # NotFound family
    "NoSuchEntity": NotFoundError,
    "ResourceNotFoundException": NotFoundError,
    "RepositoryNotFoundException": NotFoundError,
    "NoSuchHostedZone": NotFoundError,
    "ClusterNotFoundException": NotFoundError,
    "InvalidParameterException": NotFoundError,  # often "X not found"
    "ParameterNotFound": NotFoundError,
    "SecretsManager.ResourceNotFoundException": NotFoundError,
    # Conflict family
    "EntityAlreadyExists": ConflictError,
    "AlreadyExistsException": ConflictError,
    "RepositoryAlreadyExistsException": ConflictError,
    "ResourceInUseException": ConflictError,
    "ResourceAlreadyExistsException": ConflictError,
    "DuplicateResourceException": ConflictError,
    "InvalidChangeBatch": ConflictError,
    # Throttling
    "Throttling": ThrottledError,
    "ThrottlingException": ThrottledError,
    "RequestLimitExceeded": ThrottledError,
    "TooManyRequestsException": ThrottledError,
    "PriorRequestNotComplete": ThrottledError,
    # Permission
    "AccessDenied": PermissionError,
    "AccessDeniedException": PermissionError,
    "UnauthorizedOperation": PermissionError,
    "InvalidClientTokenId": PermissionError,
    # Transient
    "ServiceUnavailable": TransientError,
    "ServiceUnavailableException": TransientError,
    "InternalFailure": TransientError,
    "InternalServerError": TransientError,
    "InternalServerException": TransientError,
}


def map_client_error(exc: Any) -> ProviderError:
    """Translate a boto3 ClientError into a typed ProviderError.

    Caller does ``except ClientError as exc: raise map_client_error(exc) from exc``.
    """
    response = getattr(exc, "response", None) or {}
    error = response.get("Error", {}) or {}
    code = error.get("Code", "")
    message = error.get("Message", str(exc))

    cls = _CODE_MAP.get(code, ProviderError)
    return cls(f"{code}: {message}" if code else message)
