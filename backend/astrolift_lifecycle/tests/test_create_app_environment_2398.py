"""createAppEnvironment adds an environment after registration (#2398).

Before, an app's environments came only from registration and repo resync,
so an app registered with only ``production`` could not gain ``staging``
while its manifest was a staged draft (SteadyMD DV-9250).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import TenantCluster
from astrolift_identity.models import Organization
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.schema.mutations import CreateAppEnvironmentInput, LifecycleMutation
from astrolift_services.models import ManagedService
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_STAGING_CACHE = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"
is_public = true

  [[workloads.containers]]
  name = "web"
  is_primary = true
  port = 8080

[[managed_services]]
kind = "redis"
name = "cache"
environment = "staging"
"""


def _info():
    request = SimpleNamespace(user=None, META={})
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def placed(org, app, cluster, permission_resolver, no_temporal):
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster"])
    AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="production")
    permission_resolver.grant(Permission.APP_UPDATE)
    return SimpleNamespace(org=org, app=app, cluster=cluster)


def _create(org, **fields):
    with tenant_context(TenantContext(organization_id=org.id)):
        return LifecycleMutation().create_app_environment(_info(), input=CreateAppEnvironmentInput(**fields))


def test_creates_a_second_environment_placed_like_the_bootstrap(placed):
    result = _create(placed.org, app_slug=placed.app.slug, name="staging")

    assert result.ok, result.errors
    env = AppEnvironment.objects.get(registered_app=placed.app, name="staging")
    assert env.tenant_cluster_id == placed.cluster.pk
    # Production already renders into the app namespace on this cluster, so
    # staging gets a namespace of its own (#1922).
    assert env.k8s_namespace
    assert result.data.name == "staging"


def test_reconciles_the_managed_services_that_target_it(placed):
    placed.app.manifest_raw = _STAGING_CACHE
    placed.app.save(update_fields=["manifest_raw"])

    result = _create(placed.org, app_slug=placed.app.slug, name="staging")

    assert result.ok, result.errors
    service = ManagedService.objects.get(registered_app=placed.app, kind="redis", deleted_at__isnull=True)
    assert service.app_environment.name == "staging"


def test_refuses_a_name_the_app_already_has(placed):
    result = _create(placed.org, app_slug=placed.app.slug, name="production")

    assert not result.ok
    assert result.errors[0].code == "CONFLICT"


@pytest.mark.parametrize("name", ["", "Staging", "has space", "-lead", "x" * 64, "preview-pr-1"])
def test_refuses_names_that_cannot_be_an_environment(placed, name):
    result = _create(placed.org, app_slug=placed.app.slug, name=name)

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "name"
    assert AppEnvironment.objects.filter(registered_app=placed.app).count() == 1


def test_an_explicit_cluster_of_another_org_is_not_found(placed, provider_plugin):
    other = Organization.objects.create(name="Other", slug="other-2398")
    foreign = TenantCluster.objects.create(
        organization=other,
        name="foreign",
        slug="foreign-2398",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://foreign.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )

    result = _create(placed.org, app_slug=placed.app.slug, name="staging", cluster_slug=foreign.slug)

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert not AppEnvironment.objects.filter(registered_app=placed.app, name="staging").exists()


def test_an_app_of_another_org_is_not_found(placed):
    other = Organization.objects.create(name="Other", slug="other-2398")

    result = _create(other, app_slug=placed.app.slug, name="staging")

    assert not result.ok
    assert result.errors[0].code in {"NOT_FOUND", "PERMISSION_DENIED"}
    assert not AppEnvironment.objects.filter(registered_app=placed.app, name="staging").exists()


def test_without_a_default_cluster_the_caller_must_choose(placed):
    placed.app.default_tenant_cluster = None
    placed.app.save(update_fields=["default_tenant_cluster"])

    result = _create(placed.org, app_slug=placed.app.slug, name="staging")

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"

    result = _create(placed.org, app_slug=placed.app.slug, name="staging", cluster_slug=placed.cluster.slug)
    assert result.ok, result.errors


def test_on_another_cluster_it_still_serves_a_hostname_of_its_own(placed, provider_plugin):
    # Production renders into the app namespace on its cluster and serves the
    # app's bare hostname. An environment on a second cluster that also
    # rendered into the app namespace would serve that same hostname there,
    # and the two clusters' Ingresses would fight over production's DNS.
    second = TenantCluster.objects.create(
        organization=placed.org,
        name="second",
        slug="second-2398",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://second.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )

    result = _create(placed.org, app_slug=placed.app.slug, name="staging", cluster_slug=second.slug)

    assert result.ok, result.errors
    env = AppEnvironment.objects.get(registered_app=placed.app, name="staging")
    assert env.tenant_cluster_id == second.pk
    assert env.k8s_namespace
