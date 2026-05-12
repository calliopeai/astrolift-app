"""Tests for webhook delivery attempt history (#276)."""

from __future__ import annotations

import pytest

from astrolift_workflows.utility_workflows import WebhookOutcome
from astrolift_workflows.webhook_delivery_history import (
    MAX_ATTEMPTS,
    MAX_HISTORY_LIMIT,
    AttemptOutcome,
    AttemptPhase,
    AttemptProjection,
    BodyStorage,
    HistoryQuery,
    WebhookHistoryError,
    can_transition_phase,
    map_outcome,
    must_persist_request_first,
    plan_body_storage,
    plan_replay,
    should_blob_body,
    should_retain_response_body,
    should_scrub_request_body,
)

# ---- outcome mapping ----------------------------------------------


def test_success_maps_to_success():
    assert (
        map_outcome(
            classified=WebhookOutcome.SUCCESS,
            attempt_number=1,
            max_attempts=MAX_ATTEMPTS,
        )
        == AttemptOutcome.SUCCESS
    )


def test_permanent_maps_to_permanent():
    assert (
        map_outcome(
            classified=WebhookOutcome.PERMANENT_FAILURE,
            attempt_number=1,
            max_attempts=MAX_ATTEMPTS,
        )
        == AttemptOutcome.PERMANENT_FAILURE
    )


def test_retryable_with_retries_left():
    """Mid-chain retryable failure → RETRY (worker fires again)."""
    assert (
        map_outcome(
            classified=WebhookOutcome.RETRYABLE_FAILURE,
            attempt_number=3,
            max_attempts=9,
        )
        == AttemptOutcome.RETRY
    )


def test_retryable_at_max_promotes_to_permanent():
    """Last attempt's retryable failure becomes PERMANENT."""
    assert (
        map_outcome(
            classified=WebhookOutcome.RETRYABLE_FAILURE,
            attempt_number=9,
            max_attempts=9,
        )
        == AttemptOutcome.PERMANENT_FAILURE
    )


# ---- inline-vs-blob bodies ----------------------------------------


def test_should_blob_small_body_false():
    assert should_blob_body(byte_count=1024) is False


def test_should_blob_large_body_true():
    assert should_blob_body(byte_count=200_000) is True


def test_should_blob_at_threshold_inline():
    """Boundary inclusive (matches CommandRun semantics)."""
    assert should_blob_body(byte_count=64 * 1024) is False


def test_should_blob_negative_rejected():
    with pytest.raises(WebhookHistoryError):
        should_blob_body(byte_count=-1)


def test_plan_body_storage_small_inline():
    plan = plan_body_storage(body=b"small payload")
    assert plan["inline_only"] is True
    assert plan["blob_target"] is False


def test_plan_body_storage_large_blob():
    plan = plan_body_storage(body=b"x" * 100_000)
    assert plan["inline_only"] is False
    assert plan["blob_target"] is True


# ---- BodyStorage invariants ---------------------------------------


def test_body_storage_inline_only():
    b = BodyStorage(
        inline_bytes=b"hello",
        blob_ref="",
        total_byte_count=5,
    )
    assert b.inline_bytes == b"hello"


def test_body_storage_blob_only():
    b = BodyStorage(
        inline_bytes=b"",
        blob_ref="s3://b/k",
        total_byte_count=200_000,
    )
    assert b.blob_ref == "s3://b/k"


def test_body_storage_rejects_both():
    """Webhook bodies are EITHER inline OR blob — no head-inline."""
    with pytest.raises(WebhookHistoryError, match="cannot have both"):
        BodyStorage(
            inline_bytes=b"head",
            blob_ref="s3://b/k",
            total_byte_count=200_000,
        )


# ---- phase transitions --------------------------------------------


def test_scheduled_to_attempted():
    assert (
        can_transition_phase(
            current=AttemptPhase.SCHEDULED,
            target=AttemptPhase.ATTEMPTED,
        )
        is True
    )


def test_attempted_to_completed():
    assert (
        can_transition_phase(
            current=AttemptPhase.ATTEMPTED,
            target=AttemptPhase.COMPLETED,
        )
        is True
    )


def test_completed_is_terminal():
    for target in AttemptPhase:
        assert (
            can_transition_phase(
                current=AttemptPhase.COMPLETED,
                target=target,
            )
            is False
        )


def test_scheduled_cannot_skip_to_completed():
    """Defensive — phase order is part of the audit invariant."""
    assert (
        can_transition_phase(
            current=AttemptPhase.SCHEDULED,
            target=AttemptPhase.COMPLETED,
        )
        is False
    )


def test_must_persist_request_first():
    """Spec §6 invariant: request_* persisted BEFORE the POST."""
    assert (
        must_persist_request_first(
            phase=AttemptPhase.SCHEDULED,
        )
        is True
    )
    assert (
        must_persist_request_first(
            phase=AttemptPhase.ATTEMPTED,
        )
        is False
    )


# ---- AttemptProjection invariants ---------------------------------


