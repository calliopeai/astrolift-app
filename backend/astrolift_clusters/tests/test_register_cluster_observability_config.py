"""registerTenantCluster refuses a malformed observability driver bundle.

``provider_config`` carries the per-cluster log/metrics/trace driver
config. The read paths log-and-skip anything malformed, so before the
write-time check a typo persisted silently and surfaced weeks later as
an empty Logs tab. These tests pin that the mutation now refuses it and
names every issue at once.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    RegisterTenantClusterInput,
)
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    return ProviderPlugin.objects.create(
        name="k8s",
        slug="k8s_native",
        capabilities_manifest={},
        config_schema={},
    )


def _info():
    request = SimpleNamespace(user=None)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=None))


def _register(org, plugin, provider_config):
    with tenant_context(TenantContext(organization_id=org.id)):
        return ClustersMutation().register_tenant_cluster(
            _info(),
            RegisterTenantClusterInput(
                slug=f"c-{uuid.uuid4().hex[:6]}",
                name="dev",
                provider_plugin_slug=plugin.slug,
                auth_method="kubeconfig",
                provider_config=provider_config,
            ),
        )


def test_unknown_log_driver_is_refused(org, plugin, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    result = _register(org, plugin, {"log_driver": "splunk", "log_config": {}})
    assert result.ok is False
    assert [e.code for e in result.errors] == ["VALIDATION"]
    assert result.errors[0].field == "providerConfig"
    assert "unknown log driver" in result.errors[0].message
    assert TenantCluster.objects.count() == 0


def test_misspelled_endpoint_key_is_refused(org, plugin, permission_resolver):
    """The exact failure the read path used to swallow: ``endpint``
    instead of ``endpoint`` left log_driver=loki with no endpoint."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    result = _register(
        org,
        plugin,
        {"log_driver": "loki", "log_config": {"endpint": "http://loki:3100"}},
    )
    assert result.ok is False
    assert "endpoint" in result.errors[0].message
    assert TenantCluster.objects.count() == 0


def test_cloudwatch_missing_log_group_is_refused(org, plugin, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    result = _register(
        org,
        plugin,
        {"log_driver": "cloudwatch_logs", "log_config": {"region": "us-west-2"}},
    )
    assert result.ok is False
    assert "log_group" in result.errors[0].message
    assert TenantCluster.objects.count() == 0


def test_every_stream_issue_is_reported_in_one_round_trip(org, plugin, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    result = _register(
        org,
        plugin,
        {
            "log_driver": "loki",
            "log_config": {},
            "metrics_driver": "influxdb",
            "metrics_config": {},
            "trace_driver": "tempo",
            "trace_config": {},
        },
    )
    assert result.ok is False
    labels = {e.message.split(":")[0] for e in result.errors}
    assert labels == {
        "providerConfig.log_driver",
        "providerConfig.metrics_driver",
        "providerConfig.trace_driver",
    }
    assert TenantCluster.objects.count() == 0


def test_multiplexer_children_are_validated(org, plugin, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    result = _register(
        org,
        plugin,
        {
            "log_driver": "multiplexer",
            "log_config": {"children": [{"driver": "loki", "config": {}}]},
        },
    )
    assert result.ok is False
    assert "endpoint" in result.errors[0].message


def test_valid_observability_bundle_persists(org, plugin, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    config = {
        "log_driver": "loki",
        "log_config": {"endpoint": "http://loki:3100"},
        "trace_driver": "tempo",
        "trace_config": {"endpoint": "http://tempo:3200"},
    }
    result = _register(org, plugin, config)
    assert result.ok is True, result.errors
    cluster = TenantCluster.objects.get()
    assert cluster.provider_config == config


def test_cluster_without_observability_keys_still_registers(org, plugin, permission_resolver):
    """No aggregator wired is the normal case, not a validation error."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    result = _register(org, plugin, {"region": "us-west-2"})
    assert result.ok is True, result.errors
    assert TenantCluster.objects.count() == 1
