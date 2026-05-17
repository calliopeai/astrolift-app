"""Tests for register/unregister device GraphQL mutations (#490)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from astrolift_operations.models import DeviceRegistration
from astrolift_operations.schema.mutations import (
    OperationsMutation,
    RegisterAstroliftDeviceInput,
    UnregisterAstroliftDeviceInput,
)
from astrolift_operations.schema.queries import OperationsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(
        context=SimpleNamespace(user=None, request=None),
    )


def _user(*, email: str = "u@test.invalid"):
    User = get_user_model()
    return User.objects.create_user(
        email=email,
        username=email,
        password="x",
    )


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    user = _user()
    return org, user


def _ctx(org, *, actor_user_id):
    return tenant_context(
        TenantContext(
            organization_id=org.id,
            actor_user_id=actor_user_id,
        ),
    )


# ---- register ------------------------------------------------------


def test_register_device_happy_path(permission_resolver):
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with _ctx(org, actor_user_id=user.id):
        result = OperationsMutation().register_astrolift_device(
            _info(),
            input=RegisterAstroliftDeviceInput(
                token="tok-abc",
                kind="ios",
                label="My phone",
            ),
        )

    assert result.ok, result.errors
    assert DeviceRegistration.objects.count() == 1
    row = DeviceRegistration.objects.first()
    assert row.device_token == "tok-abc"
    assert row.platform == "ios"
    assert row.label == "My phone"
    assert row.user_id == user.id


def test_register_device_rejects_unknown_platform(permission_resolver):
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)
    with _ctx(org, actor_user_id=user.id):
        result = OperationsMutation().register_astrolift_device(
            _info(),
            input=RegisterAstroliftDeviceInput(
                token="tok",
                kind="blackberry",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "kind"


def test_register_device_rejects_empty_token(permission_resolver):
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)
    with _ctx(org, actor_user_id=user.id):
        result = OperationsMutation().register_astrolift_device(
            _info(),
            input=RegisterAstroliftDeviceInput(token="  ", kind="ios"),
        )
    assert not result.ok
    assert result.errors[0].field == "token"


def test_register_device_denied_without_permission(permission_resolver):
    org, user = _scaffold()
    # Don't grant ORG_UPDATE.
    with _ctx(org, actor_user_id=user.id):
        result = OperationsMutation().register_astrolift_device(
            _info(),
            input=RegisterAstroliftDeviceInput(
                token="tok",
                kind="ios",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- unregister ----------------------------------------------------


def test_unregister_device_happy_path(permission_resolver):
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)
    device = DeviceRegistration.objects.create(
        user=user,
        organization=org,
        device_token="tok-1",
        platform="ios",
    )

    with _ctx(org, actor_user_id=user.id):
        result = OperationsMutation().unregister_astrolift_device(
            _info(),
            input=UnregisterAstroliftDeviceInput(
                device_id=GUID(str(device.guid)),
            ),
        )

    assert result.ok, result.errors
    device.refresh_from_db()
    assert device.deleted_at is not None


def test_unregister_device_not_found_returns_failure(
    permission_resolver,
):
    org, user = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)
    with _ctx(org, actor_user_id=user.id):
        result = OperationsMutation().unregister_astrolift_device(
            _info(),
            input=UnregisterAstroliftDeviceInput(
                device_id=GUID("01910000-0000-7000-0000-000000000000"),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- query --------------------------------------------------------


def test_my_devices_returns_only_callers_devices(permission_resolver):
    org, u1 = _scaffold()
    u2 = _user(email="other@x.test")
    DeviceRegistration.objects.create(
        user=u1,
        organization=org,
        device_token="tok-1",
        platform="ios",
    )
    DeviceRegistration.objects.create(
        user=u2,
        organization=org,
        device_token="tok-2",
        platform="android",
    )

    with _ctx(org, actor_user_id=u1.id):
        rows = OperationsQuery().astrolift_my_devices(_info())

    assert len(rows) == 1
    assert rows[0].platform == "ios"


def test_my_devices_hides_soft_deleted(permission_resolver):
    org, u = _scaffold()
    DeviceRegistration.objects.create(
        user=u,
        organization=org,
        device_token="tok-live",
        platform="ios",
    )
    deleted = DeviceRegistration.objects.create(
        user=u,
        organization=org,
        device_token="tok-revoked",
        platform="android",
    )
    deleted.soft_delete()

    with _ctx(org, actor_user_id=u.id):
        rows = OperationsQuery().astrolift_my_devices(_info())

    assert len(rows) == 1
    assert rows[0].platform == "ios"
