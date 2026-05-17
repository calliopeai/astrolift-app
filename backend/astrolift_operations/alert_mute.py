"""
Alert mute helpers — runtime predicate for the alert delivery loop.

Mutes are stored in :class:`astrolift_operations.AlertMute`. The
delivery worker that fans alerts out to Slack/email/webhook calls
:func:`is_rule_muted` for each rule before invoking the channel
drivers. A muted rule still produces an ``AlertEvent`` row so the
incident timeline stays intact for post-incident review; only the
outbound notification is suppressed.

Mute semantics:

* ``ttl_until`` is the auto-unmute instant.
* Soft-deleted mute rows are treated as inactive (operators can
  unmute early by issuing the ``unmuteAlertRule`` mutation, which
  soft-deletes the row).
* If multiple non-expired mute rows exist for one rule (operator
  re-mutes on top of an existing one), the longest-lived one wins
  and ``active_mute_for_rule`` returns it. This is what surfaces in
  the UI's "muted until" badge.
"""

from __future__ import annotations

from django.utils import timezone

from astrolift_operations.models import AlertMute, AlertRule


def active_mute_for_rule(rule: AlertRule) -> AlertMute | None:
    """Return the longest-lived active mute on ``rule``, or ``None``.

    "Active" means: not soft-deleted, ``ttl_until`` in the future
    relative to the current wall clock. The longest-lived mute wins
    when more than one exists so the UI shows the latest commitment
    rather than an about-to-expire holdover.
    """
    now = timezone.now()
    return (
        AlertMute.objects.filter(
            rule=rule,
            deleted_at__isnull=True,
            ttl_until__gt=now,
        )
        .order_by("-ttl_until")
        .first()
    )


def is_rule_muted(rule: AlertRule) -> bool:
    """``True`` if the rule has any active (non-expired, non-deleted)
    mute. The delivery worker uses this to skip channel fan-out.
    Cheap query; safe to call in the firing-evaluation hot path."""
    return active_mute_for_rule(rule) is not None
