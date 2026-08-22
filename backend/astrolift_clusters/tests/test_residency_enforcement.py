"""Data-residency pinning is enforced on the deploy path (#152, spec 12 §14).

``astrolift_clusters/residency.py`` shipped fully tested and had no caller and
no backing column, so an org could deploy to any registered cluster in any
region. These tests drive the real deploy-time resolvers, not the policy
module, so they fail if the call is removed from either one.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from core.app_deploy import (
    AppDeployError,
    assert_residency_allows,
    driver_for_deployment,
    driver_for_target_cluster,
)

pytestmark = pytest.mark.django_db


def _graph(*, cluster_region: str, allowed_regions: list[str]):
    org = Organization.objects.create(
        name="Acme",
        slug="acme-res",
        residency_allowed_regions=allowed_regions,
    )
    team = Team.objects.create(organization=org, name="Eng", slug="eng-res")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo-res",
    )
    plugin = ProviderPlugin.objects.create(
        name="AWS",
        slug="aws-res",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="res-cluster",
        name="Cluster",
        provider_plugin=plugin,
        endpoint="https://example.invalid",
        region=cluster_region,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Res App",
        slug="res-app",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        image_tag="v1",
    )
    return org, cluster, app, deployment


def test_deploy_to_an_out_of_region_cluster_is_refused() -> None:
    _org, _cluster, _app, deployment = _graph(
        cluster_region="eu-west-1",
        allowed_regions=["us-east-1"],
    )
    with pytest.raises(AppDeployError, match="data residency violation"):
        driver_for_deployment(deployment)


def test_migration_target_cluster_gets_the_same_check() -> None:
    """The migration path picks a cluster the environment was never bound to,
    so the env-time binding is not where this can be enforced."""
    _org, cluster, _app, deployment = _graph(
        cluster_region="eu-west-1",
        allowed_regions=["us-east-1"],
    )
    with pytest.raises(AppDeployError, match="data residency violation"):
        driver_for_target_cluster(deployment, cluster.pk)


def test_a_blank_cluster_region_fails_closed_for_a_pinned_org() -> None:
    """A cluster whose region was never recorded cannot be proven to satisfy
    the pin, so a pinned org must not deploy to it."""
    _org, _cluster, _app, deployment = _graph(
        cluster_region="",
        allowed_regions=["us-east-1"],
    )
    with pytest.raises(AppDeployError, match="data residency violation"):
        driver_for_deployment(deployment)


def test_an_allowed_region_raises_no_residency_error() -> None:
    _org, cluster, app, _deployment = _graph(
        cluster_region="us-east-1",
        allowed_regions=["us-east-1", "us-west-2"],
    )
    assert assert_residency_allows(app, cluster) is None


def test_an_unpinned_org_deploys_anywhere() -> None:
    _org, cluster, app, _deployment = _graph(
        cluster_region="ap-southeast-2",
        allowed_regions=[],
    )
    assert assert_residency_allows(app, cluster) is None


def test_the_pin_is_an_allow_list_not_a_block_list() -> None:
    """Adding a second region must not widen anything else: a region absent
    from a non-empty list stays refused."""
    _org, cluster, app, _deployment = _graph(
        cluster_region="ca-central-1",
        allowed_regions=["us-east-1", "eu-west-1"],
    )
    with pytest.raises(AppDeployError, match="ca-central-1"):
        assert_residency_allows(app, cluster)
