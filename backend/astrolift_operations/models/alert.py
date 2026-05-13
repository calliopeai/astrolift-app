"""
AlertRule + AlertEvent — alert configuration + firing instances.

The pure-policy types in ``astrolift_operations.alert_rules``
(default templates + delivery routing + suppression) stay as the
runtime decision engine. These models are the persistent shape:

- ``AlertRule`` is what an operator (or the default-rules workflow)
  configures: a target (app | env | workload | global), a
  predicate, a severity, and a list of notification channels.
- ``AlertEvent`` is a firing instance: the moment the rule's
  predicate matched, plus the eventual resolution + ack.

The actual evaluation loop reads ``AlertRule`` rows + emits
``AlertEvent`` rows; the platform's notification fan-out (Slack,
email, webhook) reads from there.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class AlertRule(BaseCoreModel):
    class Severity(models.TextChoices):
        INFO = "info"
        WARN = "warn"
        CRITICAL = "critical"

    class Target(models.TextChoices):
        APP = "app"
        ENV = "env"
        WORKLOAD = "workload"
        GLOBAL = "global"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="alert_rules",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=200)
    target = models.CharField(
        max_length=16,
        choices=Target.choices,
        default=Target.APP,
    )
    target_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=("Slug or guid of the target object. Empty when target=global."),
    )
    predicate = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Free-form predicate definition. Today carries a "
            "'metric' / 'comparator' / 'threshold' / 'duration' "
            "shape that the evaluator translates to PromQL; the "
            "JSON shape lets future predicates land without a "
            "schema migration."
        ),
    )
    severity = models.CharField(
        max_length=16,
        choices=Severity.choices,
        default=Severity.WARN,
    )
    notify_channels = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "List of {kind: 'slack'|'email'|'webhook', ref: ...} "
            "entries. Routing fan-out reads this at fire time."
        ),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="alert_rule_name_unique_active_per_org",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "is_active"],
                name="alert_rule_org_active_idx",
            ),
            models.Index(
                fields=["target", "target_id"],
                name="alert_rule_target_idx",
            ),
        ]


class AlertEvent(BaseCoreModel):
    """A firing instance of an AlertRule.

    ``fired_at`` is when the predicate first matched; ``resolved_at``
    is set when the predicate stops matching (or an operator
    manually resolves). ``acknowledged_at`` is set by the
    acknowledge mutation — distinct from resolve so on-call can
    silence noise without claiming the alert is fixed.
    """

    rule = models.ForeignKey(
        "astrolift_operations.AlertRule",
        related_name="events",
        on_delete=models.CASCADE,
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="alert_events",
        on_delete=models.CASCADE,
    )
    fired_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="acknowledged_alert_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    severity = models.CharField(
        max_length=16,
        choices=AlertRule.Severity.choices,
        default=AlertRule.Severity.WARN,
    )
    summary = models.CharField(max_length=255, blank=True, default="")
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["rule", "-fired_at"],
                name="alert_event_rule_fired_idx",
            ),
            models.Index(
                fields=["organization", "-fired_at"],
                name="alert_event_org_fired_idx",
            ),
        ]
