"""Tests for ``ApplyError`` + ``ApplyResult.summary()`` + ``classify_apply_error``.

Closes audit issue #603 -- previously the per-manifest error context was
stringified into ``errors: list[str]``, so the workflow couldn't classify
transient (HTTP 5xx, ConnectionError, TimeoutError) from permanent (HTTP
4xx, ValidationError) without re-parsing free-text.
"""

from __future__ import annotations

from _sdk.cluster import ApplyError, ApplyResult, classify_apply_error


def test_apply_error_str_matches_legacy_shape() -> None:
    """The old ``{kind}/{name}: {exc}`` shape must still round-trip via
    ``str(err)`` so log emitters that ingest ``ApplyResult.errors``
    don't churn."""
    err = ApplyError(
        kind="Deployment",
        name="api",
        namespace="acme",
        exception_type="ConflictError",
        exception_message="409 conflict",
        is_retryable=True,
    )
    assert str(err) == "Deployment/api: 409 conflict"


def test_apply_result_summary_returns_legacy_strings() -> None:
    err = ApplyError(
        kind="Service",
        name="api",
        namespace="acme",
        exception_type="HTTPError",
        exception_message="500 server error",
        is_retryable=True,
    )
    result = ApplyResult(
        created=["Deployment/api"],
        updated=[],
        unchanged=[],
        errors=[err],
    )
    assert result.ok is False
    assert result.summary() == ["Service/api: 500 server error"]
    assert result.has_retryable is True


def test_apply_result_has_retryable_false_on_permanent() -> None:
    err = ApplyError(
        kind="Pod",
        name="x",
        namespace="acme",
        exception_type="ValidationError",
        exception_message="bad spec",
        is_retryable=False,
    )
    result = ApplyResult(created=[], updated=[], unchanged=[], errors=[err])
    assert result.has_retryable is False


def test_classify_transient_by_exception_name() -> None:
    assert classify_apply_error(ConnectionError("reset")) is True
    assert classify_apply_error(TimeoutError("slow")) is True


def test_classify_permanent_by_exception_name() -> None:
    assert classify_apply_error(ValueError("bad")) is False
    assert classify_apply_error(TypeError("wrong type")) is False


def test_classify_uses_http_status_when_set() -> None:
    """The kubernetes Python client raises ``ApiException`` with a
    ``status`` attribute on HTTP errors; the classifier prefers status
    over class name."""

    class _ApiException(Exception):
        pass

    e500 = _ApiException("err")
    e500.status = 503
    assert classify_apply_error(e500) is True

    e400 = _ApiException("err")
    e400.status = 422
    assert classify_apply_error(e400) is False
