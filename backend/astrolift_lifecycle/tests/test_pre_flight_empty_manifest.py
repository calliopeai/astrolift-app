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

from types import SimpleNamespace

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

# A static_site-only manifest (#1010): renders to ZERO K8s resources (its
# bucket + CDN are managed services), but is a valid deploy — pre_flight must
# NOT treat it as the empty-manifest error.
_MANIFEST_STATIC_ONLY = """
name = "hello-app"

[[workloads]]
name = "site"
kind = "static_site"
is_public = true
static_output_dir = "dist"
"""

# A faas-only manifest (#987): renders to ZERO K8s resources (the cloud runs the
# function; its Lambda + Function URL + CloudFront are managed services), but is
# a valid deploy — pre_flight must NOT treat it as the empty-manifest error.
_MANIFEST_FAAS_ONLY = """
name = "hello-app"

[[workloads]]
name = "api"
kind = "faas"
faas_public = true
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


def test_pre_flight_accepts_static_only_manifest_rendering_zero_resources(app, env):
    """#1010: a static_site-only app renders zero K8s resources (bucket + CDN
    are managed services, not pods), so pre_flight must NOT reject it as an
    empty manifest — the empty-render gate only fires when there's also no
    static workload."""
    app.manifest_raw = _MANIFEST_STATIC_ONLY
    app.save(update_fields=["manifest_raw"])
    deployment = _make_deployment(app, env)

    # Must not raise even though render_resources_for_deployment returns [].
    _pre_flight_sync(deployment.pk)


def test_pre_flight_accepts_faas_only_manifest_rendering_zero_resources(app, env):
    """#987: a faas-only app renders zero K8s resources (the cloud runs the
    function; its Lambda + Function URL + CloudFront are managed services, not
    pods), so pre_flight must NOT reject it as an empty manifest — the
    empty-render gate only fires when there's also no pod-less workload."""
    app.manifest_raw = _MANIFEST_FAAS_ONLY
    app.save(update_fields=["manifest_raw"])
    deployment = _make_deployment(app, env)

    # Must not raise even though render_resources_for_deployment returns [].
    _pre_flight_sync(deployment.pk)


def test_render_includes_bindings_envfrom_when_managed_service_exists(app, env):
    """Regression for #1003: render_resources_for_deployment (what
    apply_manifests actually applies) must wire the managed-service
    bindings Secret into the workload's envFrom. Before the fix it
    re-rendered without env_from, so the bindings Secret (created by
    update_secrets) was never mounted and apps saw no connection env."""
    from astrolift_services.models import ManagedService
    from core.app_deploy import render_resources_for_deployment

    app.manifest_raw = _MANIFEST_WITH_WORKLOAD
    app.save(update_fields=["manifest_raw"])
    ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="object_store",
        name="archive",
        variant="s3",
        status=ManagedService.Status.ACTIVE,
    )
    deployment = _make_deployment(app, env)

    resources = render_resources_for_deployment(deployment)
    deploy_res = [r for r in resources if r.get("kind") == "Deployment"]
    assert deploy_res, "expected a Deployment resource"
    bindings = f"astrolift-bindings-{app.slug}"
    found = False
    for d in deploy_res:
        for c in d["spec"]["template"]["spec"]["containers"]:
            refs = [e.get("secretRef", {}).get("name") for e in c.get("envFrom", [])]
            if bindings in refs:
                found = True
    assert found, f"{bindings} not mounted via envFrom in any container"


def test_render_no_bindings_envfrom_without_managed_service(app, env):
    """No managed service → no bindings Secret mounted (clean default)."""
    from core.app_deploy import render_resources_for_deployment

    app.manifest_raw = _MANIFEST_WITH_WORKLOAD
    app.save(update_fields=["manifest_raw"])
    deployment = _make_deployment(app, env)
    resources = render_resources_for_deployment(deployment)
    for d in [r for r in resources if r.get("kind") == "Deployment"]:
        for c in d["spec"]["template"]["spec"]["containers"]:
            refs = [e.get("secretRef", {}).get("name") for e in c.get("envFrom", [])]
            assert f"astrolift-bindings-{app.slug}" not in refs


def test_render_attaches_active_managed_filesystem_to_workload(app, env, monkeypatch):
    from astrolift_services.models import ManagedService, ManagedServiceVolumeBinding
    from core.app_deploy import render_resources_for_deployment

    app.manifest_raw = _MANIFEST_WITH_WORKLOAD
    app.save(update_fields=["manifest_raw"])
    service = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.FILESYSTEM,
        name="shared-data",
        variant="efs",
        status=ManagedService.Status.ACTIVE,
    )
    ManagedServiceVolumeBinding.objects.create(
        managed_service=service,
        name="shared-data",
        mount_path="/mnt/shared",
        source_kind=ManagedServiceVolumeBinding.SourceKind.CSI,
        protocol="nfs4",
        csi_driver="efs.csi.aws.com",
        volume_handle="fs-12345678",
        access_modes=["ReadWriteMany"],
    )
    driver = SimpleNamespace(list_csi_drivers=lambda _slug: ["efs.csi.aws.com"])
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda _deployment: (driver, SimpleNamespace(slug=env.tenant_cluster.slug), "unused"),
    )

    resources = render_resources_for_deployment(_make_deployment(app, env))

    assert {row["kind"] for row in resources} >= {
        "Deployment",
        "PersistentVolume",
        "PersistentVolumeClaim",
    }
    rendered = next(row for row in resources if row["kind"] == "Deployment")
    pod_spec = rendered["spec"]["template"]["spec"]
    assert pod_spec["volumes"][0]["persistentVolumeClaim"]["claimName"].startswith(
        "alft-fs-shared-data-"
    )
    assert pod_spec["containers"][0]["volumeMounts"] == [
        {"name": "shared-data", "mountPath": "/mnt/shared", "readOnly": False}
    ]
