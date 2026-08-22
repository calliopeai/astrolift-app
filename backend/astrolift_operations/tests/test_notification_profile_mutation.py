"""Tests for ``set_notification_profile`` (#490).

The policy module ``astrolift_operations.notification_profile`` had no
production caller: there was no write path for a NotificationProfile at
all, so a row with a misspelled driver or a config missing a required
key persisted from a shell and then died at send time as a
``status=no_driver`` audit with nothing telling the operator why. These
tests pin the mutation to the policy module, and pin the write path to
the dispatcher's read path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization
from astrolift_operations.models import NotificationProfile
from astrolift_operations.notification_dispatch import (
    default_driver_slug_for_registration,
    unset_driver_override_for_tests,
)
from astrolift_operations.schema.mutations import (
    OperationsMutation,
    SetNotificationProfileInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


_VALID_SNS_CONFIG = {
    "region": "us-west-2",
    "platform_applications": {"ios": "arn:aws:sns:us-west-2:111:app/APNS/ios"},
}


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme-notif-profile")
    user = get_user_model().objects.create(username="op-profile", email="op-profile@test")
    return org, user


def _set(user, org, **kwargs):
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        return OperationsMutation().set_notification_profile(
            _info(user), input=SetNotificationProfileInput(**kwargs)
        )


def test_set_notification_profile_creates_validated_row(permission_resolver):
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _set(user, org, driver="aws_sns", config=_VALID_SNS_CONFIG)

    assert result.ok, result.errors
    assert result.data.driver == "aws_sns"
    assert result.data.retention_delivery_days == 30
    row = NotificationProfile.objects.get(organization=org)
    assert row.driver == "aws_sns"
    assert row.config == _VALID_SNS_CONFIG
    assert row.is_active is True


def test_set_notification_profile_rejects_unknown_driver(permission_resolver):
    """The exact shape that used to persist and then silently drop every
    alert as ``no_driver``."""
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _set(user, org, driver="bogus_driver", config={})

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "unknown notification driver" in result.errors[0].message
    assert not NotificationProfile.objects.filter(organization=org).exists()


def test_set_notification_profile_rejects_missing_required_key(permission_resolver):
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _set(user, org, driver="aws_sns", config={"region": "us-west-2"})

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "platform_applications" in result.errors[0].message
    assert not NotificationProfile.objects.filter(organization=org).exists()


def test_set_notification_profile_rejects_non_arn_platform_value(permission_resolver):
    """The policy module's per-driver extras run too, not just the
    required-key check."""
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _set(
        user,
        org,
        driver="aws_sns",
        config={"region": "us-west-2", "platform_applications": {"ios": "x"}},
    )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "PlatformApplicationArn" in result.errors[0].message


def test_set_notification_profile_rejects_nested_multiplexer(permission_resolver):
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _set(
        user,
        org,
        driver="multiplexer",
        config={
            "primary": {"driver": "multiplexer", "config": {}},
            "secondaries": [{"driver": "gcp_fcm", "config": {"project_id": "demo"}}],
        },
    )

    assert not result.ok
    assert "cannot itself be a multiplexer" in result.errors[0].message


def test_set_notification_profile_rejects_retention_over_max(permission_resolver):
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _set(
        user,
        org,
        driver="gcp_fcm",
        config={"project_id": "demo"},
        retention_delivery_days=400,
    )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "retentionDeliveryDays"
    assert not NotificationProfile.objects.filter(organization=org).exists()


def test_set_notification_profile_upserts_the_active_row(permission_resolver):
    """One active profile per org is a DB constraint, so a re-submit has
    to rewrite the live row rather than insert a second one."""
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    first = _set(user, org, driver="aws_sns", config=_VALID_SNS_CONFIG)
    assert first.ok, first.errors
    second = _set(
        user,
        org,
        driver="gcp_fcm",
        config={"project_id": "demo"},
        retention_delivery_days=90,
    )

    assert second.ok, second.errors
    assert second.data.id == first.data.id
    assert NotificationProfile.objects.filter(organization=org).count() == 1
    row = NotificationProfile.objects.get(organization=org)
    assert row.driver == "gcp_fcm"
    assert row.config == {"project_id": "demo"}
    assert row.retention_delivery_days == 90


def test_set_notification_profile_requires_org_update(permission_resolver):  # noqa: ARG001
    org, user = _scaffold()

    result = _set(user, org, driver="gcp_fcm", config={"project_id": "demo"})

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert not NotificationProfile.objects.filter(organization=org).exists()


def test_profile_written_by_the_mutation_is_read_by_the_dispatcher(permission_resolver):
    """Closes the loop: what the mutation writes is what
    ``notification_dispatch`` resolves for the org."""
    unset_driver_override_for_tests()
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)
    assert default_driver_slug_for_registration(organization_id=org.id) == "unconfigured"

    result = _set(user, org, driver="gcp_fcm", config={"project_id": "demo"})

    assert result.ok, result.errors
    assert default_driver_slug_for_registration(organization_id=org.id) == "gcp_fcm"
