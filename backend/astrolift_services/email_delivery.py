"""Current-source mail diagnostics, durable send intent and authenticated feedback."""

import hashlib
import json
import re
from dataclasses import asdict, replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from astrolift_identity import abac
from astrolift_identity.api_tokens import get_current_api_token, session_may_act_in, with_active_org_member
from astrolift_identity.models import ApiToken
from astrolift_identity.operation_context import managed_service_operation
from astrolift_services.models import EmailDeliveryObservation, EmailDeliveryTest, ManagedService
from astrolift_services.scopes import live_managed_services, managed_service_scope_by_guid
from core.current_credential import current_dispatch_credential
from core.current_session import fresh_authenticated_session
from core.permissions import Permission, PermissionDenied, require_permission
from core.tenancy import get_current_tenant

WRITE = (Permission.APP_UPDATE, Permission.MANAGED_SERVICE_UPDATE)
READ = (Permission.APP_READ,)


class EmailTestUnavailable(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@require_permission(
    *WRITE,
    scope=managed_service_scope_by_guid("service_id", permissions=WRITE),
    operation=managed_service_operation("service_id"),
)
def _write_source(info, service_id):
    row = (
        live_managed_services(ManagedService.objects.filter(guid=str(service_id)))
        .select_related(
            "registered_app__organization",
            "app_environment__tenant_cluster__provider_plugin",
            "app_environment__registered_app",
            "tenant_cluster__provider_plugin",
        )
        .first()
    )
    if row is None or row.kind != "email":
        raise EmailTestUnavailable("EMAIL_SERVICE_UNAVAILABLE")
    if row.status != "active" or row.operation_started_at is not None and row.operation_completed_at is None:
        raise EmailTestUnavailable("EMAIL_SERVICE_NOT_READY")
    if row.registered_app_id is None or row.app_environment_id is None:
        raise EmailTestUnavailable("APP_EMAIL_BINDING_REQUIRED")
    return row


@require_permission(
    *READ,
    scope=managed_service_scope_by_guid("service_id", permissions=READ),
    operation=managed_service_operation("service_id"),
)
def _read_tests(info, service_id):
    service = live_managed_services(ManagedService.objects.filter(guid=str(service_id), kind="email")).first()
    tenant = get_current_tenant()
    if service is None or service.registered_app_id is None:
        raise EmailTestUnavailable("EMAIL_SERVICE_UNAVAILABLE")
    return (
        EmailDeliveryTest.objects.select_related("managed_service")
        .filter(
            managed_service=service,
            organization_id=tenant.organization_id,
        )
        .order_by("-created_at", "-pk")
    )


def _fresh(info, service_id, *, writing=True):
    tenant = get_current_tenant()
    request = info.context.request
    if tenant is None or tenant.actor_user_id is None or request.user.pk != tenant.actor_user_id:
        raise PermissionDenied(WRITE[0], None, "Current organization actor is required.")
    with current_dispatch_credential(WRITE[0] if writing else READ[0]):
        from django.contrib.auth import get_user_model

        actor = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
        if actor is None or not session_may_act_in(actor, tenant.organization_id):
            raise PermissionDenied(WRITE[0], None, "Current organization membership is required.")
        token = get_current_api_token()
        if token is None:
            store = fresh_authenticated_session(
                request, actor_user_id=tenant.actor_user_id, permission=WRITE[0] if writing else READ[0]
            )
        elif not with_active_org_member(
            ApiToken.objects.filter(pk=token.pk),
            user="user",
            organization="organization",
        ).exists():
            raise PermissionDenied(WRITE[0], None, "Current credential membership is unavailable.")
        else:
            store = {}
        attrs = abac.attributes_from_request(
            SimpleNamespace(session=store, META=request.META, user=actor, _api_token=token),
            tenant.actor_user_id,
        )
        with abac.request_attributes(attrs):
            return (_write_source if writing else _read_tests)(info, service_id)


def source(service):
    from core.cluster_credentials import credential_for_cluster

    cluster = service.app_environment.tenant_cluster
    provider = cluster.provider_plugin
    if provider.slug != "aws" or not provider.is_enabled or service.variant != "ses":
        raise EmailTestUnavailable("EXACT_TEST_TRANSPORT_UNSUPPORTED")
    if service.applied_config is None or not service.backend_ref.startswith("email/"):
        raise EmailTestUnavailable("APPLIED_EMAIL_BINDING_REQUIRED")
    pc = cluster.provider_config if isinstance(cluster.provider_config, dict) else {}
    ac = cluster.auth_config if isinstance(cluster.auth_config, dict) else {}
    region = pc.get("region", ac.get("region", cluster.region))
    credential = credential_for_cluster(cluster)
    if not isinstance(region, str) or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
        raise EmailTestUnavailable("EXACT_REGION_REQUIRED")
    if not re.fullmatch(r"\d{12}", credential.declared_account):
        raise EmailTestUnavailable("EXACT_ACCOUNT_REQUIRED")
    verified_account = cluster.cloud_account_id or ""
    if verified_account and verified_account != credential.declared_account:
        raise EmailTestUnavailable("EMAIL_VERIFIED_ACCOUNT_MISMATCH")
    identity = service.backend_ref.removeprefix("email/")
    cfg = service.applied_config
    env_senders = cfg.get("env_senders", {})
    sender = env_senders.get(service.app_environment.name) if isinstance(env_senders, dict) else None
    sender = sender or (identity if "@" in identity else "noreply@" + identity)
    from aws.managed.email_ses import configuration_set_name

    configuration_set = configuration_set_name(identity)
    topics = getattr(settings, "SES_EVENTS_SNS_TOPIC_ARN", "")
    from aws.email_delivery import SESTestDeliveryConfig

    config = SESTestDeliveryConfig(
        region=region,
        credential=credential,
        identity=identity,
        configuration_set=configuration_set,
        feedback_topic_arns=(topics,) if topics else (),
    )
    snapshot = {
        "service": str(service.guid),
        "version": service.version,
        "updated_at": service.updated_at.isoformat(),
        "app": str(service.registered_app.guid),
        "app_version": service.registered_app.version,
        "environment": str(service.app_environment.guid),
        "environment_version": service.app_environment.version,
        "cluster": str(cluster.guid),
        "cluster_version": cluster.version,
        "verified_account": verified_account,
        "account_verified_at": cluster.cloud_account_verified_at.isoformat()
        if cluster.cloud_account_verified_at is not None
        else None,
        "provider": str(provider.guid),
        "provider_version": provider.version,
        "applied": cfg,
        "config": service.config,
        "transport": asdict(config),
        "sender": sender,
    }
    return config, sender, digest(snapshot)


def _charge(service):
    tenant = get_current_tenant()
    minute = int(timezone.now().timestamp() // 60)
    for suffix, maximum in (
        (f"actor:{tenant.actor_user_id}", 3),
        (f"service:{service.guid}", 10),
        ("org", 30),
    ):
        key = f"email-test:v1:{tenant.organization_id}:{minute}:{suffix}"
        try:
            cache.add(key, 0, 120)
            if cache.incr(key) > maximum:
                raise EmailTestUnavailable("EMAIL_TEST_RATE_LIMITED")
        except EmailTestUnavailable:
            raise
        except Exception:
            raise EmailTestUnavailable("EMAIL_TEST_ADMISSION_UNAVAILABLE") from None


def send_test(info, *, service_id, request_id, expected_version, recipient, subject=None, body=None):
    from _sdk.email_delivery import EmailDeliveryUnavailable, EmailTestMessage
    from aws.email_delivery import AmazonSESTestDelivery

    try:
        request_id = UUID(str(request_id))
    except (TypeError, ValueError, AttributeError):
        raise EmailTestUnavailable("INVALID_REQUEST_ID") from None
    subject = (subject or "").strip() or "[Astrolift] Email delivery test"
    body = (body or "").strip() or "This is an explicitly requested Astrolift email delivery test."
    recipient = (recipient or "").strip()
    intent = digest({"service_id": str(service_id), "recipient": recipient, "subject": subject, "body": body})
    tenant = get_current_tenant()
    service = _fresh(info, service_id)
    # Organization lock serializes replay admission without holding a network transaction.
    from astrolift_identity.models import Organization

    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=tenant.organization_id)
        service = _fresh(info, service_id)
        previous = EmailDeliveryTest._base_manager.filter(
            organization_id=tenant.organization_id, request_id=request_id
        ).first()
        if previous is not None:
            if previous.deleted_at is not None:
                raise EmailTestUnavailable("EMAIL_TEST_INTENT_RETIRED")
            if previous.intent_sha256 != intent or previous.managed_service_id != service.pk:
                raise EmailTestUnavailable("EMAIL_TEST_INTENT_CONFLICT")
            return previous
        if service.version != expected_version:
            raise EmailTestUnavailable("EMAIL_SERVICE_VERSION_MISMATCH")
        config, sender, snapshot = source(service)
        # Validation precedes durable intent and quota consumption; no invented sender or global fallback.
        from aws.email_delivery import _validate

        candidate = EmailTestMessage(
            test_id=str(request_id),
            managed_service_id=str(service.guid),
            sender=sender,
            recipient=recipient,
            subject=subject,
            text=body,
        )
        _validate(config, candidate)
        _charge(service)
        row = EmailDeliveryTest.objects.create(
            organization_id=tenant.organization_id,
            managed_service=service,
            requester_id=tenant.actor_user_id,
            request_id=request_id,
            intent_sha256=intent,
            source_sha256=snapshot,
            sender=sender,
            recipient=recipient,
            account_id=config.credential.declared_account,
            region=config.region,
            identity=config.identity,
            configuration_set=config.configuration_set,
            feedback_topic_arns=list(config.feedback_topic_arns),
        )

    def checkpoint():
        current = _fresh(info, service_id)
        try:
            current_snapshot = source(current)[2]
        except EmailTestUnavailable:
            raise EmailDeliveryUnavailable("EMAIL_SOURCE_CHANGED") from None
        if current_snapshot != snapshot:
            raise EmailDeliveryUnavailable("EMAIL_SOURCE_CHANGED")

    message = replace(candidate, test_id=str(row.guid))
    try:
        receipt = AmazonSESTestDelivery(config).send_test(message=message, checkpoint=checkpoint)
    except EmailDeliveryUnavailable as exc:
        reason = str(exc)
        row.reason_code = reason
        row.status = (
            EmailDeliveryTest.Status.SUPPRESSED
            if reason == "RECIPIENT_SUPPRESSED"
            else (
                EmailDeliveryTest.Status.UNKNOWN
                if reason in {"TEST_TRANSPORT_UNAVAILABLE", "ACCEPTANCE_UNKNOWN"}
                else EmailDeliveryTest.Status.FAILED
            )
        )
        row.save(update_fields=["status", "reason_code", "updated_at", "version"])
        checkpoint()
        return row
    with transaction.atomic():
        row = EmailDeliveryTest.objects.select_for_update().get(pk=row.pk)
        row.status = EmailDeliveryTest.Status.ACCEPTED
        row.provider_message_id = receipt.message_id
        row.feedback_topic_arns = list(receipt.feedback_topic_arns)
        row.simulator = receipt.simulator
        row.accepted_at = timezone.now()
        row.save(
            update_fields=[
                "status",
                "provider_message_id",
                "feedback_topic_arns",
                "simulator",
                "accepted_at",
                "updated_at",
                "version",
            ]
        )
        pending = row.observations.filter(
            provider_message_id=receipt.message_id,
            provider_topic_arn__in=receipt.feedback_topic_arns,
        ).order_by("occurred_at", "pk")
        for observation in pending:
            _apply_observation(row, observation)
    checkpoint()
    return row


def test_history(info, service_id, *, limit=25):
    rows = list(_fresh(info, service_id, writing=False)[: max(1, min(limit, 50))])
    _fresh(info, service_id, writing=False)
    return rows


def test_history_page(info, service_id, *, after=None, limit=25):
    from astrolift_graphql.pagination import keyset_page

    tenant = get_current_tenant()
    page = keyset_page(
        _fresh(info, service_id, writing=False),
        cursor=after,
        limit=limit,
        default_limit=25,
        max_limit=50,
        cursor_scope=f"email-test:v1:{tenant.organization_id}:{service_id}",
    )
    _fresh(info, service_id, writing=False)
    return page


def display_status(row):
    if (
        row.status in {"accepted", "deferred"}
        and row.accepted_at
        and timezone.now() - row.accepted_at > timedelta(minutes=15)
    ):
        return "observation_timed_out"
    if row.status == "submitting" and timezone.now() - row.created_at > timedelta(minutes=1):
        return "unknown"
    return row.status


def _apply_observation(row, observation):
    if row.observed_at is None or observation.occurred_at >= row.observed_at:
        if observation.kind != "deferred" or row.status in {"accepted", "deferred"}:
            row.status = observation.kind
            row.observed_at = observation.occurred_at
            row.reason_code = "PROVIDER_EVENT_OBSERVED"
            row.save(update_fields=["status", "observed_at", "reason_code", "updated_at", "version"])


def observe_test(sns_body, ses_message):
    """Called only after SNS signature verification; consume tagged diagnostics privately."""
    from _sdk.email_delivery import EMAIL_DELIVERY_SERVICE_TAG

    mail = ses_message.get("mail") or {}
    if not isinstance(mail, dict):
        return False
    tags = mail.get("tags") or {}
    ids = tags.get("astrolift_test_id") if isinstance(tags, dict) else None
    if not ids:
        return False
    if not isinstance(ids, list) or len(ids) != 1:
        return True
    try:
        guid = UUID(ids[0])
    except (ValueError, TypeError, AttributeError):
        return True
    with transaction.atomic():
        row = EmailDeliveryTest.objects.select_for_update().filter(guid=guid).first()
        if row is None:
            return True
        service_ids = tags.get(EMAIL_DELIVERY_SERVICE_TAG)
        if (
            sns_body.get("TopicArn") not in row.feedback_topic_arns
            or service_ids != [str(row.managed_service.guid)]
            or row.provider_message_id
            and mail.get("messageId") != row.provider_message_id
            or not row.provider_message_id
            and row.status != "submitting"
            or mail.get("sendingAccountId") != row.account_id
            or mail.get("source") != row.sender
            or mail.get("destination") != [row.recipient]
        ):
            return True
        message_id = mail.get("messageId")
        if not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", message_id):
            return True
        kind = ses_message.get("eventType") or ses_message.get("notificationType")
        mapping = {
            "Delivery": "delivered",
            "Bounce": "bounced",
            "Complaint": "complained",
            "DeliveryDelay": "deferred",
            "Reject": "rejected",
        }
        status = mapping.get(kind)
        if status is None:
            return True
        raw_at = (ses_message.get(kind[0].lower() + kind[1:]) or {}).get("timestamp")
        if raw_at is None:
            raw_at = mail.get("timestamp")
        try:
            from datetime import datetime

            occurred_at = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
            if timezone.is_naive(occurred_at):
                return True
        except (ValueError, TypeError, AttributeError):
            return True
        if occurred_at < row.created_at - timedelta(minutes=5) or occurred_at > timezone.now() + timedelta(
            minutes=5
        ):
            return True
        event_id = sns_body.get("MessageId")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 255:
            return True
        event_hash = digest({"topic": sns_body["TopicArn"], "id": event_id, "test": str(row.guid)})
        if row.observations.count() >= 64:
            return True
        observation, created = EmailDeliveryObservation.objects.get_or_create(
            event_sha256=event_hash,
            defaults={
                "delivery_test": row,
                "kind": status,
                "occurred_at": occurred_at,
                "provider_message_id": message_id,
                "provider_topic_arn": sns_body["TopicArn"],
            },
        )
        if created and row.provider_message_id:
            _apply_observation(row, observation)
        return True
