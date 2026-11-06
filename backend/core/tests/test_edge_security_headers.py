"""Per-app security headers at the ingress-nginx edge (``core.edge_security_headers``)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_workflows.activities.app_lifecycle import _render_app_ingresses_and_tls
from core.app_deploy import render_resources_for_deployment
from core.edge_security_headers import (
    BEGIN,
    END,
    SNIPPET_KEY,
    apply_security_headers,
    honours_security_headers,
    security_headers_snippet,
)
from core.tests.test_custom_domain_pause import _by_host, _manifest, _quiet, _world  # noqa: F401

# ---- the helper, on its own ----------------------------------------------------------


def test_off_returns_nothing():
    assert security_headers_snippet(False) is None
    assert security_headers_snippet(False, "proxy_set_header X-A b;") is None


def test_on_returns_the_headers():
    snippet = security_headers_snippet(True)
    assert snippet.startswith(BEGIN) and snippet.rstrip().endswith(END)
    assert "add_header Content-Security-Policy \"frame-ancestors 'none'\" always;" in snippet
    assert 'add_header X-Frame-Options "DENY" always;' in snippet
    assert 'add_header Referrer-Policy "same-origin" always;' in snippet
    assert 'add_header X-Content-Type-Options "nosniff" always;' in snippet
    assert "Strict-Transport-Security" not in snippet


def test_single_value_headers_are_hidden_before_they_are_added_once():
    snippet = security_headers_snippet(True)
    for header in ("X-Frame-Options", "Referrer-Policy", "X-Content-Type-Options"):
        assert snippet.index(f"proxy_hide_header {header};") < snippet.index(f"add_header {header} ")
        assert snippet.count(f"add_header {header} ") == 1


def test_content_security_policy_is_added_never_replaced():
    snippet = security_headers_snippet(True)
    assert "proxy_hide_header Content-Security-Policy" not in snippet
    assert snippet.count("add_header Content-Security-Policy ") == 1


def test_an_existing_snippet_is_kept_and_the_block_added_once():
    existing = 'proxy_set_header X-Gateway "s";'
    snippet = security_headers_snippet(True, existing)
    assert snippet.startswith(existing + "\n")
    again = security_headers_snippet(True, snippet)
    assert again == snippet
    assert again.count(BEGIN) == 1


def test_an_existing_csp_line_survives():
    existing = "add_header Content-Security-Policy \"default-src 'self'\" always;"
    snippet = security_headers_snippet(True, existing)
    assert existing in snippet
    assert snippet.count("add_header Content-Security-Policy ") == 2


def test_only_ingress_nginx_honours_it():
    for cls, honoured in [
        ("nginx", True),
        ("ingress-nginx", True),
        ("envoy", False),
        ("alb", False),
        ("traefik", False),
    ]:
        assert honours_security_headers(SimpleNamespace(ingress_class=cls)) is honoured
    annotations = apply_security_headers(
        {}, SimpleNamespace(security_headers=True), SimpleNamespace(ingress_class="envoy")
    )
    assert annotations == {}


def test_apply_merges_into_an_existing_snippet_and_leaves_off_apps_alone():
    app_on, app_off = SimpleNamespace(security_headers=True), SimpleNamespace(security_headers=False)
    cluster = SimpleNamespace(ingress_class="nginx")
    merged = apply_security_headers({SNIPPET_KEY: "x;"}, app_on, cluster)
    assert merged[SNIPPET_KEY].startswith("x;\n") and BEGIN in merged[SNIPPET_KEY]
    assert apply_security_headers({SNIPPET_KEY: "x;"}, app_off, cluster) == {SNIPPET_KEY: "x;"}
    assert apply_security_headers({}, app_off, cluster) == {}


# ---- the render paths ----------------------------------------------------------------

pytestmark_db = pytest.mark.django_db


def _enable(deployment):
    app = deployment.registered_app
    app.security_headers = True
    app.save(update_fields=["security_headers"])


def _managed(deployment, path):
    if path == "applied":
        out = render_resources_for_deployment(deployment, include_managed_filesystems=False)
    else:
        out = _render_app_ingresses_and_tls(deployment.pk, "ns", _manifest())
    return [
        r
        for r in out
        if r["kind"] == "Ingress"
        and r["metadata"].get("labels", {}).get("astrolift.dev/managed-subdomain") == "true"
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["applied", "activity"])
def test_managed_ingress_carries_the_headers_when_on_and_nothing_when_off(path):
    deployment = _world()
    [off] = _managed(deployment, path)
    assert SNIPPET_KEY not in off["metadata"]["annotations"]
    _enable(deployment)
    [on] = _managed(deployment, path)
    assert BEGIN in on["metadata"]["annotations"][SNIPPET_KEY]


@pytest.mark.django_db
def test_both_render_paths_agree_on_the_managed_snippet():
    deployment = _world()
    _enable(deployment)
    [applied] = _managed(deployment, "applied")
    [activity] = _managed(deployment, "activity")
    assert applied["metadata"]["annotations"][SNIPPET_KEY] == activity["metadata"]["annotations"][SNIPPET_KEY]


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["applied", "activity"])
def test_custom_domain_ingresses_carry_the_headers_when_on_and_nothing_when_off(path):
    deployment = _world()
    hosts = _by_host(deployment)
    for ing in hosts.values():
        assert SNIPPET_KEY not in ing["metadata"]["annotations"]
    _enable(deployment)
    if path == "applied":
        hosts = _by_host(deployment)
    else:
        out = _render_app_ingresses_and_tls(deployment.pk, "ns", _manifest(), custom_domains_only=True)
        hosts = {
            r["metadata"]["labels"]["astrolift.dev/custom-domain"]: r for r in out if r["kind"] == "Ingress"
        }
    assert set(hosts) == {"paused.example.com", "live.example.com"}
    for ing in hosts.values():
        assert BEGIN in ing["metadata"]["annotations"][SNIPPET_KEY]


@pytest.mark.django_db
def test_an_existing_gate_snippet_on_the_managed_ingress_is_merged_not_overwritten():
    deployment = _world()
    _enable(deployment)
    cluster = deployment.app_environment.tenant_cluster
    cluster.oidc_auth_config = {
        "discovery_url": "https://idp.example.com",
        "client_id": "central-client",
        "auth_proxy_host": "auth.apps.example.net",
        "gateway_secret": "s" * 40,
    }
    cluster.save()
    for path in ("applied", "activity"):
        [ing] = _managed(deployment, path)
        snippet = ing["metadata"]["annotations"][SNIPPET_KEY]
        assert "proxy_set_header" in snippet and BEGIN in snippet


@pytest.mark.django_db
def test_a_cluster_that_is_not_ingress_nginx_renders_no_snippet():
    deployment = _world()
    _enable(deployment)
    cluster = deployment.app_environment.tenant_cluster
    cluster.ingress_class = "traefik"
    cluster.save()
    [ing] = _managed(deployment, "activity")
    assert SNIPPET_KEY not in ing["metadata"]["annotations"]
