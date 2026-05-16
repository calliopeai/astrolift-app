"""
pre_flight rejects empty-render manifests (#359).

When an operator saves a manifest with no ``[[workloads]]`` declared,
``render_resources_for_deployment`` returns an empty list and
``apply_manifests`` later raises with an opaque "rendered to zero
resources" error mid-workflow. Catching this in ``pre_flight``
surfaces a clearer pre-deploy error and avoids burning a Temporal
activity attempt + a cluster-driver round-trip.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_workflows.activities.app_lifecycle import _pre_flight_sync
from core.app_deploy import AppDeployError

pytestmark = pytest.mark.django_db


_MANIFEST_WITH_WORKLOAD = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true
"""

# Valid TOML, parser-accepted, but no workloads → the renderer
# produces an empty resource list. This is the case the empty-manifest
# guard catches.
_MANIFEST_NO_WORKLOADS = """
name = "hello-app"
"""


def _make_deployment(app, env, image_tag: str = "v1") -> Deployment:
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag=image_tag,
    )


def test_pre_flight_raises_when_manifest_renders_to_zero_resources(app, env):
    """Manifest that parses cleanly but declares no workloads must
    fail at pre_flight rather than slipping through to apply."""
    app.manifest_raw = _MANIFEST_NO_WORKLOADS
    app.save(update_fields=["manifest_raw"])
    deployment = _make_deployment(app, env)

    with pytest.raises(AppDeployError, match="zero Kubernetes resources"):
        _pre_flight_sync(deployment.pk)


def test_pre_flight_accepts_manifest_with_workloads(app, env):
    """Regression guard: the existing happy path (manifest with at
    least one workload) must still pass pre_flight."""
    app.manifest_raw = _MANIFEST_WITH_WORKLOAD
    app.save(update_fields=["manifest_raw"])
    deployment = _make_deployment(app, env)

    # Must not raise.
    _pre_flight_sync(deployment.pk)
