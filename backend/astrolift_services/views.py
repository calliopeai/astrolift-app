"""HTTP views for the astrolift_services app.

Today this carries the SNS webhook receiver for SES email events
(#756). The receiver is mounted at ``/app/webhooks/ses-events/`` and
accepts the two SNS POST shapes:

* ``SubscriptionConfirmation`` — fired once when a new SNS subscription
  is created against this endpoint. We auto-confirm by GETting the
  ``SubscribeURL`` so an operator never has to click the email-style
  confirm link out of the AWS console.
* ``Notification`` — every SES event delivered through the configured
  topic. The body's ``Message`` carries a JSON SES event envelope
  (``notificationType``, ``mail``, plus type-specific blobs like
  ``bounce`` / ``complaint`` / ``open``). We map ``notificationType``
  to ``EmailEventKind`` and insert one ``EmailEvent`` per recipient.

The receiver tolerates noise:

* Malformed JSON → 400 (caller is misbehaving; no point retrying)
* Unknown ``notificationType`` → 200 (don't park the SNS retry queue
  on a kind we don't surface — SES emits ``AmazonSesNotificationType``
  values we don't necessarily ingest, e.g. delivery-delay)
* Missing managed-service FK lookup → still insert (FK nullable; the
  resolver will surface unowned events filtered by ``managed_service
  IS NULL`` if anyone wants them, but the default queries scope by
  service so they're invisible until a backfill pairs them up)
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import UTC, datetime
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from astrolift_services.models import EmailEvent, EmailEventKind, ManagedService

logger = logging.getLogger(__name__)

# SNS message types (per the SNS HTTPS subscription spec).
_SUBSCRIPTION_CONFIRMATION = "SubscriptionConfirmation"
_UNSUBSCRIBE_CONFIRMATION = "UnsubscribeConfirmation"
_NOTIFICATION = "Notification"

# Map SES ``notificationType`` (PascalCase per AWS docs) to the
# lowercase token persisted on ``EmailEvent.event_kind``. Unknown kinds
# return ``None`` and the receiver acks them with 200 (SNS retries are
# expensive — a kind we don't ingest is intentionally skipped, not
# rejected).
_KIND_MAP: dict[str, str] = {
    "Send": EmailEventKind.SEND,
    "Delivery": EmailEventKind.DELIVERY,
    "Bounce": EmailEventKind.BOUNCE,
    "Complaint": EmailEventKind.COMPLAINT,
    "Open": EmailEventKind.OPEN,
    "Click": EmailEventKind.CLICK,
}

# SES configuration set names are emitted with this prefix by the
# AWS driver's ``_configuration_set_name_for``; the receiver strips
# the prefix to recover the identity-safe slug it can compare against
# the ManagedService.config.identity lookup.
_CONFIGURATION_SET_PREFIX = "astrolift-"


@csrf_exempt
@require_POST
def ses_events_webhook(request: HttpRequest) -> HttpResponse:
    """Receive SES event notifications delivered via SNS.

    The endpoint is unauthenticated because SNS POSTs over plain HTTP(S)
    without per-request auth — the trust model is that the platform
    only subscribes its own SNS topic to this URL, the topic is
    locked down to the SES account, and we don't expose admin
    affordances here (the only effect is appending an ``EmailEvent``
    row).

    Returns 200 on any well-formed body (including unrecognized kinds)
    so SNS doesn't park the message on its retry queue. Returns 400
    only on parse failure of the outer envelope.
    """
    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponse(status=400)

    if not isinstance(body, dict):
        return HttpResponse(status=400)

    # SNS puts the message type in the ``Type`` header AND the JSON
    # body. We trust the body (the header can be omitted on some SDK
    # paths) and ignore the header.
    msg_type = body.get("Type")

    if msg_type == _SUBSCRIPTION_CONFIRMATION:
        _confirm_subscription(body)
        return HttpResponse(status=200)

    if msg_type == _UNSUBSCRIBE_CONFIRMATION:
        # No-op: SNS sends this when a subscription is removed; the
        # endpoint just acknowledges so the cleanup is logged on the
        # SNS side. Future work could record the unsubscribe in
        # audit, but it's out of scope for #756.
        return HttpResponse(status=200)

    if msg_type == _NOTIFICATION:
        _handle_notification(body)
        return HttpResponse(status=200)

    # Unknown envelope type: 200 to avoid SNS retry storms, but log so
    # an operator notices.
    logger.info("ses-events-webhook: unhandled SNS Type %r", msg_type)
    return HttpResponse(status=200)


def _confirm_subscription(body: dict[str, Any]) -> None:
    """Auto-confirm a new SNS subscription by GETting SubscribeURL.

    AWS's documented confirmation flow is either:
    (a) operator clicks the link in the confirmation email
    (b) the subscriber GETs ``SubscribeURL`` themselves.

    We pick (b) so the platform self-bootstraps when opscode creates
    the topic. The URL is short-lived and AWS-signed; failure is
    logged but never raised — a missed confirmation just means the
    operator can rerun the subscription wiring.
    """
    url = body.get("SubscribeURL", "")
    if not isinstance(url, str) or not url.startswith("https://sns."):
        logger.warning(
            "ses-events-webhook: refusing to confirm subscription with non-SNS URL: %r",
            url[:120],
        )
        return
    try:
        # nosec — SNS confirmation URL is the exact resource we mean
        # to fetch; bandit's S310 warning is about the urllib library
        # being open to attacker-controlled URLs, which doesn't apply
        # here (the URL came from the same AWS API we already trust).
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            status = getattr(resp, "status", None) or resp.getcode()
        logger.info(
            "ses-events-webhook: confirmed SNS subscription (status=%s)",
            status,
        )
    except Exception as exc:  # noqa: BLE001 -- defensive: AWS-side transient
        logger.warning("ses-events-webhook: SNS confirm failed: %s", exc)


def _handle_notification(sns_body: dict[str, Any]) -> None:
    """Parse the SES envelope from the SNS ``Message`` field and insert
    one ``EmailEvent`` row per recipient + event kind."""
    raw_message = sns_body.get("Message")
    if not isinstance(raw_message, str):
        return
    try:
        ses_message = json.loads(raw_message)
    except (json.JSONDecodeError, ValueError):
        logger.warning("ses-events-webhook: SES Message body not JSON")
        return
    if not isinstance(ses_message, dict):
        return

    notification_type = ses_message.get("notificationType") or ses_message.get(
        "eventType",
    )
    if not isinstance(notification_type, str):
        return
    kind = _KIND_MAP.get(notification_type)
    if kind is None:
        return

    mail = ses_message.get("mail") or {}
    if not isinstance(mail, dict):
        mail = {}

    message_id = str(mail.get("messageId") or "")
    if not message_id:
        # SES always populates messageId on notifications; defensive.
        return

    subject = _extract_subject(mail)
    occurred_at = _extract_occurred_at(ses_message, mail)
    managed_service = _resolve_managed_service(mail)
    recipients = _extract_recipients(ses_message, mail, kind)

    rows: list[EmailEvent] = []
    for recipient in recipients:
        rows.append(
            EmailEvent(
                managed_service=managed_service,
                message_id=message_id,
                recipient=recipient,
                subject=subject,
                event_kind=kind,
                metadata=ses_message,
                occurred_at=occurred_at,
            )
        )

    if not rows:
        return

    # Bulk insert. EmailEvent is append-only at the ORM layer
    # (AppendOnlyMixin), but bulk_create skips save() so the mixin's
    # update-guard is satisfied — every row goes through INSERT only.
    try:
        EmailEvent.objects.bulk_create(rows)
    except Exception as exc:  # noqa: BLE001 -- DB-side transient
        logger.warning(
            "ses-events-webhook: bulk_create failed (kind=%s, msg=%s): %s",
            kind,
            message_id,
            exc,
        )


# ----- helpers --------------------------------------------------------


def _extract_subject(mail: dict[str, Any]) -> str:
    """SES carries the subject in ``commonHeaders.subject`` as either a
    bare string OR (older) a list of headers. Pick whichever shape's
    present and coerce to a trimmed string."""
    headers = mail.get("commonHeaders") or {}
    if not isinstance(headers, dict):
        return ""
    subject = headers.get("subject")
    if isinstance(subject, str):
        return subject[:998]
    if isinstance(subject, list) and subject:
        first = subject[0]
        if isinstance(first, str):
            return first[:998]
        if isinstance(first, dict):
            value = first.get("value")
            if isinstance(value, str):
                return value[:998]
    return ""


def _extract_occurred_at(
    ses_message: dict[str, Any],
    mail: dict[str, Any],
) -> datetime:
    """Pull the timestamp from the type-specific blob when present,
    otherwise fall back to ``mail.timestamp``, otherwise to now."""
    # Type-specific timestamps: ``bounce.timestamp``, ``delivery.timestamp``,
    # ``complaint.timestamp``, ``open.timestamp``, ``click.timestamp``,
    # ``send.timestamp`` (the last one is rare; SES often omits it).
    for key in ("bounce", "delivery", "complaint", "open", "click", "send"):
        blob = ses_message.get(key)
        if isinstance(blob, dict):
            raw = blob.get("timestamp")
            parsed = _parse_iso8601(raw)
            if parsed is not None:
                return parsed

    raw = mail.get("timestamp")
    parsed = _parse_iso8601(raw)
    if parsed is not None:
        return parsed

    return datetime.now(UTC)


def _parse_iso8601(raw: Any) -> datetime | None:
    """Parse SES's ISO-8601 timestamps; tolerate trailing ``Z`` (UTC
    designator) which ``fromisoformat`` only learned to accept in 3.11."""
    if not isinstance(raw, str):
        return None
    try:
        cleaned = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _extract_recipients(
    ses_message: dict[str, Any],
    mail: dict[str, Any],
    kind: str,
) -> list[str]:
    """Different SES notification types put the recipient list on
    different fields:

    * Send / Delivery: ``mail.destination``
    * Bounce: ``bounce.bouncedRecipients[].emailAddress``
    * Complaint: ``complaint.complainedRecipients[].emailAddress``
    * Open / Click: ``mail.destination`` (open/click is per-recipient
      but SES only fires the notification once per (message, link), so
      destination is the right surface)

    Returns a list of cleaned, deduplicated addresses; empty list when
    nothing's resolvable (in which case the caller skips the insert)."""
    addresses: list[str] = []

    if kind == EmailEventKind.BOUNCE:
        blob = ses_message.get("bounce") or {}
        if isinstance(blob, dict):
            for entry in blob.get("bouncedRecipients") or []:
                if isinstance(entry, dict):
                    addr = entry.get("emailAddress")
                    if isinstance(addr, str):
                        addresses.append(addr)
    elif kind == EmailEventKind.COMPLAINT:
        blob = ses_message.get("complaint") or {}
        if isinstance(blob, dict):
            for entry in blob.get("complainedRecipients") or []:
                if isinstance(entry, dict):
                    addr = entry.get("emailAddress")
                    if isinstance(addr, str):
                        addresses.append(addr)

    # Fall back to mail.destination for kinds that didn't yield a
    # type-specific list (covers Send / Delivery / Open / Click and
    # also Bounce / Complaint envelopes that came in without their
    # recipient sub-list).
    if not addresses:
        destinations = mail.get("destination")
        if isinstance(destinations, list):
            for entry in destinations:
                if isinstance(entry, str):
                    addresses.append(entry)

    # Dedupe while preserving order; truncate to 320 chars (max RFC
    # 5321 path) per row.
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in addresses:
        addr = raw.strip()[:320]
        if not addr or addr in seen:
            continue
        seen.add(addr)
        cleaned.append(addr)
    return cleaned


