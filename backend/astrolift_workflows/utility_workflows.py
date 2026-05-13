"""
Utility workflow policies (#130, spec 06 §4.14, §4.15, §4.21, §4.22).

Pure-Python policy. Hosts the decision logic for several utility
workflows whose logic is naturally cross-cutting:

* **DeliverWebhookWorkflow (§4.15)** — HMAC signing rules,
  exponential backoff schedule (max 8 retries, max delay 1h),
  failure classification.
* **NotifyDeploymentWorkflow (§4.14)** — channel fan-out, per-channel
  enablement, deduplication.
* **RotateDeployTokenWorkflow (§4.21)** — old-token grace window,
  plaintext one-shot invariant.
* **InviteUserWorkflow (§4.22)** — invitation TTL, expiry vs
  acceptance state machine.

Several other workflows in the spec already have their own modules
(CommandRun #142, periodic maintenance #144, app migration #67,
github PR dispatch #83); this module covers the remainder.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import StrEnum


class UtilityWorkflowError(ValueError):
    pass


# ---- DeliverWebhookWorkflow ----------------------------------------


WEBHOOK_MAX_RETRIES = 8
"""Spec §4.15."""

WEBHOOK_MAX_DELAY_SECONDS = 60 * 60
"""Spec §4.15: cap retry delay at 1 hour."""

_WEBHOOK_BASE_DELAY_SECONDS = 30
"""First retry fires 30s after initial failure."""


def webhook_retry_delay(*, attempt: int) -> int:
    """Exponential backoff: 30s, 60s, 120s, 240s, 480s, 960s,
    1920s capped at 3600s, 3600s. Caps at WEBHOOK_MAX_DELAY_SECONDS.

    ``attempt`` is 1-indexed; attempt=1 means the first retry
    AFTER the initial failure.
    """
    if attempt < 1:
        raise UtilityWorkflowError("attempt must be >= 1")
    if attempt > WEBHOOK_MAX_RETRIES:
        raise UtilityWorkflowError(f"attempt {attempt} exceeds max retries {WEBHOOK_MAX_RETRIES}")
    delay = _WEBHOOK_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
    return min(delay, WEBHOOK_MAX_DELAY_SECONDS)


class WebhookOutcome(StrEnum):
    """Spec §4.15: classify the response."""

    SUCCESS = "success"
    """2xx response."""

    RETRYABLE_FAILURE = "retryable_failure"
    """5xx, 408, 429, network timeout. Will retry."""

    PERMANENT_FAILURE = "permanent_failure"
    """4xx (other than 408/429). Subscriber misconfigured.
    Don't retry; escalate to subscription-failed state."""


def classify_webhook_response(
    *,
    status_code: int | None,
    timed_out: bool = False,
) -> WebhookOutcome:
    """``status_code=None`` means transport-level failure
    (DNS lookup failed, connection refused, etc.) — always
    retryable."""
    if timed_out or status_code is None:
        return WebhookOutcome.RETRYABLE_FAILURE
    if 200 <= status_code < 300:
        return WebhookOutcome.SUCCESS
    if status_code in (408, 429) or 500 <= status_code < 600:
        return WebhookOutcome.RETRYABLE_FAILURE
    return WebhookOutcome.PERMANENT_FAILURE


def should_mark_subscription_failed(
    *,
    outcome: WebhookOutcome,
    attempt: int,
) -> bool:
    """Spec §4.15: 'on final failure, mark subscription as
    failed.' Final = permanent failure OR retryable failure
    after WEBHOOK_MAX_RETRIES."""
    if outcome == WebhookOutcome.PERMANENT_FAILURE:
        return True
    if outcome == WebhookOutcome.RETRYABLE_FAILURE and attempt >= WEBHOOK_MAX_RETRIES:
        return True
    return False


# ---- NotifyDeploymentWorkflow --------------------------------------


class NotifyChannel(StrEnum):
    """Spec §4.14: configured notification channels."""

    EMAIL = "email"
    SLACK = "slack"
    WEBHOOK = "webhook"
    """Subscriber-defined webhook (uses DeliverWebhookWorkflow)."""


@dataclasses.dataclass(frozen=True, slots=True)
class NotifyChannelConfig:
    """One configured channel."""

    kind: NotifyChannel
    target: str
    """Email address / Slack channel ID / webhook URL.
    Validated by the registry layer; this module trusts."""

    enabled: bool = True


def channels_to_fanout(
    *,
    configured: Sequence[NotifyChannelConfig],
) -> tuple[NotifyChannelConfig, ...]:
    """Filter to enabled channels with non-empty targets.
    Stable order by (kind, target) for determinism."""
    return tuple(
        sorted(
            (c for c in configured if c.enabled and c.target),
            key=lambda c: (c.kind.value, c.target),
        )
    )


