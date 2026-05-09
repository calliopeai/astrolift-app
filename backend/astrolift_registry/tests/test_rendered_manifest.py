"""Tests for the astrolift_rendered_manifest GraphQL resolver.

The resolver glues together: app lookup, environment defaulting,
parser, normalizer, renderer. Each test exercises one decision boundary.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _scaffold():
    """Build the Org/Team/Project triple plus a TenantCluster.

    ``ProviderPlugin.version`` is a CharField, but ``BaseCoreModel.save``
    increments a numeric ``version`` field on every save. We bulk_create
    to skip that path — the test only needs a valid FK target, not a
    saved row with the audit fields populated.
    """
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug="demo"
    )
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Local",
                slug="local",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="local",
        slug="local",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    return org, team, project, cluster


def _make_app(org, team, project, *, manifest: str = "", slug: str = "hello") -> RegisteredApp:
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug=slug,
        manifest_raw=manifest,
        provisioning_status="ready",
    )


VALID_MANIFEST = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
replicas = 2

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

    [workloads.containers.healthcheck]
    kind = "http"
    value = "/healthz"
"""


# ---- happy path -------------------------------------------------------


def test_returns_rendered_resources_for_default_environment(permission_resolver):
    org, team, project, cluster = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, team, project, manifest=VALID_MANIFEST)
    AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="prod"
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_rendered_manifest(_info(), app_slug=app.slug)

    assert result is not None
    assert result.error is None
    assert result.environment_name == "prod"
    assert result.image_tag == "preview"  # default when not supplied
    assert result.namespace.endswith("-hello") or result.namespace == "acme-hello"
    kinds = {r["kind"] for r in result.resources}
    # web deployment + service for the port we declared.
    assert {"Deployment", "Service"} <= kinds


def test_image_tag_override_propagates_to_container_image(permission_resolver):
    org, team, project, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, team, project, manifest=VALID_MANIFEST)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_rendered_manifest(
            _info(), app_slug=app.slug, image_tag="abc123"
        )

    dep = next(r for r in result.resources if r["kind"] == "Deployment")
    container_image = dep["spec"]["template"]["spec"]["containers"][0]["image"]
    assert container_image.endswith(":abc123")


def test_environment_name_filter_picks_named_env(permission_resolver):
    org, team, project, cluster = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, team, project, manifest=VALID_MANIFEST)
    AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="prod"
    )
    AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="staging"
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_rendered_manifest(
            _info(), app_slug=app.slug, environment_name="staging"
        )

    assert result.environment_name == "staging"


def test_no_environment_yet_falls_back_to_preview(permission_resolver):
    """An app can have a manifest before any AppEnvironment is wired
    up — show the 'preview' env so users can iterate on the TOML."""
    org, team, project, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, team, project, manifest=VALID_MANIFEST)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_rendered_manifest(
            _info(), app_slug=app.slug
        )

    assert result.environment_name == "preview"
    assert result.error is None


# ---- error paths ------------------------------------------------------


def test_unknown_app_returns_none(permission_resolver):
    org, _, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_rendered_manifest(
            _info(), app_slug="does-not-exist"
        )

    assert result is None


def test_invalid_toml_surfaces_as_error_not_exception(permission_resolver):
    """A bad manifest should NOT 500 the resolver. The UI renders the
    error and lets the user fix the TOML in-place."""
    org, team, project, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, team, project, manifest="name = [unterminated")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_rendered_manifest(
            _info(), app_slug=app.slug
        )

    assert result is not None
    assert result.error is not None
    assert "TOML" in result.error
    assert result.resources == []


def test_invalid_workload_kind_is_a_render_error(permission_resolver):
    org, team, project, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    bad = """
name = "hello"

[[workloads]]
name = "web"
kind = "spaceship"
"""
    app = _make_app(org, team, project, manifest=bad)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_rendered_manifest(
            _info(), app_slug=app.slug
        )

    assert result.error is not None
    assert "kind" in result.error.lower()
    assert result.error_path  # path tells the UI which key was bad
    # The semantic-error locator finds the offending `kind = ...` line
    # so editors can highlight it without re-parsing the source.
    assert result.error_line is not None and result.error_line >= 1


def test_toml_syntax_error_carries_line_and_column(permission_resolver):
    """Bad TOML surfaces lineno + colno from tomllib so the editor
    UI can red-squiggle the offending position."""
    org, team, project, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(
        org,
        team,
        project,
        manifest='name = "hello"\n[[workloads]]\nkind = (\n',
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryQuery().astrolift_rendered_manifest(
            _info(), app_slug=app.slug
        )

    assert result.error is not None
    assert "TOML" in result.error
    assert result.error_line is not None
    assert result.error_column is not None
