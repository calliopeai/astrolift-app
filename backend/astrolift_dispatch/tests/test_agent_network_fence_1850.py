"""Agent pods are fenced off private networks and cloud metadata (#1850)."""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace

import pytest
from constance.test import override_config

from astrolift_clusters.egress import EgressPolicyError
from astrolift_dispatch.agent_network_fence import FENCE_NAME, agent_fence_manifests, render_agent_fence


def _public_rule(policy):
    return next(
        r for r in policy["spec"]["egress"] if r["to"][0].get("ipBlock", {}).get("cidr") == "0.0.0.0/0"
    )


def _denied(policy, ip):
    """True when no egress rule's ipBlock lets ``ip`` out."""
    addr = ipaddress.ip_address(ip)
    for rule in policy["spec"]["egress"]:
        for peer in rule["to"]:
            block = peer.get("ipBlock")
            if not block or addr not in ipaddress.ip_network(block["cidr"]):
                continue
            if not any(addr in ipaddress.ip_network(c) for c in block.get("except", [])):
                return False
    return True


def test_fence_selects_agent_and_box_pods_and_denies_inbound():
    policy = render_agent_fence(namespace="agents-acme")

    assert policy["metadata"] == {
        "name": FENCE_NAME,
        "namespace": "agents-acme",
        "labels": {"managed-by": "astrolift", "astrolift.dev/component": "agents"},
    }
    assert policy["spec"]["podSelector"]["matchExpressions"][0]["values"] == ["agent", "agent-box"]
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert policy["spec"]["ingress"] == []


@pytest.mark.parametrize(
    "ip", ["169.254.169.254", "10.0.0.5", "172.20.1.1", "192.168.1.10", "100.64.0.1", "fd00:ec2::254"]
)
def test_private_ranges_and_metadata_are_denied(ip):
    assert _denied(render_agent_fence(namespace="ns"), ip)


@pytest.mark.parametrize("ip", ["140.82.112.3", "2606:4700::6810:84e5"])
def test_public_internet_is_reachable_on_any_port(ip):
    policy = render_agent_fence(namespace="ns")

    assert not _denied(policy, ip)
    assert "ports" not in _public_rule(policy)


def test_dns_goes_to_kube_dns_in_kube_system():
    dns = render_agent_fence(namespace="ns")["spec"]["egress"][0]

    assert dns["to"][0]["namespaceSelector"] == {
        "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
    }
    assert dns["to"][0]["podSelector"] == {"matchLabels": {"k8s-app": "kube-dns"}}


def test_operator_carve_outs_reopen_private_destinations():
    policy = render_agent_fence(namespace="ns", allow_cidrs="10.1.2.0/24, 172.31.0.10/32")

    assert not _denied(policy, "10.1.2.7")
    assert not _denied(policy, "172.31.0.10")
    assert _denied(policy, "10.9.9.9")


def test_an_invalid_carve_out_is_an_error_not_a_dropped_entry():
    with pytest.raises(EgressPolicyError):
        render_agent_fence(namespace="ns", allow_cidrs="10.1.2.0/24,not-a-cidr")


def test_gke_keeps_the_metadata_server_for_workload_identity():
    gke = render_agent_fence(namespace="ns", provider_slug="gcp")
    eks = render_agent_fence(namespace="ns", provider_slug="aws")

    assert not _denied(gke, "169.254.169.254")
    assert not _denied(gke, "169.254.169.252")
    assert _denied(eks, "169.254.169.254")


def test_fence_is_off_by_default(db):
    assert agent_fence_manifests(SimpleNamespace(provider_plugin=None), "ns") == []


def test_fence_follows_constance(db):
    cluster = SimpleNamespace(provider_plugin=SimpleNamespace(slug="gcp"))
    with override_config(AGENT_NETWORK_FENCE=True, AGENT_EGRESS_ALLOW_CIDRS="10.1.0.0/16"):
        [policy] = agent_fence_manifests(cluster, "agents-acme")

    assert policy["metadata"]["namespace"] == "agents-acme"
    assert not _denied(policy, "10.1.4.4")
    assert not _denied(policy, "169.254.169.254")
