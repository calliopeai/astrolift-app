"""Tests for utility workflow policies (#130)."""

from __future__ import annotations

import pytest

from astrolift_workflows.utility_workflows import (
    DEFAULT_DEPLOY_TOKEN_GRACE_SECONDS,
    DEFAULT_INVITATION_TTL_SECONDS,
    MAX_DEPLOY_TOKEN_GRACE_SECONDS,
    MAX_INVITATION_TTL_SECONDS,
    WEBHOOK_MAX_DELAY_SECONDS,
    WEBHOOK_MAX_RETRIES,
    InvitationState,
    InvitationStatus,
    NotifyChannel,
    NotifyChannelConfig,
    UtilityWorkflowError,
    WebhookOutcome,
    channels_to_fanout,
    classify_webhook_response,
    invitation_terminal_state,
    is_invitation_actionable,
    plan_token_rotation,
    should_mark_subscription_failed,
    validate_invitation_ttl,
    webhook_retry_delay,
)

# ---- webhook retry schedule ---------------------------------------


def test_webhook_retry_constants_locked():
    """Spec §4.15: max 8 retries, max delay 1h."""
    assert WEBHOOK_MAX_RETRIES == 8
    assert WEBHOOK_MAX_DELAY_SECONDS == 60 * 60


def test_webhook_retry_delay_exponential():
    """Spec: exponential backoff."""
    assert webhook_retry_delay(attempt=1) == 30
    assert webhook_retry_delay(attempt=2) == 60
    assert webhook_retry_delay(attempt=3) == 120
    assert webhook_retry_delay(attempt=4) == 240
    assert webhook_retry_delay(attempt=5) == 480
    assert webhook_retry_delay(attempt=6) == 960
    assert webhook_retry_delay(attempt=7) == 1920


def test_webhook_retry_delay_caps_at_max():
    """Spec: max delay 1h (3600s)."""
    assert webhook_retry_delay(attempt=8) == 3600


def test_webhook_retry_delay_rejects_invalid_attempt():
    with pytest.raises(UtilityWorkflowError):
        webhook_retry_delay(attempt=0)
    with pytest.raises(UtilityWorkflowError):
        webhook_retry_delay(attempt=9)


# ---- webhook response classification ------------------------------


@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_webhook_2xx_is_success(status):
    assert classify_webhook_response(status_code=status) == WebhookOutcome.SUCCESS


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_webhook_5xx_is_retryable(status):
    assert classify_webhook_response(status_code=status) == WebhookOutcome.RETRYABLE_FAILURE


def test_webhook_408_is_retryable():
    """Request Timeout — server may have just been busy."""
    assert classify_webhook_response(status_code=408) == WebhookOutcome.RETRYABLE_FAILURE


def test_webhook_429_is_retryable():
    """Rate limited — back off and retry."""
    assert classify_webhook_response(status_code=429) == WebhookOutcome.RETRYABLE_FAILURE


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
def test_webhook_4xx_is_permanent(status):
    """Subscriber misconfigured. Don't retry — they need to fix
    on their side."""
    assert classify_webhook_response(status_code=status) == WebhookOutcome.PERMANENT_FAILURE


def test_webhook_timeout_is_retryable():
    assert (
        classify_webhook_response(
            status_code=None,
            timed_out=True,
        )
        == WebhookOutcome.RETRYABLE_FAILURE
    )


def test_webhook_transport_failure_is_retryable():
    """DNS/connection refused = no status_code at all."""
    assert (
        classify_webhook_response(
            status_code=None,
        )
        == WebhookOutcome.RETRYABLE_FAILURE
    )


# ---- subscription-failed escalation -------------------------------


def test_subscription_failed_on_permanent_first_try():
    """4xx from subscriber: instantly escalate; no retries."""
    assert (
        should_mark_subscription_failed(
            outcome=WebhookOutcome.PERMANENT_FAILURE,
            attempt=1,
        )
        is True
    )


