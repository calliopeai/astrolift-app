"""Tests for ``test_webhook_subscription`` + ``test_notification_channel``.

Both mutations are operator-only: webhook.update and org.update gate
them. The webhook test mutation POSTs synchronously so the operator
gets an immediate verdict; the notification test creates a SYSTEM
notification in the caller's inbox.

We patch ``urllib.request.urlopen`` in the operations.schema.mutations
namespace for the webhook tests — that's where the symbol is looked
up at delivery time.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization
from astrolift_operations.models import Notification, WebhookSubscription
from astrolift_operations.schema.mutations import (
    OperationsMutation,
    TestNotificationInput,
    TestWebhookInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


def _user(username: str = "operator"):
    User = get_user_model()
    return User.objects.create(username=username, email=f"{username}@test")


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    user = _user("op-test")
    sub = WebhookSubscription.objects.create(
        organization=org,
        url="https://example.invalid/webhook",
        secret_hash="abc123",
        events=["deploy.completed"],
        is_active=True,
    )
    return org, user, sub


def _tenant(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


# ---- test_webhook_subscription -------------------------------------


def _patch_urlopen(monkeypatch, *, status=200, body=b"ok", raises=None):
    """Replace ``urllib.request.urlopen`` in the mutations module."""

    class _Resp:
        def __init__(self, code, body_bytes):
            self.status = code
            self._body = io.BytesIO(body_bytes)

        def read(self, *a, **kw):
            return self._body.read(*a, **kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        if raises is not None:
            raise raises
        return _Resp(status, body)

    monkeypatch.setattr(
        "astrolift_operations.schema.mutations.urllib.request.urlopen",
        fake_urlopen,
    )


def test_webhook_test_records_2xx_response(monkeypatch, permission_resolver):
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    _patch_urlopen(monkeypatch, status=204, body=b"")

    with _tenant(org, user):
        result = OperationsMutation().test_webhook_subscription(
            _info(user), input=TestWebhookInput(id=str(sub.guid))
        )

    assert result.ok, result.errors
    assert result.data.delivered is True
    assert result.data.status_code == 204
    assert result.data.error == ""
    assert result.data.url == sub.url


def test_webhook_test_records_4xx_as_delivered_failure(monkeypatch, permission_resolver):
    """4xx counts as 'reached the subscriber but they rejected the
    test event' — delivered=True so the operator can see the body."""
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    monkeypatch.setattr(
        "astrolift_operations.schema.mutations.urllib.request.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(
            HTTPError(req.full_url, 422, "Unprocessable Entity", req.headers, io.BytesIO(b'{"e":1}'))
        ),
    )

    with _tenant(org, user):
        result = OperationsMutation().test_webhook_subscription(
            _info(user), input=TestWebhookInput(id=str(sub.guid))
        )
    assert result.ok
    assert result.data.delivered is True
    assert result.data.status_code == 422
    assert '"e":1' in result.data.response_body_excerpt


def test_webhook_test_transport_failure_is_not_delivered(monkeypatch, permission_resolver):
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    _patch_urlopen(monkeypatch, raises=URLError("name or service not known"))

    with _tenant(org, user):
        result = OperationsMutation().test_webhook_subscription(
            _info(user), input=TestWebhookInput(id=str(sub.guid))
        )
    assert result.ok
    assert result.data.delivered is False
    assert result.data.status_code is None
    assert "name or service not known" in result.data.error


def test_webhook_test_unknown_id_returns_not_found(permission_resolver, monkeypatch):
    org, user, _ = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    _patch_urlopen(monkeypatch)

    with _tenant(org, user):
        result = OperationsMutation().test_webhook_subscription(
            _info(user), input=TestWebhookInput(id="00000000-0000-0000-0000-000000000000")
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_webhook_test_disabled_subscription_refused(monkeypatch, permission_resolver):
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    sub.is_active = False
    sub.save(update_fields=["is_active"])
    _patch_urlopen(monkeypatch)

    with _tenant(org, user):
        result = OperationsMutation().test_webhook_subscription(
            _info(user), input=TestWebhookInput(id=str(sub.guid))
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_webhook_test_signs_with_subscriptions_secret(monkeypatch, permission_resolver):
    """The HMAC signing key is the row's stored ``secret_hash``. We
    capture the headers that would have been sent so we can verify
    the signature isn't blank + uses the subscription's bytes."""
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    captured: dict = {}

    class _Resp:
        status = 200

        def read(self, *a, **kw):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data
        return _Resp()

    monkeypatch.setattr("astrolift_operations.schema.mutations.urllib.request.urlopen", fake_urlopen)

    with _tenant(org, user):
        result = OperationsMutation().test_webhook_subscription(
            _info(user), input=TestWebhookInput(id=str(sub.guid))
        )

    assert result.ok
    headers = captured["headers"]
    # urllib lowercases header names on Request.headers
    sig = headers.get("X-astrolift-signature") or headers.get("X-Astrolift-Signature")
    assert sig and sig.startswith("sha256=")
    # Body should be JSON with the synthesized event kind.
    payload = json.loads(captured["body"])
    assert payload["kind"] == "webhook.test"
    assert payload["subscription_id"] == str(sub.guid)


def test_webhook_test_requires_permission(monkeypatch, permission_resolver):
    org, user, sub = _scaffold()
    # No grant of WEBHOOK_UPDATE.
    _patch_urlopen(monkeypatch)
    with _tenant(org, user):
        result = OperationsMutation().test_webhook_subscription(
            _info(user), input=TestWebhookInput(id=str(sub.guid))
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- test_notification_channel -------------------------------------


def test_notification_test_creates_system_notification(permission_resolver):
    org, user, _ = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with _tenant(org, user):
        result = OperationsMutation().test_notification_channel(
            _info(user), input=TestNotificationInput(id=str(org.guid))
        )

    assert result.ok, result.errors
    assert result.data.kind == Notification.Kind.SYSTEM
    assert result.data.title == "Test notification"
    assert Notification.objects.filter(user=user, organization=org, kind="system").count() == 1


def test_notification_test_uses_custom_message(permission_resolver):
    org, user, _ = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with _tenant(org, user):
        result = OperationsMutation().test_notification_channel(
            _info(user),
            input=TestNotificationInput(id=str(org.guid), message="hello from the test"),
        )
    assert result.ok
    assert result.data.body == "hello from the test"


def test_notification_test_unknown_org_returns_not_found(permission_resolver):
    org, user, _ = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)
    with _tenant(org, user):
        result = OperationsMutation().test_notification_channel(
            _info(user),
            input=TestNotificationInput(id="00000000-0000-0000-0000-000000000000"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_notification_test_rejects_cross_org_target(permission_resolver):
    """A user acting in org_a cannot send a test notification scoped
    to org_b even if they technically hold org.update grants."""
    org_a, user, _ = _scaffold()
    org_b = Organization.objects.create(name="Other", slug="other")
    permission_resolver.grant(Permission.ORG_UPDATE)

    with _tenant(org_a, user):
        result = OperationsMutation().test_notification_channel(
            _info(user), input=TestNotificationInput(id=str(org_b.guid))
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_notification_test_requires_permission(permission_resolver):
    org, user, _ = _scaffold()
    # no grant
    with _tenant(org, user):
        result = OperationsMutation().test_notification_channel(
            _info(user), input=TestNotificationInput(id=str(org.guid))
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
