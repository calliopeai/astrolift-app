"""Tests for CommandRun output persistence policy (#275)."""

from __future__ import annotations

import pytest

from astrolift_workflows.command_run_output import (
    DEFAULT_RETENTION_DAYS,
    INLINE_HARD_CAP_BYTES,
    INLINE_THRESHOLD_BYTES,
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    CapturedStream,
    CaptureState,
    CommandRunOutputError,
    PruneCandidate,
    can_transition_capture,
    capture_failure_message,
    cleanup_gate_open,
    is_past_retention,
    plan_capture,
    projection_for,
    safe_to_prune,
    should_use_inline,
)

# ---- inline threshold ---------------------------------------------


def test_inline_threshold_locked():
    """64KB — tuned for fast first-paint."""
    assert INLINE_THRESHOLD_BYTES == 64 * 1024


def test_should_use_inline_under_threshold():
    assert should_use_inline(byte_count=10_000) is True


def test_should_use_inline_at_threshold():
    """Boundary inclusive — exactly at threshold = still inline."""
    assert should_use_inline(byte_count=INLINE_THRESHOLD_BYTES) is True


def test_should_use_inline_over_threshold():
    assert (
        should_use_inline(
            byte_count=INLINE_THRESHOLD_BYTES + 1,
        )
        is False
    )


def test_should_use_inline_rejects_negative():
    with pytest.raises(CommandRunOutputError):
        should_use_inline(byte_count=-1)


# ---- capture state machine ----------------------------------------


def test_pending_to_capturing():
    assert (
        can_transition_capture(
            current=CaptureState.PENDING,
            target=CaptureState.CAPTURING,
        )
        is True
    )


def test_capturing_to_captured():
    assert (
        can_transition_capture(
            current=CaptureState.CAPTURING,
            target=CaptureState.CAPTURED,
        )
        is True
    )


def test_capturing_to_failed():
    assert (
        can_transition_capture(
            current=CaptureState.CAPTURING,
            target=CaptureState.CAPTURE_FAILED,
        )
        is True
    )


def test_captured_is_terminal():
    for target in CaptureState:
        assert (
            can_transition_capture(
                current=CaptureState.CAPTURED,
                target=target,
            )
            is False
        )


def test_capture_failed_is_terminal():
    for target in CaptureState:
        assert (
            can_transition_capture(
                current=CaptureState.CAPTURE_FAILED,
                target=target,
            )
            is False
        )


def test_pending_cannot_skip_to_captured():
    """Must go through CAPTURING — defensive against workflows
    that try to mark CAPTURED without doing the capture work."""
    assert (
        can_transition_capture(
            current=CaptureState.PENDING,
            target=CaptureState.CAPTURED,
        )
        is False
    )


# ---- cleanup gate (capture-before-cleanup invariant) -------------


def test_cleanup_gate_closed_when_pending():
    """The whole point of this policy: cleanup MUST NOT fire
    while logs may still arrive."""
    assert (
        cleanup_gate_open(
            capture_state=CaptureState.PENDING,
        )
        is False
    )


def test_cleanup_gate_closed_when_capturing():
    assert (
        cleanup_gate_open(
            capture_state=CaptureState.CAPTURING,
        )
        is False
    )


def test_cleanup_gate_open_when_captured():
    assert (
        cleanup_gate_open(
            capture_state=CaptureState.CAPTURED,
        )
        is True
    )


def test_cleanup_gate_open_when_capture_failed():
    """Even on capture failure, cleanup proceeds — orphan Jobs
    are worse than missing logs (which UI already surfaces)."""
    assert (
        cleanup_gate_open(
            capture_state=CaptureState.CAPTURE_FAILED,
        )
        is True
    )


# ---- CapturedStream invariants ------------------------------------


def test_inline_only_stream():
    s = CapturedStream(
        inline_bytes=b"short output",
        blob_ref="",
        total_byte_count=12,
        truncated=False,
    )
    assert s.truncated is False
    assert s.blob_ref == ""


def test_truncated_with_blob_ref():
    s = CapturedStream(
        inline_bytes=b"head" * 1000,
        blob_ref="s3://bucket/run-1/stdout",
        total_byte_count=200_000,
        truncated=True,
    )
    assert s.truncated is True


def test_truncated_without_blob_ref_rejected():
    """Truncated = blob_ref REQUIRED. Otherwise UI shows
    'View full output' that 404s."""
    with pytest.raises(CommandRunOutputError, match="blob_ref"):
        CapturedStream(
            inline_bytes=b"head",
            blob_ref="",
            total_byte_count=200_000,
            truncated=True,
        )


def test_blob_ref_without_truncated_rejected():
    """blob_ref set but truncated=False — inconsistent state."""
    with pytest.raises(CommandRunOutputError):
        CapturedStream(
            inline_bytes=b"x",
            blob_ref="s3://b/k",
            total_byte_count=1,
            truncated=False,
        )


def test_inline_bytes_over_hard_cap_rejected():
    """Even when caller intends inline, refuse beyond
    INLINE_HARD_CAP_BYTES."""
    too_big = b"x" * (INLINE_HARD_CAP_BYTES + 1)
    with pytest.raises(CommandRunOutputError, match="hard cap"):
        CapturedStream(
            inline_bytes=too_big,
            blob_ref="",
            total_byte_count=len(too_big),
            truncated=False,
        )


# ---- plan_capture --------------------------------------------------


def test_plan_capture_inline_only_for_small():
    plan = plan_capture(total_byte_count=1024)
    assert plan["inline_only"] is True
    assert plan["blob_target"] is False
    assert plan["inline_head_bytes"] == 1024


