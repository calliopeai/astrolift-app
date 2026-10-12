"""Gateway mode is a boundary: default-deny egress for gateway-mode agent pods (#1950)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from constance.test import override_config

from astrolift_dispatch.agent_network_fence import (
    GATEWAY_FENCE_NAME,
    GATEWAY_LABEL,
    agent_fence_manifests,
    render_agent_fence,
    render_gateway_fence,
)
from astrolift_dispatch.tests.test_agent_network_fence_1850 import _denied


@pytest.mark.parametrize("ip", ["104.18.6.192", "162.159.140.245", "34.117.59.81", "1.1.1.1", "10.0.0.5"])
def test_a_gateway_pod_reaches_no_provider_or_anything_else_directly(ip):
    policy = render_gateway_fence(namespace="agents-acme")
    assert policy["metadata"]["name"] == GATEWAY_FENCE_NAME
    assert policy["spec"]["podSelector"] == {"matchLabels": {GATEWAY_LABEL: "true"}}
    assert policy["spec"]["ingress"] == []
    assert _denied(policy, ip)


def test_a_gateway_pod_keeps_dns_the_gateway_and_the_allow_list():
    policy = render_gateway_fence(namespace="agents-acme", allow_cidrs="140.82.112.0/20")
    peers = [peer for rule in policy["spec"]["egress"] for peer in rule["to"]]
    assert any("namespaceSelector" in p and p.get("podSelector") for p in peers)  # DNS and gateway
    assert any(p.get("podSelector", {}).get("matchLabels") == {"app": "zentinelle-gateway"} for p in peers)
    assert not _denied(policy, "140.82.114.4")


def test_the_general_fence_leaves_gateway_pods_to_their_own_policy():
    expressions = render_agent_fence(namespace="ns")["spec"]["podSelector"]["matchExpressions"]
    assert {"key": GATEWAY_LABEL, "operator": "DoesNotExist"} in expressions


def test_the_gateway_fence_applies_whatever_the_general_fence_says(db):
    cluster = SimpleNamespace(provider_plugin=None)
    assert agent_fence_manifests(cluster, "ns") == []
    [only] = agent_fence_manifests(cluster, "ns", gateway=True)
    assert only["metadata"]["name"] == GATEWAY_FENCE_NAME
    with override_config(AGENT_NETWORK_FENCE=True, AGENT_GATEWAY_EGRESS_ALLOW_CIDRS="140.82.112.0/20"):
        general, gateway = agent_fence_manifests(cluster, "ns", gateway=True)
    assert not _denied(gateway, "140.82.114.4")
