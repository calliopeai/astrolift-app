"""
WebhookSubscription — outbound webhook fan-out.

The platform delivers each event matching ``events`` to the URL,
signing the body with the secret derived from ``secret_hash``. The
delivery loop tracks last status + failure count for backoff.

Rotation: ``secret_hash_previous`` keeps the prior digest valid for
a short grace window (Constance flag
``WEBHOOK_SECRET_ROTATION_GRACE_SECONDS``) so subscribers can roll
out the new secret without dropping deliveries. ``secret_rotated_at``
pins when the rotation happened — the verifier honours the old hash
only until ``secret_rotated_at + grace``.

Format: ``format`` selects an outbound payload shape so subscribers
that expect a vendor-specific envelope (``slack``, ``discord``) get a
ready-to-consume body without a per-subscriber adapter on the
caller's side. ``generic`` (default) is the raw Astrolift event
envelope.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class WebhookSubscription(BaseCoreModel):
    class Format(models.TextChoices):
        GENERIC = "generic", "Generic (Astrolift envelope)"
        SLACK = "slack", "Slack incoming webhook"
        DISCORD = "discord", "Discord webhook"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="webhook_subscriptions",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="webhook_subscriptions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Optional app scoping: when set, the subscription fires only on
    # events from this app and surfaces under the app's UI sub-page;
    # when null the subscription is org-wide as before.
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="webhook_subscriptions",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    url = models.URLField()
    secret_hash = models.CharField(max_length=128)
    # Previous secret digest, valid for the grace window. Blank when
    # no prior rotation has happened. We hold ONE generation back —
    # rotating twice within the window invalidates the oldest hash
    # immediately, which is the desired behaviour (operators rotating
    # twice in an hour are responding to a compromise; the older
    # secret should die).
    secret_hash_previous = models.CharField(max_length=128, blank=True, default="")
    secret_rotated_at = models.DateTimeField(null=True, blank=True)
    events = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    format = models.CharField(
        max_length=16,
        choices=Format.choices,
        default=Format.GENERIC,
    )
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_response_status = models.IntegerField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)
    disabled_at = models.DateTimeField(null=True, blank=True)
    # Free-form reason set by the auto-disable code or operators —
    # surfaces on the UI alongside the disabled badge so re-enable
    # flows can show what tripped.
    disabled_reason = models.CharField(max_length=255, blank=True, default="")

    # Reheal bookkeeping. The daily reheal sweep
    # (``astrolift_workflows.periodic_maintenance``) re-tests disabled
    # subscriptions and re-enables the ones whose endpoint came back.
    # ``last_reheal_attempt_at`` is what the policy's 6-hour backoff is
    # measured from -- without it the sweep would re-probe a dead
    # endpoint on every tick; ``consecutive_failed_reheals`` is what
    # decides the notify-exactly-once threshold. Both are distinct from
    # ``last_delivery_at`` / ``failure_count``, which track real traffic
    # and must not be moved by a probe.
    last_reheal_attempt_at = models.DateTimeField(null=True, blank=True)
    consecutive_failed_reheals = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "is_active"], name="webhook_org_active_idx"),
        ]
