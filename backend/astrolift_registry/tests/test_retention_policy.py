"""Tests for the setRetentionPolicy mutation (#742)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, RetentionPolicy
from astrolift_registry.schema.mutations import RegistryMutation, SetRetentionPolicyInput
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


def _scaffold():
    User = get_user_model()
    org = Organization.objects.create(name="Acme", slug="acme-rp")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-rp")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-rp")
    actor = User.objects.create(username="actor-rp@test", email="actor-rp@test")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="RetApp",
        slug="ret-app",
        provisioning_status="ready",
        subdomain="ret-app",
    )
    return org, actor, app


def _ctx(org, actor=None):
    return tenant_context(
        TenantContext(
            organization_id=org.id,
            actor_user_id=actor.id if actor is not None else None,
        )
    )


def test_creates_new_policy(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        result = RegistryMutation().set_retention_policy(
            _info(actor),
            input=SetRetentionPolicyInput(app_slug=app.slug, signal="logs", retention_days=30),
        )

    assert result.ok, result.errors
    assert result.data.signal == "logs"
    assert result.data.retention_days == 30
    assert RetentionPolicy.objects.filter(registered_app=app, signal="logs", deleted_at__isnull=True).count() == 1


def test_updates_existing_policy(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        RegistryMutation().set_retention_policy(
            _info(actor),
            input=SetRetentionPolicyInput(app_slug=app.slug, signal="metrics", retention_days=14),
        )
        result = RegistryMutation().set_retention_policy(
            _info(actor),
            input=SetRetentionPolicyInput(app_slug=app.slug, signal="metrics", retention_days=60),
        )

    assert result.ok, result.errors
    assert result.data.retention_days == 60
    assert RetentionPolicy.objects.filter(registered_app=app, signal="metrics", deleted_at__isnull=True).count() == 1


def test_rejects_invalid_signal(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        result = RegistryMutation().set_retention_policy(
            _info(actor),
            input=SetRetentionPolicyInput(app_slug=app.slug, signal="bad_signal", retention_days=30),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "signal"


def test_rejects_zero_retention_days(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        result = RegistryMutation().set_retention_policy(
            _info(actor),
            input=SetRetentionPolicyInput(app_slug=app.slug, signal="traces", retention_days=0),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "retentionDays"


def test_rejects_unknown_app(permission_resolver):
    org, actor, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        result = RegistryMutation().set_retention_policy(
            _info(actor),
            input=SetRetentionPolicyInput(app_slug="does-not-exist", signal="logs", retention_days=30),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"


def test_denied_without_permission():
    org, actor, app = _scaffold()

    with _ctx(org, actor):
        result = RegistryMutation().set_retention_policy(
            _info(actor),
            input=SetRetentionPolicyInput(app_slug=app.slug, signal="logs", retention_days=30),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert RetentionPolicy.objects.filter(registered_app=app).count() == 0


def test_all_four_signals_accepted(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    for signal in ("logs", "metrics", "traces", "audit_events"):
        with _ctx(org, actor):
            result = RegistryMutation().set_retention_policy(
                _info(actor),
                input=SetRetentionPolicyInput(app_slug=app.slug, signal=signal, retention_days=7),
            )
        assert result.ok, f"{signal}: {result.errors}"

    assert RetentionPolicy.objects.filter(registered_app=app, deleted_at__isnull=True).count() == 4