def _attempt(**overrides) -> AttemptProjection:
    base = dict(
        delivery_id="d1",
        subscription_id=1,
        event_id=1,
        attempt_number=1,
        phase=AttemptPhase.COMPLETED,
        outcome=AttemptOutcome.SUCCESS,
        scheduled_at_unix=1_700_000_000,
        attempted_at_unix=1_700_000_001,
        completed_at_unix=1_700_000_002,
        request_url="https://subscriber.example/webhook",
        request_body=BodyStorage(
            inline_bytes=b"req",
            blob_ref="",
            total_byte_count=3,
        ),
        response_status=200,
        response_body=BodyStorage(
            inline_bytes=b"ok",
            blob_ref="",
            total_byte_count=2,
        ),
        error_message="",
        signature_hex="abc123",
    )
    base.update(overrides)
    return AttemptProjection(**base)


def test_attempt_basic():
    a = _attempt()
    assert a.attempt_number == 1
    assert a.outcome == AttemptOutcome.SUCCESS


def test_attempt_number_floor():
    with pytest.raises(WebhookHistoryError):
        _attempt(attempt_number=0)


def test_attempt_number_ceiling():
    with pytest.raises(WebhookHistoryError, match="exceeds"):
        _attempt(attempt_number=MAX_ATTEMPTS + 1)


def test_attempt_scheduled_with_attempted_at_rejected():
    with pytest.raises(WebhookHistoryError):
        _attempt(
            phase=AttemptPhase.SCHEDULED,
            attempted_at_unix=1_700_000_001,
            completed_at_unix=None,
            outcome=None,
            response_status=None,
        )


def test_attempt_completed_without_outcome_rejected():
    with pytest.raises(WebhookHistoryError, match="outcome"):
        _attempt(
            phase=AttemptPhase.COMPLETED,
            outcome=None,
        )


def test_attempt_completed_without_attempted_at_rejected():
    with pytest.raises(WebhookHistoryError):
        _attempt(
            phase=AttemptPhase.COMPLETED,
            attempted_at_unix=None,
        )


def test_attempt_connection_error_recorded():
    """Connection error: response_status=None but error_message set."""
    a = _attempt(
        outcome=AttemptOutcome.RETRY,
        response_status=None,
        response_body=BodyStorage(
            inline_bytes=b"",
            blob_ref="",
            total_byte_count=0,
        ),
        error_message="connection refused: subscriber.example:443",
    )
    assert a.response_status is None
    assert "connection refused" in a.error_message


def test_attempt_completed_with_no_status_no_error_rejected():
    """Defensive: COMPLETED requires either status OR error.
    Both missing = inconsistent state (impossible from worker)."""
    with pytest.raises(WebhookHistoryError):
        _attempt(
            phase=AttemptPhase.COMPLETED,
            response_status=None,
            error_message="",
        )


# ---- replay --------------------------------------------------------


def test_plan_replay_creates_attempt_one():
    """Replay starts a fresh attempt chain."""
    original = _attempt()
    plan = plan_replay(
        original_attempt=original,
        new_delivery_id="d-replay",
    )
    assert plan.attempt_number == 1
    assert plan.event_id == original.event_id
    assert plan.subscription_id == original.subscription_id


def test_plan_replay_requires_completed_original():
    """Can't replay an in-flight attempt — wait for it."""
    original = _attempt(
        phase=AttemptPhase.SCHEDULED,
        outcome=None,
        attempted_at_unix=None,
        completed_at_unix=None,
        response_status=None,
    )
    with pytest.raises(WebhookHistoryError, match="COMPLETED"):
        plan_replay(
            original_attempt=original,
            new_delivery_id="d-replay",
        )


def test_plan_replay_requires_new_delivery_id():
    with pytest.raises(WebhookHistoryError):
        plan_replay(
            original_attempt=_attempt(),
            new_delivery_id="",
        )


# ---- history query pagination ------------------------------------


def test_history_query_default_limit():
    q = HistoryQuery(subscription_id=1)
    assert q.limit == 50


def test_history_query_max_limit():
    q = HistoryQuery(subscription_id=1, limit=MAX_HISTORY_LIMIT)
    assert q.limit == MAX_HISTORY_LIMIT


def test_history_query_over_max_rejected():
    """Max bounded so chatty callers can't pull megabytes per
    request."""
    with pytest.raises(WebhookHistoryError):
        HistoryQuery(subscription_id=1, limit=MAX_HISTORY_LIMIT + 1)


def test_history_query_zero_limit_rejected():
    with pytest.raises(WebhookHistoryError):
        HistoryQuery(subscription_id=1, limit=0)


# ---- privacy / scrub ----------------------------------------------


def test_should_scrub_request_body_when_anonymizing():
    """Per #169: org-deletion mid-flight scrubs cached
    request bodies too."""
    assert (
        should_scrub_request_body(
            org_anonymization_active=True,
            subscription_responses_opted_in=False,
        )
        is True
    )


def test_should_not_scrub_when_org_active():
    assert (
        should_scrub_request_body(
            org_anonymization_active=False,
            subscription_responses_opted_in=False,
        )
        is False
    )


def test_response_retention_default_off():
    """Privacy default: don't persist subscriber response bodies
    unless org opts in (subscriber-side state can leak)."""
    assert (
        should_retain_response_body(
            org_response_retention_opted_in=False,
        )
        is False
    )


def test_response_retention_opt_in():
    assert (
        should_retain_response_body(
            org_response_retention_opted_in=True,
        )
        is True
    )
