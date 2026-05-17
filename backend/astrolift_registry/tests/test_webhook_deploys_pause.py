"""Tests for the app-global webhook-deploy pause (#399).

Covers:
- ``pauseAstroliftAppWebhookDeploys`` flips the flag + stamps actor /
  timestamp / reason; idempotent on re-fire.
- ``resumeAstroliftAppWebhookDeploys`` clears flag + audit columns;
  idempotent on already-resumed.
- Permission gate (``app.deploy``, deny-by-default).
- Unknown-slug rejection.
- Reason truncation to the 512-char column ceiling.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import (
    PauseAppWebhookDeploysInput,
    RegistryMutation,
    ResumeAppWebhookDeploysInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    User = get_user_model()
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    actor = User.objects.create(username="op@test", email="op@test")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
        subdomain="hello",
    )
    return org, actor, app


def _ctx(org, actor=None):
    return tenant_context(
        TenantContext(
            organization_id=org.id,
            actor_user_id=actor.id if actor is not None else None,
        )
    )


# ---- pause ---------------------------------------------------------------


def test_pause_flips_flag_and_stamps_actor(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)
    assert app.webhook_deploys_paused is False
    assert app.webhook_deploys_paused_at is None

    with _ctx(org, actor):
        result = RegistryMutation().pause_astrolift_app_webhook_deploys(
            _info(),
            input=PauseAppWebhookDeploysInput(app_slug=app.slug, reason="ci storm"),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.webhook_deploys_paused is True
    assert app.webhook_deploys_paused_at is not None
    assert app.webhook_deploys_paused_by_id == actor.id
    assert app.webhook_deploys_pause_reason == "ci storm"
    assert result.data.webhook_deploys_paused is True
    assert result.data.webhook_deploys_paused_by_email == "op@test"
    assert result.data.webhook_deploys_pause_reason == "ci storm"


def test_pause_without_reason_uses_empty_string(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)

    with _ctx(org, actor):
        result = RegistryMutation().pause_astrolift_app_webhook_deploys(
            _info(),
            input=PauseAppWebhookDeploysInput(app_slug=app.slug, reason=None),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.webhook_deploys_pause_reason == ""


def test_pause_idempotent_preserves_original_actor(permission_resolver):
    """Re-firing pause must not overwrite the original timestamp /
    actor / reason — the UI's "paused 3h ago" copy depends on that."""
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)

    with _ctx(org, actor):
        first = RegistryMutation().pause_astrolift_app_webhook_deploys(
            _info(),
            input=PauseAppWebhookDeploysInput(app_slug=app.slug, reason="first"),
        )
    assert first.ok

    app.refresh_from_db()
    original_at = app.webhook_deploys_paused_at
    original_version = app.version

    # Different actor re-fires — the row should not bump.
    other = get_user_model().objects.create(username="other@test", email="other@test")
    with _ctx(org, other):
        second = RegistryMutation().pause_astrolift_app_webhook_deploys(
            _info(),
            input=PauseAppWebhookDeploysInput(app_slug=app.slug, reason="second"),
        )
    assert second.ok

    app.refresh_from_db()
    assert app.webhook_deploys_paused_at == original_at
    assert app.webhook_deploys_paused_by_id == actor.id
    assert app.webhook_deploys_pause_reason == "first"
    # No optimistic-lock bump on the no-op write.
    assert app.version == original_version


def test_pause_truncates_long_reason(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)

    long_reason = "x" * 1024
    with _ctx(org, actor):
        result = RegistryMutation().pause_astrolift_app_webhook_deploys(
            _info(),
            input=PauseAppWebhookDeploysInput(app_slug=app.slug, reason=long_reason),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert len(app.webhook_deploys_pause_reason) == 512


def test_pause_rejects_unknown_slug(permission_resolver):
    org, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)

    with _ctx(org):
        result = RegistryMutation().pause_astrolift_app_webhook_deploys(
            _info(),
            input=PauseAppWebhookDeploysInput(app_slug="does-not-exist"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"


def test_pause_denied_without_permission():
    org, _, app = _scaffold()
    # No grant — deny-by-default.

    with _ctx(org):
        result = RegistryMutation().pause_astrolift_app_webhook_deploys(
            _info(),
            input=PauseAppWebhookDeploysInput(app_slug=app.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    app.refresh_from_db()
    assert app.webhook_deploys_paused is False


# ---- resume --------------------------------------------------------------


def test_resume_clears_flag_and_audit_columns(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)

    with _ctx(org, actor):
        pause = RegistryMutation().pause_astrolift_app_webhook_deploys(
            _info(),
            input=PauseAppWebhookDeploysInput(app_slug=app.slug, reason="storm"),
        )
    assert pause.ok

    with _ctx(org, actor):
        resume = RegistryMutation().resume_astrolift_app_webhook_deploys(
            _info(),
            input=ResumeAppWebhookDeploysInput(app_slug=app.slug),
        )

    assert resume.ok, resume.errors
    app.refresh_from_db()
    assert app.webhook_deploys_paused is False
    assert app.webhook_deploys_paused_at is None
    assert app.webhook_deploys_paused_by_id is None
    assert app.webhook_deploys_pause_reason == ""


def test_resume_idempotent_on_already_resumed(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)

    original_version = app.version
    with _ctx(org, actor):
        result = RegistryMutation().resume_astrolift_app_webhook_deploys(
            _info(),
            input=ResumeAppWebhookDeploysInput(app_slug=app.slug),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.version == original_version


def test_resume_denied_without_permission():
    org, actor, app = _scaffold()
    app.webhook_deploys_paused = True
    app.save(update_fields=["webhook_deploys_paused", "updated_at", "version"])

    with _ctx(org, actor):
        result = RegistryMutation().resume_astrolift_app_webhook_deploys(
            _info(),
            input=ResumeAppWebhookDeploysInput(app_slug=app.slug),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    app.refresh_from_db()
    assert app.webhook_deploys_paused is True


def test_resume_rejects_unknown_slug(permission_resolver):
    org, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)

    with _ctx(org):
        result = RegistryMutation().resume_astrolift_app_webhook_deploys(
            _info(),
            input=ResumeAppWebhookDeploysInput(app_slug="does-not-exist"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"
