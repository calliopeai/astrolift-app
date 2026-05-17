"""Tests for the webhook secret rotation policy + mutation (#426)."""

from __future__ import annotations

import datetime as dt
import hashlib
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_operations.models import WebhookSubscription
from astrolift_operations.schema.mutations import (
    OperationsMutation,
    RotateOutboundWebhookSecretInput,
)
from astrolift_operations.webhook_delivery import sign_payload, verify_signature
from astrolift_operations.webhook_rotation import (
    DEFAULT_GRACE_SECONDS,
    MAX_GRACE_SECONDS,
    MIN_GRACE_SECONDS,
    RotationError,
    plan_rotation,
    previous_secret_is_in_window,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---- pure-policy ---------------------------------------------------


def test_plan_rotation_emits_prefixed_plaintext_and_matching_hash():
    now = timezone.now()
    plan = plan_rotation(current_secret_hash="oldhash", now=now)
    assert plan.plaintext_secret.startswith("alfthk_")
    assert plan.new_secret_hash == hashlib.sha256(plan.plaintext_secret.encode()).hexdigest()
    assert plan.previous_secret_hash == "oldhash"
    assert plan.rotated_at == now
    assert plan.grace_seconds == DEFAULT_GRACE_SECONDS


def test_plan_rotation_rejects_grace_below_min():
    with pytest.raises(RotationError):
        plan_rotation(
            current_secret_hash="oldhash",
            now=timezone.now(),
            grace_seconds=MIN_GRACE_SECONDS - 1,
        )


def test_plan_rotation_rejects_grace_above_max():
    with pytest.raises(RotationError):
        plan_rotation(
            current_secret_hash="oldhash",
            now=timezone.now(),
            grace_seconds=MAX_GRACE_SECONDS + 1,
        )


def test_previous_secret_in_window_true_within_grace():
    now = timezone.now()
    rotated = now - dt.timedelta(minutes=30)
    assert previous_secret_is_in_window(rotated_at=rotated, now=now, grace_seconds=3600) is True


def test_previous_secret_in_window_false_after_grace():
    now = timezone.now()
    rotated = now - dt.timedelta(hours=2)
    assert previous_secret_is_in_window(rotated_at=rotated, now=now, grace_seconds=3600) is False


def test_previous_secret_never_used_when_never_rotated():
    assert previous_secret_is_in_window(rotated_at=None, now=timezone.now(), grace_seconds=3600) is False


# ---- crypto: verify accepts old secret during grace -----------------


def test_verify_signature_accepts_previous_secret_when_supplied():
    old = b"old-secret"
    new = b"new-secret"
    body = b'{"event":"deploy.completed"}'
    ts = 1_700_000_000

    # Subscriber signed with the OLD secret because they haven't
    # rolled the rotation yet.
    presented = sign_payload(secret=old, timestamp_unix=ts, raw_body=body)

    # Verifying against the new secret alone rejects.
    assert (
        verify_signature(
            secret=new,
            timestamp_unix=ts,
            raw_body=body,
            presented=presented,
            now_unix=ts,
        )
        is False
    )

    # Verifying with the previous-secret fallback accepts.
    assert (
        verify_signature(
            secret=new,
            timestamp_unix=ts,
            raw_body=body,
            presented=presented,
            now_unix=ts,
            previous_secret=old,
        )
        is True
    )


# ---- mutation ------------------------------------------------------


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


def _user(username: str = "operator"):
    User = get_user_model()
    return User.objects.create(username=username, email=f"{username}@test")


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    user = _user("op-rotate")
    sub = WebhookSubscription.objects.create(
        organization=org,
        url="https://example.invalid/webhook",
        secret_hash="originalhash",
        events=["deploy.completed"],
        is_active=True,
    )
    return org, user, sub


def _tenant(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


def test_rotate_secret_returns_plaintext_and_persists_previous(permission_resolver):
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)

    with _tenant(org, user):
        result = OperationsMutation().rotate_outbound_webhook_secret(
            _info(user), input=RotateOutboundWebhookSecretInput(id=str(sub.guid))
        )

    assert result.ok, result.errors
    plaintext = result.data.plaintext_secret
    assert plaintext.startswith("alfthk_")

    sub.refresh_from_db()
    assert sub.secret_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    assert sub.secret_hash_previous == "originalhash"
    assert sub.secret_rotated_at is not None


def test_rotate_secret_twice_drops_oldest_generation(permission_resolver):
    """Rotating twice in a row means the very-first hash is gone —
    we only hold one generation back."""
    org, user, sub = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)

    with _tenant(org, user):
        first = OperationsMutation().rotate_outbound_webhook_secret(
            _info(user), input=RotateOutboundWebhookSecretInput(id=str(sub.guid))
        )
    assert first.ok
    sub.refresh_from_db()
    assert sub.secret_hash_previous == "originalhash"
    first_hash = sub.secret_hash

    with _tenant(org, user):
        second = OperationsMutation().rotate_outbound_webhook_secret(
            _info(user), input=RotateOutboundWebhookSecretInput(id=str(sub.guid))
        )
    assert second.ok
    sub.refresh_from_db()
    assert sub.secret_hash_previous == first_hash
    assert sub.secret_hash != first_hash


def test_rotate_secret_unknown_id_returns_not_found(permission_resolver):
    org, user, _ = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    with _tenant(org, user):
        result = OperationsMutation().rotate_outbound_webhook_secret(
            _info(user),
            input=RotateOutboundWebhookSecretInput(id="00000000-0000-0000-0000-000000000000"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_rotate_secret_requires_permission(permission_resolver):
    org, user, sub = _scaffold()
    # No grant.
    with _tenant(org, user):
        result = OperationsMutation().rotate_outbound_webhook_secret(
            _info(user), input=RotateOutboundWebhookSecretInput(id=str(sub.guid))
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
