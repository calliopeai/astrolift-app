"""Tests for gcp/_errors.py — the cross-driver error mapping."""

from __future__ import annotations

from gcp._errors import (
    ConflictError,
    NotFoundError,
    PermissionError,
    ProviderError,
    ThrottledError,
    TransientError,
    map_api_error,
)


def _make_exc(name: str, message: str = "boom") -> Exception:
    """Forge an exception whose .__class__.__name__ matches the
    google-cloud SDK's class names — that's what map_api_error
    pattern-matches on."""
    cls = type(name, (Exception,), {})
    return cls(message)


def test_not_found_maps() -> None:
    err = map_api_error(_make_exc("NotFound"))
    assert isinstance(err, NotFoundError)


def test_already_exists_is_conflict() -> None:
    err = map_api_error(_make_exc("AlreadyExists"))
    assert isinstance(err, ConflictError)


def test_failed_precondition_is_conflict() -> None:
    err = map_api_error(_make_exc("FailedPrecondition"))
    assert isinstance(err, ConflictError)


def test_permission_denied_maps() -> None:
    err = map_api_error(_make_exc("PermissionDenied"))
    assert isinstance(err, PermissionError)


def test_resource_exhausted_throttle() -> None:
    err = map_api_error(_make_exc("ResourceExhausted"))
    assert isinstance(err, ThrottledError)


def test_deadline_exceeded_is_transient() -> None:
    err = map_api_error(_make_exc("DeadlineExceeded"))
    assert isinstance(err, TransientError)


def test_service_unavailable_is_transient() -> None:
    err = map_api_error(_make_exc("ServiceUnavailable"))
    assert isinstance(err, TransientError)


def test_unknown_falls_back_to_provider_error() -> None:
    err = map_api_error(_make_exc("SomeNovelError"))
    assert isinstance(err, ProviderError)
    assert not isinstance(err, NotFoundError)


def test_message_preserved() -> None:
    err = map_api_error(_make_exc("NotFound", "secret xyz missing"))
    assert "secret xyz missing" in str(err)
