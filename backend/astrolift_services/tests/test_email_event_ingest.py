"""Tests for the SES → SNS event ingestion pipeline (#756).

Covers:
* SNS subscription confirmation auto-confirm (URL safety check + happy
  path through a mocked urlopen).
* Notification ingest end-to-end: SNS envelope → SES message → per-
  recipient ``EmailEvent`` insert, with managed-service FK resolution
  via the configuration set name embedded in ``mail.tags``.
* Notification ingest fan-out: bounce / complaint blobs carry their
  own recipient sub-list and we should emit one row per address.
* Unknown ``notificationType`` is acknowledged (200) but no rows are
  inserted.
* Malformed envelope is rejected (400) without an insert.
* GraphQL resolvers ``astrolift_email_messages`` +
  ``astrolift_email_engagement_metrics`` filter by managed service,
  apply event_kind / recipient filters, and compute the percentage
  derivations.
* Permission gate on both resolvers — denied caller sees [] / None.

Tests run against a real Postgres (``pytest.mark.django_db``); no DB
mocks, per the project's testing posture.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.contrib.auth import get_user_model
from django.test import Client

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services import views as ses_views
from astrolift_services.models import EmailEvent, EmailEventKind, ManagedService
from astrolift_services.schema.queries import ServicesQuery
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---- Scaffolding -----------------------------------------------------


def _scaffold(*, plugin_slug: str = "aws", region: str = "us-east-1"):
    org = Organization.objects.create(name="Acme", slug=f"acme-emev-{plugin_slug}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-emev-{plugin_slug}")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug=f"demo-emev-{plugin_slug}",
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=plugin_slug.upper(),
                slug=plugin_slug,
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug=plugin_slug)
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-emev-{plugin_slug}",
        name=f"Cluster {plugin_slug}",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
        region=region,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=f"App {plugin_slug}",
        slug=f"app-emev-{plugin_slug}",
        provisioning_status="ready",
        manifest_raw='astrolift_version = 1\nname = "app"\n',
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return org, app, env


def _make_email_service(app, env, *, identity: str = "send.example.com"):
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.EMAIL,
        variant="ses",
        name="ses",
        status=ManagedService.Status.ACTIVE,
        config={"identity": identity, "email_from": f"noreply@{identity}"},
    )


def _make_user(username: str = "ses-events-test"):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _info(user=None):
    if user is None:
        request = SimpleNamespace(user=None, META={})
    else:
        request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- SNS signing infra (#1060) --------------------------------------
#
# Every inbound SNS message is RSA-signed with an AWS-issued cert; the
# receiver now verifies that signature before doing any work. The tests
# stand in their own keypair, sign the canonical string the receiver
# rebuilds, and patch the cert loader to hand back the matching public
# key — so the receiver runs its real verification path against a valid
# signature instead of being bypassed.

_TEST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_PUBLIC_KEY = _TEST_KEY.public_key()
_TEST_CERT_URL = "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-test.pem"


@pytest.fixture(autouse=True)
def _sns_cert(monkeypatch):
    """Hand the receiver our test public key for every SNS verify."""
    ses_views._load_signing_cert_public_key.cache_clear()
    monkeypatch.setattr(
        ses_views,
        "_load_signing_cert_public_key",
        lambda url: _TEST_PUBLIC_KEY,
    )
    yield


def _sign_envelope(body: dict) -> None:
    """Sign ``body`` in place exactly as the receiver verifies it: build
    the canonical string with the receiver's own ``_canonical_message``,
    RSA-SHA256 sign it, and attach the v2 signature fields."""
    canonical = ses_views._canonical_message(body, body["Type"])
    assert canonical is not None, "envelope missing a required signed field"
    signature = _TEST_KEY.sign(canonical, padding.PKCS1v15(), hashes.SHA256())
    body["SignatureVersion"] = "2"
    body["SigningCertURL"] = _TEST_CERT_URL
    body["Signature"] = base64.b64encode(signature).decode()


# ---- SNS envelope helpers -------------------------------------------


def _sns_envelope(
    *,
    msg_type: str = "Notification",
    message: dict | None = None,
    subscribe_url: str = "",
    signed: bool = True,
) -> bytes:
    body: dict = {
        "Type": msg_type,
        "MessageId": "11111111-2222-3333-4444-555555555555",
        "Timestamp": "2026-06-30T00:00:00.000Z",
        "TopicArn": "arn:aws:sns:us-east-1:123456789012:ses-events",
    }
    if msg_type in ("SubscriptionConfirmation", "UnsubscribeConfirmation"):
        # SubscribeURL + Token are required signed fields for these types.
        body["SubscribeURL"] = subscribe_url or "https://sns.us-east-1.amazonaws.com/?Action=Confirm"
        body["Token"] = "confirm-token"
        body["Message"] = "You have chosen to subscribe to the topic."
    elif subscribe_url:
        body["SubscribeURL"] = subscribe_url
    if message is not None:
        body["Message"] = json.dumps(message)
    if signed:
        _sign_envelope(body)
    return json.dumps(body).encode()


def _ses_delivery(
    *,
    message_id: str = "msg-1",
    recipients: list[str] | None = None,
    subject: str = "Hello",
    config_set: str = "astrolift-send-example-com",
    notification_type: str = "Delivery",
) -> dict:
    return {
        "notificationType": notification_type,
        "mail": {
            "messageId": message_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "source": "noreply@send.example.com",
            "destination": recipients or ["user@example.com"],
            "commonHeaders": {"subject": subject},
            "tags": {"ses:configuration-set": [config_set]},
        },
        "delivery": {
            "timestamp": datetime.now(UTC).isoformat(),
            "processingTimeMillis": 412,
            "recipients": recipients or ["user@example.com"],
        },
    }


def _ses_bounce(
    *,
    message_id: str = "msg-bnc",
    bounced_recipients: list[str] | None = None,
    config_set: str = "astrolift-send-example-com",
) -> dict:
    bounced = bounced_recipients or ["bouncy@example.com", "alsobouncy@example.com"]
    return {
        "notificationType": "Bounce",
        "mail": {
            "messageId": message_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "destination": bounced,
            "source": "noreply@send.example.com",
            "commonHeaders": {"subject": "Hi"},
            "tags": {"ses:configuration-set": [config_set]},
        },
        "bounce": {
            "bounceType": "Permanent",
            "bounceSubType": "General",
            "timestamp": datetime.now(UTC).isoformat(),
            "bouncedRecipients": [{"emailAddress": addr, "status": "5.1.1"} for addr in bounced],
        },
    }


# ---- SNS subscription confirm ---------------------------------------


def test_subscription_confirmation_fetches_subscribe_url():
    body = _sns_envelope(
        msg_type="SubscriptionConfirmation",
        subscribe_url=("https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription"),
    )
    with patch("astrolift_services.views.urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value.getcode.return_value = 200
        mock_open.return_value.__enter__.return_value.status = 200
        resp = Client().post(
            "/app/webhooks/ses-events/",
            data=body,
            content_type="application/json",
        )
    assert resp.status_code == 200
    assert mock_open.called
    fetched = mock_open.call_args[0][0]
    assert fetched.startswith("https://sns.")


def test_subscription_confirmation_refuses_non_sns_url():
    body = _sns_envelope(
        msg_type="SubscriptionConfirmation",
        subscribe_url="https://evil.example.com/steal-secrets",
    )
    with patch("astrolift_services.views.urllib.request.urlopen") as mock_open:
        resp = Client().post(
            "/app/webhooks/ses-events/",
            data=body,
            content_type="application/json",
        )
    assert resp.status_code == 200
    assert not mock_open.called


# ---- Notification ingest --------------------------------------------


def test_delivery_notification_inserts_one_event_per_recipient():
    _org, app, env = _scaffold()
    svc = _make_email_service(app, env, identity="send.example.com")

    body = _sns_envelope(
        message=_ses_delivery(
            message_id="msg-deliv-1",
            recipients=["a@example.com", "b@example.com"],
            subject="Welcome",
        ),
    )
    resp = Client().post(
        "/app/webhooks/ses-events/",
        data=body,
        content_type="application/json",
    )
    assert resp.status_code == 200

    rows = list(EmailEvent.objects.order_by("recipient"))
    assert len(rows) == 2
    for row in rows:
        assert row.managed_service_id == svc.id
        assert row.message_id == "msg-deliv-1"
        assert row.event_kind == EmailEventKind.DELIVERY
        assert row.subject == "Welcome"
    assert {r.recipient for r in rows} == {"a@example.com", "b@example.com"}


def test_bounce_notification_fans_out_to_bounced_recipients():
    _org, app, env = _scaffold(plugin_slug="aws-bnc")
    _make_email_service(app, env, identity="send.example.com")

    body = _sns_envelope(
        message=_ses_bounce(
            message_id="msg-bnc-1",
            bounced_recipients=["x@example.com", "y@example.com"],
        ),
    )
    resp = Client().post(
        "/app/webhooks/ses-events/",
        data=body,
        content_type="application/json",
    )
    assert resp.status_code == 200

    rows = list(EmailEvent.objects.filter(event_kind=EmailEventKind.BOUNCE).order_by("recipient"))
    assert len(rows) == 2
    assert {r.recipient for r in rows} == {"x@example.com", "y@example.com"}
    # Bounce metadata is preserved verbatim so the FE can render the
    # reason chain without a follow-up call.
    sample = rows[0]
    assert sample.metadata.get("bounce", {}).get("bounceType") == "Permanent"


def test_unknown_notification_type_acked_but_no_rows():
    body = _sns_envelope(
        message={
            "notificationType": "DeliveryDelay",
            "mail": {
                "messageId": "delayed-1",
                "destination": ["someone@example.com"],
                "commonHeaders": {"subject": "Slow mail"},
            },
        },
    )
    resp = Client().post(
        "/app/webhooks/ses-events/",
        data=body,
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert EmailEvent.objects.count() == 0


def test_malformed_outer_envelope_rejected():
    resp = Client().post(
        "/app/webhooks/ses-events/",
        data=b"not json",
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert EmailEvent.objects.count() == 0


# ---- SNS signature auth boundary (#1060) ----------------------------


def test_unsigned_notification_rejected_403():
    """A well-formed SNS body with no signature fields is rejected before
    any EmailEvent is inserted — anyone with the URL must not be able to
    forge events."""
    body = _sns_envelope(
        message=_ses_delivery(message_id="forged-1", recipients=["x@example.com"]),
        signed=False,
    )
    resp = Client().post(
        "/app/webhooks/ses-events/",
        data=body,
        content_type="application/json",
    )
    assert resp.status_code == 403
    assert EmailEvent.objects.count() == 0


def test_tampered_payload_rejected_403():
    """A valid signature over one payload doesn't carry to a mutated one:
    flipping the Message after signing invalidates the signature."""
    raw = _sns_envelope(
        message=_ses_delivery(message_id="orig-1", recipients=["a@example.com"]),
        signed=True,
    )
    body = json.loads(raw)
    # Swap in a different SES message; the signature no longer matches.
    body["Message"] = json.dumps(_ses_delivery(message_id="tampered-1", recipients=["attacker@example.com"]))
    resp = Client().post(
        "/app/webhooks/ses-events/",
        data=json.dumps(body).encode(),
        content_type="application/json",
    )
    assert resp.status_code == 403
    assert EmailEvent.objects.count() == 0


def test_non_sns_signing_cert_url_rejected_403():
    """A signed body whose SigningCertURL points off-AWS is rejected — the
    host allowlist closes both the attacker-cert and the SSRF-fetch holes.
    The cert loader must never be consulted for a disallowed host."""
    raw = _sns_envelope(
        message=_ses_delivery(message_id="evil-cert-1", recipients=["a@example.com"]),
        signed=True,
    )
    body = json.loads(raw)
    body["SigningCertURL"] = "https://evil.example.com/cert.pem"
    with patch.object(ses_views, "_load_signing_cert_public_key") as mock_loader:
        resp = Client().post(
            "/app/webhooks/ses-events/",
            data=json.dumps(body).encode(),
            content_type="application/json",
        )
    assert resp.status_code == 403
    assert not mock_loader.called
    assert EmailEvent.objects.count() == 0


def test_notification_with_unresolvable_service_still_inserts_unlinked():
    body = _sns_envelope(
        message=_ses_delivery(
            message_id="msg-unowned-1",
            config_set="astrolift-unknown-identity",
            recipients=["someone@example.com"],
        ),
    )
    resp = Client().post(
        "/app/webhooks/ses-events/",
        data=body,
        content_type="application/json",
    )
    assert resp.status_code == 200
    rows = list(EmailEvent.objects.all())
    assert len(rows) == 1
    assert rows[0].managed_service_id is None


def test_send_notification_resolves_service_through_source_address():
    """When the config-set tag is missing, the receiver falls back to
    matching the sender domain in ``mail.source``."""
    _org, app, env = _scaffold(plugin_slug="aws-src")
    svc = _make_email_service(app, env, identity="send.example.com")

    msg = _ses_delivery(
        message_id="msg-src-1",
        recipients=["dest@example.com"],
        config_set="",  # blank → no tag fallback
    )
    msg["mail"].pop("tags", None)
    body = _sns_envelope(message=msg)
    resp = Client().post(
        "/app/webhooks/ses-events/",
        data=body,
        content_type="application/json",
    )
    assert resp.status_code == 200
    row = EmailEvent.objects.get(message_id="msg-src-1")
    assert row.managed_service_id == svc.id


# ---- GraphQL resolvers ----------------------------------------------


def test_email_messages_resolver_filters_by_service_and_kind(permission_resolver):
    org, app, env = _scaffold(plugin_slug="aws-q1")
    svc = _make_email_service(app, env, identity="q1.example.com")

    other_org, other_app, other_env = _scaffold(plugin_slug="aws-q1-other")
    other_svc = _make_email_service(other_app, other_env, identity="other.example.com")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    now = datetime.now(UTC)
    EmailEvent.objects.bulk_create(
        [
            EmailEvent(
                managed_service=svc,
                message_id="m1",
                recipient="a@example.com",
                event_kind=EmailEventKind.DELIVERY,
                occurred_at=now,
            ),
            EmailEvent(
                managed_service=svc,
                message_id="m2",
                recipient="b@example.com",
                event_kind=EmailEventKind.BOUNCE,
                occurred_at=now,
            ),
            EmailEvent(
                managed_service=other_svc,
                message_id="other-1",
                recipient="z@example.com",
                event_kind=EmailEventKind.DELIVERY,
                occurred_at=now,
            ),
        ]
    )

    with _ctx(org):
        unfiltered = ServicesQuery().astrolift_email_messages(
            _info(_make_user()),
            managed_service_id=GUID(str(svc.guid)),
        )
        bounces = ServicesQuery().astrolift_email_messages(
            _info(_make_user()),
            managed_service_id=GUID(str(svc.guid)),
            event_kind="bounce",
        )
        by_recipient = ServicesQuery().astrolift_email_messages(
            _info(_make_user()),
            managed_service_id=GUID(str(svc.guid)),
            recipient="a@",
        )

    assert {row.message_id for row in unfiltered} == {"m1", "m2"}
    assert {row.message_id for row in bounces} == {"m2"}
    assert {row.message_id for row in by_recipient} == {"m1"}
    # Spillover into the other tenant's service is impossible — the
    # filter is on managed_service FK, not just kind.
    assert all(row.message_id != "other-1" for row in unfiltered)


def test_email_messages_rejects_unknown_event_kind(permission_resolver):
    org, app, env = _scaffold(plugin_slug="aws-q2")
    svc = _make_email_service(app, env, identity="q2.example.com")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    EmailEvent.objects.create(
        managed_service=svc,
        message_id="m1",
        recipient="a@example.com",
        event_kind=EmailEventKind.DELIVERY,
        occurred_at=datetime.now(UTC),
    )
    with _ctx(org):
        result = ServicesQuery().astrolift_email_messages(
            _info(_make_user()),
            managed_service_id=GUID(str(svc.guid)),
            event_kind="not-a-kind",
        )
    assert result == []


def test_email_messages_requires_permission():
    org, app, env = _scaffold(plugin_slug="aws-q3")
    svc = _make_email_service(app, env, identity="q3.example.com")
    EmailEvent.objects.create(
        managed_service=svc,
        message_id="m1",
        recipient="a@example.com",
        event_kind=EmailEventKind.DELIVERY,
        occurred_at=datetime.now(UTC),
    )
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            # No permission_resolver fixture → deny-by-default.
            ServicesQuery().astrolift_email_messages(
                _info(_make_user("noperm")),
                managed_service_id=GUID(str(svc.guid)),
            )


def test_email_messages_caps_limit_at_500(permission_resolver):
    org, app, env = _scaffold(plugin_slug="aws-cap")
    svc = _make_email_service(app, env, identity="cap.example.com")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    now = datetime.now(UTC)
    EmailEvent.objects.bulk_create(
        [
            EmailEvent(
                managed_service=svc,
                message_id=f"m{i}",
                recipient=f"u{i}@example.com",
                event_kind=EmailEventKind.DELIVERY,
                occurred_at=now,
            )
            for i in range(5)
        ]
    )
    with _ctx(org):
        result = ServicesQuery().astrolift_email_messages(
            _info(_make_user()),
            managed_service_id=GUID(str(svc.guid)),
            limit=9999,
        )
    # Cap doesn't break under-limit results; the limit clamp is just a
    # safety belt — five rows still come back.
    assert len(result) == 5


def test_engagement_metrics_computes_rates(permission_resolver):
    org, app, env = _scaffold(plugin_slug="aws-met")
    svc = _make_email_service(app, env, identity="met.example.com")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    now = datetime.now(UTC)
    rows = []
    # 100 sends, 80 deliveries (4 bounces, 1 complaint, 40 opens, 8 clicks).
    for i in range(100):
        rows.append(
            EmailEvent(
                managed_service=svc,
                message_id=f"send-{i}",
                recipient=f"u{i}@example.com",
                event_kind=EmailEventKind.SEND,
                occurred_at=now,
            )
        )
    for i in range(80):
        rows.append(
            EmailEvent(
                managed_service=svc,
                message_id=f"send-{i}",
                recipient=f"u{i}@example.com",
                event_kind=EmailEventKind.DELIVERY,
                occurred_at=now,
            )
        )
    for i in range(4):
        rows.append(
            EmailEvent(
                managed_service=svc,
                message_id=f"send-{i + 80}",
                recipient=f"u{i + 80}@example.com",
                event_kind=EmailEventKind.BOUNCE,
                occurred_at=now,
            )
        )
    rows.append(
        EmailEvent(
            managed_service=svc,
            message_id="send-84",
            recipient="u84@example.com",
            event_kind=EmailEventKind.COMPLAINT,
            occurred_at=now,
        )
    )
    for i in range(40):
        rows.append(
            EmailEvent(
                managed_service=svc,
                message_id=f"send-{i}",
                recipient=f"u{i}@example.com",
                event_kind=EmailEventKind.OPEN,
                occurred_at=now,
            )
        )
    for i in range(8):
        rows.append(
            EmailEvent(
                managed_service=svc,
                message_id=f"send-{i}",
                recipient=f"u{i}@example.com",
                event_kind=EmailEventKind.CLICK,
                occurred_at=now,
            )
        )
    EmailEvent.objects.bulk_create(rows)

    with _ctx(org):
        result = ServicesQuery().astrolift_email_engagement_metrics(
            _info(_make_user()),
            managed_service_id=GUID(str(svc.guid)),
            days=30,
        )
    assert result is not None
    assert result.total_sends == 100
    assert result.total_deliveries == 80
    assert result.total_bounces == 4
    assert result.total_complaints == 1
    assert result.total_opens == 40
    assert result.total_clicks == 8
    # 4 / 100 → 4.0 %
    assert result.bounce_rate_pct == pytest.approx(4.0)
    # 1 / 100 → 1.0 %
    assert result.complaint_rate_pct == pytest.approx(1.0)
    # 40 / 80 → 50.0 %
    assert result.open_rate_pct == pytest.approx(50.0)
    # 8 / 80 → 10.0 %
    assert result.click_rate_pct == pytest.approx(10.0)
    assert result.window_days == 30


def test_engagement_metrics_zero_denominator(permission_resolver):
    org, app, env = _scaffold(plugin_slug="aws-met-zero")
    svc = _make_email_service(app, env, identity="metzero.example.com")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    with _ctx(org):
        result = ServicesQuery().astrolift_email_engagement_metrics(
            _info(_make_user()),
            managed_service_id=GUID(str(svc.guid)),
            days=30,
        )
    assert result is not None
    assert result.total_sends == 0
    assert result.bounce_rate_pct == 0.0
    assert result.complaint_rate_pct == 0.0
    assert result.open_rate_pct == 0.0
    assert result.click_rate_pct == 0.0


def test_engagement_metrics_window_excludes_old_rows(permission_resolver):
    org, app, env = _scaffold(plugin_slug="aws-met-win")
    svc = _make_email_service(app, env, identity="metwin.example.com")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)

    now = datetime.now(UTC)
    EmailEvent.objects.bulk_create(
        [
            EmailEvent(
                managed_service=svc,
                message_id="recent",
                recipient="u@example.com",
                event_kind=EmailEventKind.SEND,
                occurred_at=now - timedelta(days=1),
            ),
            EmailEvent(
                managed_service=svc,
                message_id="old",
                recipient="u@example.com",
                event_kind=EmailEventKind.SEND,
                occurred_at=now - timedelta(days=120),
            ),
        ]
    )
    with _ctx(org):
        result = ServicesQuery().astrolift_email_engagement_metrics(
            _info(_make_user()),
            managed_service_id=GUID(str(svc.guid)),
            days=30,
        )
    assert result is not None
    assert result.total_sends == 1


def test_engagement_metrics_requires_permission():
    org, app, env = _scaffold(plugin_slug="aws-met-perm")
    svc = _make_email_service(app, env, identity="metperm.example.com")
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            ServicesQuery().astrolift_email_engagement_metrics(
                _info(_make_user("noperm-met")),
                managed_service_id=GUID(str(svc.guid)),
            )


def test_email_messages_returns_empty_for_non_email_service(permission_resolver):
    org, app, env = _scaffold(plugin_slug="aws-q4")
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        variant="rds",
        name="db",
        status=ManagedService.Status.ACTIVE,
        config={},
    )
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    with _ctx(org):
        result = ServicesQuery().astrolift_email_messages(
            _info(_make_user()),
            managed_service_id=GUID(str(svc.guid)),
        )
    assert result == []
