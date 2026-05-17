"""Tests for the ``astroliftWorkloadManifest`` GraphQL resolver (#430).

The resolver:
  - filters the app-wide rendered manifest down to one workload's
    resources via the ``astrolift.dev/workload`` label
  - looks up the most recent prior deployment for diff context
  - renders the manifest at the prior image tag when the tags differ

The full renderer + parser + normalizer are covered by their own
suites; here we test the filtering + diff-pairing decision boundaries
specific to the workload-scoped query.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _scaffold():
    org = Organization.objects.create(name="Acme M", slug="acme-m")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-m")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-m")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Local",
                slug="local-m",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug="prod-m",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    return org, team, project, cluster


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


# Two workloads so we can verify the filter doesn't leak resources
# across siblings on the same app.
TWO_WORKLOAD_MANIFEST = """
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

[[workloads]]
name = "worker"
kind = "deployment"
replicas = 1

  [[workloads.containers]]
  name = "worker"
  is_primary = true
"""


def _make_app(org, team, project, manifest: str = TWO_WORKLOAD_MANIFEST) -> RegisteredApp:
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-m",
        manifest_raw=manifest,
        provisioning_status="ready",
    )


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ----------------------------------------------------------------------
# happy path: filter scopes to one workload
# ----------------------------------------------------------------------


def test_workload_manifest_filters_to_one_workload(permission_resolver):
    org, team, project, cluster = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, team, project)
    AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="prod")

    with _tenant(org):
        result = RegistryQuery().astrolift_workload_manifest(
            _info(),
            app_slug=app.slug,
            workload_slug="web",
        )

    assert result is not None
    assert result.workload_slug == "web"
    assert result.error is None
    # Every returned resource must carry the workload label set to "web".
    for resource in result.resources:
        labels = (resource.get("metadata") or {}).get("labels") or {}
        assert labels.get("astrolift.dev/workload") == "web"
    # And the "worker" workload's deployment must NOT appear.
    names = {r.get("metadata", {}).get("name") for r in result.resources}
    assert "worker" not in names


def test_workload_manifest_unknown_app_returns_null(permission_resolver):
    org, _team, _project, _cluster = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = RegistryQuery().astrolift_workload_manifest(
            _info(),
            app_slug="nope",
            workload_slug="web",
        )
    assert result is None


def test_workload_manifest_unknown_workload_returns_empty_resources(permission_resolver):
    """A workload slug that doesn't exist in the manifest → empty
    resources list with no error. The FE renders the empty-state copy."""
    org, team, project, _cluster = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, team, project)
    with _tenant(org):
        result = RegistryQuery().astrolift_workload_manifest(
            _info(),
            app_slug=app.slug,
            workload_slug="ghost",
        )
    assert result is not None
    assert result.error is None
    assert result.resources == []


# ----------------------------------------------------------------------
# diff-against-previous behaviour
# ----------------------------------------------------------------------


def test_previous_image_tag_populated_from_prior_deployment(permission_resolver):
    """When a prior RUNNING deployment exists for the same env, its
    image tag + GUID are surfaced and the prior-rendered resources
    are included."""
    org, team, project, cluster = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, team, project)
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="prod")
    prior = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v1.2.3",
    )

    with _tenant(org):
        result = RegistryQuery().astrolift_workload_manifest(
            _info(),
            app_slug=app.slug,
            workload_slug="web",
            image_tag="v1.2.4",  # different tag → diff is meaningful
        )

    assert result is not None
    assert result.previous_image_tag == "v1.2.3"
    assert result.previous_deployment_id == str(prior.guid)
    assert result.resources_previous, "should render the prior manifest for diff"
    # The previous-rendered Deployment should carry the prior image tag.
    prior_dep = next(r for r in result.resources_previous if r["kind"] == "Deployment")
    image = prior_dep["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image.endswith(":v1.2.3")


def test_diff_skipped_when_current_image_matches_prior(permission_resolver):
    """No diff to render when the current image tag already matches
    the most recent deployment — saves the redundant render pass."""
    org, team, project, cluster = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, team, project)
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="prod")
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v1.2.3",
    )
    with _tenant(org):
        result = RegistryQuery().astrolift_workload_manifest(
            _info(),
            app_slug=app.slug,
            workload_slug="web",
            image_tag="v1.2.3",
        )
    assert result is not None
    # The diff finder excludes rows with the same image tag, so
    # there's no prior to render against.
    assert result.previous_image_tag == ""
    assert result.resources_previous == []


def test_diff_only_considers_running_or_terminal_deployments(permission_resolver):
    """A pending / failed / deploying deployment shouldn't serve as a
    diff baseline."""
    org, team, project, cluster = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, team, project)
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="prod")
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.FAILED.value,
        image_tag="never-applied",
    )
    with _tenant(org):
        result = RegistryQuery().astrolift_workload_manifest(
            _info(),
            app_slug=app.slug,
            workload_slug="web",
        )
    assert result is not None
    assert result.previous_image_tag == ""
    assert result.previous_deployment_id == ""


# ----------------------------------------------------------------------
# permission gate
# ----------------------------------------------------------------------


def test_workload_manifest_permission_denied_raises(permission_resolver):
    org, team, project, _cluster = _scaffold()
    app = _make_app(org, team, project)
    with _tenant(org), pytest.raises(PermissionDenied):
        RegistryQuery().astrolift_workload_manifest(
            _info(),
            app_slug=app.slug,
            workload_slug="web",
        )