def test_subscription_failed_on_max_retries_exhausted():
    assert (
        should_mark_subscription_failed(
            outcome=WebhookOutcome.RETRYABLE_FAILURE,
            attempt=WEBHOOK_MAX_RETRIES,
        )
        is True
    )


def test_subscription_not_failed_when_retries_remain():
    assert (
        should_mark_subscription_failed(
            outcome=WebhookOutcome.RETRYABLE_FAILURE,
            attempt=3,
        )
        is False
    )


def test_subscription_not_failed_on_success():
    assert (
        should_mark_subscription_failed(
            outcome=WebhookOutcome.SUCCESS,
            attempt=1,
        )
        is False
    )


# ---- channel fan-out ----------------------------------------------


def test_channels_to_fanout_filters_disabled():
    configured = (
        NotifyChannelConfig(
            kind=NotifyChannel.EMAIL,
            target="ops@acme.com",
        ),
        NotifyChannelConfig(
            kind=NotifyChannel.SLACK,
            target="#alerts",
            enabled=False,
        ),
    )
    out = channels_to_fanout(configured=configured)
    assert len(out) == 1
    assert out[0].kind == NotifyChannel.EMAIL


def test_channels_to_fanout_filters_empty_target():
    """Empty target = misconfigured; skip silently rather than
    sending to empty string."""
    configured = (
        NotifyChannelConfig(kind=NotifyChannel.EMAIL, target=""),
        NotifyChannelConfig(kind=NotifyChannel.SLACK, target="#alerts"),
    )
    out = channels_to_fanout(configured=configured)
    assert len(out) == 1
    assert out[0].kind == NotifyChannel.SLACK


def test_channels_to_fanout_stable_order():
    """Determinism for tests + audit log."""
    configured = (
        NotifyChannelConfig(kind=NotifyChannel.WEBHOOK, target="https://b.example"),
        NotifyChannelConfig(kind=NotifyChannel.EMAIL, target="ops@acme.com"),
        NotifyChannelConfig(kind=NotifyChannel.WEBHOOK, target="https://a.example"),
    )
    out = channels_to_fanout(configured=configured)
    assert [c.kind for c in out] == [
        NotifyChannel.EMAIL,
        NotifyChannel.WEBHOOK,
        NotifyChannel.WEBHOOK,
    ]
    # Within same kind, by target
    assert out[1].target == "https://a.example"
    assert out[2].target == "https://b.example"


# ---- token rotation -----------------------------------------------


def test_token_rotation_basic():
    plan = plan_token_rotation(
        old_token_id=1,
        new_token_plaintext="alft_deploy_abcdef",
        now_unix=1_700_000_000,
    )
    assert plan.grace_period_seconds == DEFAULT_DEPLOY_TOKEN_GRACE_SECONDS
    assert plan.revoke_at_unix == 1_700_000_000 + DEFAULT_DEPLOY_TOKEN_GRACE_SECONDS
    assert plan.new_token_plaintext == "alft_deploy_abcdef"


def test_token_rotation_requires_alft_prefix():
    """Hash-at-rest tokens use alft_ prefix; refuse anything else
    so we don't accidentally store a non-platform token."""
    with pytest.raises(UtilityWorkflowError, match="alft_"):
        plan_token_rotation(
            old_token_id=1,
            new_token_plaintext="not-our-format",
            now_unix=0,
        )


def test_token_rotation_requires_non_empty():
    with pytest.raises(UtilityWorkflowError):
        plan_token_rotation(
            old_token_id=1,
            new_token_plaintext="",
            now_unix=0,
        )


def test_token_rotation_grace_too_long_rejected():
    """Long grace = long-lived exposed token. Refuse for security."""
    with pytest.raises(UtilityWorkflowError, match="exceeds max"):
        plan_token_rotation(
            old_token_id=1,
            new_token_plaintext="alft_deploy_x",
            now_unix=0,
            grace_seconds=MAX_DEPLOY_TOKEN_GRACE_SECONDS + 1,
        )


