"""Tests for the ``setCustomDomainPaused`` mutation and ``CustomDomain.paused``."""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization
from astrolift_lifecycle.models import AppEnvironment, CustomDomain
from astrolift_lifecycle.schema.mutations import LifecycleMutation, SetCustomDomainPausedInput
from astrolift_lifecycle.schema.types import app_domain_to_type
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


@pytest.fixture
def domain(app):
    return CustomDomain.objects.create(
        registered_app=app,
        hostname="www.example.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        is_active=True,
    )


@pytest.fixture
def reapplied(monkeypatch):
    """The re-apply is Temporal-backed; record what it was asked to do instead."""
    calls = []

    def fake(app, *, environment_name, actor, organization_id):
        calls.append(environment_name)
        return []

    monkeypatch.setattr("astrolift_lifecycle.reprovision.reprovision", fake)
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.mutations.helpers._deploy_pipeline_disabled", lambda: False
    )
    return calls


def _set(domain, paused, info, org, actor):
    with _tenant_for(org, actor):
        return LifecycleMutation().set_custom_domain_paused(
            info=info, input=SetCustomDomainPausedInput(id=str(domain.guid), paused=paused)
        )


@pytest.mark.django_db
def test_paused_defaults_to_false(domain):
    domain.refresh_from_db()
    assert domain.paused is False
    assert app_domain_to_type(domain).is_paused is False


@pytest.mark.django_db
def test_pause_then_resume(domain, fake_info, org, actor, permission_resolver, reapplied):
    permission_resolver.grant(Permission.APP_UPDATE)
    result = _set(domain, True, fake_info, org, actor)
    assert result.ok is True, result.errors
    assert result.data.is_paused is True
    domain.refresh_from_db()
    assert domain.paused is True
    result = _set(domain, False, fake_info, org, actor)
    assert result.ok is True and result.data.is_paused is False
    domain.refresh_from_db()
    assert domain.paused is False


@pytest.mark.django_db
def test_idempotent_changes_nothing(domain, fake_info, org, actor, permission_resolver, reapplied):
    permission_resolver.grant(Permission.APP_UPDATE)
    domain.paused = True
    domain.save(update_fields=["paused", "updated_at", "version"])
    version = domain.version
    result = _set(domain, True, fake_info, org, actor)
    assert result.ok is True and result.data.is_paused is True
    domain.refresh_from_db()
    assert domain.version == version
    assert reapplied == []


@pytest.mark.django_db
def test_unknown_id_is_not_found(fake_info, org, actor, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    with _tenant_for(org, actor):
        result = LifecycleMutation().set_custom_domain_paused(
            info=fake_info,
            input=SetCustomDomainPausedInput(id="00000000-0000-0000-0000-000000000000", paused=True),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value


@pytest.mark.django_db
def test_other_orgs_domain_is_not_found(domain, fake_info, actor, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    other = Organization.objects.create(name="Other", slug="other-pause")
    result = _set(domain, True, fake_info, other, actor)
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    domain.refresh_from_db()
    assert domain.paused is False


@pytest.mark.django_db
def test_denied_without_app_update(domain, fake_info, org, actor, permission_resolver):
    result = _set(domain, True, fake_info, org, actor)
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    domain.refresh_from_db()
    assert domain.paused is False


@pytest.mark.django_db
def test_change_reapplies_each_non_preview_environment(
    domain, app, cluster, fake_info, org, actor, permission_resolver, reapplied
):
    permission_resolver.grant(Permission.APP_UPDATE)
    prod = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="production")
    AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="pr-1", previewed_environment=prod
    )
    assert _set(domain, True, fake_info, org, actor).ok is True
    assert reapplied == ["production"]


@pytest.mark.django_db
def test_reapply_failure_does_not_fail_the_mutation(
    domain, app, cluster, fake_info, org, actor, permission_resolver, monkeypatch, caplog
):
    permission_resolver.grant(Permission.APP_UPDATE)
    AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="production")

    def boom(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr("astrolift_lifecycle.reprovision.reprovision", boom)
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.mutations.helpers._deploy_pipeline_disabled", lambda: False
    )
    result = _set(domain, True, fake_info, org, actor)
    assert result.ok is True
    domain.refresh_from_db()
    assert domain.paused is True
    assert "could not re-apply" in caplog.text
