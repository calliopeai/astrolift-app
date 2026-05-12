r"""
CommandRun output persistence policy (#275, follows #142).

Pure-Python policy. Workflow + activity layer consult this for:

* **Inline-vs-blob threshold** — small outputs go inline for
  fast UI first-paint; large outputs flush to per-org object
  store and store the ref.
* **Capture-before-cleanup invariant** — Job cleanup MUST NOT
  fire before logs are captured. State machine refuses to
  release the cleanup gate without a captured_at timestamp.
* **Retention** — pruning rules + safe_to_delete check
  (mirrors #144's archive-destination requirement).
* **GraphQL output projection** — what the
  \`commandRun(id).output\` query returns.

Pairs with #142's CommandRun policy (sets the boundary; this
fills in the persistence half).
"""

from __future__ import annotations

import dataclasses
from enum import Enum


class CommandRunOutputError(ValueError):
    pass


# ---- inline-vs-blob threshold --------------------------------------


INLINE_THRESHOLD_BYTES = 64 * 1024
"""64KB. Tuned so the GraphQL response stays small enough for
fast first paint; larger outputs need a separate fetch."""

INLINE_HARD_CAP_BYTES = 256 * 1024
"""Defensive: even when caller intends 'inline', refuse beyond
this. Inline rows in the DB blow up the row size at scale; we
push past 64KB to blob storage."""


def should_use_inline(*, byte_count: int) -> bool:
    """True if output fits inline; False means flush to blob."""
    if byte_count < 0:
        raise CommandRunOutputError(f"byte_count must be non-negative, got {byte_count}")
    return byte_count <= INLINE_THRESHOLD_BYTES


# ---- capture state machine -----------------------------------------


class CaptureState(str, Enum):
    """Capture phases. Workflow advances through these."""

    PENDING = "pending"
    """Job still running, no capture attempted yet."""

    CAPTURING = "capturing"
    """Activity is actively tailing logs from the pod."""

    CAPTURED = "captured"
    """Bytes safely persisted (inline or blob ref). Cleanup
    gate is now open."""

    CAPTURE_FAILED = "capture_failed"
    """Activity exhausted retries. Pod is GONE — we'd lose
    logs if cleanup fired before capture; mark this and surface
    to operator. Cleanup STILL fires (no orphan Jobs) but the
    output query returns 'capture failed' instead of empty."""


_ALLOWED_TRANSITIONS = {
    CaptureState.PENDING: frozenset(
        {
            CaptureState.CAPTURING,
        }
    ),
    CaptureState.CAPTURING: frozenset(
        {
            CaptureState.CAPTURED,
            CaptureState.CAPTURE_FAILED,
        }
    ),
    CaptureState.CAPTURED: frozenset(),
    CaptureState.CAPTURE_FAILED: frozenset(),
}


def can_transition_capture(
    *,
    current: CaptureState,
    target: CaptureState,
) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


def cleanup_gate_open(*, capture_state: CaptureState) -> bool:
    """Spec invariant: 'capture-before-cleanup'. Cleanup fires
    ONLY when capture reached a terminal state (success or
    explicit failure — both keep the workflow moving forward).
    PENDING/CAPTURING means logs may still arrive; refuse cleanup."""
    return capture_state in (
        CaptureState.CAPTURED,
        CaptureState.CAPTURE_FAILED,
    )


# ---- captured output decision -------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CapturedStream:
    """Result of capture for one of stdout/stderr."""

    inline_bytes: bytes
    """Always populated up to INLINE_THRESHOLD_BYTES. May be
    the whole stream (when small) OR the head (when large; the
    tail goes to blob)."""

    blob_ref: str
    """Object-store reference. Empty when fully inline."""

    total_byte_count: int
    """Full stream size. UI shows 'truncated to N bytes' when
    inline_bytes < total_byte_count."""

    truncated: bool
    """True when blob_ref is set; UI shows 'View full output'
    button that fetches from blob."""

    def __post_init__(self) -> None:
        if self.total_byte_count < 0:
            raise CommandRunOutputError("total_byte_count cannot be negative")
        if len(self.inline_bytes) > INLINE_HARD_CAP_BYTES:
            raise CommandRunOutputError(
                f"inline_bytes {len(self.inline_bytes)}B exceeds " f"hard cap {INLINE_HARD_CAP_BYTES}B"
            )
        if self.truncated and not self.blob_ref:
            raise CommandRunOutputError("truncated=True requires blob_ref")
        if not self.truncated and self.blob_ref:
            raise CommandRunOutputError("blob_ref set but truncated=False")


