"""The applied render pins to Deployment.image_digest (spec 12 §9).

``render_resources_for_deployment`` is the render ``apply_manifests``
actually ships (#1003), and the ``render_manifests`` activity is kept in
lock-step with it. Both stored the build's digest on the Deployment row
and then threw it away at render time, so what reached the cluster was a
mutable ``<repo>:<tag>``. ``rollback.py`` refuses a target without a
digest precisely to guarantee byte-identical bytes; that guarantee is
only real if the digest reaches the pod spec.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from core.app_deploy import render_resources_for_deployment

pytestmark = pytest.mark.django_db

DIGEST = "sha256:" + "1f" * 32

_MANIFEST = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true
"""


def _deployment(app, env, *, image_digest: str = "") -> Deployment:
    app.manifest_raw = _MANIFEST
    app.registry_repo_uri = "registry.acme.com/hello"
    app.save(update_fields=["manifest_raw", "registry_repo_uri"])
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
        image_digest=image_digest,
    )


def _images(resources: list[dict]) -> list[str]:
    return [
        c["image"]
        for r in resources
        if r.get("kind") == "Deployment"
        for c in r["spec"]["template"]["spec"]["containers"]
    ]


def test_applied_render_pins_to_the_recorded_digest(app, env):
    resources = render_resources_for_deployment(_deployment(app, env, image_digest=DIGEST))
    assert _images(resources) == [f"registry.acme.com/hello@{DIGEST}"]


def test_applied_render_falls_back_to_the_tag_without_a_digest(app, env):
    """build_image reads the digest from the registry best-effort, so a
    successful build can leave the column empty — that must still deploy."""
    resources = render_resources_for_deployment(_deployment(app, env))
    assert _images(resources) == ["registry.acme.com/hello:v1"]