def test_token_rotation_zero_grace_rejected():
    with pytest.raises(UtilityWorkflowError):
        plan_token_rotation(
            old_token_id=1,
            new_token_plaintext="alft_deploy_x",
            now_unix=0,
            grace_seconds=0,
        )


# ---- invitation -------------------------------------------------


def test_invitation_pending_within_window_actionable():
    status = InvitationStatus(
        state=InvitationState.PENDING,
        accepted_at_unix=None,
        expires_at_unix=1_700_000_000 + DEFAULT_INVITATION_TTL_SECONDS,
    )
    assert is_invitation_actionable(status=status, now_unix=1_700_000_000) is True


def test_invitation_past_expiry_not_actionable():
    status = InvitationStatus(
        state=InvitationState.PENDING,
        accepted_at_unix=None,
        expires_at_unix=1_700_000_000,
    )
    assert (
        is_invitation_actionable(
            status=status,
            now_unix=1_700_000_001,
        )
        is False
    )


def test_invitation_already_accepted_not_actionable():
    status = InvitationStatus(
        state=InvitationState.ACCEPTED,
        accepted_at_unix=1_700_000_000,
        expires_at_unix=1_700_000_000 + 86400,
    )
    assert (
        is_invitation_actionable(
            status=status,
            now_unix=1_700_000_000,
        )
        is False
    )


def test_invitation_terminal_pending_within_window_returns_none():
    status = InvitationStatus(
        state=InvitationState.PENDING,
        accepted_at_unix=None,
        expires_at_unix=1_700_000_000 + 86400,
    )
    assert (
        invitation_terminal_state(
            status=status,
            now_unix=1_700_000_000,
        )
        is None
    )


def test_invitation_terminal_past_expiry_is_expired():
    status = InvitationStatus(
        state=InvitationState.PENDING,
        accepted_at_unix=None,
        expires_at_unix=1_700_000_000,
    )
    assert (
        invitation_terminal_state(
            status=status,
            now_unix=1_700_000_001,
        )
        == InvitationState.EXPIRED
    )


def test_invitation_terminal_accepted_returns_accepted():
    status = InvitationStatus(
        state=InvitationState.ACCEPTED,
        accepted_at_unix=1_700_000_000,
        expires_at_unix=1_700_000_000 + 86400,
    )
    assert (
        invitation_terminal_state(
            status=status,
            now_unix=1_700_000_001,
        )
        == InvitationState.ACCEPTED
    )


def test_invitation_terminal_revoked_returns_revoked():
    status = InvitationStatus(
        state=InvitationState.REVOKED,
        accepted_at_unix=None,
        expires_at_unix=1_700_000_000 + 86400,
    )
    assert (
        invitation_terminal_state(
            status=status,
            now_unix=1_700_000_001,
        )
        == InvitationState.REVOKED
    )


# ---- TTL validation -----------------------------------------------


def test_validate_invitation_ttl_default_ok():
    validate_invitation_ttl(ttl_seconds=DEFAULT_INVITATION_TTL_SECONDS)


def test_validate_invitation_ttl_rejects_zero():
    with pytest.raises(UtilityWorkflowError, match="positive"):
        validate_invitation_ttl(ttl_seconds=0)


def test_validate_invitation_ttl_rejects_too_short():
    """Email delivery often exceeds 1 minute; refuse trivially
    short TTLs as operator typo (e.g. days as seconds)."""
    with pytest.raises(UtilityWorkflowError, match="too short"):
        validate_invitation_ttl(ttl_seconds=30)


def test_validate_invitation_ttl_rejects_too_long():
    with pytest.raises(UtilityWorkflowError, match="exceeds max"):
        validate_invitation_ttl(
            ttl_seconds=MAX_INVITATION_TTL_SECONDS + 1,
        )
