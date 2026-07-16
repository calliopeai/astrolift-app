"""Tests for archiveApp / restoreApp mutations (#743)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.mutations import ArchiveAppInput, RegistryMutation, RestoreAppInput
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


def _scaffold():
    User = get_user_model()
    org = Organization.objects.create(name="Acme", slug="acme-ar")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-ar")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-ar")
    actor = User.objects.create(username="actor-ar@test", email="actor-ar@test")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="ArApp",
        slug="ar-app",
        provisioning_status="ready",
        subdomain="ar-app",
    )
    return org, actor, app


def _add_workloads(app, actor, replicas_list):
    wls = []
    for i, r in enumerate(replicas_list):
        wl = Workload.objects.create(
            registered_app=app,
            name=f"web-{i}",
            slug=f"web-{i}",
            kind="deployment",
            replicas=r,
            created_by=actor,
            updated_by=actor,
        )
        wls.append(wl)
    return wls


def _ctx(org, actor=None):
    return tenant_context(
        TenantContext(
            organization_id=org.id,
            actor_user_id=actor.id if actor is not None else None,
        )
    )


def test_archive_sets_archived_at_and_zeros_replicas(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    wls = _add_workloads(app, actor, [3, 2])

    with _ctx(org, actor):
        result = RegistryMutation().archive_app(_info(actor), input=ArchiveAppInput(app_slug=app.slug))

    assert result.ok, result.errors
    assert result.data.is_archived is True
    app.refresh_from_db()
    assert app.archived_at is not None
    assert app.archived_by_id == actor.id
    for wl in wls:
        wl.refresh_from_db()
        assert wl.replicas == 0
        assert wl.pre_archive_replicas in (3, 2)


def test_archive_idempotent(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        RegistryMutation().archive_app(_info(actor), input=ArchiveAppInput(app_slug=app.slug))
        first_at = RegisteredApp.objects.get(pk=app.pk).archived_at
        result = RegistryMutation().archive_app(_info(actor), input=ArchiveAppInput(app_slug=app.slug))

    assert result.ok, result.errors
    assert RegisteredApp.objects.get(pk=app.pk).archived_at == first_at


def test_restore_clears_archived_at_and_restores_replicas(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    wls = _add_workloads(app, actor, [3, 2])

    with _ctx(org, actor):
        RegistryMutation().archive_app(_info(actor), input=ArchiveAppInput(app_slug=app.slug))
        result = RegistryMutation().restore_app(_info(actor), input=RestoreAppInput(app_slug=app.slug))

    assert result.ok, result.errors
    assert result.data.is_archived is False
    app.refresh_from_db()
    assert app.archived_at is None
    expected = {wl.pk: wl for wl in wls}
    for wl_pk, original_wl in expected.items():
        wl = Workload.objects.get(pk=wl_pk)
        assert wl.replicas == original_wl.replicas


def test_restore_idempotent_when_not_archived(permission_resolver):
    org, actor, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        result = RegistryMutation().restore_app(_info(actor), input=RestoreAppInput(app_slug=app.slug))

    assert result.ok, result.errors
    assert result.data.is_archived is False


def test_archive_denied_without_permission():
    org, actor, app = _scaffold()

    with _ctx(org, actor):
        result = RegistryMutation().archive_app(_info(actor), input=ArchiveAppInput(app_slug=app.slug))

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_archive_rejects_unknown_app(permission_resolver):
    org, actor, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        result = RegistryMutation().archive_app(
            _info(actor), input=ArchiveAppInput(app_slug="does-not-exist")
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
