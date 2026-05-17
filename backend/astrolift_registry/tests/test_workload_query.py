"""Tests for the astrolift_workload (single by slug) resolver."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
    )
    return org, app


def test_returns_workload_by_app_and_slug(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    Workload.objects.create(registered_app=app, name="Web", slug="web", kind="deployment")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_workload(_info(), app_slug="hello-app", slug="web")

    assert result is not None
    assert result.slug == "web"
    assert result.kind == "deployment"


def test_returns_none_for_unknown_workload(permission_resolver):
    org, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_workload(_info(), app_slug="hello-app", slug="ghost")

    assert result is None


def test_returns_none_when_app_slug_does_not_match(permission_resolver):
    """Resolver must enforce the app+slug pair, not just slug. A user
    asking for ``hello-app/web`` shouldn't see a workload that
    belongs to a different app even if slugs collide."""
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    Workload.objects.create(registered_app=app, name="Web", slug="web", kind="deployment")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_workload(_info(), app_slug="other-app", slug="web")

    assert result is None


def test_in_cluster_service_fqdn_uses_explicit_namespace(permission_resolver):
    """When the app row pins an explicit ``k8s_namespace`` the FQDN
    surfaces that namespace verbatim — operators sometimes override
    the renderer default and the in-cluster DNS name has to follow.
    (#429)"""
    org, app = _scaffold()
    app.k8s_namespace = "acme-prod"
    app.save(update_fields=["k8s_namespace"])
    permission_resolver.grant(Permission.APP_READ)
    Workload.objects.create(registered_app=app, name="Web", slug="web", kind="deployment")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_workload(_info(), app_slug="hello-app", slug="web")

    assert result is not None
    assert result.in_cluster_service_fqdn == "web.acme-prod.svc.cluster.local"


def test_in_cluster_service_fqdn_falls_back_to_org_app_slug(permission_resolver):
    """Without an explicit namespace, the FQDN mirrors the renderer
    default (``<orgSlug>-<appSlug>``) so it matches what the manifest
    layer would render. (#429)"""
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    Workload.objects.create(registered_app=app, name="API", slug="api", kind="deployment")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_workload(_info(), app_slug="hello-app", slug="api")

    assert result is not None
    assert result.in_cluster_service_fqdn == "api.acme-hello-app.svc.cluster.local"
