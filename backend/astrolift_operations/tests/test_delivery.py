"""Tests for webhook delivery outcome tracking + auto-disable (#161)."""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization
from astrolift_operations.delivery import (
    AUTO_DISABLE_THRESHOLD,
    record_delivery_outcome,
    reenable,
)
from astrolift_operations.models import WebhookSubscription


pytestmark = pytest.mark.django_db


def _sub(**overrides):
    org = overrides.pop(
        "organization",
        Organization.objects.create(name="Acme", slug="acme"),
    )
    return WebhookSubscription.objects.create(
        organization=org,
        url="https://example.invalid/hook",
        secret_hash="sha256:dummy",
        events=["deployment.*"],
        **overrides,
    )


def test_success_resets_failure_count():
    sub = _sub(failure_count=3)
    out = record_delivery_outcome(sub, status_code=200, succeeded=True)
    assert out.succeeded is True
    assert out.failure_count == 0
    sub.refresh_from_db()
    assert sub.failure_count == 0
    assert sub.last_response_status == 200
    assert sub.last_delivery_at is not None


def test_failure_increments_count_without_disabling():
    sub = _sub(failure_count=10)
    out = record_delivery_outcome(sub, status_code=503, succeeded=False)
    assert out.succeeded is False
    assert out.auto_disabled is False
    assert out.failure_count == 11
    sub.refresh_from_db()
    assert sub.is_active is True


def test_auto_disable_at_threshold():
    """The 50th consecutive failure flips is_active off and stamps a
    machine-readable reason that the UI surfaces."""
    sub = _sub(failure_count=AUTO_DISABLE_THRESHOLD - 1)
    out = record_delivery_outcome(sub, status_code=502, succeeded=False)
    assert out.auto_disabled is True
    assert out.failure_count == AUTO_DISABLE_THRESHOLD
    sub.refresh_from_db()
    assert sub.is_active is False
    assert sub.disabled_at is not None
    assert "consecutive failures" in sub.disabled_reason
    assert "502" in sub.disabled_reason


def test_failures_past_threshold_dont_re_disable():
    """Once disabled, subsequent failures keep counting but don't
    re-stamp ``disabled_at`` — that timestamp pins when the policy
    kicked in for the audit log."""
    sub = _sub(failure_count=AUTO_DISABLE_THRESHOLD)
    record_delivery_outcome(sub, status_code=502, succeeded=False)
    sub.refresh_from_db()
    first_disabled_at = sub.disabled_at

    record_delivery_outcome(sub, status_code=502, succeeded=False)
    sub.refresh_from_db()
    # Counter keeps climbing so reheal workflows can prioritise the
    # most-broken subscriptions, but the policy timestamp is pinned.
    assert sub.failure_count == AUTO_DISABLE_THRESHOLD + 2
    assert sub.disabled_at == first_disabled_at


def test_success_does_not_auto_re_enable():
    """A delivery succeeding after auto-disable doesn't silently
    re-enable the subscription — operators audit the recovery via
    explicit re-enable + test delivery."""
    sub = _sub(failure_count=AUTO_DISABLE_THRESHOLD, is_active=False)
    sub.disabled_at = sub.created_at
    sub.disabled_reason = "auto-disabled"
    sub.save(update_fields=["disabled_at", "disabled_reason"])

    out = record_delivery_outcome(sub, status_code=200, succeeded=True)
    sub.refresh_from_db()
    assert sub.is_active is False  # still disabled
    assert sub.failure_count == 0  # but counter resets
    assert out.succeeded is True


def test_reenable_clears_counters():
    sub = _sub(failure_count=AUTO_DISABLE_THRESHOLD, is_active=False)
    sub.disabled_reason = "auto-disabled after 50 consecutive failures"
    sub.save(update_fields=["disabled_reason"])

    reenable(sub)
    sub.refresh_from_db()
    assert sub.is_active is True
    assert sub.failure_count == 0
    assert sub.disabled_at is None
    assert sub.disabled_reason == ""


def test_reenable_is_idempotent():
    """Calling reenable on a healthy subscription is a no-op — no
    spurious version bumps."""
    sub = _sub(is_active=True, failure_count=0)
    version_before = sub.version
    reenable(sub)
    sub.refresh_from_db()
    assert sub.version == version_before
