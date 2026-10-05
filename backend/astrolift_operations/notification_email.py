"""SES email transport for platform notices (uptime + ops alerts).

Thin wrapper over Django's configured ``EMAIL_BACKEND`` (``django_ses``
in prod -- see :mod:`config.settings`). Callers pass a rendered
subject + HTML/plain body; this attaches the SES *configuration set*
and a per-message *tag* so the SES event stream can attribute bounces
and complaints without parsing the subject line.

The whole path is gated by the ``EMAIL_NOTIFICATIONS`` constance flag
(default off) so a fresh install never emails until an operator turns
it on. Per-user, per-event opt-in still runs upstream in the
dispatcher via :class:`NotificationPreference` on the ``email`` channel.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

log = logging.getLogger(__name__)


def notifications_email_enabled() -> bool:
    """Master gate: the ``EMAIL_NOTIFICATIONS`` constance flag.

    Read at send time (not import time) so an operator toggling it in
    the constance admin takes effect without a redeploy. Any failure
    resolving constance is treated as "off" -- we never send by
    accident when the flag machinery is unavailable.
    """
    try:
        from constance import config

        return bool(config.EMAIL_NOTIFICATIONS)
    except Exception:  # noqa: BLE001 -- flag lookup must never raise into a send
        return False


def build_notice_email(
    *,
    to: Iterable[str],
    subject: str,
    html_body: str,
    text_body: str,
    tag: str = "",
) -> EmailMultiAlternatives:
    """Shared notice composition for ordinary fan-out and exact-channel tests."""
    recipients = [r for r in to if r]

    from_email = getattr(settings, "FROM_EMAIL", None) or "no-reply@astrolift.dev"
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=recipients,
    )
    msg.attach_alternative(html_body, "text/html")

    # django_ses passes these headers straight through to SES so the
    # configuration set's event destinations (bounce/complaint/delivery)
    # capture our deliverability metrics, and MessageTag makes each
    # notice attributable by event type.
    config_set = os.getenv("SES_CONFIGURATION_SET", "")
    if config_set:
        msg.extra_headers["X-SES-CONFIGURATION-SET"] = config_set
    if tag:
        msg.extra_headers["X-SES-MESSAGE-TAGS"] = f"MessageTag={tag}"

    return msg


def send_notice_email(
    *,
    to: Iterable[str],
    subject: str,
    html_body: str,
    text_body: str,
    tag: str = "",
) -> int:
    """Best-effort ordinary notice transport; a send count is not delivery."""
    msg = build_notice_email(to=to, subject=subject, html_body=html_body, text_body=text_body, tag=tag)
    if not msg.recipients():
        return 0

    try:
        return msg.send(fail_silently=False)
    except Exception:  # noqa: BLE001 -- transport failure is audited by the caller
        log.warning("notice email transport failed")
        return 0
