"""
Webhook delivery attempt history policy (#276, follows #37).

Pure-Python policy. The DeliverWebhookWorkflow + delivery
history GraphQL query consult this for:

* **Capture-before-POST invariant** — request_* fields persist
  BEFORE the network call so a hung POST still leaves an
  audit record.
* **Inline-vs-blob threshold** — same shape as #275 for
  request/response bodies.
* **Outcome classification** — bridges #37's WebhookOutcome
  to the persisted Attempt.outcome enum.
* **Replay decision** — operator-initiated replay creates a
  fresh attempt linked to the original event_id.
* **Privacy/scrub** — bodies obey the org's #169 anonymization
  rules.
"""

from __future__ import annotations

import dataclasses
from enum import Enum

from astrolift_workflows.command_run_output import (
    INLINE_HARD_CAP_BYTES,
    INLINE_THRESHOLD_BYTES,
)
from astrolift_workflows.utility_workflows import WebhookOutcome


class WebhookHistoryError(ValueError):
    pass


# ---- attempt outcome ----------------------------------------------


class AttemptOutcome(str, Enum):
    """Spec §6: persisted alongside each attempt."""

    SUCCESS = "success"
    RETRY = "retry"
    """Retryable failure; worker will fire again per #37's
    backoff schedule."""

    PERMANENT_FAILURE = "permanent_failure"
    """Non-retryable; subscription unaffected."""

    IMMEDIATE_DISABLE = "immediate_disable"
    """Specific 4xx classes that mean 'subscriber configured
    wrong; stop trying' (per #37's classify). Subscription
    state moves to disabled in the same transaction."""


# Bridge from #130's webhook outcome (SUCCESS / RETRYABLE_FAILURE
# / PERMANENT_FAILURE) to the per-attempt persisted outcome,
# accounting for max-retries-exhausted promotion.
def map_outcome(
    *,
    classified: WebhookOutcome,
    attempt_number: int,
    max_attempts: int,
) -> AttemptOutcome:
    """The classifier returns RETRYABLE; we promote to
    PERMANENT_FAILURE when this is the last allowed attempt."""
    if classified == WebhookOutcome.SUCCESS:
        return AttemptOutcome.SUCCESS
    if classified == WebhookOutcome.PERMANENT_FAILURE:
        return AttemptOutcome.PERMANENT_FAILURE
    if classified == WebhookOutcome.RETRYABLE_FAILURE:
        if attempt_number >= max_attempts:
            return AttemptOutcome.PERMANENT_FAILURE
        return AttemptOutcome.RETRY
    raise WebhookHistoryError(
        f"unknown classified outcome {classified!r}"
    )


# ---- inline-vs-blob (request + response bodies) -------------------


def should_blob_body(*, byte_count: int) -> bool:
    """Bodies share #275's threshold: <=64KB inline, larger to
    blob. Different from CommandRun's hard cap behavior — a
    subscriber's giant response gets entirely blobbed (we
    don't want a chatty subscriber filling the DB)."""
    if byte_count < 0:
        raise WebhookHistoryError(
            f"byte_count must be non-negative, got {byte_count}"
        )
    return byte_count > INLINE_THRESHOLD_BYTES


@dataclasses.dataclass(frozen=True, slots=True)
class BodyStorage:
    """One body's storage decision."""

    inline_bytes: bytes
    blob_ref: str
    total_byte_count: int

    def __post_init__(self) -> None:
        if self.total_byte_count < 0:
            raise WebhookHistoryError("total_byte_count negative")
        if len(self.inline_bytes) > INLINE_HARD_CAP_BYTES:
            raise WebhookHistoryError(
                "inline_bytes exceeds hard cap"
            )
        if self.blob_ref and len(self.inline_bytes) > 0:
            # Webhook bodies: when blobbed, fully blobbed (no
            # head-inline). Differs from CommandRun where
            # head-inline supports fast UI paint.
            raise WebhookHistoryError(
                "webhook body cannot have both blob_ref and "
                "inline_bytes; pick one"
            )


def plan_body_storage(*, body: bytes) -> dict:
    """Returns instructions for the activity layer.
    - tiny/empty body → inline_only
    - small body → inline_only
    - large body → blob_only (no head-inline; chatty subscribers
      shouldn't bloat the DB)
    """
    n = len(body)
    if not should_blob_body(byte_count=n):
        return {"inline_only": True, "blob_target": False}
    return {"inline_only": False, "blob_target": True}


# ---- attempt lifecycle gates --------------------------------------


class AttemptPhase(str, Enum):
    """Spec §6 capture-before-POST flow."""

    SCHEDULED = "scheduled"
    """Row created with request_* fields populated, before
    network call. Hung POSTs still leave an audit record."""

    ATTEMPTED = "attempted"
    """Network call returned (success or failure). response_*
    fields populated."""

    COMPLETED = "completed"
    """Outcome persisted; row is final."""


_PHASE_TRANSITIONS = {
    AttemptPhase.SCHEDULED: frozenset({AttemptPhase.ATTEMPTED}),
    AttemptPhase.ATTEMPTED: frozenset({AttemptPhase.COMPLETED}),
    AttemptPhase.COMPLETED: frozenset(),
}


def can_transition_phase(
    *,
    current: AttemptPhase,
    target: AttemptPhase,
) -> bool:
    return target in _PHASE_TRANSITIONS.get(current, frozenset())


