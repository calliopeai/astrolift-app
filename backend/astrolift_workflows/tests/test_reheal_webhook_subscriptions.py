"""The daily webhook reheal sweep (#144, spec 06 §5).

``astrolift_workflows.periodic_maintenance`` has held the reheal policy
since #144 -- the 6-hour backoff, the 2xx-means-re-enable rule and the
notify-exactly-once counter -- while the activity behind
``RehealWebhookSubscriptionsWorkflow`` returned
``WebhookSubscription.objects.count()`` and touched nothing. A
subscription auto-disabled by a transient endpoint outage stayed
disabled until a human noticed.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from astrolift_operations.models import WebhookSubscription
from astrolift_workflows.activities.scheduled import _reheal_webhook_subscriptions_sync

pytestmark = pytest.mark.django_db


def _probe(status_code: int | None):
    def _fake(**_kwargs):
        return {
            "delivered": status_code is not None,
            "status_code": status_code,
            "duration_ms": 12,
            "response_body_excerpt": "",
            "error": "",
            "delivery_id": "abc",
            "timestamp_unix": int(timezone.now().timestamp()),
        }

    return patch(
        "astrolift_operations.schema.mutations.helpers._deliver_test_webhook",
        _fake,
    )


def _disabled_subscription(org, *, hours_ago: int, failed_reheals: int = 0, last_attempt=None):
    return WebhookSubscription.objects.create(
        organization=org,
        url="https://hooks.example.com/astrolift",
        secret_hash="deadbeef",
        events=["deploy.started"],
        is_active=False,
        failure_count=50,
        disabled_at=timezone.now() - timedelta(hours=hours_ago),
        disabled_reason="auto-disabled after 50 consecutive failures",
        consecutive_failed_reheals=failed_reheals,
        last_reheal_attempt_at=last_attempt,
    )


def test_endpoint_that_came_back_is_reenabled(org):
    sub = _disabled_subscription(org, hours_ago=24)

    with _probe(200):
        assert _reheal_webhook_subscriptions_sync() == 1

    sub.refresh_from_db()
    assert sub.is_active is True
    assert sub.disabled_at is None
    assert sub.disabled_reason == ""
    # The live-traffic counter that tripped auto-disable is cleared too,
    # or the next single failure would disable the row again.
    assert sub.failure_count == 0
    assert sub.consecutive_failed_reheals == 0
    assert sub.last_reheal_attempt_at is not None


def test_still_dead_endpoint_stays_disabled_and_counts_the_attempt(org):
    sub = _disabled_subscription(org, hours_ago=24)

    with _probe(502):
        assert _reheal_webhook_subscriptions_sync() == 0

    sub.refresh_from_db()
    assert sub.is_active is False
    assert sub.consecutive_failed_reheals == 1
    assert sub.last_reheal_attempt_at is not None


def test_backoff_skips_a_subscription_probed_recently(org):
    """The policy's 6-hour floor: without it the sweep hammers dead
    endpoints on every daily tick and never records why."""
    sub = _disabled_subscription(
        org,
        hours_ago=48,
        last_attempt=timezone.now() - timedelta(hours=1),
    )
    calls: list[int] = []

    def _fake(**_kwargs):
        calls.append(1)
        return {"status_code": 200, "delivered": True, "duration_ms": 1}

    with patch(
        "astrolift_operations.schema.mutations.helpers._deliver_test_webhook",
        _fake,
    ):
        assert _reheal_webhook_subscriptions_sync() == 0

    assert calls == []
    sub.refresh_from_db()
    assert sub.is_active is False
    assert sub.last_reheal_attempt_at is not None
    assert sub.consecutive_failed_reheals == 0


def test_freshly_disabled_subscription_is_not_probed_at_all(org):
    sub = _disabled_subscription(org, hours_ago=1)

    with _probe(200):
        assert _reheal_webhook_subscriptions_sync() == 0

    sub.refresh_from_db()
    assert sub.is_active is False
    assert sub.last_reheal_attempt_at is None


def test_active_subscriptions_are_left_alone(org):
    sub = WebhookSubscription.objects.create(
        organization=org,
        url="https://hooks.example.com/live",
        secret_hash="deadbeef",
        events=["deploy.started"],
        is_active=True,
    )

    with _probe(500):
        assert _reheal_webhook_subscriptions_sync() == 0

    sub.refresh_from_db()
    assert sub.is_active is True
    assert sub.last_reheal_attempt_at is None


def test_operator_is_notified_once_at_the_threshold(org):
    from astrolift_operations.models import Event

    sub = _disabled_subscription(org, hours_ago=24, failed_reheals=4)

    with _probe(503):
        _reheal_webhook_subscriptions_sync()

    sub.refresh_from_db()
    assert sub.consecutive_failed_reheals == 5
    row = Event.objects.get(event_type="webhook_subscription.reheal_exhausted")
    assert row.payload["subscription_guid"] == str(sub.guid)

    # Sixth failure: past the threshold, so no second notification.
    sub.last_reheal_attempt_at = timezone.now() - timedelta(hours=12)
    sub.save(update_fields=["last_reheal_attempt_at", "updated_at", "version"])
    with _probe(503):
        _reheal_webhook_subscriptions_sync()

    sub.refresh_from_db()
    assert sub.consecutive_failed_reheals == 6
    assert Event.objects.filter(event_type="webhook_subscription.reheal_exhausted").count() == 1


def test_unsendable_probe_counts_as_a_failure(org):
    sub = _disabled_subscription(org, hours_ago=24)

    def _boom(**_kwargs):
        raise OSError("dns failure")

    with patch(
        "astrolift_operations.schema.mutations.helpers._deliver_test_webhook",
        _boom,
    ):
        assert _reheal_webhook_subscriptions_sync() == 0

    sub.refresh_from_db()
    assert sub.is_active is False
    assert sub.consecutive_failed_reheals == 1