def _resolve_managed_service(mail: dict[str, Any]) -> ManagedService | None:
    """Look up the owning ``ManagedService`` from the SES envelope.

    Strategy (each falls through to the next):

    1. ``mail.tags["ses:configuration-set"][0]`` — SES stamps this on
       every notification when the message was sent with a configuration
       set. We strip the platform prefix
       (``astrolift-`` + identity-safe slug) to recover an identity
       fragment matching ``ManagedService.config['identity']``.
    2. ``mail.source`` or ``mail.sourceArn`` — falls back to the sender
       address's domain (for an explicit identity) or the identity ARN
       (for an SES domain identity).

    Returns ``None`` when no service matches; the receiver still
    inserts the row with a null FK so the event is preserved for a
    later backfill.
    """
    tags = mail.get("tags") or {}
    config_set_name = ""
    if isinstance(tags, dict):
        raw = tags.get("ses:configuration-set")
        if isinstance(raw, list) and raw:
            first = raw[0]
            if isinstance(first, str):
                config_set_name = first
        elif isinstance(raw, str):
            config_set_name = raw

    candidate_identities: list[str] = []
    if config_set_name.startswith(_CONFIGURATION_SET_PREFIX):
        candidate_identities.append(config_set_name[len(_CONFIGURATION_SET_PREFIX) :])

    source = mail.get("source")
    if isinstance(source, str) and "@" in source:
        candidate_identities.append(source.split("@", 1)[1])
        candidate_identities.append(source)

    # Each candidate is matched two ways: an exact ``config.identity``
    # equality (the common case), and a slug-equality against the
    # safe-form used in the SES configuration set name.
    for raw_candidate in candidate_identities:
        candidate = raw_candidate.strip()
        if not candidate:
            continue
        svc = (
            ManagedService.objects.filter(
                kind=ManagedService.Kind.EMAIL,
                deleted_at__isnull=True,
                config__identity=candidate,
            )
            .order_by("id")
            .first()
        )
        if svc is not None:
            return svc
        # Slug-form: SES configuration-set names lowercase + replace
        # non-[a-z0-9._-] with '-'. Iterate every active email service
        # and compare its safe-form against the candidate. This is
        # bounded by tenant size (handful of services in practice)
        # and runs only on tag-stamped notifications.
        for svc in ManagedService.objects.filter(
            kind=ManagedService.Kind.EMAIL,
            deleted_at__isnull=True,
        ).only("id", "config"):
            identity_value = (svc.config or {}).get("identity") or ""
            if _safe_slug(identity_value) == _safe_slug(candidate):
                return svc

    return None


def _safe_slug(value: str) -> str:
    """Mirror of ``providers/aws/managed/email_ses._safe`` — kept inline
    to avoid pulling the boto3-heavy provider import path into the
    request handler. The two functions must stay aligned; we cover the
    invariant with a regression test."""
    cleaned = "".join(c if (c.isalnum() or c in "-._") else "-" for c in value.lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


# Defensive: keep the settings name on the public surface so admins
# can ``from astrolift_services import views; views.SES_EVENTS_SNS_TOPIC_ARN``
# in shell sessions when debugging the wiring without rooting through
# config.settings.
def get_configured_topic_arn() -> str:
    return getattr(settings, "SES_EVENTS_SNS_TOPIC_ARN", "") or ""
