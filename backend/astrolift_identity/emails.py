"""
Outbound mail for the identity app.

The functions in this module are *best-effort* — every entry point
swallows transport / template errors and logs them, so a misconfigured
SES (or a Mailpit container that's not running) can never block an
inbound mutation. The corresponding UI affordances (copy-link for
invitations, etc.) remain the durable delivery channel.
"""

from __future__ import annotations

import logging
from datetime import datetime

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _resolve_inviter_name(inviter) -> str:
    """Best name we can render for the invitation 'from' line."""
    if inviter is None:
        return "An administrator"
    full = (inviter.get_full_name() or "").strip() if hasattr(inviter, "get_full_name") else ""
    if full:
        return full
    return getattr(inviter, "username", None) or getattr(inviter, "email", None) or "An administrator"


def send_invitation_email(
    *,
    to_email: str,
    org_name: str,
    inviter,
    accept_url: str,
    expires_at: datetime,
) -> bool:
    """Send the invitation email. Returns True on success, False on
    any failure. Never raises.

    The mutation path treats this as fire-and-forget — the copy-link
    in the UI is the durable channel, so a failed send must not roll
    back the invitation row.
    """
    try:
        context = {
            "inviter_name": _resolve_inviter_name(inviter),
            "org_name": org_name or "Astrolift",
            "accept_url": accept_url,
            "expires_at": expires_at,
        }
        subject = (
            render_to_string("astrolift_identity/emails/invitation_subject.txt", context)
            .strip()
            .splitlines()[0]
        )
        body_text = render_to_string("astrolift_identity/emails/invitation_body.txt", context)
        body_html = render_to_string("astrolift_identity/emails/invitation_body.html", context)

        from_email = getattr(settings, "FROM_EMAIL", None) or "no-reply@example.com"
        message = EmailMultiAlternatives(
            subject=subject,
            body=body_text,
            from_email=from_email,
            to=[to_email],
        )
        message.attach_alternative(body_html, "text/html")
        message.send(fail_silently=False)
        logger.info(
            "invitation email sent to=%s org=%r",
            to_email,
            org_name,
        )
        return True
    except Exception:
        # We deliberately do not surface this — the copy-link UX is
        # the durable channel. Log loudly for ops.
        logger.exception(
            "invitation email send failed to=%s org=%r — copy-link remains the fallback",
            to_email,
            org_name,
        )
        return False


def build_invitation_accept_url(plaintext_token: str, *, base_url: str | None = None) -> str:
    """Compose the absolute URL the invitee clicks to accept.

    The path shape matches the frontend route in
    ``frontend/app/auth/invitation/[token]``.
    """
    root = (base_url or getattr(settings, "APP_BASE_URL", "") or "").rstrip("/")
    return f"{root}/auth/invitation/{plaintext_token}"
