"""Blobs land in the org's own cloud (#1610).

The decision on the issue was to key resolution on the org's default
cluster's plugin, so artifacts and snapshots live in the same cloud as the
workloads that produce them.

Everything that does not resolve at that step falls through to the install
bucket rather than raising. An org with no default cluster, a cloud with no
builder, or a cluster that has not been told which bucket to use has not made
a mistake -- and every install working today is in exactly that state, so a
raise would break all of them.
"""

from __future__ import annotations

import pytest

from core.blob_store_resolution import driver_for_org

pytestmark = pytest.mark.django_db


class _Plugin:
    def __init__(self, slug):
        self.slug = slug


class _Cluster:
    def __init__(self, slug="c1", plugin="aws", **pc):
        self.slug = slug
        self.region = "us-west-2"
        self.provider_plugin = _Plugin(plugin)
        self.provider_config = dict(pc)


class _Org:
    slug = "acme"

    def __init__(self, cluster=None):
        self.default_tenant_cluster = cluster


@pytest.fixture
def install_bucket(settings):
    settings.AWS_STORAGE_BUCKET_NAME = "install-media"
    settings.AWS_S3_REGION_NAME = "us-east-1"


def test_an_aws_cluster_gets_its_own_bucket(install_bucket):
    org = _Org(_Cluster(plugin="aws", blob_bucket="acme-artifacts"))

    driver = driver_for_org(org, purpose="test")

    assert driver._bucket == "acme-artifacts"


def test_a_prefix_is_honoured(install_bucket):
    org = _Org(_Cluster(plugin="aws", blob_bucket="acme-artifacts", blob_prefix="pipelines/"))

    driver = driver_for_org(org, purpose="test")

    assert driver._prefix == "pipelines"


def test_an_org_with_no_default_cluster_uses_the_install_bucket(install_bucket):
    """Every install today. This is the case that must not change."""
    driver = driver_for_org(_Org(None), purpose="test")

    assert driver._bucket == "install-media"


def test_a_cluster_with_no_bucket_configured_uses_the_install_bucket(install_bucket):
    """Having a cluster is not the same as having told it where blobs go."""
    org = _Org(_Cluster(plugin="aws"))

    driver = driver_for_org(org, purpose="test")

    assert driver._bucket == "install-media"


def test_an_unsupported_cloud_falls_through(install_bucket):
    """k8s_native has no builder: MinIO needs an access key and a secret key,
    and provider_config is a plaintext column whose own guard refuses
    credential-bearing keys. Half-supporting it would be worse."""
    org = _Org(_Cluster(plugin="k8s_native", blob_bucket="on-prem"))

    driver = driver_for_org(org, purpose="test")

    assert driver._bucket == "install-media"


def test_azure_needs_both_values_or_neither(install_bucket):
    """A container with no account URL cannot be addressed. Returning a
    driver that fails on first use would be worse than the install bucket,
    which works."""
    org = _Org(_Cluster(plugin="azure", blob_container="artifacts"))

    driver = driver_for_org(org, purpose="test")

    assert driver._bucket == "install-media"


def test_a_builder_that_raises_does_not_take_the_install_bucket_down(install_bucket, monkeypatch):
    """A missing cloud SDK is the likely cause, and it must degrade rather
    than fail the operation."""
    import core.blob_store_resolution as res

    def _boom(cluster, pc):
        raise ImportError("google.cloud.storage is not installed")

    monkeypatch.setitem(res._BLOB_BUILDERS, "gcp", _boom)
    org = _Org(_Cluster(plugin="gcp", blob_bucket="acme-gcs"))

    driver = driver_for_org(org, purpose="test")

    assert driver._bucket == "install-media"


def test_nothing_configured_at_all_returns_none(settings):
    """Callers treat None as 'not configured' and fall through to their own
    next step; it is not an error."""
    settings.AWS_STORAGE_BUCKET_NAME = ""

    assert driver_for_org(_Org(None), purpose="test") is None
