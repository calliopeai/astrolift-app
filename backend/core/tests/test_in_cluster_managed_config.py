"""An in-cluster driver booked on a cloud cluster drives that cloud's cluster (#1484).

Reaching the ``k8s_native`` driver is half of the fix; the other half is what
it is configured with. ``managed_config_for`` dispatches on a plugin slug, and
the ``k8s_native`` branch attaches ``_driver_for_cluster(cluster)`` -- whatever
``ClusterDriver`` the cluster actually has. That is what makes an in-cluster
variant portable rather than only reachable: the Memcached StatefulSet lands on
the EKS cluster through the EKS driver, with no vanilla-Kubernetes kubeconfig
anywhere in the path.

Exercised on AWS because the AWS extra is the one every install carries; the
GCP and Azure branches of ``_config_for`` differ only in which ``*Config`` they
build, and ``tests/_sdk/test_coverage_runtime.py`` covers the resolution half
for all three.
"""

from __future__ import annotations

import pytest
from aws.cluster_eks import EKSClusterDriver
from k8s_native.managed.cache_memcached import MemcachedConfig, MemcachedDriver

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_drivers.managed_resolution import resolve_managed_driver
from astrolift_identity.models import Organization
from core.cluster_observability import ClusterObservabilityError, managed_config_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def eks_cluster() -> TenantCluster:
    org = Organization.objects.create(name="Acme", slug="acme-icmc")
    # bulk_create bypasses BaseCoreModel.save(), whose integer optimistic-version
    # bump collides with ProviderPlugin's CharField ``version``.
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
        ignore_conflicts=True,
    )
    return TenantCluster.objects.create(
        organization=org,
        slug="icmc-eks",
        name="EKS",
        provider_plugin=ProviderPlugin.objects.get(slug="aws"),
        endpoint="https://example.invalid",
        region="us-east-1",
        provider_config={"region": "us-east-1", "cluster_name": "icmc"},
    )


def test_the_in_cluster_driver_resolved_from_eks_is_configured_against_eks(eks_cluster):
    """The whole acceptance criterion in one path: resolve, configure,
    construct."""
    resolved = resolve_managed_driver(cluster_plugin_slug="aws", kind="cache", variant="memcached")
    config = managed_config_for(resolved.plugin_slug, eks_cluster, kind="cache", variant="memcached")
    driver = resolved.driver_cls(config=config)

    assert resolved.driver_cls is MemcachedDriver
    assert isinstance(config, MemcachedConfig)
    assert isinstance(config.cluster_driver, EKSClusterDriver), (
        "an in-cluster service on EKS has to apply its manifests through the EKS driver; "
        f"got {type(config.cluster_driver).__name__}"
    )
    assert isinstance(driver, MemcachedDriver)


def test_operator_pinned_in_cluster_settings_are_read_from_the_cloud_clusters_config(eks_cluster):
    """The in-cluster knobs live in the same ``provider_config`` bundle as the
    cloud ones, so an operator pins a Memcached image on an EKS cluster the way
    they would on a vanilla one. Without this the fallback would silently
    ignore every k8s_native setting on a cloud install."""
    eks_cluster.provider_config = {
        **eks_cluster.provider_config,
        "memcached_image": "registry.example.invalid/memcached:1.6-alpine",
    }
    eks_cluster.save(update_fields=["provider_config", "updated_at", "version"])

    config = managed_config_for("k8s_native", eks_cluster, kind="cache", variant="memcached")

    assert config.image == "registry.example.invalid/memcached:1.6-alpine"


def test_building_a_cloud_config_for_an_in_cluster_driver_is_still_an_error(eks_cluster):
    """The failure a partial fix produces: driver from k8s_native, config from
    the cluster's own plugin. It has to stay loud -- a Memcached driver holding
    an AWS config would try to apply against nothing."""
    with pytest.raises(ClusterObservabilityError):
        managed_config_for("aws", eks_cluster, kind="cache", variant="memcached")
