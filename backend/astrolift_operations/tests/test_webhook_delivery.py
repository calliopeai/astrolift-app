"""Tests for webhook delivery pipeline policy (#37, spec 17 §6-§7)."""

from __future__ import annotations

import random

import pytest

from astrolift_operations.webhook_delivery import (
    MAX_ATTEMPTS,
    MAX_INFLIGHT_PER_SUBSCRIPTION,
    RETRY_SCHEDULE_SECONDS,
    DeliveryAttemptResult,
    DeliveryClassification,
    build_headers,
    can_send,
    classify,
    next_retry_delay_seconds,
    sign_payload,
    verify_signature,
)

SECRET = b"k" * 32
BODY = b'{"event":"app.deployed"}'
TS = 1_700_000_000


# ---- signing -------------------------------------------------------


def test_sign_returns_sha256_prefix_and_hex():
    sig = sign_payload(secret=SECRET, timestamp_unix=TS, raw_body=BODY)
    assert sig.startswith("sha256=")
    # 64 hex chars after prefix
    assert len(sig) == len("sha256=") + 64
    int(sig.split("=", 1)[1], 16)  # decodes


def test_sign_different_for_different_timestamp():
    """Replay defense: same body at different timestamps produces
    different sigs, so a captured sig can't be replayed at any
    later moment."""
    a = sign_payload(secret=SECRET, timestamp_unix=TS, raw_body=BODY)
    b = sign_payload(secret=SECRET, timestamp_unix=TS + 1, raw_body=BODY)
    assert a != b


def test_sign_different_for_different_body():
    a = sign_payload(secret=SECRET, timestamp_unix=TS, raw_body=BODY)
    b = sign_payload(secret=SECRET, timestamp_unix=TS, raw_body=BODY + b"x")
    assert a != b


def test_sign_rejects_empty_secret():
    with pytest.raises(ValueError):
        sign_payload(secret=b"", timestamp_unix=TS, raw_body=BODY)


def test_sign_rejects_nonpositive_timestamp():
    with pytest.raises(ValueError):
        sign_payload(secret=SECRET, timestamp_unix=0, raw_body=BODY)


def test_verify_passes_for_valid_signature():
    sig = sign_payload(secret=SECRET, timestamp_unix=TS, raw_body=BODY)
    assert verify_signature(
        secret=SECRET, timestamp_unix=TS, raw_body=BODY,
        presented=sig, now_unix=TS,
    ) is True


def test_verify_rejects_wrong_signature():
    assert verify_signature(
        secret=SECRET, timestamp_unix=TS, raw_body=BODY,
        presented="sha256=" + "0" * 64,
    ) is False


def test_verify_rejects_unprefixed():
    assert verify_signature(
        secret=SECRET, timestamp_unix=TS, raw_body=BODY,
        presented="just-some-hex",
    ) is False


def test_verify_rejects_stale_timestamp():
    """Beyond freshness window → reject. Catches captured-and-replayed
    deliveries even with a valid signature."""
    sig = sign_payload(secret=SECRET, timestamp_unix=TS, raw_body=BODY)
    assert verify_signature(
        secret=SECRET, timestamp_unix=TS, raw_body=BODY,
        presented=sig, now_unix=TS + 600, freshness_window_seconds=300,
    ) is False


def test_verify_rejects_none_presented():
    assert verify_signature(
        secret=SECRET, timestamp_unix=TS, raw_body=BODY,
        presented=None,  # type: ignore[arg-type]
    ) is False


# ---- headers -------------------------------------------------------


def test_build_headers_complete_envelope():
    h = build_headers(
        event_type="app.deployed",
        event_id="evt_123",
        delivery_id="dlv_456",
        schema_version="1.0",
        signature="sha256=abc",
        timestamp_unix=TS,
    )
    assert h["X-Astrolift-Event-Type"] == "app.deployed"
    assert h["X-Astrolift-Event-Id"] == "evt_123"
    assert h["X-Astrolift-Delivery-Id"] == "dlv_456"
    assert h["X-Astrolift-Signature"] == "sha256=abc"
    assert h["X-Astrolift-Timestamp"] == str(TS)
    assert h["X-Astrolift-Schema"] == "1.0"
    assert h["Content-Type"] == "application/json"
    assert "User-Agent" in h


# ---- retry schedule ------------------------------------------------


def test_retry_schedule_matches_spec_17():
    assert RETRY_SCHEDULE_SECONDS == (5, 30, 120, 600, 1800, 7200, 21600, 86400)


def test_max_attempts_is_initial_plus_8_retries():
    assert MAX_ATTEMPTS == 9  # 1 initial + 8 retries


def test_first_retry_is_5s_with_jitter():
    """No jitter (rng=fixed seed) — base delay must be the schedule
    entry."""
    rng = random.Random(0)
    delay = next_retry_delay_seconds(attempt=1, jitter_ratio=0.0, rng=rng)
    assert delay == 5


def test_retry_jitter_within_bounds():
    """20% jitter on a 5s delay → between 4 and 6 seconds (rounded
    to int, min 1)."""
    rng = random.Random(0)
    for _ in range(50):
        delay = next_retry_delay_seconds(attempt=1, jitter_ratio=0.2, rng=rng)
        assert 4 <= delay <= 6


def test_no_retry_after_max_attempts():
    """Attempt 9 just failed → no more retries (8 already used)."""
    assert next_retry_delay_seconds(attempt=MAX_ATTEMPTS) is None
    assert next_retry_delay_seconds(attempt=MAX_ATTEMPTS + 5) is None


def test_retry_attempt_zero_rejected():
    with pytest.raises(ValueError):
        next_retry_delay_seconds(attempt=0)


# ---- classify ------------------------------------------------------


@pytest.mark.parametrize("code", [200, 201, 202, 204, 299])
def test_classify_2xx_is_success(code):
    assert classify(DeliveryAttemptResult(status_code=code)) == DeliveryClassification.SUCCESS


def test_classify_410_immediate_disable():
    """410 Gone is the subscriber's 'unsubscribe me' signal — disable
    on the first occurrence, don't burn 8 retries."""
    assert classify(DeliveryAttemptResult(status_code=410)) == DeliveryClassification.IMMEDIATE_DISABLE


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_classify_other_4xx_permanent_failure(code):
    assert classify(DeliveryAttemptResult(status_code=code)) == DeliveryClassification.PERMANENT_FAILURE


@pytest.mark.parametrize("code", [500, 502, 503, 504, 599])
def test_classify_5xx_retry(code):
    assert classify(DeliveryAttemptResult(status_code=code)) == DeliveryClassification.RETRY


def test_classify_connection_error_retry():
    """No HTTP response (timeout / DNS / TCP reset) → transient."""
    out = classify(DeliveryAttemptResult(status_code=None, connection_error="timeout"))
    assert out == DeliveryClassification.RETRY


# ---- rate limit ----------------------------------------------------


def test_rate_limit_value_matches_spec_17_section_7():
    assert MAX_INFLIGHT_PER_SUBSCRIPTION == 100


def test_can_send_below_limit():
    assert can_send(current_inflight=0) is True
    assert can_send(current_inflight=99) is True


def test_can_send_at_or_above_limit():
    assert can_send(current_inflight=100) is False
    assert can_send(current_inflight=999) is False
