"""Workloads bound to an RDS-family service trust the Amazon RDS CA (#2404).

node-postgres reads ``sslmode=require`` as verify-full and Node does not
ship the RDS CA, so a Node app on an RDS binding could not connect. The
render now mounts the vendored bundle and points ``NODE_EXTRA_CA_CERTS`` and
``PGSSLROOTCERT`` at it, as the lowest-precedence env source.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_services.models import ManagedService
from astrolift_services.rds_ca import (
    BUNDLE_KEY,
    BUNDLE_PATH,
    MOUNT_DIR,
    VOLUME_NAME,
    bundle_configmap_name,
    bundle_pem,
    env_configmap_name,
    inject_rds_ca,
)
from astrolift_workflows.activities.app_lifecycle import _bindings_secret_name
from core.app_deploy import render_resources_for_deployment

_MANIFEST = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true

[[workloads]]
name = "worker"
kind = "deployment"

  [[workloads.containers]]
  name = "worker"
  is_primary = true
"""


def _deploy(app, env):
    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])
    d = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )
    return render_resources_for_deployment(d)


def _service(app, env, *, variant, bind_workloads=None):
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="orders",
        variant=variant,
        status=ManagedService.Status.ACTIVE,
        bind_workloads=bind_workloads or [],
    )


def _deployment(resources, name):
    return next(r for r in resources if r["kind"] == "Deployment" and r["metadata"]["name"].endswith(name))


def _has_ca(deployment) -> bool:
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    return any(v["name"] == VOLUME_NAME for v in pod.get("volumes") or []) and any(
        m["name"] == VOLUME_NAME for m in container.get("volumeMounts") or []
    )


@pytest.mark.django_db
def test_rds_postgres_binding_mounts_the_ca_and_points_env_at_it(app, env):
    _service(app, env, variant="rds")

    resources = _deploy(app, env)

    configmaps = {r["metadata"]["name"]: r for r in resources if r["kind"] == "ConfigMap"}
    assert configmaps[bundle_configmap_name(app.slug)]["data"] == {BUNDLE_KEY: bundle_pem()}
    assert configmaps[env_configmap_name(app.slug)]["data"] == {
        "NODE_EXTRA_CA_CERTS": BUNDLE_PATH,
        "PGSSLROOTCERT": BUNDLE_PATH,
    }
    web = _deployment(resources, "web")
    pod = web["spec"]["template"]["spec"]
    container = pod["containers"][0]
    # First in envFrom: every later source and every explicit env entry wins.
    assert container["envFrom"][0] == {"configMapRef": {"name": env_configmap_name(app.slug)}}
    assert {"secretRef": {"name": _bindings_secret_name(app.slug)}} in container["envFrom"]
    assert {"name": VOLUME_NAME, "mountPath": MOUNT_DIR, "readOnly": True} in container["volumeMounts"]
    assert {"name": VOLUME_NAME, "configMap": {"name": bundle_configmap_name(app.slug)}} in pod["volumes"]


@pytest.mark.django_db
def test_non_rds_postgres_is_left_alone(app, env):
    _service(app, env, variant="postgres_flexible")

    resources = _deploy(app, env)

    assert not [r for r in resources if r["kind"] == "ConfigMap"]
    assert not _has_ca(_deployment(resources, "web"))


@pytest.mark.django_db
def test_a_workload_scoped_binding_only_reaches_its_workloads(app, env):
    _service(app, env, variant="aurora_postgres", bind_workloads=["worker"])

    resources = _deploy(app, env)

    assert _has_ca(_deployment(resources, "worker"))
    assert not _has_ca(_deployment(resources, "web"))


def _cronjob(env=None):
    container = {"name": "job", "envFrom": [{"secretRef": {"name": "astrolift-bindings-app"}}]}
    if env:
        container["env"] = env
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": "app-job"},
        "spec": {"jobTemplate": {"spec": {"template": {"spec": {"containers": [container]}}}}},
    }


def test_an_env_var_the_app_sets_is_not_overridden():
    own = [{"name": "NODE_EXTRA_CA_CERTS", "value": "/app/my-ca.pem"}]
    out = inject_rds_ca(
        [_cronjob(env=own)], app_slug="app", namespace="ns", secret_names={"astrolift-bindings-app"}
    )

    job = next(r for r in out if r["kind"] == "CronJob")
    container = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    assert container["env"] == own
    assert container["envFrom"] == [
        {"configMapRef": {"name": env_configmap_name("app")}},
        {"secretRef": {"name": "astrolift-bindings-app"}},
    ]


def test_injection_is_idempotent_and_skips_unbound_containers():
    once = inject_rds_ca(
        [_cronjob()], app_slug="app", namespace="ns", secret_names={"astrolift-bindings-app"}
    )
    twice = inject_rds_ca(
        [r for r in once if r["kind"] == "CronJob"],
        app_slug="app",
        namespace="ns",
        secret_names={"astrolift-bindings-app"},
    )
    pod = next(r for r in twice if r["kind"] == "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert len(pod["volumes"]) == 1
    assert len(pod["containers"][0]["volumeMounts"]) == 1
    assert len(pod["containers"][0]["envFrom"]) == 2

    untouched = [_cronjob()]
    assert inject_rds_ca(untouched, app_slug="app", namespace="ns", secret_names={"other"}) is untouched


def test_the_vendored_bundle_is_a_pem_ca_bundle():
    pem = bundle_pem()
    assert pem.count("-----BEGIN CERTIFICATE-----") >= 50
    assert "\r" not in pem
