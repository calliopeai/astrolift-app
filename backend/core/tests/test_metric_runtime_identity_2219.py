"""Real PostgreSQL and explicit disposable Kind runtime identity boundaries."""

import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import Workload
from core.app_deploy import render_resources_for_deployment
from core.tests.utils.scope_world import ScopeWorld, make_cluster
from providers._sdk.workload_metrics import APP_ID_LABEL, ENVIRONMENT_ID_LABEL, WORKLOAD_ID_LABEL

pytestmark = pytest.mark.django_db


@pytest.fixture
def runtime():
    world = ScopeWorld("signals-2219")
    world.app = world.platform_app
    world.cluster = make_cluster(world, "signals-2219")
    world.environment = AppEnvironment.objects.create(
        registered_app=world.app,
        tenant_cluster=world.cluster,
        name="selected",
        k8s_namespace="signals-selected",
    )
    world.workload = Workload.objects.create(
        registered_app=world.app, slug="api", name="API", kind="deployment"
    )
    world.other = Workload.objects.create(
        registered_app=world.app, slug="worker", name="Worker", kind="deployment"
    )
    world.app.manifest_raw = """name = "signal-fixture"
[[workloads]]
name = "api"
kind = "deployment"
[[workloads.containers]]
name = "api"
image_ref = "busybox:1.36"
command = ["sh", "-c", "sleep 3600"]
[[workloads]]
name = "worker"
kind = "deployment"
[[workloads.containers]]
name = "worker"
image_ref = "busybox:1.36"
command = ["sh", "-c", "sleep 3600"]
"""
    world.app.save(update_fields=["manifest_raw"])
    world.deployment = Deployment.objects.create(
        registered_app=world.app, app_environment=world.environment, image_tag="fixture"
    )
    return world


def labels_for(runtime, workload):
    return {
        APP_ID_LABEL: str(runtime.app.guid),
        ENVIRONMENT_ID_LABEL: str(runtime.environment.guid),
        WORKLOAD_ID_LABEL: str(workload.guid),
    }


def test_production_deploy_render_stamps_controller_and_pod_identity_without_changing_selectors(runtime):
    resources = render_resources_for_deployment(runtime.deployment)
    controllers = [row for row in resources if row["kind"] == "Deployment"]
    assert len(controllers) == 2
    for resource in controllers:
        workload = runtime.workload if resource["metadata"]["name"] == "api" else runtime.other
        expected = labels_for(runtime, workload)
        for key, value in expected.items():
            assert resource["metadata"]["labels"][key] == value
            assert resource["spec"]["template"]["metadata"]["labels"][key] == value
        assert resource["spec"]["selector"]["matchLabels"] == {
            "astrolift.dev/app": runtime.app.slug,
            "astrolift.dev/workload": workload.slug,
        }


def test_same_name_recreated_workload_and_selected_environment_have_distinct_render_identity(runtime):
    previous = str(runtime.workload.guid)
    runtime.workload.deleted_at = timezone.now()
    runtime.workload.save(update_fields=["deleted_at"])
    replacement = Workload.objects.create(
        registered_app=runtime.app, slug="api", name="Replacement", kind="deployment"
    )
    different = AppEnvironment.objects.create(
        registered_app=runtime.app,
        tenant_cluster=runtime.cluster,
        name="other",
        k8s_namespace="signals-other",
    )
    runtime.deployment.app_environment = different
    resources = render_resources_for_deployment(runtime.deployment)
    api = next(row for row in resources if row["kind"] == "Deployment" and row["metadata"]["name"] == "api")
    assert api["metadata"]["labels"][WORKLOAD_ID_LABEL] == str(replacement.guid) != previous
    assert api["spec"]["template"]["metadata"]["labels"][ENVIRONMENT_ID_LABEL] == str(different.guid)
    assert api["metadata"]["namespace"] == different.k8s_namespace


@pytest.fixture
def native(runtime):
    path = os.environ.get("ASTROLIFT_METRICS_TEST_KUBECONFIG")
    if not path:
        pytest.skip("requires explicit disposable Kind kubeconfig")
    import yaml
    from _sdk.cluster import ClusterAuth
    from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig
    from kubernetes import client, config

    content = Path(path).read_text()
    document = yaml.safe_load(content)
    assert document["current-context"] == "kind-astrolift-env-target-2217-a", "refusing an unowned context"
    configuration = client.Configuration()
    config.load_kube_config(
        config_file=path, context=document["current-context"], client_configuration=configuration
    )
    assert configuration.host.startswith("https://127.0.0.1:"), "refusing a non-local API"
    api_client = client.ApiClient(configuration)
    core, apps = client.CoreV1Api(api_client), client.AppsV1Api(api_client)
    namespace = "signals-2219-" + uuid4().hex[:12]
    core.create_namespace({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}})
    runtime.environment.k8s_namespace = namespace
    runtime.environment.save(update_fields=["k8s_namespace"])
    try:
        for resource in render_resources_for_deployment(runtime.deployment):
            if resource["kind"] == "Deployment":
                apps.create_namespaced_deployment(namespace, resource)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            pods = core.list_namespaced_pod(namespace).items
            if len(pods) == 2 and all(
                pod.status.container_statuses and pod.status.container_statuses[0].state.running
                for pod in pods
            ):
                break
            time.sleep(0.5)
        else:
            pytest.fail("task-owned deployments did not run within 90 seconds")
        driver = K8sNativeClusterDriver(config=K8sNativeConfig(kubeconfig_path=path))
        auth = ClusterAuth(
            slug="signals-2219",
            auth_method="kubeconfig",
            auth_config={"kubeconfig": content, "context": document["current-context"]},
        )
        yield runtime, driver, auth, core, apps
    finally:
        core.delete_namespace(namespace)
        api_client.close()


def membership(native, workload=None):
    runtime, driver, auth, _, _ = native
    workload = workload or runtime.workload
    return driver.metric_containers(
        auth=auth,
        namespace=runtime.environment.k8s_namespace,
        app_slug=runtime.app.slug,
        app_id=str(runtime.app.guid),
        environment_id=str(runtime.environment.guid),
        workload_slug=workload.slug,
        workload_id=str(workload.guid),
        workload_kind=workload.kind,
    )


def test_native_owner_reference_chain_selects_only_exact_workload_and_physical_container(native):
    runtime, _, _, core, _ = native
    own, sibling = membership(native), membership(native, runtime.other)
    assert len(own) == len(sibling) == 1
    assert own[0].container_name == "api" and sibling[0].container_name == "worker"
    assert own[0].pod_name != sibling[0].pod_name
    pod = core.read_namespaced_pod(own[0].pod_name, runtime.environment.k8s_namespace)
    assert own[0].pod_uid == pod.metadata.uid
    assert own[0].container_id == pod.status.container_statuses[0].container_id.split("://")[1]
    assert own[0].started_at == pod.status.container_statuses[0].state.running.started_at.timestamp()


def test_native_same_name_replacement_cannot_adopt_old_runtime(native):
    from _sdk.workload_metrics import MetricMembershipUnavailable

    runtime, _, _, _, _ = native
    assert membership(native)
    runtime.workload.deleted_at = timezone.now()
    runtime.workload.save(update_fields=["deleted_at"])
    replacement = Workload.objects.create(
        registered_app=runtime.app, slug="api", name="Replacement", kind="deployment"
    )
    with pytest.raises(MetricMembershipUnavailable, match="OWNERSHIP_UNVERIFIED"):
        membership(native, replacement)
