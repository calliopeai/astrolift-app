"""A bound workload's pods carry its Cloud Operations bundle's label, and only the platform sets it (#2099).

The bundle's route sink takes the GKE container entries of pods carrying the
label into the bundle's bucket and keeps them out of ``_Default``, and the
workload holds no log-write role. So the deploy render stamps the label on the
pods of every workload a log-writing bundle is bound to, and refuses a render
that already carries it, in any spelling GKE folds onto the same log label.

The routing proof provisions two real bundles against the provider tests' fake
Cloud Logging client and replays the project's Log Router over what GKE would
write for each rendered pod.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from _sdk.managed_service import ServiceHandle
from gcp.managed.observability_cloud_operations import BUNDLE_POD_LABEL

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from core.app_deploy import AppDeployError, render_resources_for_deployment
from providers.tests.gcp.test_bundle_log_view_2099 import LogRouter, gke_container_entry
from providers.tests.gcp.test_managed_observability_cloud_operations import (
    FakeCloudOperationsClient,
    _driver,
    _spec,
)

pytestmark = pytest.mark.django_db

_MANIFEST = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true
  port = 8080

[[workloads]]
name = "worker"
kind = "deployment"

  [[workloads.containers]]
  name = "worker"
  is_primary = true

[[workloads]]
name = "nightly"
kind = "cronjob"
schedule = "0 3 * * *"

  [[workloads.containers]]
  name = "nightly"
  is_primary = true
"""


@pytest.fixture
def bundles():
    """Two bundles provisioned into one project, as two tenants' would be."""
    logging = FakeCloudOperationsClient()
    out = SimpleNamespace(logging=logging, handle={}, bucket={})
    for name in ("a", "b"):
        driver, _, _ = _driver(logging=logging)
        msid = f"bundle-{name}"
        result = driver.provision(replace(_spec({"deletion_protection": False}), managed_service_id=msid))
        assert result.ok, result.message
        out.handle[name] = result.handle
        binding = driver.binding(ServiceHandle(result.handle, managed_service_id=msid), {})
        out.bucket[name] = f"logging.googleapis.com/{binding.env_vars['GCP_LOG_BUCKET'].literal}"
    return out


def _bundle(app, env, handle, *, name="ops", config=None, bind_workloads=None):
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.OBSERVABILITY,
        variant="cloud_operations",
        name=name,
        status=ManagedService.Status.ACTIVE,
        backend_ref=handle,
        config=config or {},
        bind_workloads=bind_workloads or [],
    )


def _render(app, env):
    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )
    return render_resources_for_deployment(deployment)


def _resource(resources, kind, name):
    return next(r for r in resources if r["kind"] == kind and r["metadata"]["name"] == name)


def _pod_labels(resources, kind, name):
    spec = _resource(resources, kind, name)["spec"]
    if kind == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return spec["template"]["metadata"]["labels"]


def _bundle_id(handle):
    return handle.rsplit("/", 1)[-1]


def test_every_pod_of_a_bound_workload_carries_the_bundle_label(app, env, bundles):
    _bundle(app, env, bundles.handle["a"])

    resources = _render(app, env)

    for kind, name in (("Deployment", "web"), ("Deployment", "worker"), ("CronJob", "nightly")):
        assert _pod_labels(resources, kind, name)[BUNDLE_POD_LABEL] == _bundle_id(bundles.handle["a"])
        # The pods are what GKE reads; the controller does not need it.
        assert BUNDLE_POD_LABEL not in _resource(resources, kind, name)["metadata"]["labels"]
    # The renderer shares one label map between a workload's controller, pods
    # and Service; stamping the pods must not leak onto the others.
    assert BUNDLE_POD_LABEL not in _resource(resources, "Service", "web")["metadata"]["labels"]


def test_only_the_workloads_a_bundle_is_bound_to_are_labelled(app, env, bundles):
    _bundle(app, env, bundles.handle["a"], bind_workloads=["worker"])

    resources = _render(app, env)

    assert _pod_labels(resources, "Deployment", "worker")[BUNDLE_POD_LABEL] == _bundle_id(bundles.handle["a"])
    assert BUNDLE_POD_LABEL not in _pod_labels(resources, "Deployment", "web")
    assert BUNDLE_POD_LABEL not in _pod_labels(resources, "CronJob", "nightly")


