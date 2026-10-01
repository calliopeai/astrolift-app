"""Driver manifests and observed credential rollout for actual shared placement."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from _sdk.managed_service import ClusterModelPlacement, ModelConsumer, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.managed.model_endpoint_vllm import VLLMConfig, VLLMDriver
from k8s_native.managed.shared_model_runtime import AUTH_REVISION, LAUNCHER, shared_runtime

IMAGE = "vllm/vllm-openai-cpu@sha256:" + "a" * 64


class Cluster:
    def __init__(self):
        self.objects = {}
        self.writes = []
        self.pods = []

    def get_manifest(self, cluster, namespace, kind, name):
        return copy.deepcopy(self.objects.get((kind, namespace, name)))

    def list_manifests(self, cluster, namespace, kind):
        assert kind == "v1/Pod"
        return copy.deepcopy(self.pods)

    def apply_manifests(self, cluster, namespace, manifests):
        self.writes.append((cluster, namespace, copy.deepcopy(manifests)))
        for manifest in manifests:
            object_namespace = None if manifest["kind"] == "Namespace" else namespace
            self.objects[
                (f"{manifest['apiVersion']}/{manifest['kind']}", object_namespace, manifest["metadata"]["name"])
            ] = copy.deepcopy(manifest)
        return SimpleNamespace(ok=True, summary=lambda: [])


class Secrets:
    def __init__(self):
        self.data = {}
        self.writes = []

    def get(self, path):
        return self.data.get(path)

    def upsert(self, path, value):
        self.writes.append(path)
        self.data[path] = value


def declaration(mode="cpu"):
    return {
        mode: {
            "image": IMAGE,
            "version": "0.15.1",
            "architecture": "amd64",
            "hardware_certified": True,
            "node_selector": {"astrolift.dev/vllm-compatible": mode},
        }
    }


def setup(mode="cpu"):
    cluster, secrets = Cluster(), Secrets()
    org, cid, sid = (str(uuid4()) for _ in range(3))
    consumers = []
    for environment in ("production", "staging"):
        sub = str(uuid4())
        ref = f"services/{org}/{sid}/subscriptions/{sub}#api_key"
        secrets.data[ref.partition("#")[0]] = {"api_key": uuid4().hex + uuid4().hex}
        consumers.append(ModelConsumer(sub, "acme-chat", "chat", environment, ref))
    placement = ClusterModelPlacement(org, cid, sid, 1, tuple(consumers))
    spec = ProvisionSpec(
        org,
        "acme",
        "",
        "",
        "",
        "",
        cid,
        "display",
        "custom",
        config={
            "model": "Qwen/Qwen3-0.6B",
            "model_revision": "b" * 40,
            "frontend": "python",
            "compute_mode": mode,
            "gpu": 0 if mode == "cpu" else 1,
            "cpu": "4",
            "memory": "16Gi",
            "cpu_kv_cache_gib": 4,
        },
        managed_service_id=sid,
        cluster_model=placement,
    )
    driver = VLLMDriver(
        config=VLLMConfig(
            cluster_driver=cluster,
            secrets_backend=secrets,
            shared_runtimes=declaration(mode),
            metrics={"namespace": "monitoring"},
        )
    )
    return driver, cluster, secrets, spec


def object_of(cluster, kind):
    return next(row for (object_kind, _, _), row in cluster.objects.items() if object_kind == kind)


@pytest.mark.parametrize("mode", ["cpu", "gpu"])
def test_shared_deployment_has_explicit_runtime_revision_and_independent_keys(mode):
    driver, cluster, secrets, spec = setup(mode)
    result = driver.provision(spec)
    assert result.ok, result.message
    deployment = object_of(cluster, "apps/v1/Deployment")
    namespace = deployment["metadata"]["namespace"]
    assert namespace.startswith("astrolift-model-") and namespace != "acme-chat"
    assert deployment["metadata"]["name"] == f"vllm-{spec.managed_service_id}"
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {row["name"]: row.get("value") for row in container["env"]}
    assert container["image"] == IMAGE and container["command"] == ["python3", "/opt/astrolift/shared-model/launch.py"]
    assert "--api-key" not in container["args"]
    assert container["args"][container["args"].index("--runner") + 1] == "generate"
    assert container["args"][container["args"].index("--convert") + 1] == "none"
    assert container["args"][container["args"].index("--revision") + 1] == "b" * 40
    assert env["ASTROLIFT_MODEL_AUTH_REVISION"] == "1" and "VLLM_API_KEY" not in env
    assert pod["nodeSelector"] == {"astrolift.dev/vllm-compatible": mode, "kubernetes.io/arch": "amd64"}
    if mode == "cpu":
        assert env["VLLM_CPU_KVCACHE_SPACE"] == "4"
        assert "--gpu-memory-utilization" not in container["args"]
        assert "limits" not in container["resources"] and "tolerations" not in pod
    else:
        assert container["resources"]["limits"] == {"nvidia.com/gpu": "1"}
    secret = object_of(cluster, "v1/Secret")
    snapshot = json.loads(secret["stringData"]["keys.json"])
    assert snapshot["operator_key"] == secret["stringData"]["api_key"]
    assert snapshot["subscription_keys"] == [
        secrets.data[c.credential_ref.partition("#")[0]]["api_key"] for c in spec.cluster_model.consumers
    ]
    configmap = object_of(cluster, "v1/ConfigMap")
    assert configmap["data"]["launch.py"] == LAUNCHER
    assert "class SharedModelAuth" in configmap["data"]["astrolift_shared_model_auth.py"]
    rendered_pod = json.dumps(pod)
    assert all(key not in rendered_pod for key in [snapshot["operator_key"], *snapshot["subscription_keys"]])
    monitor = object_of(cluster, "monitoring.coreos.com/v1/ServiceMonitor")
    assert monitor["spec"]["endpoints"][0]["authorization"] == {
        "type": "Bearer",
        "credentials": {"name": secret["metadata"]["name"], "key": "api_key"},
    }


def test_network_admission_is_namespace_and_exact_app_environment_not_same_namespace_all_pods():
    driver, cluster, _, spec = setup()
    assert driver.provision(spec).ok
    ingress = object_of(cluster, "networking.k8s.io/v1/NetworkPolicy")["spec"]["ingress"]
    subscriber_rules = [rule for rule in ingress if rule["from"][0].get("podSelector")]
    assert len(subscriber_rules) == 2
    assert {
        rule["from"][0]["podSelector"]["matchLabels"]["astrolift.dev/environment"] for rule in subscriber_rules
    } == {"production", "staging"}
    for rule in subscriber_rules:
        assert rule["from"][0]["namespaceSelector"] == {"matchLabels": {"kubernetes.io/metadata.name": "acme-chat"}}
        assert rule["from"][0]["podSelector"]["matchLabels"]["astrolift.dev/app"] == "chat"


@pytest.mark.parametrize(
    "failure",
    [
        "missing_runtime",
        "unknown_version",
        "rust",
        "not_certified",
        "only_arch",
        "unpinned",
        "wrong_revision",
        "missing_memory",
        "cpu_gpu",
        "missing_kv",
    ],
)
def test_unsupported_shared_runtime_refuses_before_any_secret_or_cluster_write(failure):
    _, cluster, secrets, spec = setup()
    runtimes = declaration()
    cfg = dict(spec.config)
    if failure == "missing_runtime":
        runtimes = {}
    elif failure == "unknown_version":
        runtimes["cpu"]["version"] = "0.99.0"
    elif failure == "rust":
        cfg["frontend"] = "rust"
    elif failure == "not_certified":
        runtimes["cpu"]["hardware_certified"] = False
    elif failure == "only_arch":
        runtimes["cpu"]["node_selector"] = {"kubernetes.io/arch": "amd64"}
    elif failure == "unpinned":
        runtimes["cpu"]["image"] = "vllm/vllm-openai:latest"
    elif failure == "wrong_revision":
        cfg["model_revision"] = "main"
    elif failure == "missing_memory":
        del cfg["memory"]
    elif failure == "cpu_gpu":
        cfg["gpu"] = 1
    else:
        del cfg["cpu_kv_cache_gib"]
    driver = VLLMDriver(config=VLLMConfig(cluster_driver=cluster, secrets_backend=secrets, shared_runtimes=runtimes))
    result = driver.provision(replace(spec, config=cfg))
    assert not result.ok and not cluster.writes and not secrets.writes


@pytest.mark.parametrize(
    "failure",
    ["foreign_org", "fake_app", "foreign_ref", "foreign_namespace", "duplicate_consumer", "replaced_namespace"],
)
def test_incoherent_shared_placement_refuses_before_secret_or_cluster_write(failure):
    driver, cluster, secrets, spec = setup()
    placement = spec.cluster_model
    consumer = placement.consumers[0]
    if failure == "foreign_org":
        placement = replace(placement, organization_id=str(uuid4()))
    elif failure == "fake_app":
        spec = replace(spec, app_slug="placeholder")
    elif failure == "foreign_ref":
        consumer = replace(consumer, credential_ref="services/foreign/key#api_key")
    elif failure == "foreign_namespace":
        consumer = replace(consumer, namespace="other-app")
    elif failure == "duplicate_consumer":
        placement = replace(placement, consumers=(consumer, consumer))
    else:
        namespace = driver._namespace(spec)
        cluster.objects[("v1/Namespace", None, namespace)] = {
            "metadata": {"labels": {"app.kubernetes.io/managed-by": "other"}}
        }
    if failure in ("foreign_ref", "foreign_namespace"):
        placement = replace(placement, consumers=(consumer,))
    result = driver.provision(replace(spec, cluster_model=placement))
    assert not result.ok and not cluster.writes and not secrets.writes


def ready_observations(cluster, *, revision="1", generation=3):
    deployment = object_of(cluster, "apps/v1/Deployment")
    deployment["metadata"]["generation"] = generation
    deployment["status"] = {"observedGeneration": generation, "replicas": 1, "updatedReplicas": 1, "readyReplicas": 1}
    cluster.pods = [
        {
            "metadata": {"labels": deployment["metadata"]["labels"], "annotations": {AUTH_REVISION: revision}},
            "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
        }
    ]
    return deployment


@pytest.mark.parametrize(
    "observation", ["ready", "stale_generation", "old_key_pod", "old_terminating_pod", "missing_pod", "not_ready"]
)
def test_shared_readiness_never_accepts_stale_auth_generation_or_old_frontend(observation):
    driver, cluster, _, spec = setup()
    result = driver.provision(spec)
    deployment = ready_observations(cluster)
    if observation == "stale_generation":
        deployment["status"]["observedGeneration"] = 2
    elif observation == "old_key_pod":
        cluster.pods[0]["metadata"]["annotations"][AUTH_REVISION] = "0"
    elif observation == "old_terminating_pod":
        cluster.pods.append(copy.deepcopy(cluster.pods[0]))
        cluster.pods[-1]["metadata"]["deletionTimestamp"] = "now"
    elif observation == "missing_pod":
        cluster.pods = []
    elif observation == "not_ready":
        cluster.pods[0]["status"]["conditions"][0]["status"] = "False"
    status = driver.status(ServiceHandle(result.handle))
    assert (status.state == "available") == (observation == "ready")


def test_revoke_updates_key_snapshot_only_one_key_and_keeps_other_key_and_operator():
    driver, cluster, _, spec = setup()
    result = driver.provision(spec)
    old = json.loads(object_of(cluster, "v1/Secret")["stringData"]["keys.json"])
    next_placement = replace(spec.cluster_model, revision=2, consumers=spec.cluster_model.consumers[1:])
    updated = driver.update(
        UpdateSpec(
            result.handle, config=spec.config, managed_service_id=spec.managed_service_id, cluster_model=next_placement
        )
    )
    assert updated.ok, updated.message
    new = json.loads(object_of(cluster, "v1/Secret")["stringData"]["keys.json"])
    assert new["revision"] == 2 and new["operator_key"] == old["operator_key"]
    assert new["subscription_keys"] == old["subscription_keys"][1:]
    assert object_of(cluster, "apps/v1/Deployment")["spec"]["template"]["metadata"]["annotations"][AUTH_REVISION] == "2"
    assert driver.status(ServiceHandle(result.handle)).state == "provisioning"
    ready_observations(cluster, revision="2")
    assert driver.status(ServiceHandle(result.handle)).state == "available"


def test_launcher_secret_lists_redact_upstream_style_argument_logging_without_changing_keys(monkeypatch):
    from k8s_native.managed import shared_model_auth

    monkeypatch.setitem(sys.modules, "astrolift_shared_model_auth", shared_model_auth)
    namespace = {"__name__": "launcher_test"}
    exec(LAUNCHER, namespace)
    keys = namespace["SecretKeys"](
        [namespace["SecretValue"]("operator-sensitive"), namespace["SecretValue"]("subscriber-sensitive")]
    )
    logged = "non-default args: %s" % {"api_key": keys}  # noqa: UP031 - pinned upstream startup log format
    assert "operator-sensitive" not in logged and "subscriber-sensitive" not in logged
    assert list(keys) == ["operator-sensitive", "subscriber-sensitive"]


def test_architecture_only_is_not_runtime_compatibility_proof():
    _, _, _, spec = setup()
    config = declaration()
    config["cpu"]["node_selector"] = {"kubernetes.io/arch": "amd64"}
    with pytest.raises(ValueError, match="Architecture alone"):
        shared_runtime(config, spec.config, "python")


@pytest.mark.parametrize(
    "cpu,memory",
    [
        ("0", "16Gi"),
        ("0m", "16Gi"),
        ("4", "0"),
        ("257", "16Gi"),
        ("4", "2Ti"),
        ("1Gi", "16Gi"),
        ("4", "4Gi"),
        ("NaN", "16Gi"),
    ],
)
def test_shared_resource_admission_refuses_zero_unbounded_or_cache_only_memory(cpu, memory):
    _, cluster, secrets, spec = setup()
    spec = replace(spec, config={**spec.config, "cpu": cpu, "memory": memory})
    driver = VLLMDriver(
        config=VLLMConfig(cluster_driver=cluster, secrets_backend=secrets, shared_runtimes=declaration())
    )
    assert not driver.provision(spec).ok
    assert not secrets.writes and not cluster.writes


@pytest.mark.parametrize("key", ["https://unsafe/label", "Upper.domain/label", "a..b/label", "/label", "a/b/label"])
def test_shared_selector_prefix_is_validated_before_credentials(key):
    _, cluster, secrets, spec = setup()
    runtime = declaration()
    runtime["cpu"]["node_selector"] = {key: "compatible"}
    driver = VLLMDriver(config=VLLMConfig(cluster_driver=cluster, secrets_backend=secrets, shared_runtimes=runtime))
    assert not driver.provision(spec).ok
    assert not secrets.writes and not cluster.writes


@pytest.mark.parametrize(
    "mode,expected,actual,admitted",
    [
        ("cpu", "0.15.1+cpu", "0.15.1+cpu", True),
        ("gpu", "0.15.1", "0.15.1", True),
        ("gpu", "0.15.1+cu128", "0.15.1+cu128", True),
        ("cpu", "0.15.1+cpu", "0.15.1", False),
        ("gpu", "0.15.1", "0.15.1+cpu", False),
        ("cpu", "0.15.1+cpu", "0.15.1.dev1+cpu", False),
        ("gpu", "0.15.1+cu128", "0.15.1+cu129", False),
        ("cpu", "0.16.0+cpu", "0.16.0+cpu", False),
    ],
)
def test_launcher_mode_bound_actual_distribution_version_admission(monkeypatch, mode, expected, actual, admitted):
    import importlib.metadata

    from k8s_native.managed import shared_model_auth

    monkeypatch.setitem(sys.modules, "astrolift_shared_model_auth", shared_model_auth)
    monkeypatch.setenv("ASTROLIFT_MODEL_COMPUTE_MODE", mode)
    monkeypatch.setenv("ASTROLIFT_MODEL_RUNTIME_PACKAGE_VERSION", expected)
    monkeypatch.setenv("ASTROLIFT_MODEL_AUTH_REVISION", "1")
    monkeypatch.setattr(importlib.metadata, "version", lambda package: actual)

    def snapshot(**kwargs):
        raise RuntimeError("snapshot reached")

    monkeypatch.setattr(shared_model_auth, "load_key_snapshot", snapshot)
    namespace = {"__name__": "launcher_test"}
    exec(LAUNCHER, namespace)
    with pytest.raises(RuntimeError, match="snapshot reached" if admitted else "Unsupported"):
        namespace["main"]()


def test_replaced_model_owner_cannot_claim_ready():
    driver, cluster, _secrets, spec = setup()
    result = driver.provision(spec)
    deployment = object_of(cluster, "apps/v1/Deployment")
    deployment["metadata"]["labels"]["astrolift.io/managed-service-id"] = str(uuid4())
    state = driver.status(ServiceHandle(result.handle, managed_service_id=spec.managed_service_id))
    assert state.state == "error" and "another managed service" in state.message
