"""
Webhook delivery outcome tracking + auto-disable (#161).

The pipeline that actually POSTs the body is separate (each provider
does its own retry shape). This module is the *bookkeeping*: per
delivery attempt, update the subscription's last_response_status +
failure counter, and auto-disable when consecutive failures cross
the threshold.

Why bookkeeping is its own module
---------------------------------

* The delivery loop happens in async code paths (workflows /
  background tasks). Keeping the DB writes in a small pure-ish
  function makes it easy to test the policy without standing up
  the full worker.
* Auto-disable logic is sensitive: setting it too aggressive
  silently kills integrations; too lax leaves zombie webhooks
  hammering dead endpoints. One source of truth for the threshold
  + reason text.
"""

from __future__ import annotations

import dataclasses

from django.utils import timezone

# Spec 17 §6.3: 50 consecutive failures → auto-disable.
AUTO_DISABLE_THRESHOLD = 50


@dataclasses.dataclass(slots=True)
class DeliveryOutcome:
    """Returned by record_delivery_outcome so callers (alert wiring,
    audit log, retry policy) can react without re-querying."""

    succeeded: bool
    failure_count: int
    auto_disabled: bool
    reason: str = ""


def record_delivery_outcome(
    subscription,
    *,
    status_code: int | None,
    succeeded: bool,
) -> DeliveryOutcome:
    """Apply one delivery attempt's outcome to ``subscription``.

    Success: ``failure_count`` resets to 0; the subscription stays
    enabled. (We intentionally do NOT auto-re-enable a previously
    disabled subscription on a single success — operators must
    flip is_active manually so they audit the recovery.)

    Failure: increment ``failure_count``; if the new count crosses
    AUTO_DISABLE_THRESHOLD, flip ``is_active=False`` and stamp
    ``disabled_at`` + ``disabled_reason``.
    """
    fields_to_update: list[str] = ["last_delivery_at", "last_response_status"]
    subscription.last_delivery_at = timezone.now()
    subscription.last_response_status = status_code

    if succeeded:
        if subscription.failure_count > 0:
            subscription.failure_count = 0
            fields_to_update.append("failure_count")
        subscription.save(update_fields=fields_to_update + ["updated_at", "version"])
        return DeliveryOutcome(succeeded=True, failure_count=0, auto_disabled=False)

    subscription.failure_count = (subscription.failure_count or 0) + 1
    fields_to_update.append("failure_count")

    auto_disabled = False
    reason = ""
    if subscription.failure_count >= AUTO_DISABLE_THRESHOLD and subscription.is_active:
        subscription.is_active = False
        subscription.disabled_at = timezone.now()
        reason = (
            f"auto-disabled after {AUTO_DISABLE_THRESHOLD} consecutive "
            f"failures (last status: {status_code})"
        )
        subscription.disabled_reason = reason
        fields_to_update += ["is_active", "disabled_at", "disabled_reason"]
        auto_disabled = True

    subscription.save(update_fields=fields_to_update + ["updated_at", "version"])
    return DeliveryOutcome(
        succeeded=False,
        failure_count=subscription.failure_count,
        auto_disabled=auto_disabled,
        reason=reason,
    )


def reenable(subscription, *, by_user_id: int | None = None) -> None:
    """Operator-initiated re-enable. Resets the failure counter and
    clears ``disabled_at`` / ``disabled_reason``. The caller is
    responsible for issuing a test delivery to confirm the endpoint
    works before traffic resumes — that's a separate workflow.
    """
    if subscription.is_active and subscription.failure_count == 0:
        return  # already in a good state, idempotent
    subscription.is_active = True
    subscription.failure_count = 0
    subscription.disabled_at = None
    subscription.disabled_reason = ""
    subscription.save(
        update_fields=[
            "is_active",
            "failure_count",
            "disabled_at",
            "disabled_reason",
            "updated_at",
            "version",
        ]
    )
