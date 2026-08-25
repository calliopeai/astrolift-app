"""One webhook delivery attempt, as a Temporal activity (#1598).

The retry loop lives in ``DeliverWebhookWorkflow`` rather than in Temporal's
own ``RetryPolicy``, because the schedule is a product decision already
written down: ``webhook_delivery.RETRY_SCHEDULE_SECONDS`` with jitter, and
``classify`` deciding which outcomes are worth retrying at all. A 410 Gone
means the endpoint is deliberately unsubscribing and must not be retried;
Temporal's policy cannot see that without this classification.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger(__name__)


def _deliver_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from astrolift_operations.delivery import record_delivery_outcome
    from astrolift_operations.models import WebhookSubscription
    from astrolift_operations.webhook_delivery import (
        DeliveryAttemptResult,
        DeliveryClassification,
        classify,
        post_webhook,
    )

    subscription_id = int(payload["subscription_id"])
    attempt = int(payload.get("attempt", 1))
    envelope = payload.get("envelope") or {}
    event_type = str(payload.get("event_type") or envelope.get("event_type") or "")

    sub = WebhookSubscription.objects.filter(pk=subscription_id, deleted_at__isnull=True).first()
    if sub is None:
        # Deleted between fan-out and delivery. Not an error: the operator
        # asked for it to stop, and this is it stopping.
        return {"classification": "dropped", "reason": "subscription no longer exists"}
    if not sub.is_active or sub.disabled_at is not None:
        return {"classification": "dropped", "reason": "subscription is not active"}

    secret = _secret_for(sub)
    if secret is None:
        # Signing is not optional: an unsigned delivery is indistinguishable
        # from a forged one at the receiver, so this refuses rather than
        # sending something the subscriber cannot verify.
        log.error("webhook delivery: subscription %s has no usable secret", sub.pk)
        return {"classification": "dropped", "reason": "no signing secret"}

    result = post_webhook(
        url=sub.url,
        secret=secret,
        payload=envelope,
        event_type=event_type,
        format=sub.format,
    )
    verdict = classify(
        DeliveryAttemptResult(
            status_code=result["status_code"],
            connection_error=result.get("error", ""),
        )
    )

    record_delivery_outcome(
        sub,
        status_code=result["status_code"],
        succeeded=verdict is DeliveryClassification.SUCCESS,
        event_type=event_type,
        retry_attempt=attempt,
        latency_ms=result["duration_ms"],
        response_body_excerpt=result["response_body_excerpt"],
    )

    if verdict is DeliveryClassification.IMMEDIATE_DISABLE:
        # 410 Gone and friends: the endpoint is telling us to stop, and
        # retrying it is the thing it explicitly asked us not to do. Disable
        # rather than delete -- the operator should see why it stopped.
        from django.utils import timezone

        sub.is_active = False
        sub.disabled_at = timezone.now()
        sub.save(update_fields=["is_active", "disabled_at", "updated_at", "version"])
        log.warning(
            "webhook delivery: subscription %s disabled after %s from %s",
            sub.pk,
            result["status_code"],
            sub.url,
        )

    return {
        "classification": verdict.value,
        "status_code": result["status_code"],
        "delivery_id": result["delivery_id"],
    }


def _secret_for(subscription) -> bytes | None:
    """The signing secret for this subscription.

    ``secret_hash`` is what the column is called and what the signer is
    handed; the name is the model's, not this module's. Rotation keeps a
    previous value, but a *new* delivery always signs with the current one --
    ``secret_hash_previous`` exists so a receiver mid-rotation can verify an
    in-flight delivery, not so we sign new ones with a retired secret.
    """
    raw = (subscription.secret_hash or "").strip()
    if not raw:
        return None
    return raw.encode("utf-8")


@activity.defn(name="astrolift.webhook.deliver")
async def deliver_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_deliver_sync)(payload)
