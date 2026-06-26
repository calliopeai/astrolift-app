"""Render wiring for the static_site topology (#1010, Layer 4).

A ``static_site`` workload serves built assets from S3 + CloudFront -- it
has no container and emits NO Kubernetes resource. Its reachability is a
``CNAME host -> CloudFront`` record written by the deploy flow, not an
Ingress. These tests pin:

* a lone static_site renders nothing,
* a HYBRID manifest (a container deployment + a static_site) still renders
  the Deployment + Service for the container and skips the static workload,
* ``workloads_from_resources`` treats a static-only render as rollout-clean
  (no Deployment/StatefulSet/DaemonSet to poll).
"""

from __future__ import annotations

from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.render import render_manifests
from core.app_deploy import workloads_from_resources

_STATIC_ONLY = """name = "marketing"
[[workloads]]
name = "site"
kind = "static_site"
is_public = true
static_build_command = "npm ci && npm run build"
static_output_dir = "dist"
static_spa = true
"""

_HYBRID = """name = "shop"
[[workloads]]
name = "api"
kind = "deployment"
is_public = true
replicas = 2
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


def _render(manifest_toml: str):
    manifest = normalize(parse_raw(manifest_toml), defaults=NormalizationDefaults())
    return render_manifests(
        manifest,
        app_slug="app",
        namespace="acme-app",
        image_tag="main-abc123",
        image_repository="ghcr.io/acme/app",
        environment_name="prod",
    )


def test_static_site_renders_no_k8s_resource():
    resources = _render(_STATIC_ONLY)
    assert resources == [], f"a lone static_site must emit no K8s resource, got {resources!r}"


def test_static_site_rollout_clean():
    # workloads_from_resources selects Deployment/StatefulSet/DaemonSet for the
    # rollout poll. A static-only render yields none -> the deploy is treated
    # as rollout-clean (success), not stuck waiting on a workload that never
    # produces a pod.
    assert workloads_from_resources(_render(_STATIC_ONLY)) == []


def test_hybrid_renders_container_and_skips_static():
    resources = _render(_HYBRID)
    kinds = sorted(r["kind"] for r in resources)
    # The container workload still gets its Deployment + Service...
    assert "Deployment" in kinds
    assert "Service" in kinds

    # ...and every rendered resource belongs to the container workload, never
    # the static one (no Deployment/Service/etc. is fabricated for it).
    static_resources = [
        r for r in resources if r["metadata"]["labels"].get("astrolift.dev/workload") == "storefront"
    ]
    assert static_resources == [], f"static workload must emit nothing, got {static_resources!r}"

    api_resources = [r for r in resources if r["metadata"]["labels"].get("astrolift.dev/workload") == "api"]
    assert {r["kind"] for r in api_resources} == {"Deployment", "Service"}

    # The rollout poll set is exactly the container's Deployment.
    assert workloads_from_resources(resources) == [("Deployment", "api")]