@pytest.mark.parametrize("config", [{"access_mode": "read"}, {"access_mode": "metrics"}, {"log_bucket": False}])
def test_a_binding_that_writes_no_logs_labels_no_pod(app, env, bundles, config):
    _bundle(app, env, bundles.handle["a"], config=config)

    resources = _render(app, env)

    for kind, name in (("Deployment", "web"), ("Deployment", "worker"), ("CronJob", "nightly")):
        assert BUNDLE_POD_LABEL not in _pod_labels(resources, kind, name)


def test_a_project_bundle_labels_its_attached_workloads_until_it_is_detached(app, env, cluster, bundles):
    shared = ManagedService.objects.create(
        project=app.project,
        tenant_cluster=cluster,
        kind=ManagedService.Kind.OBSERVABILITY,
        variant="cloud_operations",
        name="shared-ops",
        status=ManagedService.Status.ACTIVE,
        backend_ref=bundles.handle["b"],
    )
    attachment = ManagedServiceAttachment.objects.create(
        managed_service=shared, app_environment=env, workload_names=["web"]
    )

    attached = _render(app, env)
    attachment.soft_delete()
    detached = _render(app, env)

    assert _pod_labels(attached, "Deployment", "web")[BUNDLE_POD_LABEL] == _bundle_id(bundles.handle["b"])
    assert BUNDLE_POD_LABEL not in _pod_labels(attached, "Deployment", "worker")
    assert BUNDLE_POD_LABEL not in _pod_labels(detached, "Deployment", "web")


@pytest.mark.parametrize(
    "key",
    [
        BUNDLE_POD_LABEL,
        # GKE folds each "." of a pod label key to "_", so these reach the
        # entry as the same log label and would pick a bucket just the same.
        "astrolift.io.observability.bundle",
        "astrolift.io_observability.bundle",
    ],
)
@pytest.mark.parametrize("where", ["pod", "controller"])
@pytest.mark.parametrize("bound", [True, False])
def test_a_label_the_manifest_supplied_is_refused(app, env, bundles, monkeypatch, key, where, bound):
    """astrolift.toml has no labels field today, so the value is supplied where
    any future one would arrive: in what the renderer hands the deploy layer."""
    from astrolift_manifest import render

    if bound:
        _bundle(app, env, bundles.handle["a"])
    real = render.render_manifests

    def supplying(*args, **kwargs):
        resources = real(*args, **kwargs)
        web = _resource(resources, "Deployment", "web")
        target = web["spec"]["template"]["metadata"] if where == "pod" else web["metadata"]
        target["labels"] = {**target["labels"], key: _bundle_id(bundles.handle["b"])}
        return resources

    monkeypatch.setattr(render, "render_manifests", supplying)

    with pytest.raises(AppDeployError, match="only Astrolift sets it"):
        _render(app, env)


def test_a_workload_two_log_writing_bundles_are_bound_to_is_refused(app, env, bundles):
    _bundle(app, env, bundles.handle["a"], name="first")
    second = _bundle(app, env, bundles.handle["b"], name="second", bind_workloads=["web"])

    with pytest.raises(AppDeployError, match="'web' is bound to 2 Cloud Operations bundles"):
        _render(app, env)

    second.config = {"access_mode": "read"}
    second.save(update_fields=["config"])
    resources = _render(app, env)
    assert _pod_labels(resources, "Deployment", "web")[BUNDLE_POD_LABEL] == _bundle_id(bundles.handle["a"])


def test_a_workload_labelled_for_bundle_a_cannot_land_logs_in_bundle_b(app, env, cluster, bundles):
    """End to end: render each environment, write each pod's container log the
    way GKE does, and route it through the project's Log Router."""
    second = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="staging", k8s_namespace="hello-staging"
    )
    unbound = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="sandbox", k8s_namespace="hello-sandbox"
    )
    _bundle(app, env, bundles.handle["a"])
    _bundle(app, second, bundles.handle["b"])
    router = LogRouter(bundles.logging)

    reached = {}
    for name, environment in (("a", env), ("b", second), ("none", unbound)):
        pods = [
            _pod_labels(resources, kind, workload)
            for resources in [_render(app, environment)]
            for kind, workload in (("Deployment", "web"), ("Deployment", "worker"), ("CronJob", "nightly"))
        ]
        reached[name] = {frozenset(router.destinations(gke_container_entry(labels))) for labels in pods}

    assert bundles.bucket["a"] != bundles.bucket["b"]
    assert reached["a"] == {frozenset({bundles.bucket["a"]})}
    assert reached["b"] == {frozenset({bundles.bucket["b"]})}
    assert reached["none"] == {frozenset({"_Default"})}
