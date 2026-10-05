"""Current original-caller install SMTP diagnostics; no app or transport fallback."""

import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from astrolift_identity import abac
from astrolift_identity.models import Member, Organization
from astrolift_operations.install_alert_smtp import AlertMailUnavailable, mailbox
from astrolift_operations.models import DEFAULT_PREFERENCES, InstallAlertMailTest, is_enabled
from astrolift_operations.notification_email import build_notice_email, notifications_email_enabled
from astrolift_operations.scopes import org_scope
from core.current_credential import current_dispatch_credential
from core.current_session import fresh_authenticated_session
from core.permissions import Permission, PermissionDenied, check_platform_operator, require_permission
from core.tenancy import get_current_tenant

GATE = Permission.ORG_UPDATE
EVENTS = frozenset(kind for channel, kind in DEFAULT_PREFERENCES if channel == "email")


@require_permission(GATE, scope=org_scope(GATE))
def _authorized(info):
    return True


def admitted(info):
    from astrolift_identity.api_tokens import get_current_api_token

    tenant = get_current_tenant()
    request = info.context.request
    if tenant is None or tenant.actor_user_id != getattr(request.user, "pk", None):
        raise PermissionDenied(GATE, None, "Current organization actor is required.")
    with current_dispatch_credential(GATE):
        actor = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
        org = Organization.objects.filter(pk=tenant.organization_id, deleted_at__isnull=True).first()
        if (
            actor is None
            or org is None
            or not Member.objects.filter(
                user=actor, scope_kind="ORG", scope_id=org.pk, is_active=True, deleted_at__isnull=True
            ).exists()
        ):
            raise PermissionDenied(GATE, None, "Current organization membership is required.")
        check_platform_operator(actor, gate=GATE)
        token = get_current_api_token()
        session = (
            fresh_authenticated_session(request, actor_user_id=actor.pk, permission=GATE)
            if token is None
            else {}
        )
        attrs = abac.attributes_from_request(
            SimpleNamespace(session=session, META=request.META, user=actor, _api_token=token), actor.pk
        )
        with abac.request_attributes(attrs):
            _authorized(info)
        return org, actor


@dataclass(frozen=True)
class MailSource:
    host: str
    port: int
    sender: str
    recipient: str
    use_tls: bool
    use_ssl: bool
    username: str = field(repr=False)
    password: str = field(repr=False)
    ssl_keyfile: str | None = field(repr=False)
    ssl_certfile: str | None = field(repr=False)
    fingerprint: str

    def backend_kwargs(self):
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "use_tls": self.use_tls,
            "use_ssl": self.use_ssl,
            "ssl_keyfile": self.ssl_keyfile,
            "ssl_certfile": self.ssl_certfile,
        }


def observed_source(actor, event_kind):
    if event_kind not in EVENTS:
        raise AlertMailUnavailable("ALERT_MAIL_EVENT_UNSUPPORTED")
    if not notifications_email_enabled():
        raise AlertMailUnavailable("ALERT_MAIL_DISABLED")
    if not is_enabled(user_id=actor.pk, channel="email", event_kind=event_kind):
        raise AlertMailUnavailable("ALERT_MAIL_PREFERENCE_DISABLED")
    if settings.EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
        raise AlertMailUnavailable("ALERT_MAIL_TRANSPORT_UNSUPPORTED")
    tls, implicit = settings.EMAIL_USE_TLS, settings.EMAIL_USE_SSL
    if type(tls) is not bool or type(implicit) is not bool or tls == implicit:
        raise AlertMailUnavailable("ALERT_MAIL_VERIFIED_TLS_REQUIRED")
    sender, recipient = getattr(settings, "FROM_EMAIL", ""), actor.email
    if not mailbox(sender) or not mailbox(recipient):
        raise AlertMailUnavailable("ALERT_MAIL_MAILBOX_UNAVAILABLE")
    host, port = settings.EMAIL_HOST, settings.EMAIL_PORT
    if not isinstance(host, str) or len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9.\-]+", host):
        raise AlertMailUnavailable("ALERT_MAIL_SOURCE_UNAVAILABLE")
    if type(port) is not int or not 1 <= port <= 65535:
        raise AlertMailUnavailable("ALERT_MAIL_SOURCE_UNAVAILABLE")
    username, password = settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD
    keyfile, certfile = settings.EMAIL_SSL_KEYFILE, settings.EMAIL_SSL_CERTFILE
    if (
        not isinstance(username, str)
        or not isinstance(password, str)
        or len(username) > 1024
        or len(password) > 16384
        or any(
            value is not None and (not isinstance(value, str) or len(value) > 4096)
            for value in (keyfile, certfile)
        )
    ):
        raise AlertMailUnavailable("ALERT_MAIL_SOURCE_UNAVAILABLE")
    fields = {
        "host": host,
        "port": port,
        "sender": sender,
        "recipient": recipient,
        "use_tls": tls,
        "use_ssl": implicit,
        "username": username,
        "password": password,
        "ssl_keyfile": keyfile,
        "ssl_certfile": certfile,
    }
    canonical = json.dumps({"backend": settings.EMAIL_BACKEND, "event": event_kind, **fields}, sort_keys=True)
    fingerprint = salted_hmac(
        "astrolift.install-alert-mail.source.v1", canonical, algorithm="sha256"
    ).hexdigest()
    return MailSource(**fields, fingerprint=fingerprint)


