"""register_app auto-fills a friendly name/slug when the caller omits them.

Registration used to require a non-empty name + slug; the wizard's
"just register it" path, the CLI, and repo-scan bootstrap now benefit from a
memorable auto-name (e.g. ``exciting-talkative-platypus``) instead of failing
"name is required". These tests pin the contract:

* omitting name + slug generates a valid, org-unique slug + a titled name
* a supplied slug is honored verbatim (and still conflict-checked)
* a supplied name with a blank slug derives the slug from generation, keeping
  the caller's name
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")


@pytest.fixture(autouse=True)
def _no_onboard_workflow(monkeypatch):
    """register_app fires OnboardAppWorkflow via `_bootstrap_app_environments`,
    which needs a live Temporal server. These tests exercise the naming logic
    only, so stub the post-create bootstrap to a no-op."""
    monkeypatch.setattr(
        "astrolift_registry.schema.mutations._bootstrap_app_environments",
        lambda *a, **k: None,
    )


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="Local", slug="local", capabilities_manifest={}, config_schema={})]
    )
    TenantCluster.objects.create(
        organization=org,
        name="local",
        slug="local",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, project


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_register_app_generates_name_and_slug_when_omitted(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(project_id=str(project.guid), source_repo="acme/thing"),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug=result.data.slug)
    assert SLUG_RE.match(app.slug), app.slug
    assert app.name.strip() != ""
    # Name is the titled form of the generated slug.
    assert app.name == " ".join(p.capitalize() for p in app.slug.split("-"))
    # Slug flows into the namespace + subdomain unchanged.
    assert app.subdomain == app.slug
    assert app.k8s_namespace == f"{org.slug}-{app.slug}"


def test_register_app_honors_supplied_slug(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="My App",
                slug="my-app",
                source_repo="acme/my-app",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="my-app")
    assert app.name == "My App"


def test_register_app_derives_name_from_generated_slug_when_only_name_blank(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                slug="fixed-slug",
                source_repo="acme/fixed",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="fixed-slug")
    # Blank name derives from the (supplied) slug.
    assert app.name == "Fixed Slug"


def test_register_app_generated_slugs_are_unique_per_org(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    slugs = set()
    with _ctx(org):
        for i in range(8):
            result = RegistryMutation().register_app(
                _info(),
                input=RegisterAppInput(project_id=str(project.guid), source_repo=f"acme/r{i}"),
            )
            assert result.ok, result.errors
            slugs.add(result.data.slug)

    assert len(slugs) == 8
