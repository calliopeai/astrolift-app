"""Self-hosted vLLM model endpoint (#2040)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.managed.model_endpoint_vllm import VLLMConfig, VLLMDriver

IMAGE = "vllm/vllm-openai@sha256:" + "a" * 64


class _Result:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def summary(self) -> list[str]:
        return [] if self.ok else ["rejected"]


class _Cluster:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.deleted: list[str] = []

    def get_manifest(self, _cluster, namespace, kind, name):
        value = self.objects.get((kind, namespace, name))
        return copy.deepcopy(value) if value else None

    def apply_manifests(self, _cluster, namespace, manifests):
        for m in manifests:
            self.objects[(f"{m['apiVersion']}/{m['kind']}", namespace, m["metadata"]["name"])] = copy.deepcopy(m)
        return _Result()

    def delete_manifests(self, _cluster, namespace, manifests, **_):
        self.deleted += [f"{m['kind']}/{m['metadata']['name']}" for m in manifests]
        return _Result()


class _Secrets:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = dict(data or {})

    def get(self, path):
        return self.data.get(path)

    def upsert(self, path, value):
        self.data[path] = value

    def delete(self, path):
        self.data.pop(path, None)


def _driver(**config) -> tuple[VLLMDriver, _Cluster, _Secrets]:
    cluster, secrets = _Cluster(), _Secrets(config.pop("secret_data", None))
    return (
        VLLMDriver(config=VLLMConfig(cluster_driver=cluster, secrets_backend=secrets, image=IMAGE, **config)),
        cluster,
        secrets,
    )


def _spec(**cfg) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="chat",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="c1",
        service_handle_hint="llm",
        size="custom",
        config={"model": "Qwen/Qwen3-8B", **cfg},
        managed_service_id="svc-1",
    )


def _deployment(cluster: _Cluster) -> dict[str, Any]:
    return next(v for (kind, _, _), v in cluster.objects.items() if kind == "apps/v1/Deployment")


def _container(cluster: _Cluster) -> dict[str, Any]:
    return _deployment(cluster)["spec"]["template"]["spec"]["containers"][0]


def _env(container) -> dict[str, Any]:
    return {e["name"]: e.get("value", e.get("valueFrom")) for e in container["env"]}


def test_provision_renders_an_openai_server_on_one_gpu_with_the_rust_frontend_by_default():
    driver, cluster, secrets = _driver()
    result = driver.provision(_spec())
    assert result.ok, result.message

    container = _container(cluster)
    assert container["args"][:2] == ["--model", "Qwen/Qwen3-8B"]
    assert "--tensor-parallel-size" in container["args"] and "--api-key" not in container["args"]
    assert container["resources"]["limits"] == {"nvidia.com/gpu": "1"}
    assert _env(container)["VLLM_USE_RUST_FRONTEND"] == "1"
    assert _env(container)["VLLM_API_KEY"] == {"secretKeyRef": {"name": "chat-prod-llm-vllm", "key": "api_key"}}
    pod = _deployment(cluster)["spec"]["template"]["spec"]
    assert {t["key"] for t in pod["tolerations"]} == {"nvidia.com/gpu", "astrolift.io/gpu"}
    assert _deployment(cluster)["metadata"]["annotations"]["astrolift.io/vllm-frontend-source"] == "install"
    assert any(path.endswith("/credentials") for path in secrets.data)


def test_tensor_parallel_mig_and_stopped_replicas():
    driver, cluster, _ = _driver()
    assert driver.provision(_spec(gpu=4, tensor_parallel_size=2)).ok
    args = _container(cluster)["args"]
    assert args[args.index("--tensor-parallel-size") + 1] == "2"

    driver, cluster, _ = _driver()
    assert driver.provision(_spec(gpu=1, mig_profile="3g.47gb", replicas=0)).ok
    assert _container(cluster)["resources"]["limits"] == {"nvidia.com/mig-3g.47gb": "1"}
    assert _deployment(cluster)["spec"]["replicas"] == 0


def test_a_cpu_model_has_no_gpu_request_or_tolerations():
    driver, cluster, _ = _driver()
    assert driver.provision(_spec(gpu=0)).ok
    assert "limits" not in _container(cluster)["resources"]
    assert "tolerations" not in _deployment(cluster)["spec"]["template"]["spec"]


@pytest.mark.parametrize(
    ("config", "cfg", "expected"),
    [
        ({}, {"frontend": "python"}, ("python", "service")),
        ({"model_defaults": {"Qwen/*": {"frontend": "python"}}}, {}, ("python", "model")),
        ({"cluster_frontend": "python"}, {}, ("python", "cluster")),
        ({"frontend_default": "python"}, {}, ("python", "install")),
        ({"cluster_frontend": "python", "model_defaults": {"Qwen/*": {"frontend": "rust"}}}, {}, ("rust", "model")),
    ],
)
def test_the_most_specific_frontend_layer_wins(config, cfg, expected):
    driver, cluster, _ = _driver(**config)
    assert driver.provision(_spec(**cfg)).ok
    annotations = _deployment(cluster)["metadata"]["annotations"]
    assert (annotations["astrolift.io/vllm-frontend"], annotations["astrolift.io/vllm-frontend-source"]) == expected
    assert ("VLLM_USE_RUST_FRONTEND" in _env(_container(cluster))) == (expected[0] == "rust")


def test_rust_refuses_what_it_cannot_serve_and_names_the_deciding_layer():
    driver, _, _ = _driver(cluster_frontend="rust")
    refused = driver.provision(_spec(task="embed"))
    assert not refused.ok and "cluster setting" in refused.message and 'frontend = "python"' in refused.message

    refused = driver.provision(_spec(tool_call_parser="hermes"))
    assert not refused.ok and "tool_call_parser" in refused.message

    driver, cluster, _ = _driver(cluster_frontend="rust")
    assert driver.provision(_spec(task="embed", frontend="python")).ok
    assert "--task" in _container(cluster)["args"]


def test_the_endpoint_is_private_to_the_app_namespace_and_the_binding_carries_the_key_as_a_secret():
    driver, cluster, _ = _driver()
    result = driver.provision(_spec())
    policy = next(v for (kind, _, _), v in cluster.objects.items() if kind.endswith("/NetworkPolicy"))
    assert policy["spec"]["ingress"] == [{"from": [{"podSelector": {}}], "ports": [{"port": 8000, "protocol": "TCP"}]}]

    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["MODEL_ENDPOINT_URL"].literal.endswith(":8000/v1")
    assert binding.env_vars["MODEL_API_KEY"].secret_ref.endswith("/credentials#api_key")
    assert binding.env_vars["MODEL_DEPLOYMENT_NAME"].literal == "Qwen/Qwen3-8B"


def test_hf_token_comes_from_the_platform_secret_store():
    driver, cluster, _ = _driver(secret_data={"services/o/a/hf": {"token": "hf_x"}})
    assert driver.provision(_spec(hf_token_secret_ref="services/o/a/hf#token")).ok
    secret = next(v for (kind, _, _), v in cluster.objects.items() if kind == "v1/Secret")
    assert secret["stringData"]["hf_token"] == "hf_x"
    assert _env(_container(cluster))["HF_TOKEN"]["secretKeyRef"]["key"] == "hf_token"

    driver, _, _ = _driver()
    assert not driver.provision(_spec(hf_token_secret_ref="services/o/a/missing#token")).ok


def test_another_services_deployment_is_not_adopted_and_the_key_is_stable():
    driver, _, secrets = _driver()
    assert driver.provision(_spec()).ok
    key = next(iter(secrets.data.values()))["api_key"]
    assert driver.provision(_spec()).ok
    assert next(iter(secrets.data.values()))["api_key"] == key

    other = ProvisionSpec(**{**_spec().__dict__, "managed_service_id": "svc-2"})
    refused = driver.provision(other)
    assert not refused.ok and "another managed service" in refused.message


def test_update_reapplies_and_deprovision_keeps_the_weights_unless_asked():
    driver, cluster, secrets = _driver()
    handle = driver.provision(_spec()).handle
    assert driver.update(UpdateSpec(handle, config={"model": "Qwen/Qwen3-8B", "replicas": 0})).ok
    assert _deployment(cluster)["spec"]["replicas"] == 0
    assert driver.status(ServiceHandle(handle)).state == "stopped"

    assert driver.deprovision(DeprovisionSpec(handle)).ok
    assert "PersistentVolumeClaim/chat-prod-llm-cache" not in cluster.deleted
    assert secrets.data == {}


@pytest.mark.parametrize(
    "cfg",
    [
        {"model": "../etc"},
        {"model": "ok/m", "gpu": 17},
        {"model": "ok/m", "gpu_memory_utilization": 0.99},
        {"model": "ok/m", "extra": 1},
    ],
)
def test_invalid_config_is_refused(cfg):
    driver, _, _ = _driver()
    spec = _spec()
    spec = ProvisionSpec(**{**spec.__dict__, "config": cfg})
    assert not driver.provision(spec).ok


def test_no_image_is_refused():
    cluster, secrets = _Cluster(), _Secrets()
    driver = VLLMDriver(config=VLLMConfig(cluster_driver=cluster, secrets_backend=secrets))
    assert "vllm_image" in driver.provision(_spec()).message


def test_metrics_add_a_service_monitor_and_admit_only_the_prometheus_namespace():
    driver, cluster, _ = _driver(
        metrics={"namespace": "monitoring", "labels": {"release": "kps", "app.kubernetes.io/managed-by": "x"}}
    )
    handle = driver.provision(_spec()).handle
    monitor = next(v for (kind, _, _), v in cluster.objects.items() if kind.endswith("/ServiceMonitor"))
    assert monitor["metadata"]["labels"]["release"] == "kps"
    # The platform's own labels win over configured ones.
    assert monitor["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "astrolift"
    relabel = monitor["spec"]["endpoints"][0]["relabelings"][0]
    assert relabel["targetLabel"] == "managed_service"
    policy = next(v for (kind, _, _), v in cluster.objects.items() if kind.endswith("/NetworkPolicy"))
    assert policy["spec"]["ingress"][1]["from"] == [
        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}}}
    ]
    assert driver.deprovision(DeprovisionSpec(handle)).ok
    assert "ServiceMonitor/chat-prod-llm" in cluster.deleted


def test_no_metrics_config_means_no_service_monitor_and_no_extra_ingress():
    driver, cluster, _ = _driver()
    driver.provision(_spec())
    assert not any(kind.endswith("/ServiceMonitor") for (kind, _, _) in cluster.objects)
    policy = next(v for (kind, _, _), v in cluster.objects.items() if kind.endswith("/NetworkPolicy"))
    assert len(policy["spec"]["ingress"]) == 1


# ---- keep-alive agent test-prompt relay (#2064) --------------------------


def test_agent_test_admits_only_the_configured_namespace_and_pod():
    driver, cluster, _ = _driver(agent_test={"namespace": "astrolift-system"})
    driver.provision(_spec())
    policy = next(v for (kind, _, _), v in cluster.objects.items() if kind.endswith("/NetworkPolicy"))
    assert policy["spec"]["ingress"][1]["from"] == [
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "astrolift-system"}},
            "podSelector": {"matchLabels": {"app": "astrolift-agent"}},
        }
    ]


def test_agent_test_pod_labels_are_configurable():
    driver, cluster, _ = _driver(agent_test={"namespace": "astrolift-system", "pod_labels": {"app": "custom-agent"}})
    driver.provision(_spec())
    policy = next(v for (kind, _, _), v in cluster.objects.items() if kind.endswith("/NetworkPolicy"))
    assert policy["spec"]["ingress"][1]["from"][0]["podSelector"] == {"matchLabels": {"app": "custom-agent"}}


def test_no_agent_test_config_means_no_extra_ingress():
    driver, cluster, _ = _driver()
    driver.provision(_spec())
    policy = next(v for (kind, _, _), v in cluster.objects.items() if kind.endswith("/NetworkPolicy"))
    assert len(policy["spec"]["ingress"]) == 1


def test_resolve_agent_test_target_matches_the_driver_naming():
    """Pure derivation the control plane uses for ``testModelEndpoint`` must
    agree byte-for-byte with what ``provision`` actually names -- otherwise
    the agent gets pointed at a Service/Secret that doesn't exist."""
    from k8s_native.managed.model_endpoint_vllm import resolve_agent_test_target

    driver, cluster, _ = _driver()
    driver.provision(_spec())
    deployment_name = next(name for (kind, _ns, name) in cluster.objects if kind == "apps/v1/Deployment")
    secret_name = next(name for (kind, _ns, name) in cluster.objects if kind == "v1/Secret")

    target = resolve_agent_test_target(
        organization_slug="acme",
        app_slug="chat",
        environment_name="prod",
        service_handle_hint="llm",
        model="Qwen/Qwen3-8B",
    )
    assert target.base_url == f"http://{deployment_name}.acme-chat.svc.cluster.local:8000/v1"
    assert target.model == "Qwen/Qwen3-8B"
    assert target.api_key_secret_namespace == "acme-chat"
    assert target.api_key_secret_name == secret_name
    assert target.api_key_secret_key == "api_key"
