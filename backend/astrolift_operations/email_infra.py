"""
Platform email infrastructure policy (#135).

Pure-Python policy + pluggable transport contract for the four
transactional email types the control plane sends:

  * deploy approval magic links
  * member invitations
  * deploy failure alerts
  * scheduled job failure alerts

Three pieces:

* **Transport contract** — ``Transport = Callable[[Email], None]``.
  Plugins register one (SES, SendGrid, Postmark, generic SMTP);
  caller picks via ``EMAIL_BACKEND``. Self-hosted installs
  without a configured provider get a refusal with admin
  surfaces; we never silently drop email.
* **Suppression list policy** — addresses that hard-bounce or
  generate complaints land on a suppression list. Subsequent
  sends to those addresses are short-circuited (never re-attempt
  hard-bounced addresses; provider rate-limits us if we do).
* **Template kind enum** — closed vocabulary so the templating
  layer can switch reliably; per-org branding override hooks
  into render time.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from datetime import datetime
from enum import Enum


class EmailKind(str, Enum):
    """Locked vocabulary so the renderer's template registry can
    rely on it."""

    DEPLOY_APPROVAL = "deploy_approval"
    MEMBER_INVITATION = "member_invitation"
    DEPLOY_FAILURE = "deploy_failure"
    SCHEDULED_JOB_FAILURE = "scheduled_job_failure"


class TransportKind(str, Enum):
    AWS_SES = "aws_ses"
    SENDGRID = "sendgrid"
    POSTMARK = "postmark"
    SMTP = "smtp"
    NONE = "none"
    """Self-hosted install without a configured provider. Sends
    refused; admin warning surfaces in the platform UI."""


# Spec gives us four transports; locked-tested.
SUPPORTED_TRANSPORTS: tuple[TransportKind, ...] = (
    TransportKind.AWS_SES,
    TransportKind.SENDGRID,
    TransportKind.POSTMARK,
    TransportKind.SMTP,
)


# RFC 5322 simplified email regex. Stricter rules exist; this
# catches the obvious typos.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
)


class EmailError(ValueError):
    pass


def is_valid_email(addr: str) -> bool:
    """Catches the common typos. Provider-side validation is the
    authoritative check; this is a cheap pre-filter."""
    return bool(_EMAIL_RE.match(addr or ""))


# ---- email payload --------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Email:
    """The shape transports receive."""

    to_address: str
    subject: str
    html_body: str
    plain_body: str
    """Plain-text fallback. RFC 8551 mandates plain alongside HTML
    so screen readers + plain-text MUAs can render."""

    from_address: str
    kind: EmailKind
    """Recorded by the audit log + suppression check; never sent
    in headers."""

    def __post_init__(self) -> None:
        if not is_valid_email(self.to_address):
            raise EmailError(f"to_address {self.to_address!r} not valid")
        if not is_valid_email(self.from_address):
            raise EmailError(f"from_address {self.from_address!r} not valid")
        if not self.subject:
            raise EmailError("subject is required")
        if not self.plain_body:
            raise EmailError(
                "plain_body required (RFC 8551 multipart/alternative)"
            )


# ---- suppression list ----------------------------------------------


class SuppressionReason(str, Enum):
    HARD_BOUNCE = "hard_bounce"
    """Permanent failure (user doesn't exist, mailbox full,
    domain unreachable). Never retry."""

    COMPLAINT = "complaint"
    """Recipient marked the email as spam. Stop sending or risk
    sender reputation."""

    UNSUBSCRIBE = "unsubscribe"
    """Recipient explicitly opted out. Soft-suppress: only
    affects marketing-shaped sends. Transactional emails (deploy
    approvals, security alerts) bypass."""


@dataclasses.dataclass(frozen=True, slots=True)
class SuppressionEntry:
    address: str
    reason: SuppressionReason
    suppressed_at: datetime
    detail: str = ""


# Transactional kinds that BYPASS unsubscribe suppression — they
# carry security or operational signals the user can't opt out of
# while keeping their account active.
_TRANSACTIONAL_KINDS: frozenset[EmailKind] = frozenset({
    EmailKind.DEPLOY_APPROVAL,
})


def is_suppressed(
    *,
    address: str,
    kind: EmailKind,
    suppression_lookup: Callable[[str], SuppressionEntry | None],
) -> bool:
    """Should this address be skipped for a send of this kind?

    HARD_BOUNCE and COMPLAINT always suppress (provider rate-limits
    us if we keep hammering bounced addresses).

    UNSUBSCRIBE suppresses only NON-transactional kinds. Deploy
    approval bypasses — security signal the user can't opt out of."""
    entry = suppression_lookup(address)
    if entry is None:
        return False
    if entry.reason in (SuppressionReason.HARD_BOUNCE, SuppressionReason.COMPLAINT):
        return True
    if entry.reason == SuppressionReason.UNSUBSCRIBE:
        return kind not in _TRANSACTIONAL_KINDS
    return False


# ---- transport registry --------------------------------------------


Transport = Callable[[Email], None]
"""Transport contract. Real implementations:

  - AWS SES (boto3 SES client)
  - SendGrid (sendgrid python sdk)
  - Postmark (postmarker)
  - SMTP (django.core.mail.backends.smtp.EmailBackend)

Caller registers exactly one via ``set_transport``. Failure to
register yields refusal at send time."""


_TRANSPORT: Transport | None = None
_TRANSPORT_KIND: TransportKind = TransportKind.NONE


def set_transport(*, kind: TransportKind, fn: Transport) -> None:
    """Caller (settings module) registers one transport at startup."""
    if kind == TransportKind.NONE:
        raise EmailError(
            "cannot set NONE as a transport; leave unconfigured instead"
        )
    if kind not in SUPPORTED_TRANSPORTS:
        raise EmailError(
            f"unsupported transport {kind!r}; "
            f"supported: {[k.value for k in SUPPORTED_TRANSPORTS]}"
        )
    global _TRANSPORT, _TRANSPORT_KIND
    _TRANSPORT = fn
    _TRANSPORT_KIND = kind


def clear_transport() -> None:
    """Test helper."""
    global _TRANSPORT, _TRANSPORT_KIND
    _TRANSPORT = None
    _TRANSPORT_KIND = TransportKind.NONE


def configured_transport() -> TransportKind:
    """Returns the currently-configured transport kind. Admin UI
    surfaces ``NONE`` as a warning."""
    return _TRANSPORT_KIND


def is_configured() -> bool:
    return _TRANSPORT is not None


def send(
    email: Email,
    *,
    suppression_lookup: Callable[[str], SuppressionEntry | None],
) -> None:
    """Send an email. Suppression check runs first; transport
    not invoked when suppressed.

    Self-hosted install without a configured provider raises
    EmailError so admin surfaces the gap rather than silently
    dropping mail.
    """
    if not is_configured():
        raise EmailError(
            "no email transport configured; admin must set "
            "EMAIL_BACKEND in settings (refusing to silently drop "
            "email)"
        )
    if is_suppressed(
        address=email.to_address,
        kind=email.kind,
        suppression_lookup=suppression_lookup,
    ):
        return  # Quiet skip; suppression is auditable separately

    assert _TRANSPORT is not None
    _TRANSPORT(email)
