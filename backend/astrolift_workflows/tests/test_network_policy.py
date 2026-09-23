"""Tests for NetworkPolicy generation (#24, spec 12 §7.1-§7.2)."""

from __future__ import annotations

import pytest

from astrolift_clusters.egress import EgressPolicy
from astrolift_workflows.activities.network_policy import (
    DEFAULT_INGRESS_NS_LABEL,
    EDGE_TLS_DEFAULTS,
    ManagedServiceCIDR,
    edge_annotations,
    render_network_policy,
)

# ---- ingress rules -------------------------------------------------


def test_ingress_only_from_ingress_controller():
    out = render_network_policy(
        namespace="acme-api",
        app_label="api",
        egress_policy=EgressPolicy(),
    )
    ingress = out["spec"]["ingress"]
    assert len(ingress) == 1
    selector = ingress[0]["from"][0]["namespaceSelector"]["matchLabels"]
    assert DEFAULT_INGRESS_NS_LABEL in selector


def test_ingress_namespace_label_overridable():
    out = render_network_policy(
        namespace="acme-api",
        app_label="api",
        egress_policy=EgressPolicy(),
        ingress_namespace_label="custom.io/ingress",
    )
    selector = out["spec"]["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"]
    assert "custom.io/ingress" in selector


# ---- egress rules --------------------------------------------------


def test_egress_always_allows_dns():
    """Without DNS, pods can't resolve anything — always allow it."""
    out = render_network_policy(
        namespace="acme-api",
        app_label="api",
        egress_policy=EgressPolicy(),
    )
    dns_peer = {
        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
        "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
    }
    # #1868: the pod label goes on podSelector; as a namespaceSelector it
    # matched no namespace and cut DNS for every opted-in app.
    dns_rules = [e for e in out["spec"]["egress"] if dns_peer in e.get("to", [])]
    assert len(dns_rules) == 1
    ports = dns_rules[0]["ports"]
    assert {"protocol": "UDP", "port": 53} in ports
    assert {"protocol": "TCP", "port": 53} in ports


def test_egress_includes_managed_service_cidrs():
    out = render_network_policy(
        namespace="acme-api",
        app_label="api",
        egress_policy=EgressPolicy(),
        managed_service_cidrs=[
            ManagedServiceCIDR(name="main-db", cidr="10.20.0.0/24", port=5432),
        ],
    )
    rules = out["spec"]["egress"]
    db_rules = [r for r in rules if r.get("to") == [{"ipBlock": {"cidr": "10.20.0.0/24"}}]]
    assert len(db_rules) == 1
    assert db_rules[0]["ports"] == [{"protocol": "TCP", "port": 5432}]


def test_egress_includes_allowlist_cidrs():
    policy = EgressPolicy(allowed_cidrs=("203.0.113.0/24",))
    out = render_network_policy(
        namespace="acme-api",
        app_label="api",
        egress_policy=policy,
    )
    allowlist = [r for r in out["spec"]["egress"] if r.get("to") == [{"ipBlock": {"cidr": "203.0.113.0/24"}}]]
    assert len(allowlist) == 1


def test_internet_https_excludes_internal_cidrs():
    """Pods need internet HTTPS but must NOT reach control-plane
    or other tenants. Use NetworkPolicy 'except' to carve out the
    deny floor from 0.0.0.0/0."""
    out = render_network_policy(
        namespace="acme-api",
        app_label="api",
        egress_policy=EgressPolicy(),
        allow_internet_https=True,
    )
    https_rules = [
        r
        for r in out["spec"]["egress"]
        if any(t.get("ipBlock", {}).get("cidr") == "0.0.0.0/0" for t in r.get("to", []))
    ]
    assert len(https_rules) == 1
    block = https_rules[0]["to"][0]["ipBlock"]
    # 'except' must include RFC 1918 ranges so pods can't reach
    # control-plane DB / other tenants
    assert "10.0.0.0/8" in block["except"]
    assert "192.168.0.0/16" in block["except"]


def test_internet_https_can_be_disabled():
    out = render_network_policy(
        namespace="acme-api",
        app_label="api",
        egress_policy=EgressPolicy(),
        allow_internet_https=False,
    )
    rules = out["spec"]["egress"]
    https = [
        r for r in rules if any(t.get("ipBlock", {}).get("cidr") == "0.0.0.0/0" for t in r.get("to", []))
    ]
    assert https == []


# ---- shape ---------------------------------------------------------


def test_metadata_has_app_label_and_managed_by():
    out = render_network_policy(
        namespace="acme-api",
        app_label="api",
        egress_policy=EgressPolicy(),
    )
    labels = out["metadata"]["labels"]
    assert labels["app"] == "api"
    assert labels["managed-by"] == "astrolift"


def test_pod_selector_targets_app_label():
    out = render_network_policy(
        namespace="acme-api",
        app_label="api",
        egress_policy=EgressPolicy(),
    )
    assert out["spec"]["podSelector"] == {"matchLabels": {"app": "api"}}


def test_policy_types_includes_both_directions():
    out = render_network_policy(
        namespace="acme-api",
        app_label="api",
        egress_policy=EgressPolicy(),
    )
    assert out["spec"]["policyTypes"] == ["Ingress", "Egress"]


def test_rejects_empty_namespace_or_app():
    with pytest.raises(ValueError):
        render_network_policy(
            namespace="",
            app_label="api",
            egress_policy=EgressPolicy(),
        )
    with pytest.raises(ValueError):
        render_network_policy(
            namespace="ns",
            app_label="",
            egress_policy=EgressPolicy(),
        )


# ---- edge / TLS defaults -------------------------------------------


def test_edge_defaults_tls_1_3():
    """Spec 12 §7.2: TLS 1.3 minimum on all ingress."""
    assert EDGE_TLS_DEFAULTS["tls_min_version"] == "1.3"


def test_edge_defaults_hsts_1_year():
    """1 year max-age."""
    assert EDGE_TLS_DEFAULTS["hsts_max_age"] == "31536000"
    assert EDGE_TLS_DEFAULTS["hsts_include_subdomains"] == "true"


def test_edge_defaults_http_redirect():
    assert EDGE_TLS_DEFAULTS["http_to_https_redirect"] == "true"


def test_edge_annotations_supports_override():
    out = edge_annotations(overrides={"hsts_max_age": "63072000"})
    # 2-year override applied; other defaults preserved
    assert out["hsts_max_age"] == "63072000"
    assert out["tls_min_version"] == "1.3"