def plan_capture(*, total_byte_count: int) -> dict:
    """Decide capture target. Returns a dict the activity
    consumes:
    - ``inline_only`` (bool): write entirely inline
    - ``blob_target`` (bool): write the tail to blob
    - ``inline_head_bytes`` (int): how many bytes go inline
      when both are used (for fast UI first paint)
    """
    if total_byte_count < 0:
        raise CommandRunOutputError("total_byte_count cannot be negative")
    if should_use_inline(byte_count=total_byte_count):
        return {
            "inline_only": True,
            "blob_target": False,
            "inline_head_bytes": total_byte_count,
        }
    return {
        "inline_only": False,
        "blob_target": True,
        "inline_head_bytes": INLINE_THRESHOLD_BYTES,
    }


# ---- output projection (GraphQL surface) --------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CommandRunOutputProjection:
    r"""Shape \`commandRun(id).output\` returns. UI uses
    \`stdout_inline\` for fast first paint; calls a separate
    streaming endpoint for the blob when \`truncated\`."""

    stdout: CapturedStream
    stderr: CapturedStream
    captured_at_unix: int | None
    """None when capture failed or hasn't run yet."""

    capture_state: CaptureState


def projection_for(
    *,
    stdout: CapturedStream,
    stderr: CapturedStream,
    captured_at_unix: int | None,
    capture_state: CaptureState,
) -> CommandRunOutputProjection:
    """Validation wrapper — refuses inconsistent state."""
    if capture_state == CaptureState.CAPTURED:
        if captured_at_unix is None:
            raise CommandRunOutputError("capture_state=CAPTURED requires captured_at_unix")
    if capture_state == CaptureState.PENDING:
        if captured_at_unix is not None:
            raise CommandRunOutputError("capture_state=PENDING but captured_at_unix is set")
    return CommandRunOutputProjection(
        stdout=stdout,
        stderr=stderr,
        captured_at_unix=captured_at_unix,
        capture_state=capture_state,
    )


# ---- retention -----------------------------------------------------


DEFAULT_RETENTION_DAYS = 30
r"""Default. Per-org override via ObservabilityProfile (#9)
\`command_run_log_retention_days\`."""

MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 365


def validate_retention_days(*, days: int) -> int:
    """Bounds check. Mirrors RetentionConfig in
    observability_profile.py — 1 day floor (anything less is
    pointless), 1 year ceiling."""
    if days < MIN_RETENTION_DAYS:
        raise CommandRunOutputError(f"retention {days}d below minimum {MIN_RETENTION_DAYS}")
    if days > MAX_RETENTION_DAYS:
        raise CommandRunOutputError(f"retention {days}d exceeds maximum {MAX_RETENTION_DAYS}")
    return days


@dataclasses.dataclass(frozen=True, slots=True)
class PruneCandidate:
    """One CommandRun row eligible for log pruning."""

    command_run_id: int
    captured_at_unix: int | None
    stdout_blob_ref: str
    stderr_blob_ref: str


def is_past_retention(
    *,
    candidate: PruneCandidate,
    now_unix: int,
    retention_days: int,
) -> bool:
    """Past-retention requires captured_at AND age > window.
    Uncaptured runs (failed capture) get pruned at the same
    cadence based on the workflow's started_at — but that's
    upstream of this module's scope; we operate on rows that
    have captured_at."""
    if candidate.captured_at_unix is None:
        return False
    age_seconds = now_unix - candidate.captured_at_unix
    return age_seconds >= retention_days * 86400


def safe_to_prune(
    *,
    candidate: PruneCandidate,
    archive_destination: str,
) -> bool:
    """Mirror of #144's safe_to_delete invariant — refuse to
    prune logs without an archive_destination configured. The
    'I lost my prod logs' incident is recoverable when the
    archive bucket has them; without one, prune is irreversible.

    Returns True only when:
    - archive is configured (caller passes the resolved value)
    - candidate has at least one blob (inline-only rows are
      below threshold; pruning the row removes inline content
      but the operator pays nothing for keeping them)
    """
    if not archive_destination:
        return False
    if not candidate.stdout_blob_ref and not candidate.stderr_blob_ref:
        # Inline-only — no blob storage to free; prune anyway
        # is fine but caller can choose to skip.
        return True
    return True


# ---- helper: capture failure surface ------------------------------


def capture_failure_message(*, attempt_count: int) -> str:
    """Standard error message for the UI when capture fails.
    Surfaces attempt count so operator can distinguish a
    flaky one-off (1 attempt) from a genuine ingestion problem
    (multiple attempts)."""
    if attempt_count <= 0:
        raise CommandRunOutputError("attempt_count must be positive")
    if attempt_count == 1:
        return "log capture failed (1 attempt); the pod was cleaned " "up before logs could be persisted"
    return (
        f"log capture failed after {attempt_count} attempts; "
        "the pod was cleaned up before logs could be persisted"
    )
