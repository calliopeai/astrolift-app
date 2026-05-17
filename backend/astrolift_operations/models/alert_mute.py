"""
AlertMute — operator-initiated silence on an AlertRule with a TTL.

Mute lets on-call drop noise during planned maintenance or known
outages without disabling the rule (which would silently stay off
after the incident). The mute carries a TTL so it auto-expires;
no human has to remember to unmute.

The delivery worker that fires alerts consults :func:`is_rule_muted`
before fan-out. A muted rule keeps producing ``AlertEvent`` rows so
the timeline is intact for post-incident review — only the outbound
notification (Slack / email / webhook) is suppressed.

Audit invariants:

* Mute / unmute is recorded via the standard ``@mutation_audit``
  decorator on the mutations.
* ``created_by`` and ``reason`` are required so post-incident
  review can answer "who muted what, and why".
* Soft delete via :class:`BaseCoreModel` is the unmute path —
  unmute soft-deletes the mute row rather than rewriting state.
  The history of past mutes stays queryable.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class AlertMute(BaseCoreModel):
    rule = models.ForeignKey(
        "astrolift_operations.AlertRule",
        related_name="mutes",
        on_delete=models.CASCADE,
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="alert_mutes",
        on_delete=models.CASCADE,
    )
    ttl_until = models.DateTimeField(
        help_text="When the mute auto-expires. The delivery worker treats the rule as live again after this instant.",
    )
    reason = models.TextField(
        blank=True,
        default="",
        help_text="Why the rule was muted. Required by ops convention even though the field is blank-tolerant — the mutation enforces presence.",
    )
    muted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="alert_mutes_created",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="User who issued the mute. Distinct from BaseCoreModel.created_by because that field tracks the row writer; this one is semantically the mute owner.",
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["rule", "ttl_until"],
                name="alert_mute_rule_ttl_idx",
            ),
            models.Index(
                fields=["organization", "ttl_until"],
                name="alert_mute_org_ttl_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"AlertMute rule={self.rule_id} until={self.ttl_until.isoformat()}"
