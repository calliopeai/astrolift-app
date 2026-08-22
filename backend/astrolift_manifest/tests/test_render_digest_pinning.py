"""The renderer pins platform-built containers to the image digest (spec 12 §9).

``build_image`` records the ``sha256:...`` the push produced on the
Deployment row, but until this was wired the renderer still composed
``<repo>:<tag>`` — so every workload ran a mutable tag and a "rollback to
release N" could ship whatever bytes that tag happens to point at today.
"""

from __future__ import annotations

from typing import Any

import pytest

from astrolift_manifest.render import render_manifests
from astrolift_manifest.types import (
    ContainerManifest,
    NormalizedManifest,
    WorkloadManifest,
)

DIGEST = "sha256:" + "ab" * 32


def _container(name: str = "app", *, image_ref: str | None = None) -> ContainerManifest:
    return ContainerManifest(
        name=name,
        is_primary=name == "app",
        image_ref=image_ref,
        port=8080 if name == "app" else 0,
    )


def _manifest(kind: str, *containers: ContainerManifest) -> NormalizedManifest:
    return NormalizedManifest(
        name="hello",
        workloads=(
            WorkloadManifest(
                name="web",
                kind=kind,
                schedule="0 * * * *" if kind == "cronjob" else None,
                storage_size="1Gi" if kind == "statefulset" else None,
                containers=containers or (_container(),),
            ),
        ),
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )


def _render(manifest: NormalizedManifest, **kwargs: Any) -> list[dict[str, Any]]:
    return render_manifests(
        manifest,
        app_slug="hello",
        namespace="ns",
        image_tag="v1.2.3",
        image_repository="registry.acme.com/hello",
        environment_name="prod",
        **kwargs,
    )


def _images(node: Any) -> list[str]:
    """Every container image in a rendered resource set, whatever the
    workload kind buries its pod spec under (Deployment, CronJob's
    jobTemplate, Knative Service's template, ...)."""
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "containers" and isinstance(value, list):
                out += [c["image"] for c in value if isinstance(c, dict) and "image" in c]
            else:
                out += _images(value)
    elif isinstance(node, list):
        for item in node:
            out += _images(item)
    return out


@pytest.mark.parametrize("kind", ["deployment", "cronjob", "task", "statefulset", "function"])
def test_digest_pins_every_pod_bearing_workload_kind(kind):
    images = _images(_render(_manifest(kind), image_digest=DIGEST))
    assert images, f"{kind} rendered no containers"
    assert images == [f"registry.acme.com/hello@{DIGEST}"] * len(images)


def test_no_digest_keeps_the_tag_form():
    """Dry-run previews and pre-build renders have no digest yet — they must
    still render, not blow up or emit a half-formed ref."""
    assert _images(_render(_manifest("deployment"))) == ["registry.acme.com/hello:v1.2.3"]


def test_container_image_ref_override_is_not_repinned():
    """A sidecar pinning its own image is not this app's build output, so the
    app digest must not be stamped onto it."""
    resources = _render(
        _manifest(
            "deployment",
            _container(),
            _container("sidecar", image_ref="docker.io/envoy:v1.29"),
        ),
        image_digest=DIGEST,
    )
    dep = next(r for r in resources if r["kind"] == "Deployment")
    by_name = {c["name"]: c["image"] for c in dep["spec"]["template"]["spec"]["containers"]}
    assert by_name["app"] == f"registry.acme.com/hello@{DIGEST}"
    assert by_name["sidecar"] == "docker.io/envoy:v1.29"


def test_malformed_digest_is_rejected_not_silently_rendered():
    """The pin goes through the canonicalizer, so junk cannot reach a pod
    spec as an unpullable image ref."""
    from astrolift_workflows.activities.image_digest import ImageRefError

    with pytest.raises(ImageRefError):
        _render(_manifest("deployment"), image_digest="sha256:nope")


def test_registry_port_is_not_mistaken_for_a_tag():
    resources = render_manifests(
        _manifest("deployment"),
        app_slug="hello",
        namespace="ns",
        image_tag="v1",
        image_repository="registry:5000/hello",
        image_digest=DIGEST,
        environment_name="prod",
    )
    assert _images(resources) == [f"registry:5000/hello@{DIGEST}"]