# ---- RotateDeployTokenWorkflow -------------------------------------


DEFAULT_DEPLOY_TOKEN_GRACE_SECONDS = 24 * 60 * 60
"""Spec §4.21: old token works for 24h after rotation by default."""

MAX_DEPLOY_TOKEN_GRACE_SECONDS = 7 * 24 * 60 * 60
"""Operator-extended ceiling. Longer windows mean longer-lived
exposed tokens — refuse for security."""


@dataclasses.dataclass(frozen=True, slots=True)
class TokenRotationPlan:
    """Output of rotation planning."""

    old_token_id: int
    new_token_plaintext: str
    """One-shot exposure. NEVER stored persistently — only the
    SHA-256 hash goes into the DB. Exposed via the rotation
    workflow's return value, displayed once in the UI/CLI."""

    grace_period_seconds: int
    revoke_at_unix: int
    """When the old token becomes unusable. Caller schedules a
    delayed activity to revoke at this time."""


def plan_token_rotation(
    *,
    old_token_id: int,
    new_token_plaintext: str,
    now_unix: int,
    grace_seconds: int = DEFAULT_DEPLOY_TOKEN_GRACE_SECONDS,
) -> TokenRotationPlan:
    """Validate inputs + build the rotation plan."""
    if not new_token_plaintext:
        raise UtilityWorkflowError("new_token_plaintext is required")
    if not new_token_plaintext.startswith("alft_"):
        raise UtilityWorkflowError("new_token_plaintext must start with 'alft_' prefix")
    if grace_seconds <= 0:
        raise UtilityWorkflowError(f"grace_seconds must be > 0, got {grace_seconds}")
    if grace_seconds > MAX_DEPLOY_TOKEN_GRACE_SECONDS:
        raise UtilityWorkflowError(
            f"grace_seconds {grace_seconds} exceeds max {MAX_DEPLOY_TOKEN_GRACE_SECONDS} (1 week)"
        )
    return TokenRotationPlan(
        old_token_id=old_token_id,
        new_token_plaintext=new_token_plaintext,
        grace_period_seconds=grace_seconds,
        revoke_at_unix=now_unix + grace_seconds,
    )


# ---- InviteUserWorkflow --------------------------------------------


DEFAULT_INVITATION_TTL_SECONDS = 7 * 24 * 60 * 60
"""Spec §4.22: 7-day default."""

MAX_INVITATION_TTL_SECONDS = 30 * 24 * 60 * 60


class InvitationState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"
    """Operator manually revoked before acceptance."""


@dataclasses.dataclass(frozen=True, slots=True)
class InvitationStatus:
    """Read-side state of an Invitation row, derived for the
    workflow's signal-await loop."""

    state: InvitationState
    accepted_at_unix: int | None
    expires_at_unix: int


def is_invitation_actionable(
    *,
    status: InvitationStatus,
    now_unix: int,
) -> bool:
    """True if the invitation is still in PENDING and not
    past expiry. The workflow signal-await loops while this
    is True; transitions out trigger terminal handling."""
    if status.state != InvitationState.PENDING:
        return False
    if now_unix >= status.expires_at_unix:
        return False
    return True


def invitation_terminal_state(
    *,
    status: InvitationStatus,
    now_unix: int,
) -> InvitationState | None:
    """Decide what state to transition to. Returns None if the
    invitation is still actionable. Otherwise:

    * ACCEPTED → already in terminal state
    * REVOKED → already in terminal state
    * PENDING + past expiry → EXPIRED
    """
    if status.state in (
        InvitationState.ACCEPTED,
        InvitationState.REVOKED,
        InvitationState.EXPIRED,
    ):
        return status.state
    if status.state == InvitationState.PENDING:
        if now_unix >= status.expires_at_unix:
            return InvitationState.EXPIRED
        return None
    raise UtilityWorkflowError(f"unknown invitation state {status.state!r}")


def validate_invitation_ttl(*, ttl_seconds: int) -> None:
    """Refuse 0/negative or absurd values. Operator typo
    defense — 'ttl_days = 70 → ttl_seconds = 70 (a minute!)'."""
    if ttl_seconds <= 0:
        raise UtilityWorkflowError(f"invitation ttl must be positive, got {ttl_seconds}s")
    if ttl_seconds > MAX_INVITATION_TTL_SECONDS:
        raise UtilityWorkflowError(
            f"invitation ttl {ttl_seconds}s exceeds max {MAX_INVITATION_TTL_SECONDS}s (30 days)"
        )
    if ttl_seconds < 60:
        # Less than a minute is meaningless — by the time the
        # email is delivered, it's already expired.
        raise UtilityWorkflowError(
            f"invitation ttl {ttl_seconds}s is too short to be "
            "useful (email delivery alone often exceeds this)"
        )
