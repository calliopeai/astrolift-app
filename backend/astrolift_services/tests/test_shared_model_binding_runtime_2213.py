"""Persisted subscriptions and Kubernetes destination identities (#2213).

The in-memory SDK boundary models conditional writes, not successful readiness.
The optional kind checks exercise the same production helper and real SDK client.
"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from _sdk.k8s_naming import app_namespace, cluster_model_namespace, cluster_model_resource_name
from django.utils import timezone
from k8s_native.managed._handle import pack
from k8s_native.managed.model_endpoint_vllm import VLLMConfig, VLLMDriver

from astrolift_clusters.models import TenantCluster
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import Workload
from astrolift_services.model_subscriptions import (
    apply_destination_binding,
    binding_secret,
    destination_ready,
    subscription_namespace,
    subscription_secret_name,
)
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from core.tests.utils.scope_world import ScopeWorld, make_cluster

pytestmark = pytest.mark.django_db(transaction=True)


class SecretStore:
    def __init__(self):
        self.data = {}
        self.deleted = []
        self.reads = []

    def get(self, path):
        self.reads.append(path)
        return deepcopy(self.data.get(path))

    def upsert(self, path, value):
        self.data[path] = deepcopy(value)

    def delete(self, path):
        self.deleted.append(path)
        self.data.pop(path, None)


class ConditionalCluster:
    """SDK test boundary: stale writes fail and readiness is independently observed."""

    def __init__(self):
        self.objects = {}
        self.writes = []
        self.deletes = []
        self.dry_runs = []
        self.rejected = False
        self.converge = False
        self.observations = {}

    @staticmethod
    def key(namespace, manifest):
        return (f"{manifest['apiVersion']}/{manifest['kind']}", namespace, manifest["metadata"]["name"])

    def put(self, namespace, manifest):
        manifest = deepcopy(manifest)
        meta = manifest["metadata"]
        meta.setdefault("uid", str(uuid4()))
        meta.setdefault("resourceVersion", "1")
        meta.setdefault("generation", 1)
        if manifest["kind"] == "Deployment":
            rs_name = f"{meta['name']}-{meta['generation']}"
            self.objects[("apps/v1/ReplicaSet", namespace, rs_name)] = {
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {
                    "name": rs_name,
                    "uid": f"rs-{meta['uid']}-{meta['generation']}",
                    "labels": deepcopy(manifest["spec"]["template"]["metadata"]["labels"]),
                    "ownerReferences": [
                        {"kind": "Deployment", "name": meta["name"], "uid": meta["uid"], "controller": True}
                    ],
                },
            }
        self.objects[self.key(namespace, manifest)] = manifest
        return manifest

    def get_manifest(self, cluster, namespace, kind, name):
        key = (kind, namespace, name)
        row = self.objects.get(key)
        if row and self.converge and kind == "apps/v1/Deployment":
            self.observations[key] = self.observations.get(key, 0) + 1
            if self.observations[key] >= 2:
                replicas = row["spec"].get("replicas", 1)
                row["status"] = {
                    "observedGeneration": row["metadata"]["generation"],
                    "replicas": replicas,
                    "readyReplicas": replicas,
                    "updatedReplicas": replicas,
                    "availableReplicas": replicas,
                    "unavailableReplicas": 0,
                }
        return deepcopy(row)

    def list_manifests(self, cluster, namespace, kind):
        if kind == "apps/v1/ReplicaSet":
            return deepcopy(
                [
                    row
                    for (resource_kind, ns, _), row in self.objects.items()
                    if resource_kind == kind and ns == namespace
                ]
            )
        assert kind == "v1/Pod"
        pods = []
        for (resource_kind, ns, _), row in self.objects.items():
            if resource_kind != "apps/v1/Deployment" or ns != namespace:
                continue
            if row.get("status", {}).get("readyReplicas") != row["spec"].get("replicas", 1):
                continue
            for index in range(row["spec"].get("replicas", 1)):
                metadata = deepcopy(row["spec"]["template"]["metadata"])
                metadata.update(
                    name=f"{row['metadata']['name']}-{index}",
                    uid=f"pod-{index}",
                    ownerReferences=[
                        {
                            "kind": "ReplicaSet",
                            "name": f"{row['metadata']['name']}-{row['metadata']['generation']}",
                            "uid": f"rs-{row['metadata']['uid']}-{row['metadata']['generation']}",
                            "controller": True,
                        }
                    ],
                )
                pods.append(
                    {
                        "metadata": metadata,
                        "spec": deepcopy(row["spec"]["template"]["spec"]),
                        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
                    }
                )
        return pods

    def apply_manifests(self, cluster, namespace, manifests, *, create_only=False, dry_run=False):
        if dry_run:
            self.dry_runs.append(deepcopy(manifests))
            return SimpleNamespace(ok=True, summary=list)
        self.writes.append((namespace, deepcopy(manifests), create_only))
        for manifest in manifests:
            ns = None if manifest["kind"] == "Namespace" else namespace
            key = self.key(ns, manifest)
            existing = self.objects.get(key)
            meta = manifest["metadata"]
            if (
                self.rejected
                or (create_only and existing)
                or (
                    existing
                    and any(
                        meta.get(field, existing["metadata"][field]) != existing["metadata"][field]
                        for field in ("uid", "resourceVersion")
                    )
                )
            ):
                return SimpleNamespace(ok=False)
            new = deepcopy(manifest)
            if existing:
                new["metadata"]["uid"] = existing["metadata"]["uid"]
                new["metadata"]["resourceVersion"] = str(int(existing["metadata"]["resourceVersion"]) + 1)
                new["metadata"]["generation"] = existing["metadata"]["generation"] + 1
                if manifest["kind"] == "Deployment" and "replicas" not in new["spec"]:
                    new["spec"]["replicas"] = existing["spec"]["replicas"]
            self.put(ns, new)
            self.observations[key] = 0
        return SimpleNamespace(ok=True, summary=list)

    def delete_manifests(self, cluster, namespace, manifests, **kwargs):
        for manifest in manifests:
            key = self.key(namespace, manifest)
            existing = self.objects.get(key)
            if existing and any(
                manifest["metadata"].get(field) != existing["metadata"][field]
                for field in ("uid", "resourceVersion")
            ):
                return SimpleNamespace(ok=False)
            self.deletes.append(deepcopy(manifest))
            self.objects.pop(key, None)
        return SimpleNamespace(ok=True)


def workload_resource(env, workload, *, hpa=False, stateful=False):
    labels = {
        "astrolift.dev/app": env.registered_app.slug,
        "astrolift.dev/environment": env.name,
        "astrolift.dev/workload": workload.name,
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet" if stateful else "Deployment",
        "metadata": {
            "name": workload.name,
            "namespace": subscription_namespace(env),
            "labels": labels,
            "annotations": {"astrolift.dev/replica-owner": "hpa"} if hpa else {},
        },
        "spec": {
            "replicas": 3,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "terminationGracePeriodSeconds": 1,
                    "containers": [
                        {"name": "app", "image": "python:3.12-alpine", "command": ["sleep", "3600"]}
                    ],
                },
            },
        },
    }


@pytest.fixture
def runtime_world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))
    w = ScopeWorld("binding2213")
    w.cluster = make_cluster(w, "binding2213")
    w.cluster.lifecycle = TenantCluster.Lifecycle.MANAGED
    w.cluster.save()
    w.apps = [w.medops_app, w.platform_app]
    w.envs = []
    w.workloads = []
    for app in w.apps:
        app.k8s_namespace = app_namespace(organization_slug=w.org.slug, app_slug=app.slug)
        app.save()
        w.envs.append(
            AppEnvironment.objects.create(registered_app=app, tenant_cluster=w.cluster, name="production")
        )
        w.workloads.append(Workload.objects.create(registered_app=app, name="api", slug="api"))
    w.model = ManagedService.objects.create(
        organization=w.org,
        tenant_cluster=w.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="shared",
        status="updating",
        subscription_revision=1,
        model_operation_cluster_guid=w.cluster.guid,
        model_operation_provider_guid=w.cluster.provider_plugin.guid,
        config={
            "model": "Qwen/Qwen3-0.6B",
            "model_revision": "b" * 40,
            "frontend": "python",
            "task": "generate",
            "compute_mode": "cpu",
            "gpu": 0,
            "cpu": "4",
            "memory": "16Gi",
            "cpu_kv_cache_gib": 4,
            "allow_subscriptions": True,
        },
    )
    ns = cluster_model_namespace(
        organization_id=str(w.org.guid), cluster_id=str(w.cluster.guid), managed_service_id=str(w.model.guid)
    )
    name = cluster_model_resource_name(str(w.model.guid))
    w.model.backend_ref = pack(kind="model_endpoint", cluster_id=str(w.cluster.guid), namespace=ns, name=name)
    w.model.save()
    w.rows = []
    w.secrets = SecretStore()
    w.driver = ConditionalCluster()
    for index, env in enumerate(w.envs):
        row = ManagedServiceAttachment.objects.create(
            managed_service=w.model,
            app_environment=env,
            model_subscription=True,
            binding_alias="chat" if index == 0 else "other",
            desired_revision=1,
            subscription_status="pending",
            workload_names=["*"],
        )
        row.credential_ref = f"services/{w.org.guid}/{w.model.guid}/subscriptions/{row.guid}#api_key"
        row.save()
        w.secrets.upsert(row.credential_ref.partition("#")[0], {"api_key": str(index + 1) * 64})
        w.rows.append(row)
        w.driver.put(subscription_namespace(env), workload_resource(env, w.workloads[index]))
    w.cfg = VLLMConfig(
        cluster_driver=w.driver,
        secrets_backend=w.secrets,
        metrics={"namespace": "monitoring"},
        shared_runtimes={
            "cpu": {
                "image": "vllm/vllm-openai-cpu@sha256:" + "a" * 64,
                "version": "0.15.1",
                "architecture": "amd64",
                "hardware_certified": True,
                "node_selector": {"astrolift.dev/vllm-compatible": "cpu"},
            }
        },
    )
    w.vllm = VLLMDriver(config=w.cfg)
    return w


def mark_model_observed(w):
    w.model.applied_config = dict(w.model.config)
    w.model.applied_subscription_revision = w.model.subscription_revision
    w.model.model_ready_auth_revision = w.model.subscription_revision
    w.model.model_ready_generation = 1
    w.model.model_ready_observed_at = timezone.now()
    w.model.model_ready_provider_guid = w.cluster.provider_plugin.guid
    w.model.model_ready_backend_ref = w.model.backend_ref
    w.model.save()
    for row in w.rows:
        row.managed_service = w.model


def test_independent_alias_secrets_never_copy_operator_envelope(runtime_world):
    w = runtime_world
    mark_model_observed(w)
    w.secrets.data["operator-only-envelope"] = {
        "api_key": "operator-secret-never-bind",
        "keys.json": {"subscription_keys": ["all-other-keys"]},
    }
    # A second alias in the same app remains a different credential and Secret.
    second = w.rows[1]
    second.app_environment = w.envs[0]
    second.binding_alias = "other"
    second.save()
    keys = []
    for row in w.rows:
        manifest = binding_secret(row, w.secrets)
        keys.append(manifest["stringData"][f"MODEL_{row.binding_alias.upper()}_API_KEY"])
        assert set(manifest["stringData"]) == {
            f"MODEL_{row.binding_alias.upper()}_{field}"
            for field in ("API_KEY", "ENDPOINT_URL", "DEPLOYMENT_NAME", "REGION", "API_STYLE", "AUTH_MODE")
        }
        assert "keys.json" not in manifest["stringData"] and "operator_key" not in str(manifest)
        apply_destination_binding(row, w.driver, w.secrets)
    assert keys[0] != keys[1]
    assert set(w.secrets.reads) == {row.credential_ref.partition("#")[0] for row in w.rows}
    resource = w.driver.get_manifest("", subscription_namespace(w.envs[0]), "apps/v1/Deployment", "api")
    assert resource["spec"]["template"]["spec"]["containers"][0]["envFrom"] == [
        {"secretRef": {"name": subscription_secret_name(row)}} for row in w.rows
    ]


@pytest.mark.parametrize(
    "corruption", ["missing", "foreign_app", "foreign_environment", "missing_uid", "missing_rv"]
)
def test_binding_refuses_unconfirmed_workload_identity(runtime_world, corruption):
    w = runtime_world
    mark_model_observed(w)
    resource = w.driver.objects[("apps/v1/Deployment", subscription_namespace(w.envs[0]), "api")]
    if corruption == "missing":
        w.driver.objects.pop(("apps/v1/Deployment", subscription_namespace(w.envs[0]), "api"))
    elif corruption.startswith("foreign"):
        key = "astrolift.dev/app" if corruption == "foreign_app" else "astrolift.dev/environment"
        resource["metadata"]["labels"][key] = "other-owner"
    else:
        resource["metadata"].pop("uid" if corruption == "missing_uid" else "resourceVersion")
    with pytest.raises(ValueError):
        apply_destination_binding(w.rows[0], w.driver, w.secrets)
    assert not any(
        manifest["kind"] == "Deployment" for _, manifests, _ in w.driver.writes for manifest in manifests
    )


def test_failed_conditional_write_never_becomes_ready(runtime_world):
    w = runtime_world
    mark_model_observed(w)
    w.driver.rejected = True
    with pytest.raises(ValueError):
        apply_destination_binding(w.rows[0], w.driver, w.secrets)
    assert not destination_ready(w.rows[0], w.driver)


def test_hpa_replica_owner_and_first_envfrom_preserve_identity(runtime_world):
    w = runtime_world
    mark_model_observed(w)
    resource = w.driver.objects[("apps/v1/Deployment", subscription_namespace(w.envs[0]), "api")]
    resource["metadata"]["annotations"]["astrolift.dev/replica-owner"] = "hpa"
    uid, rv = resource["metadata"]["uid"], resource["metadata"]["resourceVersion"]
    apply_destination_binding(w.rows[0], w.driver, w.secrets)
    sent = next(
        manifest
        for _, manifests, _ in w.driver.writes
        for manifest in manifests
        if manifest["kind"] == "Deployment"
    )
    assert "replicas" not in sent["spec"]
    assert sent["metadata"]["uid"] == uid and sent["metadata"]["resourceVersion"] == rv
    assert sent["spec"]["template"]["spec"]["containers"][0]["envFrom"] == [
        {"secretRef": {"name": subscription_secret_name(w.rows[0])}}
    ]
    assert not destination_ready(w.rows[0], w.driver)
    w.driver.converge = True
    assert not destination_ready(w.rows[0], w.driver)
    assert destination_ready(w.rows[0], w.driver)


def test_revoke_one_alias_preserves_another_app_and_key(runtime_world):
    w = runtime_world
    mark_model_observed(w)
    for row in w.rows:
        apply_destination_binding(row, w.driver, w.secrets)
    other_key = deepcopy(w.secrets.data[w.rows[1].credential_ref.partition("#")[0]])
    other_resource = deepcopy(
        w.driver.objects[("apps/v1/Deployment", subscription_namespace(w.envs[1]), "api")]
    )
    w.rows[0].desired_enabled = False
    w.rows[0].desired_revision = 2
    w.rows[0].save()
    apply_destination_binding(w.rows[0], w.driver, w.secrets)
    assert (
        "v1/Secret",
        subscription_namespace(w.envs[0]),
        subscription_secret_name(w.rows[0]),
    ) not in w.driver.objects
    assert (
        w.driver.objects[("apps/v1/Deployment", subscription_namespace(w.envs[1]), "api")] == other_resource
    )
    assert w.secrets.data[w.rows[1].credential_ref.partition("#")[0]] == other_key
    assert w.driver.deletes[0]["metadata"]["uid"] and w.driver.deletes[0]["metadata"]["resourceVersion"]
    assert not destination_ready(w.rows[0], w.driver)


@pytest.fixture
def kind_destination(runtime_world):
    """Explicit opt-in disposable kind only; never discover a default cluster."""
    import os
    from pathlib import Path

    kubeconfig = os.environ.get("ASTROLIFT_TEST_SHARED_MODEL_KUBECONFIG")
    if not kubeconfig:
        pytest.skip("set ASTROLIFT_TEST_SHARED_MODEL_KUBECONFIG for disposable kind proof")
    assert Path(kubeconfig).resolve() == Path("/tmp/astrolift-model-2213.kubeconfig").resolve()
    from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig

    w = runtime_world
    w.org.slug = f"binding-{uuid4().hex[:10]}"
    w.org.save()
    for app in w.apps:
        app.k8s_namespace = app_namespace(organization_slug=w.org.slug, app_slug=app.slug)
        app.save()
    driver = K8sNativeClusterDriver(config=K8sNativeConfig(kubeconfig_path=kubeconfig))
    ns = subscription_namespace(w.envs[0])
    cid = str(w.cluster.guid)
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}}
    assert driver.apply_manifests(cid, ns, [namespace], create_only=True).ok
    try:
        resource = workload_resource(w.envs[0], w.workloads[0], hpa=True)
        assert driver.apply_manifests(cid, ns, [resource]).ok
        import time

        deadline = time.monotonic() + 45
        while True:
            observed = driver.get_manifest(cid, ns, "apps/v1/Deployment", "api")
            status = observed.get("status", {})
            if (
                status.get("observedGeneration") == observed["metadata"]["generation"]
                and status.get("readyReplicas") == 3
                and status.get("availableReplicas") == 3
            ):
                break
            assert time.monotonic() < deadline, "initial disposable app did not become ready"
            time.sleep(0.2)
        mark_model_observed(w)
        yield w, driver, ns, cid
    finally:
        current = driver.get_manifest(cid, None, "v1/Namespace", ns)
        if current:
            stub = {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {key: current["metadata"][key] for key in ("name", "uid", "resourceVersion")},
            }
            assert driver.delete_manifests(cid, None, [stub]).ok


def _wait_destination(row, driver):
    import time

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if destination_ready(row, driver):
            return
        time.sleep(0.2)
    pytest.fail("disposable kind destination did not converge within 45 seconds")


@pytest.mark.integration
def test_kind_actual_secret_rollout_and_replica_handover(kind_destination):
    """Real SSA/scale/pods: first envFrom, independent alias, one-alias revoke."""
    from kubernetes import client
    from kubernetes.stream import stream

    w, driver, ns, cid = kind_destination
    original = driver.get_manifest(cid, ns, "apps/v1/Deployment", "api")
    apply_destination_binding(w.rows[0], driver, w.secrets)
    _wait_destination(w.rows[0], driver)
    bound = driver.get_manifest(cid, ns, "apps/v1/Deployment", "api")
    assert bound["metadata"]["uid"] == original["metadata"]["uid"]
    assert bound["spec"]["replicas"] == 3
    # An actual scale subresource write emulates the HPA's independently owned count.
    client.AppsV1Api().patch_namespaced_deployment_scale(
        "api", ns, {"spec": {"replicas": 5}}, field_manager="hpa-controller"
    )
    _wait_destination(w.rows[0], driver)
    second = w.rows[1]
    second.app_environment = w.envs[0]
    second.save()
    apply_destination_binding(second, driver, w.secrets)
    _wait_destination(second, driver)
    bound = driver.get_manifest(cid, ns, "apps/v1/Deployment", "api")
    assert bound["spec"]["replicas"] == 5 and bound["metadata"]["uid"] == original["metadata"]["uid"]
    refs = bound["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    assert refs == [{"secretRef": {"name": subscription_secret_name(row)}} for row in w.rows]
    w.rows[0].desired_enabled = False
    w.rows[0].desired_revision = 2
    w.rows[0].save()
    apply_destination_binding(w.rows[0], driver, w.secrets)
    _wait_destination(w.rows[0], driver)
    assert driver.get_manifest(cid, ns, "v1/Secret", subscription_secret_name(w.rows[0])) is None
    other = driver.get_manifest(cid, ns, "v1/Secret", subscription_secret_name(second))
    import base64

    assert base64.b64decode(other["data"]["MODEL_OTHER_API_KEY"]).decode() == "2" * 64
    pods = driver.list_manifests(cid, ns, "v1/Pod")
    ready = [
        pod
        for pod in pods
        if not pod["metadata"].get("deletionTimestamp")
        and any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in pod.get("status", {}).get("conditions", [])
        )
    ]
    assert len(ready) == 5
    for pod in ready:
        assert pod["spec"]["containers"][0]["envFrom"] == [
            {"secretRef": {"name": subscription_secret_name(second)}}
        ]
    output = stream(
        client.CoreV1Api().connect_get_namespaced_pod_exec,
        ready[0]["metadata"]["name"],
        ns,
        container="app",
        command=[
            "python3",
            "-c",
            "import os; assert os.getenv('MODEL_CHAT_API_KEY') is None; "
            "assert os.getenv('MODEL_OTHER_API_KEY') == '2' * 64; "
            "assert os.getenv('MODEL_OTHER_API_STYLE') == 'openai'",
        ],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
    assert output == "", "actual pod must receive only the surviving alias binding"


@pytest.mark.integration
@pytest.mark.parametrize("race", ["resource_version", "replacement"])
def test_kind_stale_workload_observation_refuses_actual_api_write(kind_destination, race):
    from kubernetes import client

    w, driver, ns, cid = kind_destination
    observed = driver.get_manifest(cid, ns, "apps/v1/Deployment", "api")
    original_apply = driver.apply_manifests
    raced = False
    errors = []

    def race_before_apply(cluster, namespace, manifests, **kwargs):
        nonlocal raced
        if not raced and manifests[0]["kind"] == "Deployment":
            raced = True
            if race == "resource_version":
                client.AppsV1Api().patch_namespaced_deployment(
                    "api", ns, {"metadata": {"annotations": {"astrolift.io/concurrent-test": "newer"}}}
                )
            else:
                assert driver.delete_manifests(
                    cid,
                    ns,
                    [
                        {
                            "apiVersion": "apps/v1",
                            "kind": "Deployment",
                            "metadata": {
                                key: driver.get_manifest(cid, ns, "apps/v1/Deployment", "api")["metadata"][
                                    key
                                ]
                                for key in ("name", "uid", "resourceVersion")
                            },
                        }
                    ],
                ).ok
                # Foreground-independent API deletion may remain briefly while finalizers settle.
                import time

                deadline = time.monotonic() + 10
                while driver.get_manifest(cid, ns, "apps/v1/Deployment", "api") is not None:
                    assert time.monotonic() < deadline
                    time.sleep(0.1)
                assert original_apply(
                    cid, ns, [workload_resource(w.envs[0], w.workloads[0], hpa=True)], create_only=True
                ).ok
            result = original_apply(cluster, namespace, manifests, **kwargs)
            errors.extend(result.errors)
            return result
        return original_apply(cluster, namespace, manifests, **kwargs)

    driver.apply_manifests = race_before_apply
    with pytest.raises(ValueError, match="could not be applied"):
        apply_destination_binding(w.rows[0], driver, w.secrets)
    assert errors, "real SDK/API refusal must explain the rejected conditional write"
    assert all(error.exception_type == "PreconditionFailedError" for error in errors)
    current = driver.get_manifest(cid, ns, "apps/v1/Deployment", "api")
    assert not current["spec"]["template"]["spec"]["containers"][0].get("envFrom")
    if race == "replacement":
        assert current["metadata"]["uid"] != observed["metadata"]["uid"]
    else:
        assert current["metadata"]["annotations"]["astrolift.io/concurrent-test"] == "newer"


@pytest.mark.parametrize("state", ["extra_old_pod", "unavailable", "wrong_observed_generation"])
def test_destination_controller_counts_cannot_claim_incomplete_rollout_ready(runtime_world, state):
    w = runtime_world
    mark_model_observed(w)
    apply_destination_binding(w.rows[0], w.driver, w.secrets)
    resource = w.driver.objects[("apps/v1/Deployment", subscription_namespace(w.envs[0]), "api")]
    resource["spec"]["replicas"] = 1
    resource["status"] = {
        "observedGeneration": resource["metadata"]["generation"],
        "replicas": 1,
        "readyReplicas": 1,
        "updatedReplicas": 1,
        "availableReplicas": 1,
    }
    if state == "extra_old_pod":
        resource["status"]["replicas"] = 2
        resource["status"]["unavailableReplicas"] = 1
    elif state == "unavailable":
        resource["status"]["unavailableReplicas"] = 1
    else:
        resource["status"]["observedGeneration"] -= 1
    assert not destination_ready(w.rows[0], w.driver)


@pytest.mark.parametrize(
    "revisions",
    [{}, {"currentRevision": "old", "updateRevision": "new"}, {"currentRevision": "", "updateRevision": ""}],
)
def test_statefulset_requires_actual_matching_controller_revision(runtime_world, revisions):
    w = runtime_world
    mark_model_observed(w)
    w.workloads[0].kind = "statefulset"
    w.workloads[0].save()
    resource = workload_resource(w.envs[0], w.workloads[0], stateful=True)
    w.driver.put(subscription_namespace(w.envs[0]), resource)
    apply_destination_binding(w.rows[0], w.driver, w.secrets)
    current = w.driver.objects[("apps/v1/StatefulSet", subscription_namespace(w.envs[0]), "api")]
    current["status"] = dict(
        observedGeneration=current["metadata"]["generation"],
        replicas=3,
        readyReplicas=3,
        updatedReplicas=3,
        **revisions,
    )
    assert not destination_ready(w.rows[0], w.driver)


@pytest.mark.parametrize(
    "defect",
    [
        "old_template",
        "foreign_app",
        "foreign_env",
        "foreign_workload",
        "terminating",
        "unready",
        "pending",
        "extra_old_pod",
        "missing",
        "foreign_controller",
        "replaced_replica_set",
        "foreign_replica_set_controller",
        "missing_secret",
    ],
)
def test_destination_requires_real_current_template_owned_ready_pods(runtime_world, defect):
    w = runtime_world
    mark_model_observed(w)
    apply_destination_binding(w.rows[0], w.driver, w.secrets)
    current = w.driver.objects[("apps/v1/Deployment", subscription_namespace(w.envs[0]), "api")]
    current["spec"]["replicas"] = 1
    current["status"] = {
        "observedGeneration": current["metadata"]["generation"],
        "replicas": 1,
        "readyReplicas": 1,
        "updatedReplicas": 1,
        "availableReplicas": 1,
    }
    pods = w.driver.list_manifests("", subscription_namespace(w.envs[0]), "v1/Pod")
    assert len(pods) == 1
    assert destination_ready(w.rows[0], w.driver), "positive observed controller/pod fixture must be coherent"
    if defect == "old_template":
        pods[0]["metadata"]["annotations"][f"astrolift.io/model-binding-{w.rows[0].guid}"] = "0"
    elif defect in ("foreign_app", "foreign_env", "foreign_workload"):
        label = {"foreign_app": "app", "foreign_env": "environment", "foreign_workload": "workload"}[defect]
        pods[0]["metadata"]["labels"][f"astrolift.dev/{label}"] = "foreign"
    elif defect == "terminating":
        pods[0]["metadata"]["deletionTimestamp"] = "2026-09-30T00:00:00Z"
    elif defect == "unready":
        pods[0]["status"]["conditions"][0]["status"] = "False"
    elif defect == "pending":
        pods[0]["status"]["phase"] = "Pending"
    elif defect == "foreign_controller":
        pods[0]["metadata"]["ownerReferences"][0]["uid"] = "other-rs"
    elif defect in ("replaced_replica_set", "foreign_replica_set_controller"):
        rs = w.driver.objects[
            (
                "apps/v1/ReplicaSet",
                subscription_namespace(w.envs[0]),
                pods[0]["metadata"]["ownerReferences"][0]["name"],
            )
        ]
        if defect == "replaced_replica_set":
            rs["metadata"]["uid"] = "replacement-rs"
        else:
            rs["metadata"]["ownerReferences"][0]["uid"] = "foreign-deployment"
    elif defect == "missing_secret":
        pods[0]["spec"]["containers"][0]["envFrom"] = []
    elif defect == "extra_old_pod":
        pods.append(deepcopy(pods[0]))
        pods[1]["metadata"]["annotations"][f"astrolift.io/model-binding-{w.rows[0].guid}"] = "0"
    else:
        pods.clear()
    w.driver.list_manifests = lambda *args: deepcopy(pods)
    assert not destination_ready(w.rows[0], w.driver)


def _app_deployment(w):
    from astrolift_lifecycle.models import Deployment

    return Deployment.objects.create(
        registered_app=w.apps[0],
        app_environment=w.envs[0],
        workload=w.workloads[0],
        trigger_kind="manual",
        status="deploying",
    )


def test_regular_app_rollout_has_first_envfrom_only_for_observed_own_bindings(runtime_world):
    from astrolift_services.model_subscriptions import stamp_binding_revisions
    from core.app_deploy import deployment_env_from

    w = runtime_world
    deployment = _app_deployment(w)
    refs, _ = deployment_env_from(deployment)
    assert not any(ref.startswith("astrolift-model-") for ref in refs)
    mark_model_observed(w)
    refs, _ = deployment_env_from(deployment)
    assert refs == [subscription_secret_name(w.rows[0])]
    assert subscription_secret_name(w.rows[1]) not in refs
    resource = workload_resource(w.envs[0], w.workloads[0])
    stamp_binding_revisions([resource], w.envs[0])
    assert resource["spec"]["template"]["metadata"]["annotations"] == {
        f"astrolift.io/model-binding-{w.rows[0].guid}": "1"
    }
    # A newer accepted revision cannot claim a new binding is already applied.
    w.rows[0].desired_revision = 2
    w.rows[0].save()
    assert deployment_env_from(deployment)[0] == []


@pytest.mark.parametrize(
    "change",
    [
        "revoked",
        "retired_env",
        "inactive_cluster",
        "retired_provider",
        "replaced_provider",
        "replaced_handle",
    ],
)
def test_regular_app_rollout_never_restores_revoked_or_retargeted_binding(runtime_world, change):
    from core.app_deploy import deployment_env_from

    w = runtime_world
    deployment = _app_deployment(w)
    mark_model_observed(w)
    assert deployment_env_from(deployment)[0] == [subscription_secret_name(w.rows[0])]
    if change == "revoked":
        w.rows[0].desired_enabled = False
        w.rows[0].subscription_status = "revoking"
        w.rows[0].save()
    elif change == "retired_env":
        w.envs[0].soft_delete()
    elif change == "inactive_cluster":
        w.cluster.is_active = False
        w.cluster.save()
    elif change == "retired_provider":
        w.cluster.provider_plugin.soft_delete()
    elif change == "replaced_provider":
        w.cluster.provider_plugin = make_cluster(w, "new-provider2213").provider_plugin
        w.cluster.save()
    else:
        w.model.backend_ref += "-changed"
        w.model.save()
    deployment.refresh_from_db()
    assert deployment_env_from(deployment)[0] == []


def test_real_app_secret_materializer_copies_only_selected_subscription(runtime_world, monkeypatch):
    from astrolift_workflows.activities.app_lifecycle import _update_secrets_sync

    w = runtime_world
    w.apps[
        0
    ].manifest_raw = 'name = "binding-app"\n[[workloads]]\nname = "api"\nkind = "deployment"\n[[workloads.containers]]\nname = "app"\nis_primary = true\n'
    w.apps[0].save()
    deployment = _app_deployment(w)
    mark_model_observed(w)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda deployment: (
            w.driver,
            SimpleNamespace(slug=str(w.cluster.guid)),
            subscription_namespace(w.envs[0]),
        ),
    )
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, capability: w.secrets)
    assert _update_secrets_sync(deployment.pk) == 1
    rendered = next(resource for resource in w.driver.dry_runs[0] if resource["kind"] == "Deployment")
    assert rendered["spec"]["template"]["spec"]["containers"][0]["envFrom"] == [
        {"secretRef": {"name": subscription_secret_name(w.rows[0])}}
    ]
    manifests = [manifest for _, resources, _ in w.driver.writes for manifest in resources]
    assert len(manifests) == 1
    assert manifests[0]["metadata"]["name"] == subscription_secret_name(w.rows[0])
    assert manifests[0]["stringData"]["MODEL_CHAT_API_KEY"] == "1" * 64
    assert "keys.json" not in manifests[0]["stringData"]
    assert set(w.secrets.reads) == {w.rows[0].credential_ref.partition("#")[0]}
    w.rows[0].desired_enabled = False
    w.rows[0].save()
    w.driver.writes.clear()
    assert _update_secrets_sync(deployment.pk) == 0
    assert not w.driver.writes


@pytest.mark.parametrize("stale_field", ["uid", "resourceVersion"])
def test_sdk_hpa_handover_cannot_replace_caller_identity_before_any_write(stale_field, monkeypatch):
    pytest.importorskip("kubernetes")
    from _sdk.k8s_dynamic_client import KubernetesDynamicClient, PreconditionFailedError

    observed = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "api",
            "uid": "new-uid",
            "resourceVersion": "9",
            "generation": 1,
            "managedFields": [{"manager": "hpa-controller", "fieldsV1": {"f:spec": {"f:replicas": {}}}}],
        },
        "spec": {"replicas": 5},
    }
    writes = []

    def apply(**kwargs):
        writes.append(deepcopy(kwargs))
        return deepcopy(observed)

    resource = SimpleNamespace(
        namespaced=True, get=lambda **kwargs: deepcopy(observed), server_side_apply=apply
    )
    helper = KubernetesDynamicClient.from_api_client(api_client=SimpleNamespace())
    monkeypatch.setattr(helper, "_resource_for", lambda *args: resource)
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "api",
            "uid": "new-uid",
            "resourceVersion": "9",
            "annotations": {"astrolift.dev/replica-owner": "hpa"},
        },
        "spec": {"template": {"metadata": {"annotations": {"test-bind": "new"}}}},
    }
    manifest["metadata"][stale_field] = "old-observation"
    with pytest.raises((ValueError, PreconditionFailedError)):
        helper.server_side_apply(namespace="explicit-test", manifest=manifest, dry_run=False)
    assert not writes, "stale caller identity must be refused before replica handover or binding apply"


@pytest.mark.parametrize("kind", ["Secret", "ConfigMap"])
@pytest.mark.parametrize("prefix", ["", "MODEL_CHAT_"])
def test_existing_bundle_alias_collision_refuses_before_any_write(runtime_world, kind, prefix):
    w = runtime_world
    mark_model_observed(w)
    ns = subscription_namespace(w.envs[0])
    key = "MODEL_CHAT_API_KEY" if not prefix else "API_KEY"
    source = {
        "apiVersion": "v1",
        "kind": kind,
        "metadata": {"name": "existing-bundle"},
        "data": {key: "existing-source-value"},
    }
    w.driver.put(ns, source)
    before = deepcopy(w.driver.objects[(f"v1/{kind}", ns, "existing-bundle")])
    target = w.driver.objects[("apps/v1/Deployment", ns, "api")]
    target["spec"]["template"]["spec"]["containers"][0]["envFrom"] = [
        {"secretRef" if kind == "Secret" else "configMapRef": {"name": "existing-bundle"}, "prefix": prefix}
    ]
    with pytest.raises(ValueError, match="existing environment source"):
        apply_destination_binding(w.rows[0], w.driver, w.secrets)
    assert not w.driver.writes and not w.secrets.reads
    assert w.driver.objects[(f"v1/{kind}", ns, "existing-bundle")] == before
    assert ("v1/Secret", ns, subscription_secret_name(w.rows[0])) not in w.driver.objects


@pytest.mark.parametrize("kind", ["secretRef", "configMapRef"])
def test_missing_required_bundle_refuses_before_any_write(runtime_world, kind):
    w = runtime_world
    mark_model_observed(w)
    target = w.driver.objects[("apps/v1/Deployment", subscription_namespace(w.envs[0]), "api")]
    target["spec"]["template"]["spec"]["containers"][0]["envFrom"] = [{kind: {"name": "missing-source"}}]
    with pytest.raises(ValueError, match="environment source is unavailable"):
        apply_destination_binding(w.rows[0], w.driver, w.secrets)
    assert not w.driver.writes and not w.secrets.reads
