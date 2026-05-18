"""Shared fixtures for Azure driver tests.

Azure SDKs raise azure.core.exceptions.{ResourceNotFoundError,
ResourceExistsError, HttpResponseError, ClientAuthenticationError}.
We use stub exception classes whose __name__ matches the SDK's so
the gcp/aws/azure error-mapping pattern (read .__class__.__name__)
works without importing the real azure-core package.
"""

from __future__ import annotations

import pytest


class ResourceNotFoundError(Exception):
    pass


class ResourceExistsError(Exception):
    pass


class HttpResponseError(Exception):
    def __init__(self, message: str = "", status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ClientAuthenticationError(Exception):
    pass


@pytest.fixture
def azure_exceptions() -> dict[str, type[Exception]]:
    return {
        "ResourceNotFoundError": ResourceNotFoundError,
        "ResourceExistsError": ResourceExistsError,
        "HttpResponseError": HttpResponseError,
        "ClientAuthenticationError": ClientAuthenticationError,
    }
