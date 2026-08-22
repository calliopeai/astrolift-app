"""
NotificationPreference — per-user opt-in/out for a single
(channel, event_kind) tuple (#476 §F, #499 §E).

Default behaviour (no row) follows ``DEFAULT_PREFERENCES`` below:
security-critical events default ON, noisier audit-style events
default OFF. The dispatcher consults this table on every fan-out so
operators can flip preferences without redeploying.

One row per (user, channel, event_kind). ``channel="push"`` is
today's only channel; the column is reserved for future email /
slack / webhook channels reusing the same shape.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class NotificationChannel(models.TextChoices):
    """Delivery channel a preference applies to.

    ``PUSH`` covers FCM / APNs / web-push — anything going through
    the NotificationDriver. Email lives on a parallel system today;
    we model it here so future migrations consolidate cleanly.
    """

    PUSH = "push", "Mobile push"
    EMAIL = "email", "Email"
    WEBHOOK = "webhook", "Webhook"


# Default-on for security-critical events that the user wants to
# know about immediately; default-off for noisy operational chatter
# that an operator would rather subscribe to deliberately. The
# dispatcher consults ``DEFAULT_PREFERENCES`` when no row exists so
# a fresh install doesn't require seed data.
DEFAULT_PREFERENCES: dict[tuple[str, str], bool] = {
    # ---- #476 ops events -------------------------------------------
    ("push", "deploy.approved"): True,
    ("push", "deploy.rejected"): True,
    ("push", "deploy.failed"): True,
    ("push", "secret.revealed"): True,
    ("push", "alert.fired"): True,
    ("push", "cluster.bootstrap_failed"): True,
    ("push", "app.deregister_pending"): True,
    # ---- uptime: an app going unreachable is a page-me event ----------
    ("push", "app.down"): True,
    ("push", "app.recovered"): True,
    # ---- certs (#155): a lapsing cert breaks TLS for everyone ------
    ("push", "domain.cert_expiring"): True,
    ("push", "domain.cert_renewal_failed"): True,
    # ---- #499 new-session ------------------------------------------
    # Mobile/web new sessions are security-critical → ON.
    # CLI sessions are a noisy bootstrap signal → OFF by default so
    # the user doesn't get a push every time their CI re-auths.
    ("push", "auth.session.created.web"): True,
    ("push", "auth.session.created.mobile"): True,
    ("push", "auth.session.created.cli"): False,
    ("push", "auth.session.created.api_token"): False,
    ("push", "auth.session.created.browser_extension"): True,
    # Fallback when the dispatcher can't infer client_kind.
    ("push", "auth.session.created"): True,
    # ---- email channel --------------------------------------------
    # Mirrors the page-me operational events onto email for an
    # out-of-band notice. The whole email leg is master-gated by the
    # EMAIL_NOTIFICATIONS constance flag (off by default), so these
    # defaults only take effect once an operator enables email; a
    # user can still mute any of them per-event. Noisy session events
    # are intentionally push-only.
    ("email", "app.down"): True,
    ("email", "app.recovered"): True,
    ("email", "domain.cert_expiring"): True,
    ("email", "domain.cert_renewal_failed"): True,
    ("email", "deploy.failed"): True,
    ("email", "cluster.bootstrap_failed"): True,
    ("email", "secret.revealed"): True,
    ("email", "app.deregister_pending"): True,
}


def default_enabled(*, channel: str, event_kind: str) -> bool:
    """Default for a (channel, event_kind) when no DB row exists.

    Unknown event kinds default to ``False`` — silence on a
    misspelled event type is better than spamming everyone with a
    push for a payload the user has never seen.
    """
    return DEFAULT_PREFERENCES.get((channel, event_kind), False)


class NotificationPreference(BaseCoreModel):
    """One opt-in/out row for a user × channel × event_kind."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="notification_preferences",
        on_delete=models.CASCADE,
    )
    channel = models.CharField(max_length=16, choices=NotificationChannel.choices)
    event_kind = models.CharField(max_length=64)
    enabled = models.BooleanField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "channel", "event_kind"),
                condition=models.Q(deleted_at__isnull=True),
                name="notif_pref_user_chan_kind_live_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "channel"], name="notif_pref_user_chan_idx"),
        ]

    def __str__(self) -> str:
        return (
            f"NotificationPreference(user_id={self.user_id}, {self.channel}/{self.event_kind}={self.enabled})"
        )


def is_enabled(*, user_id: int, channel: str, event_kind: str) -> bool:
    """Resolve the effective preference for a (user, channel, kind).

    Looks up an explicit row first; falls back to
    :func:`default_enabled` when none exists. Soft-deleted rows are
    skipped (treated as if absent) so a user who once opted out and
    then deleted the preference reverts to the default.
    """
    row = (
        NotificationPreference.objects.filter(
            user_id=user_id,
            channel=channel,
            event_kind=event_kind,
        )
        .order_by("-updated_at")
        .first()
    )
    if row is not None:
        return row.enabled
    return default_enabled(channel=channel, event_kind=event_kind)
