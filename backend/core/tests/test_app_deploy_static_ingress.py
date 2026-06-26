"""Ingress filter for the static_site topology (#1010, Layer 4).

A ``static_site`` workload has no Service, so an Ingress backend pointing at
it would be rejected by the ALB/nginx controller ("unable to find Service").
``_render_managed_subdomain_ingress`` must therefore drop static workloads
from the hostname-to-backend map while still emitting an Ingress for any
co-resident container workload. These tests pin both the HYBRID and the
static-only cases.
"""

from __future__ import annotations

from types import SimpleNamespace

from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from core.app_deploy import _render_managed_subdomain_ingress

_HYBRID = """name = "shop"
[[workloads]]
name = "api"
kind = "deployment"
is_public = true
  [[workloads.containers]]
  name = "app"
  is_primary = true
  image_ref = "docker.io/acme/shop-api:main"
  port = 8080
[[workloads]]
name = "storefront"
kind = "static_site"
is_public = true
static_build_command = "npm ci && npm run build"
static_output_dir = "build"
"""

_STATIC_ONLY = """name = "marketing"
[[workloads]]
name = "site"
kind = "static_site"
is_public = true
static_output_dir = "dist"
"""


def _scenario(manifest_toml: str):
    manifest = normalize(parse_raw(manifest_toml), defaults=NormalizationDefaults())
    deployment = SimpleNamespace(
        pk=1,
        registered_app=SimpleNamespace(
            slug="shop",
            organization=SimpleNamespace(slug="acme"),
            organization_id=1,
        ),
        app_environment=SimpleNamespace(ingress_paused=False),
    )
    managed_domain = SimpleNamespace(zone="apps.example.net", dns_config={})
    cluster = SimpleNamespace(ingress_class="alb", region="us-west-2", alb_auth_config=None)
    return deployment, manifest, managed_domain, cluster


def test_hybrid_ingress_only_for_container_workload():
    deployment, manifest, md, cluster = _scenario(_HYBRID)
    out = _render_managed_subdomain_ingress(
        deployment, manifest, namespace="acme-shop", managed_domain=md, cluster=cluster
    )
    assert isinstance(out, list)
    # Exactly one Ingress -- for the container workload. The static workload is
    # filtered out (no Service -> the LB controller would reject it).
    assert len(out) == 1, f"static workload must be skipped from the ingress set, got {len(out)}"
    ing = out[0]
    hosts = [r["host"] for r in ing["spec"]["rules"]]
    assert "shop-api.apps.example.net" in hosts
    assert not any("storefront" in h for h in hosts), f"static host leaked into the Ingress: {hosts}"
    backend = ing["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"]["number"]
    assert backend == 8080


def test_static_only_emits_no_ingress():
    deployment, manifest, md, cluster = _scenario(_STATIC_ONLY)
    deployment.registered_app.slug = "marketing"
    out = _render_managed_subdomain_ingress(
        deployment, manifest, namespace="acme-marketing", managed_domain=md, cluster=cluster
    )
    # A public static_site has a hostname (so `computed` is non-empty) but no
    # Service -- the filter drops it, leaving no Ingress at all.
    assert out == [], f"a lone static_site must produce no Ingress, got {out!r}"
