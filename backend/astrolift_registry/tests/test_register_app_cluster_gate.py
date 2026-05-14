"""Tests for the cluster-precondition gate on register_app (#315).

An app is a deployment target. Registering one against an
organization that has zero active clusters produces an app that
can't deploy — the downstream workflow fails with an opaque "no
cluster available" error. The mutation refuses up front so the
wizard can surface the actionable "connect a cluster first" empty
state.

These tests pin three boundaries:

* zero clusters -> PRECONDITION envelope, no row written
* one active cluster -> happy path, row written
* one soft-deleted cluster (no active rows) -> PRECONDITION
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold_org():
    org = Organization.objects.create(name="Acme", slug="acme-gate")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-gate")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo-gate",
    )
    return org, project


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_register_app_rejects_when_org_has_no_clusters(permission_resolver):
    org, project = _scaffold_org()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="App",
                slug="no-clusters",
                source_repo="acme/no-clusters",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "cluster" in result.errors[0].message.lower()
    assert result.errors[0].field is None
    assert not RegisteredApp.objects.filter(slug="no-clusters").exists()


def test_register_app_accepts_when_org_has_active_cluster(permission_resolver, seed_cluster):
    org, project = _scaffold_org()
    seed_cluster(org, slug="gate-active")
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="App",
                slug="with-cluster",
                source_repo="acme/with-cluster",
            ),
        )

    assert result.ok, result.errors
    assert RegisteredApp.objects.filter(slug="with-cluster").exists()


def test_register_app_rejects_when_only_soft_deleted_clusters(permission_resolver, seed_cluster):
    """Soft-deleted clusters do not satisfy the precondition. A row
    that's been removed from the catalogue can't accept a deploy, so
    the wizard should still steer the operator at /clusters."""
    org, project = _scaffold_org()
    cluster = seed_cluster(org, slug="gate-soft-deleted")
    cluster.soft_delete()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="App",
                slug="only-soft-deleted",
                source_repo="acme/only-soft-deleted",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert not RegisteredApp.objects.filter(slug="only-soft-deleted").exists()


def test_register_app_rejects_when_only_inactive_clusters(permission_resolver, seed_cluster):
    """Inactive clusters (``is_active=False``) are likewise unfit
    deploy targets — the precondition counts only active rows."""
    org, project = _scaffold_org()
    cluster = seed_cluster(org, slug="gate-inactive")
    cluster.is_active = False
    cluster.save(update_fields=["is_active", "updated_at", "version"])
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="App",
                slug="only-inactive",
                source_repo="acme/only-inactive",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert not RegisteredApp.objects.filter(slug="only-inactive").exists()
