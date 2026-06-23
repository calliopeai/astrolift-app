"""Regression tests for the managed-subdomain Ingress render (#992).

``_render_managed_subdomain_ingress`` built its ``out`` list but had no
``return out`` at the end — it fell through to an implicit ``return None``.
The caller's ``if ingress_resources:`` then treated None as falsy and silently
dropped the Ingress, so ``is_public`` apps deployed with no ALB/DNS while the
deploy still reported success. These tests pin that the function returns the
rendered Ingress list (never None) for the ALB path.
"""

from __future__ import annotations

from types import SimpleNamespace

from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from core.app_deploy import _render_managed_subdomain_ingress

_MANIFEST = """name = "hello-web"
[[workloads]]
name = "web"
kind = "deployment"
is_public = true
replicas = 2
  [[workloads.containers]]
  name = "app"
  is_primary = true
  image_ref = "docker.io/calliopeai/astrolift-sample-web:main-80acde1"
  port = 8080
    [workloads.containers.healthcheck]
    kind = "http"
    value = "/health"
    port = 8080
"""


def _scenario():
    manifest = normalize(parse_raw(_MANIFEST), defaults=NormalizationDefaults())
    deployment = SimpleNamespace(
        pk=1,
        registered_app=SimpleNamespace(
            slug="hello-web",
            organization=SimpleNamespace(slug="acme"),
            organization_id=1,
        ),
        app_environment=SimpleNamespace(ingress_paused=False),
    )
    managed_domain = SimpleNamespace(zone="apps.example.net", dns_config={})  # no ACM cert
    cluster = SimpleNamespace(ingress_class="alb", region="us-west-2", alb_auth_config=None)
    return deployment, manifest, managed_domain, cluster


def test_managed_subdomain_ingress_returns_ingress_for_alb():
    # The bug: this returned None (no `return out`), so the caller dropped it.
    deployment, manifest, md, cluster = _scenario()
    out = _render_managed_subdomain_ingress(
        deployment, manifest, namespace="acme-hello-web", managed_domain=md, cluster=cluster
    )
    assert out is not None, "must not return None — that silently drops the Ingress (#992)"
    assert isinstance(out, list) and len(out) == 1
    ing = out[0]
    assert ing["kind"] == "Ingress"
    assert ing["metadata"]["namespace"] == "acme-hello-web"
    assert ing["spec"]["ingressClassName"] == "alb"
    hosts = [r["host"] for r in ing["spec"]["rules"]]
    assert "hello-web.apps.example.net" in hosts
