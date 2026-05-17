"""
Webhook delivery outcome tracking + auto-disable (#161, #426).

The pipeline that actually POSTs the body is separate (each provider
does its own retry shape). This module is the *bookkeeping*: per
delivery attempt, update the subscription's last_response_status +
failure counter, persist a ``WebhookDelivery`` history row, and
auto-disable when consecutive failures cross the threshold.

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
* Operator-disabled subscriptions soft-skip — the worker calls
  ``should_skip_delivery`` and never counts a non-attempt as a
  failure, so re-enable doesn't see a poisoned counter.
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


def should_skip_delivery(subscription) -> bool:
    """Operator-disable check. Workers consult this before fan-out
    so a paused subscription's queue silently drains instead of
    racking up failures the operator didn't ask for.

    Soft-deleted rows skip too — covers the race between a delete
    and an in-flight enqueue."""
    if subscription.deleted_at is not None:
        return True
    return not subscription.is_active


def record_delivery_outcome(
    subscription,
    *,
    status_code: int | None,
    succeeded: bool,
    event_type: str = "",
    retry_attempt: int = 1,
    latency_ms: int = 0,
    response_body_excerpt: str = "",
    request_payload_excerpt: str = "",
    error: str = "",
    delivery_id: str = "",
    is_test: bool = False,
) -> DeliveryOutcome:
    """Apply one delivery attempt's outcome to ``subscription``.

    Success: ``failure_count`` resets to 0; the subscription stays
    enabled. (We intentionally do NOT auto-re-enable a previously
    disabled subscription on a single success — operators must
    flip is_active manually so they audit the recovery.)

    Failure: increment ``failure_count``; if the new count crosses
    AUTO_DISABLE_THRESHOLD, flip ``is_active=False`` and stamp
    ``disabled_at`` + ``disabled_reason``.

    A ``WebhookDelivery`` row persists for every call so the
    operator UI's expand-row + history query (#426) has a stable
    audit surface.
    """
    from astrolift_operations.models import WebhookDelivery

    fields_to_update: list[str] = ["last_delivery_at", "last_response_status"]
    now = timezone.now()
    subscription.last_delivery_at = now
    subscription.last_response_status = status_code

    auto_disabled = False
    reason = ""

    if succeeded:
        if subscription.failure_count > 0:
            subscription.failure_count = 0
            fields_to_update.append("failure_count")
    else:
        subscription.failure_count = (subscription.failure_count or 0) + 1
        fields_to_update.append("failure_count")

        if subscription.failure_count >= AUTO_DISABLE_THRESHOLD and subscription.is_active:
            subscription.is_active = False
            subscription.disabled_at = now
            reason = (
                f"auto-disabled after {AUTO_DISABLE_THRESHOLD} consecutive failures "
                f"(last status: {status_code})"
            )
            subscription.disabled_reason = reason
            fields_to_update += ["is_active", "disabled_at", "disabled_reason"]
            auto_disabled = True

    subscription.save(update_fields=fields_to_update + ["updated_at", "version"])

    # History persistence: scoped to the same org as the subscription
    # so per-tenant queries filter cleanly; ``is_test`` lets the UI
    # split probe traffic from real fan-out.
    try:
        WebhookDelivery.objects.create(
            subscription=subscription,
            event_type=event_type or "",
            retry_attempt=max(1, int(retry_attempt or 1)),
            status_code=status_code,
            latency_ms=max(0, int(latency_ms or 0)),
            success=bool(succeeded),
            is_test=bool(is_test),
            request_payload_excerpt=(request_payload_excerpt or "")[:8192],
            response_body_excerpt=(response_body_excerpt or "")[:8192],
            error=(error or "")[:512],
            delivery_id=(delivery_id or "")[:64],
            delivered_at=now,
        )
    except Exception:  # noqa: BLE001 — history write must never break delivery
        import logging

        logging.getLogger(__name__).exception(
            "failed to persist WebhookDelivery row",
            extra={"subscription_id": subscription.pk, "status_code": status_code},
        )

    return DeliveryOutcome(
        succeeded=bool(succeeded),
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
