"""Render wiring for the faas topology (#987).

A ``faas`` workload runs on the cloud's function runtime (AWS Lambda v1) -- it
has no container/pod and emits NO Kubernetes resource. Its Lambda, Function URL
and fronting CloudFront (cdn) are managed services provisioned by the faas
deploy activities, not pods. These tests pin:

* a lone faas renders nothing,
* a HYBRID manifest (a container deployment + a faas) still renders the
  Deployment + Service for the container and skips the faas workload,
* ``workloads_from_resources`` treats a faas-only render as rollout-clean
  (no Deployment/StatefulSet/DaemonSet to poll).
"""

from __future__ import annotations

from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.render import render_manifests
from core.app_deploy import workloads_from_resources

_FAAS_ONLY = """name = "fn"
[[workloads]]
name = "api"
kind = "faas"
is_public = true
faas_public = true
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
name = "thumbnailer"
kind = "faas"
faas_public = false
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


def test_faas_renders_no_k8s_resource():
    resources = _render(_FAAS_ONLY)
    assert resources == [], f"a lone faas must emit no K8s resource, got {resources!r}"


def test_faas_rollout_clean():
    # workloads_from_resources selects Deployment/StatefulSet/DaemonSet for the
    # rollout poll. A faas-only render yields none -> the deploy is treated as
    # rollout-clean (success), not stuck waiting on a workload that never
    # produces a pod.
    assert workloads_from_resources(_render(_FAAS_ONLY)) == []


def test_hybrid_renders_container_and_skips_faas():
    resources = _render(_HYBRID)
    kinds = sorted(r["kind"] for r in resources)
    # The container workload still gets its Deployment + Service...
    assert "Deployment" in kinds
    assert "Service" in kinds

    # ...and the faas workload fabricates nothing.
    faas_resources = [
        r for r in resources if r["metadata"]["labels"].get("astrolift.dev/workload") == "thumbnailer"
    ]
    assert faas_resources == [], f"faas workload must emit nothing, got {faas_resources!r}"

    api_resources = [r for r in resources if r["metadata"]["labels"].get("astrolift.dev/workload") == "api"]
    assert {r["kind"] for r in api_resources} == {"Deployment", "Service"}

    # The rollout poll set is exactly the container's Deployment.
    assert workloads_from_resources(resources) == [("Deployment", "api")]
