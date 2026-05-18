"""Tests for azure/_errors.py — the cross-driver error mapping."""

from __future__ import annotations

import pytest

from azure._errors import (
    ConflictError,
    NotFoundError,
    PermissionError,
    ProviderError,
    ThrottledError,
    TransientError,
    map_api_error,
)


def _make_exc(
    name: str,
    *,
    message: str = "boom",
    status_code: int | None = None,
) -> Exception:
    cls = type(name, (Exception,), {})
    exc = cls(message)
    if status_code is not None:
        exc.status_code = status_code  # type: ignore[attr-defined]
    return exc


def test_resource_not_found_maps() -> None:
    err = map_api_error(_make_exc("ResourceNotFoundError"))
    assert isinstance(err, NotFoundError)


def test_resource_exists_is_conflict() -> None:
    err = map_api_error(_make_exc("ResourceExistsError"))
    assert isinstance(err, ConflictError)


def test_client_auth_is_permission() -> None:
    err = map_api_error(_make_exc("ClientAuthenticationError"))
    assert isinstance(err, PermissionError)


def test_service_request_is_transient() -> None:
    err = map_api_error(_make_exc("ServiceRequestError"))
    assert isinstance(err, TransientError)


def test_status_404_overrides_to_not_found() -> None:
    err = map_api_error(
        _make_exc("HttpResponseError", status_code=404),
    )
    assert isinstance(err, NotFoundError)


def test_status_409_overrides_to_conflict() -> None:
    err = map_api_error(
        _make_exc("HttpResponseError", status_code=409),
    )
    assert isinstance(err, ConflictError)


def test_status_429_throttled() -> None:
    err = map_api_error(
        _make_exc("HttpResponseError", status_code=429),
    )
    assert isinstance(err, ThrottledError)


def test_status_503_transient() -> None:
    err = map_api_error(
        _make_exc("HttpResponseError", status_code=503),
    )
    assert isinstance(err, TransientError)


def test_unknown_falls_back_to_provider_error() -> None:
    err = map_api_error(_make_exc("SomeNovelError"))
    assert isinstance(err, ProviderError)


def test_message_preserved() -> None:
    err = map_api_error(
        _make_exc("ResourceNotFoundError", message="missing thing"),
    )
    assert "missing thing" in str(err)
