"""Fenced agent pods reach the cluster's Zentinelle gateway by label, not by CIDR (#1887)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from constance.test import override_config

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_dispatch.agent_network_fence import agent_fence_manifests, render_agent_fence
from astrolift_identity.models import Organization
from astrolift_operations import zentinelle_connect
from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection

pytestmark = pytest.mark.django_db


def _gateway_rules(policy):
    return [
        rule
        for rule in policy["spec"]["egress"]
        if any("podSelector" in peer and "namespaceSelector" in peer for peer in rule["to"])
        and rule["to"][0]["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] != "kube-system"
    ]


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def cluster():
    org = Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="k8s", slug="k8s_native", capabilities_manifest={}, config_schema={})]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"dev-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
    )
    connection = ZentinelleConnection.objects.create(organization=org, base_url="https://zentinelle.test")
    ZentinelleClusterGateway.objects.create(
        connection=connection, cluster=cluster, zentinelle_cluster_id=str(cluster.guid)
    )
    return cluster


def test_the_gateway_rule_selects_the_gateway_pods_on_its_port():
    [rule] = _gateway_rules(render_agent_fence(namespace="agents-acme", zentinelle_gateway=True))

    assert rule == {
        "to": [
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": zentinelle_connect.GATEWAY_NAMESPACE}
                },
                "podSelector": {"matchLabels": {"app": zentinelle_connect.GATEWAY_NAME}},
            }
        ],
        "ports": [{"protocol": "TCP", "port": zentinelle_connect.GATEWAY_PORT}],
    }
    [deployment, _service] = zentinelle_connect.gateway_manifests(
        cluster_id="c", zentinelle_url="https://z.test", image="i"
    )
    assert deployment["spec"]["template"]["metadata"]["labels"]["app"] == zentinelle_connect.GATEWAY_NAME
    assert _gateway_rules(render_agent_fence(namespace="agents-acme")) == []


@override_config(AGENT_NETWORK_FENCE=True)
def test_no_gateway_rule_while_the_install_flag_is_off(cluster):
    [policy] = agent_fence_manifests(cluster, "agents-acme")

    assert _gateway_rules(policy) == []


@override_config(AGENT_NETWORK_FENCE=True, ZENTINELLE_GATEWAY_ENABLED=True)
def test_the_gateway_rule_follows_the_clusters_gateway(cluster):
    [policy] = agent_fence_manifests(cluster, "agents-acme")
    assert len(_gateway_rules(policy)) == 1

    ZentinelleClusterGateway.objects.filter(cluster=cluster).update(gateway_enabled=False)
    [policy] = agent_fence_manifests(cluster, "agents-acme")
    assert _gateway_rules(policy) == []


@override_config(AGENT_NETWORK_FENCE=True, ZENTINELLE_GATEWAY_ENABLED=True)
def test_a_cluster_without_a_registered_gateway_gets_no_rule(cluster):
    ZentinelleClusterGateway.objects.get(cluster=cluster).soft_delete()

    [policy] = agent_fence_manifests(cluster, "agents-acme")
    assert _gateway_rules(policy) == []
    [policy] = agent_fence_manifests(SimpleNamespace(provider_plugin=None), "agents-acme")
    assert _gateway_rules(policy) == []


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_no_fence_no_rule(cluster):
    assert agent_fence_manifests(cluster, "agents-acme") == []