def current_source(info, event_kind, expected):
    org, actor = admitted(info)
    try:
        source = observed_source(actor, event_kind)
    except AlertMailUnavailable:
        raise AlertMailUnavailable("ALERT_MAIL_SOURCE_CHANGED") from None
    if source.fingerprint != expected:
        raise AlertMailUnavailable("ALERT_MAIL_SOURCE_CHANGED")
    return org, actor


def _charge(org, actor):
    minute = int(timezone.now().timestamp() // 60)
    for suffix, maximum in ((f"actor:{actor.pk}", 3), ("org", 10)):
        key = f"install-alert-mail:v1:{org.pk}:{minute}:{suffix}"
        try:
            cache.add(key, 0, 120)
            if cache.incr(key) > maximum:
                raise AlertMailUnavailable("ALERT_MAIL_RATE_LIMITED")
        except AlertMailUnavailable:
            raise
        except Exception:
            raise AlertMailUnavailable("ALERT_MAIL_ADMISSION_UNAVAILABLE") from None


def send_test(info, *, request_id, expected_source, event_kind):
    from astrolift_operations.install_alert_smtp import send_notice

    org, actor = admitted(info)
    try:
        request_id = UUID(str(request_id))
    except (TypeError, ValueError, AttributeError):
        raise AlertMailUnavailable("ALERT_MAIL_REQUEST_INVALID") from None
    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=org.pk)
        org, actor = admitted(info)
        prior = InstallAlertMailTest._base_manager.filter(organization=org, request_id=request_id).first()
        if prior:
            if prior.deleted_at or prior.requester_id != actor.pk or prior.event_kind != event_kind:
                raise AlertMailUnavailable("ALERT_MAIL_INTENT_CONFLICT")
            if expected_source != prior.source_sha256:
                raise AlertMailUnavailable("ALERT_MAIL_SOURCE_CHANGED")
            current_source(info, event_kind, prior.source_sha256)
            return prior
        source = observed_source(actor, event_kind)
        if source.fingerprint != expected_source:
            raise AlertMailUnavailable("ALERT_MAIL_SOURCE_CHANGED")
        _charge(org, actor)
        row = InstallAlertMailTest.objects.create(
            organization=org,
            requester=actor,
            request_id=request_id,
            event_kind=event_kind,
            source_sha256=source.fingerprint,
            sender=source.sender,
            recipient=source.recipient,
        )

    def current():
        current_source(info, event_kind, source.fingerprint)

    def before_data():
        with transaction.atomic():
            current()
            locked = InstallAlertMailTest.objects.select_for_update().get(pk=row.pk)
            if locked.status != "reserved" or locked.source_sha256 != source.fingerprint:
                raise AlertMailUnavailable("ALERT_MAIL_INTENT_CHANGED")
            locked.status = "sent"
            locked.save(update_fields=["status", "updated_at", "version"])

    message = build_notice_email(
        to=[row.recipient],
        subject="[Astrolift] Install alert email test",
        text_body=f"Explicit install alert-channel test. Correlation ID: {row.guid}. Transport acceptance is not recipient delivery.",
        html_body=f"<p>Explicit install alert-channel test.</p><p>Correlation ID: {row.guid}.</p><p>Transport acceptance is not recipient delivery.</p>",
        tag=event_kind,
    )
    message.extra_headers["Message-ID"] = f"<{row.guid}@astrolift.invalid>"
    try:
        send_notice(message, source=source, checkpoint=current, before_data=before_data)
    except (AlertMailUnavailable, PermissionDenied) as exc:
        row.refresh_from_db()
        known_rejection = isinstance(exc, AlertMailUnavailable) and str(exc) == "ALERT_MAIL_DATA_REJECTED"
        row.status = "failed" if row.status == "reserved" or known_rejection else "unknown"
        row.reason_code = (
            str(exc) if isinstance(exc, AlertMailUnavailable) else "ALERT_MAIL_AUTHORITY_WITHDRAWN"
        )
        row.save(update_fields=["status", "reason_code", "updated_at", "version"])
        current()
        return row
    row.refresh_from_db()
    row.status = "accepted"
    row.accepted_at = timezone.now()
    row.reason_code = "SMTP_ACCEPTED_DELIVERY_UNOBSERVED"
    row.save(update_fields=["status", "accepted_at", "reason_code", "updated_at", "version"])
    current()
    return row


def history(info, *, event_kind, after, limit):
    from astrolift_graphql.pagination import keyset_page

    org, actor = admitted(info)
    page = keyset_page(
        InstallAlertMailTest.objects.filter(
            organization=org, requester=actor, event_kind=event_kind
        ).order_by("-created_at", "-pk"),
        cursor=after,
        limit=limit,
        default_limit=25,
        max_limit=50,
        cursor_scope=f"install-alert-mail:v1:{org.guid}:{actor.pk}:{event_kind}",
    )
    admitted(info)
    return page