def test_plan_capture_blob_for_large():
    plan = plan_capture(total_byte_count=200_000)
    assert plan["inline_only"] is False
    assert plan["blob_target"] is True
    assert plan["inline_head_bytes"] == INLINE_THRESHOLD_BYTES


# ---- projection ----------------------------------------------------


def _stream(*, inline=b"out", blob_ref="", total=3, truncated=False):
    return CapturedStream(
        inline_bytes=inline,
        blob_ref=blob_ref,
        total_byte_count=total,
        truncated=truncated,
    )


def test_projection_captured_with_timestamp():
    proj = projection_for(
        stdout=_stream(),
        stderr=_stream(inline=b""),
        captured_at_unix=1_700_000_000,
        capture_state=CaptureState.CAPTURED,
    )
    assert proj.capture_state == CaptureState.CAPTURED


def test_projection_captured_without_timestamp_rejected():
    with pytest.raises(CommandRunOutputError, match="captured_at"):
        projection_for(
            stdout=_stream(),
            stderr=_stream(inline=b""),
            captured_at_unix=None,
            capture_state=CaptureState.CAPTURED,
        )


def test_projection_pending_with_timestamp_rejected():
    """Inconsistency: PENDING but captured_at populated."""
    with pytest.raises(CommandRunOutputError):
        projection_for(
            stdout=_stream(),
            stderr=_stream(inline=b""),
            captured_at_unix=1_700_000_000,
            capture_state=CaptureState.PENDING,
        )


def test_projection_pending_without_timestamp_ok():
    proj = projection_for(
        stdout=_stream(inline=b""),
        stderr=_stream(inline=b""),
        captured_at_unix=None,
        capture_state=CaptureState.PENDING,
    )
    assert proj.captured_at_unix is None


# ---- retention ----------------------------------------------------


def test_retention_constants_locked():
    assert DEFAULT_RETENTION_DAYS == 30
    assert MIN_RETENTION_DAYS == 1
    assert MAX_RETENTION_DAYS == 365


def test_validate_retention_below_min():
    with pytest.raises(CommandRunOutputError, match="below minimum"):
        from astrolift_workflows.command_run_output import (
            validate_retention_days,
        )

        validate_retention_days(days=0)


def test_validate_retention_above_max():
    from astrolift_workflows.command_run_output import (
        validate_retention_days,
    )

    with pytest.raises(CommandRunOutputError, match="exceeds"):
        validate_retention_days(days=400)


# ---- past retention -----------------------------------------------


def test_is_past_retention_old_capture():
    candidate = PruneCandidate(
        command_run_id=1,
        captured_at_unix=1_700_000_000,
        stdout_blob_ref="s3://b/k",
        stderr_blob_ref="",
    )
    # 31 days later
    assert (
        is_past_retention(
            candidate=candidate,
            now_unix=1_700_000_000 + 31 * 86400,
            retention_days=30,
        )
        is True
    )


def test_is_past_retention_recent_capture():
    candidate = PruneCandidate(
        command_run_id=1,
        captured_at_unix=1_700_000_000,
        stdout_blob_ref="",
        stderr_blob_ref="",
    )
    # 1 day later
    assert (
        is_past_retention(
            candidate=candidate,
            now_unix=1_700_000_000 + 86400,
            retention_days=30,
        )
        is False
    )


def test_is_past_retention_no_capture():
    """Uncaptured rows aren't pruned by this policy — upstream
    handles based on workflow started_at."""
    candidate = PruneCandidate(
        command_run_id=1,
        captured_at_unix=None,
        stdout_blob_ref="",
        stderr_blob_ref="",
    )
    assert (
        is_past_retention(
            candidate=candidate,
            now_unix=1_700_000_000,
            retention_days=30,
        )
        is False
    )


# ---- safe_to_prune (mirrors #144 invariant) ----------------------


def test_safe_to_prune_requires_archive():
    """No archive_destination → refuse prune. Same invariant as
    #144's safe_to_delete: 'I lost my prod logs' incidents are
    only recoverable when archive bucket has them."""
    candidate = PruneCandidate(
        command_run_id=1,
        captured_at_unix=1_700_000_000,
        stdout_blob_ref="s3://b/k",
        stderr_blob_ref="",
    )
    assert (
        safe_to_prune(
            candidate=candidate,
            archive_destination="",
        )
        is False
    )


def test_safe_to_prune_with_archive():
    candidate = PruneCandidate(
        command_run_id=1,
        captured_at_unix=1_700_000_000,
        stdout_blob_ref="s3://b/k",
        stderr_blob_ref="",
    )
    assert (
        safe_to_prune(
            candidate=candidate,
            archive_destination="s3://archive-bucket/",
        )
        is True
    )


def test_safe_to_prune_inline_only_with_archive():
    """Inline-only rows have no blobs to archive; prune is
    nondestructive at the storage level."""
    candidate = PruneCandidate(
        command_run_id=1,
        captured_at_unix=1_700_000_000,
        stdout_blob_ref="",
        stderr_blob_ref="",
    )
    assert (
        safe_to_prune(
            candidate=candidate,
            archive_destination="s3://archive-bucket/",
        )
        is True
    )


# ---- capture_failure_message --------------------------------------


def test_failure_message_one_attempt():
    msg = capture_failure_message(attempt_count=1)
    assert "1 attempt" in msg


def test_failure_message_multi_attempt():
    msg = capture_failure_message(attempt_count=3)
    assert "3 attempts" in msg


def test_failure_message_zero_attempts_rejected():
    with pytest.raises(CommandRunOutputError):
        capture_failure_message(attempt_count=0)
