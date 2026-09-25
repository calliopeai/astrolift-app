"""One org's app label per shared managed zone (#1930).

Hostnames carry no org, and every org without its own zone resolves to the
shared zone, so org A registering (or renaming to) org B's label got B's
hostname. The second claim is refused before any DNS write or render.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.hostname_claims import hostname_label_refusal
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation, SetAppSubdomainInput
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_workflows(monkeypatch):
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *a, **k: None)


@pytest.fixture
def shared():
    return ManagedDomain.objects.create(
        organization=None, zone=f"apps-{uuid.uuid4().hex[:6]}.test", default_for=ManagedDomain.DefaultFor.BOTH
    )


def _org(tag):
    org = Organization.objects.create(name=tag, slug=f"{tag}-{uuid.uuid4().hex[:6]}")
    team = Team.objects.create(organization=org, name="t", slug=f"t-{uuid.uuid4().hex[:4]}")
    project = Project.objects.create(organization=org, team=team, name="p", slug=f"p-{uuid.uuid4().hex[:4]}")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Local", slug=f"local-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    TenantCluster.objects.create(
        organization=org,
        name="local",
        slug=f"local-{uuid.uuid4().hex[:6]}",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, project


def _app(org, project, slug, subdomain=""):
    return RegisteredApp.objects.create(
        organization=org, team=project.team, project=project, name=slug, slug=slug, subdomain=subdomain
    )


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def test_register_refuses_another_orgs_label_in_the_shared_zone(shared, permission_resolver):
    permission_resolver.grant(Permission.APP_CREATE)
    a, a_project = _org("a")
    b, b_project = _org("b")
    _app(b, b_project, "web")

    with tenant_context(TenantContext(organization_id=a.id)):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(a_project.guid), name="web", slug="web", source_repo="a/web"
            ),
        )

    assert result.ok is False and result.errors[0].code == "CONFLICT", result
    assert not RegisteredApp.objects.filter(organization=a).exists()


def test_set_subdomain_refuses_another_orgs_label_in_the_shared_zone(shared, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    a, a_project = _org("a")
    b, b_project = _org("b")
    _app(b, b_project, "shop", subdomain="web")
    mine = _app(a, a_project, "site")

    with tenant_context(TenantContext(organization_id=a.id)):
        result = RegistryMutation().set_app_subdomain(
            _info(), input=SetAppSubdomainInput(id=GUID(str(mine.guid)), subdomain="web")
        )

    assert result.ok is False and result.errors[0].code == "CONFLICT", result
    mine.refresh_from_db()
    assert mine.subdomain == ""


def test_an_org_with_its_own_zone_is_not_bound_by_the_shared_one(shared):
    a, _ = _org("a")
    b, b_project = _org("b")
    _app(b, b_project, "web")
    ManagedDomain.objects.create(
        organization=a, zone=f"a-{uuid.uuid4().hex[:6]}.test", default_for=ManagedDomain.DefaultFor.BOTH
    )

    assert hostname_label_refusal("web", organization=a) is None


def test_the_other_orgs_own_zone_frees_the_label_in_the_shared_one(shared):
    a, _ = _org("a")
    b, b_project = _org("b")
    _app(b, b_project, "web")
    ManagedDomain.objects.create(
        organization=b, zone=f"b-{uuid.uuid4().hex[:6]}.test", default_for=ManagedDomain.DefaultFor.BOTH
    )

    assert hostname_label_refusal("web", organization=a) is None


def test_the_same_org_and_deleted_apps_do_not_block(shared):
    a, a_project = _org("a")
    b, b_project = _org("b")
    _app(a, a_project, "web")
    _app(b, b_project, "api").soft_delete()

    assert hostname_label_refusal("web", organization=a) is None
    assert hostname_label_refusal("api", organization=a) is None


def test_no_shared_zone_no_refusal():
    a, _ = _org("a")
    b, b_project = _org("b")
    _app(b, b_project, "web")

    assert hostname_label_refusal("web", organization=a) is None