def must_persist_request_first(*, phase: AttemptPhase) -> bool:
    """Spec §6 invariant: request_* MUST be persisted before
    the POST fires. Worker creates the row in SCHEDULED phase
    BEFORE making the network call."""
    return phase == AttemptPhase.SCHEDULED


# ---- attempt projection -------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class AttemptProjection:
    """Pure-Python mirror of WebhookDeliveryAttempt row."""

    delivery_id: str
    """UUIDv7 — chronological."""

    subscription_id: int
    event_id: int
    attempt_number: int
    """1..MAX_ATTEMPTS=9 (covers initial + 8 retries from #130)."""

    phase: AttemptPhase
    outcome: AttemptOutcome | None
    """None until phase reaches COMPLETED."""

    scheduled_at_unix: int
    attempted_at_unix: int | None
    completed_at_unix: int | None

    request_url: str
    request_body: BodyStorage
    response_status: int | None
    response_body: BodyStorage
    error_message: str
    """Connection-error string when response_status is None."""

    signature_hex: str
    """X-Astrolift-Signature header we sent. Operator copies
    when subscribers want to verify offline."""

    replay_of_attempt_id: int | None = None
    """When this is a replay (#276 scope), points at the
    original attempt_id."""

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise WebhookHistoryError(
                f"attempt_number must be >= 1, got {self.attempt_number}"
            )
        if self.attempt_number > MAX_ATTEMPTS:
            raise WebhookHistoryError(
                f"attempt_number {self.attempt_number} exceeds "
                f"max {MAX_ATTEMPTS}"
            )
        # Phase / timestamp consistency
        if self.phase == AttemptPhase.SCHEDULED:
            if self.attempted_at_unix is not None:
                raise WebhookHistoryError(
                    "SCHEDULED phase but attempted_at set"
                )
            if self.completed_at_unix is not None:
                raise WebhookHistoryError(
                    "SCHEDULED phase but completed_at set"
                )
        if self.phase == AttemptPhase.COMPLETED:
            if self.attempted_at_unix is None:
                raise WebhookHistoryError(
                    "COMPLETED phase but attempted_at missing"
                )
            if self.completed_at_unix is None:
                raise WebhookHistoryError(
                    "COMPLETED phase but completed_at missing"
                )
            if self.outcome is None:
                raise WebhookHistoryError(
                    "COMPLETED phase but outcome missing"
                )
        # Connection-error invariant
        if self.response_status is None and not self.error_message:
            if self.phase == AttemptPhase.COMPLETED:
                # No status code AND no error message AND
                # COMPLETED — should never happen.
                raise WebhookHistoryError(
                    "COMPLETED with no response_status AND no "
                    "error_message; one or the other must be set"
                )


MAX_ATTEMPTS = 9
"""Initial + 8 retries per #130's WEBHOOK_MAX_RETRIES schedule."""


# ---- replay -------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ReplayPlan:
    r"""Output of \`plan_replay\` — what the worker should
    persist when an operator triggers replay."""

    new_delivery_id: str
    original_attempt_id: int
    event_id: int
    subscription_id: int
    attempt_number: int
    """The replay starts a fresh attempt-numbering sequence
    for the event from the operator's perspective; the
    workflow's persisted attempt_number for the original is
    preserved separately. Replay = attempt 1 of a new chain.
    """


def plan_replay(
    *,
    original_attempt: AttemptProjection,
    new_delivery_id: str,
) -> ReplayPlan:
    """Operator-initiated replay. Original attempt's outcome
    isn't touched; replay creates a new attempt linked back."""
    if not new_delivery_id:
        raise WebhookHistoryError("new_delivery_id is required")
    if original_attempt.phase != AttemptPhase.COMPLETED:
        raise WebhookHistoryError(
            f"can't replay attempt in phase "
            f"{original_attempt.phase.value}; wait for COMPLETED"
        )
    return ReplayPlan(
        new_delivery_id=new_delivery_id,
        original_attempt_id=hash(original_attempt.delivery_id) & 0xFFFFFFFF,
        # ^ placeholder for the actual integer attempt_id —
        # caller passes the persisted ID; this stub keeps the
        # type consistent for tests
        event_id=original_attempt.event_id,
        subscription_id=original_attempt.subscription_id,
        attempt_number=1,
    )


# ---- pagination ---------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryQuery:
    r"""Shape of \`webhookSubscription.deliveryHistory(...)\`."""

    subscription_id: int
    event_type_filter: tuple[str, ...] = ()
    """Empty = all event types."""

    since_unix: int | None = None
    limit: int = 50
    cursor: str = ""

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > MAX_HISTORY_LIMIT:
            raise WebhookHistoryError(
                f"limit must be in [1, {MAX_HISTORY_LIMIT}], "
                f"got {self.limit}"
            )


MAX_HISTORY_LIMIT = 200
"""Tuned: smaller than typical paginations because the per-row
payload (with request/response bodies) is heavy. Caller paginates
when they want more."""


# ---- privacy / scrub ----------------------------------------------


def should_scrub_request_body(
    *,
    org_anonymization_active: bool,
    subscription_responses_opted_in: bool,
) -> bool:
    """When the org is mid-deletion (per #169 anonymization),
    cached request bodies obey the same scrub rules."""
    return org_anonymization_active


def should_retain_response_body(
    *,
    org_response_retention_opted_in: bool,
) -> bool:
    """Per spec privacy note: response bodies can leak
    subscriber-side state. Default off; org explicitly opts in
    when they want subscriber response bodies persisted."""
    return org_response_retention_opted_in
