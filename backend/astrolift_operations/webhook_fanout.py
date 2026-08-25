"""Route emitted events to the org's webhook subscriptions (#1598).

Everything either side of this existed and nothing joined them.
`core.events` has had a subscriber mechanism (`register_subscriber`,
`_fanout_to_subscribers`) since events landed, and **nothing ever
registered a subscriber**. `webhook_delivery` has signing, headers, a retry
schedule with jitter, outcome classification and an in-flight cap;
`webhook_format` has Slack and Discord adapters; `delivery` has
`record_delivery_outcome`. `WebhookSubscription` rows are creatable through
the API, carry an `events` allow-list, and were never consulted.

So an operator could add a subscription, see it listed, send a test delivery
that worked, and never receive a single real event. The test path is the
tell: `_deliver_test_webhook` bypasses `record_delivery_outcome` **on
purpose**, because a manual test must not touch `failure_count` -- which
means the one live outbound POST in the tree was deliberately the one that
does not look like real delivery.

This module is the join. It is deliberately thin: matching is here, and
everything about *how* to deliver stays where it already was.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def matching_subscriptions(envelope) -> list[Any]:
    """Active subscriptions in the event's org that want this event type.

    Scoping is org-first and explicit. `WebhookSubscription` also carries
    optional `team` and `registered_app` FKs, and a subscription narrowed to
    one app must not receive another app's events -- a webhook is an egress
    channel to a third party, so a wrong match is a cross-tenant disclosure
    rather than a cosmetic bug.

    An event with no organization matches nothing. Platform-level events
    exist (`organization` is nullable on `Event`) and there is no org whose
    subscribers should see them.
    """
    from astrolift_operations.models import WebhookSubscription

    org_id = getattr(envelope, "organization_id", None)
    if not org_id:
        return []

    event_type = str(getattr(envelope, "event_type", "") or "")
    if not event_type:
        return []

    rows = WebhookSubscription.objects.filter(
        organization_id=org_id,
        is_active=True,
        disabled_at__isnull=True,
        deleted_at__isnull=True,
    )

    out = []
    for sub in rows:
        if not _wants(sub, event_type):
            continue
        if sub.team_id and sub.team_id != getattr(envelope, "team_id", None):
            continue
        if sub.registered_app_id and sub.registered_app_id != getattr(envelope, "registered_app_id", None):
            continue
        out.append(sub)
    return out


def _wants(subscription, event_type: str) -> bool:
    """Does this subscription's allow-list cover ``event_type``?

    An empty list means every event, which is what the API's own default
    produces and what an operator who left the field alone expects.

    Supports a trailing ``*`` so `deployment.*` covers `deployment.started`
    and `deployment.succeeded`. Without it a subscriber has to enumerate
    every event type the platform will ever emit, and silently miss the ones
    added after they wrote the list.
    """
    wanted = subscription.events or []
    if not wanted:
        return True
    for pattern in wanted:
        p = str(pattern).strip()
        if not p:
            continue
        if p == event_type or p == "*":
            return True
        if p.endswith("*") and event_type.startswith(p[:-1]):
            return True
    return False


def dispatch(envelope) -> int:
    """Start a delivery workflow per matching subscription. Returns the count.

    Registered as an event subscriber, so this runs inside `Event.emit`, in
    whatever request or activity emitted the event. It therefore does no
    network I/O and never raises: it starts workflows and returns.
    `_fanout_to_subscribers` already swallows exceptions, but relying on that
    would mean an emit-time failure is invisible, so failures are logged
    here with the event type.
    """
    from astrolift_workflows.client import start_workflow

    started = 0
    try:
        subs = matching_subscriptions(envelope)
    except Exception:
        logger.exception(
            "webhook fanout: could not resolve subscriptions for %s",
            getattr(envelope, "event_type", "?"),
        )
        return 0

    for sub in subs:
        try:
            start_workflow(
                "DeliverWebhookWorkflow",
                [
                    {
                        "subscription_id": sub.pk,
                        "event_type": envelope.event_type,
                        "envelope": _serializable(envelope),
                    }
                ],
                workflow_id=f"webhook-{sub.pk}-{getattr(envelope, 'guid', '') or id(envelope)}",
            )
            started += 1
        except Exception:
            # One unreachable Temporal must not stop the other subscriptions,
            # and must never fail the emit that triggered it.
            logger.exception(
                "webhook fanout: could not start delivery for subscription %s",
                sub.pk,
            )
    return started


def _serializable(envelope) -> dict[str, Any]:
    """The envelope as plain JSON for the workflow argument.

    Deliberately not the model instance: a Temporal argument is serialized
    and may be replayed long after the row has changed, so the delivery must
    carry what was true when the event fired.
    """
    import dataclasses

    if dataclasses.is_dataclass(envelope):
        return dataclasses.asdict(envelope)
    return {
        k: getattr(envelope, k, None)
        for k in (
            "event_type",
            "payload",
            "organization_id",
            "team_id",
            "project_id",
            "registered_app_id",
            "resource_kind",
            "resource_id",
            "actor_user_id",
            "request_id",
            "trace_id",
            "severity",
        )
    }
