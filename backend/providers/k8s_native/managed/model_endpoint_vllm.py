"""Self-hosted model serving with vLLM (#2040, epic #2037).

One OpenAI-compatible vLLM server per service, the same on EKS, AKS, GKE and
on-prem: a Deployment on GPU nodes (or CPU for small models), a weight-cache
PVC so restarts and "stop" keep the weights, a platform-generated API key, and
a NetworkPolicy that admits only the owning app's namespace.

vLLM has two HTTP frontends in front of the same engine: the Python API
server and the Rust one (``VLLM_USE_RUST_FRONTEND=1``). Operators pick per
service, per model, per cluster or install-wide; the most specific wins, and
the layer that decided is recorded on the Deployment. The Rust frontend does
not yet serve everything the Python one does
(https://github.com/vllm-project/vllm/issues/44280), so a service that needs
an API only Python serves is refused on Rust, never silently switched.
"""

from __future__ import annotations

import fnmatch
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op
from _sdk.k8s_naming import app_namespace, dns_label
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle

KIND = "model_endpoint"
VARIANT = "vllm"
PORT = 8000
_OWNER = "app.kubernetes.io/managed-by"
_OWNER_ID = "astrolift.io/managed-service-id"
_FRONTEND = "astrolift.io/vllm-frontend"
_FRONTEND_SOURCE = "astrolift.io/vllm-frontend-source"
FRONTENDS = ("rust", "python")
TASKS = ("generate", "embed", "score", "rerank")

# What the Rust frontend serves today, per vLLM's parity roadmap
# (https://github.com/vllm-project/vllm/issues/44280). Update as upstream
# closes items. Only text generation (chat and completions) is served; the
# pooling APIs (embeddings, score, rerank) are Python-only for now.
RUST_TASKS = frozenset({"generate"})
# Tool-call and reasoning parsers confirmed in vLLM's rust/ tree. A parser not
# listed here needs frontend = "python".
RUST_TOOL_PARSERS = frozenset({"qwen3_xml"})
RUST_REASONING_PARSERS: frozenset[str] = frozenset()

_GPU_POOL_TAINT_KEYS = ("nvidia.com/gpu", "astrolift.io/gpu")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/]{0,199}")
_PARSER_RE = re.compile(r"[a-z0-9_]{1,40}")
_MIG_RE = re.compile(r"[1-7]g\.[0-9]+gb")
_QUANTITY_RE = re.compile(r"[0-9]+(\.[0-9]+)?(m|Ki|Mi|Gi|Ti)?")
_DTYPES = ("auto", "float16", "bfloat16", "float32")

_CONFIG_FIELDS = frozenset(
    {
        "model",
        "task",
        "frontend",
        "gpu",
        "gpu_type",
        "mig_profile",
        "tensor_parallel_size",
        "max_model_len",
        "gpu_memory_utilization",
        "dtype",
        "max_num_seqs",
        "enable_prefix_caching",
        "tool_call_parser",
        "reasoning_parser",
        "replicas",
        "cpu",
        "memory",
        "ephemeral_storage",
        "cache_size",
        "hf_token_secret_ref",
    }
)


@dataclass(frozen=True)
class VLLMConfig:
    cluster_driver: Any = None
    secrets_backend: Any = None
    namespace: str | None = None
    image: str = ""
    """Pinned vLLM image (by digest or version tag), e.g. ``vllm/vllm-openai@sha256:...``
    or the AWS Deep Learning Container on EKS. Required: there is no safe default."""
    storage_class: str = ""
    credential_path_prefix: str = "managed/vllm"
    frontend_default: str = "rust"
    """Install-wide default (constance ``VLLM_FRONTEND_DEFAULT``)."""
    cluster_frontend: str = ""
    """This cluster's choice (``provider_config.vllm_frontend``); empty to inherit."""
    model_defaults: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Per-model defaults keyed by model id or glob, e.g. ``{"Qwen/*": {"frontend": "rust"}}``."""


class VLLMDriver(ManagedServiceDriver):
    """Own one vLLM OpenAI-compatible server (Deployment, Service, cache PVC, Secret, NetworkPolicy)."""

    def __init__(self, *, config: VLLMConfig) -> None:
        self._config = config

    # ---- lifecycle ------------------------------------------------------

    @driver_op(cloud="k8s_native", driver="model_endpoint_vllm", audit=True, sensitive_kind="managed_service_provision")
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._require()
            if not spec.managed_service_id:
                raise ValueError("vLLM requires a managed_service_id for safe ownership")
            namespace = self._namespace(spec)
            name = dns_label(spec.app_slug, spec.environment_name, spec.service_handle_hint or "model")
            cfg = self._normalize(spec.config)
            self._assert_adoptable(spec.tenant_cluster_id, namespace, name, spec.managed_service_id)
            manifests = self._manifests(spec=spec, namespace=namespace, name=name, cfg=cfg)
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_vllm_config"])
        handle = _pack_handle(kind=KIND, cluster_id=spec.tenant_cluster_id, namespace=namespace, name=name)
        result = self._config.cluster_driver.apply_manifests(spec.tenant_cluster_id, namespace, manifests)
        if not result.ok:
            return ProvisionResult(False, handle, "vLLM manifests were rejected", result.summary())
        return ProvisionResult(True, handle, f"vLLM server {namespace}/{name} submitted", ready=False)

    @driver_op(cloud="k8s_native", driver="model_endpoint_vllm")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._require()
            parsed = _unpack_handle(spec.handle)
            deployment = self._deployment(parsed.cluster_id, parsed.namespace, parsed.name)
            if deployment is None:
                raise ValueError("vLLM Deployment does not exist")
            owner_id = self._owner_id(deployment)
            cfg = self._normalize(spec.config)
            owner = ProvisionSpec(
                organization_id="",
                organization_slug=self._label(deployment, "astrolift.io/organization"),
                app_id="",
                app_slug=self._label(deployment, "astrolift.io/app"),
                environment_id="",
                environment_name="",
                tenant_cluster_id=parsed.cluster_id,
                service_handle_hint="",
                size=spec.size or "custom",
                config=cfg,
                managed_service_id=owner_id,
            )
            manifests = self._manifests(spec=owner, namespace=parsed.namespace, name=parsed.name, cfg=cfg)
        except (TypeError, ValueError) as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_vllm_config"])
        result = self._config.cluster_driver.apply_manifests(parsed.cluster_id, parsed.namespace, manifests)
        if not result.ok:
            return UpdateResult(False, spec.handle, "vLLM update was rejected", result.summary())
        return UpdateResult(True, spec.handle, "vLLM update submitted")

    @driver_op(
        cloud="k8s_native", driver="model_endpoint_vllm", audit=True, sensitive_kind="managed_service_deprovision"
    )
    def deprovision(
        self, spec: DeprovisionSpec, *, delete_data: bool = False, force_destroy: bool = False
    ) -> DeprovisionResult:
        del force_destroy
        self._require()
        parsed = _unpack_handle(spec.handle)
        deployment = self._deployment(parsed.cluster_id, parsed.namespace, parsed.name)
        if deployment is not None:
            try:
                self._owner_id(deployment)
            except ValueError as exc:
                return DeprovisionResult(False, spec.handle, str(exc), ["ownership_mismatch"], retryable=False)
        doomed = [
            self._stub("apps/v1", "Deployment", parsed.namespace, parsed.name),
            self._stub("v1", "Service", parsed.namespace, parsed.name),
            self._stub("networking.k8s.io/v1", "NetworkPolicy", parsed.namespace, parsed.name),
            self._stub("v1", "Secret", parsed.namespace, self._secret_name(parsed.name)),
        ]
        if delete_data:
            doomed.append(self._stub("v1", "PersistentVolumeClaim", parsed.namespace, self._cache_name(parsed.name)))
        result = self._config.cluster_driver.delete_manifests(parsed.cluster_id, parsed.namespace, doomed)
        if not result.ok:
            return DeprovisionResult(False, spec.handle, "vLLM deletion failed", result.summary())
        delete_secret = getattr(self._config.secrets_backend, "delete", None)
        if callable(delete_secret):
            delete_secret(self._credential_path(parsed.cluster_id, parsed.namespace, parsed.name))
        kept = "" if delete_data else "; the weight cache was kept"
        return DeprovisionResult(True, spec.handle, f"vLLM server {parsed.name} deleted{kept}")

    @driver_op(cloud="k8s_native", driver="model_endpoint_vllm")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        self._require()
        parsed = _unpack_handle(handle.handle)
        deployment = self._deployment(parsed.cluster_id, parsed.namespace, parsed.name)
        if deployment is None:
            return ServiceStatus(handle.handle, "deprovisioned", "vLLM Deployment not found")
        try:
            self._owner_id(deployment)
        except ValueError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        annotations = (deployment.get("metadata") or {}).get("annotations") or {}
        frontend = f"{annotations.get(_FRONTEND, '?')} frontend, set by {annotations.get(_FRONTEND_SOURCE, '?')}"
        desired = int((deployment.get("spec") or {}).get("replicas") or 0)
        ready = int((deployment.get("status") or {}).get("readyReplicas") or 0)
        if desired == 0:
            return ServiceStatus(handle.handle, "stopped", f"vLLM stopped; weights kept ({frontend})")
        if ready >= 1:
            return ServiceStatus(handle.handle, "available", f"vLLM ready, {ready}/{desired} replicas ({frontend})")
        return ServiceStatus(
            handle.handle,
            "provisioning",
            f"vLLM starting: downloading weights or loading the engine, which can take many minutes ({frontend})",
        )

    @driver_op(cloud="k8s_native", driver="model_endpoint_vllm")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        self._require()
        parsed = _unpack_handle(handle.handle)
        deployment = self._deployment(parsed.cluster_id, parsed.namespace, parsed.name)
        if deployment is None:
            raise ValueError("vLLM Deployment does not exist")
        self._owner_id(deployment)
        model = self._annotation(deployment, "astrolift.io/vllm-model")
        path = self._credential_path(parsed.cluster_id, parsed.namespace, parsed.name)
        return Binding(
            env_vars={
                "MODEL_ENDPOINT_URL": ValueRef(
                    literal=f"http://{parsed.name}.{parsed.namespace}.svc.cluster.local:{PORT}/v1"
                ),
                "MODEL_API_KEY": ValueRef(secret_ref=f"{path}#api_key"),
                "MODEL_DEPLOYMENT_NAME": ValueRef(literal=model),
                "MODEL_REGION": ValueRef(literal="kubernetes"),
                "MODEL_API_STYLE": ValueRef(literal="openai"),
                "MODEL_AUTH_MODE": ValueRef(literal="api_key"),
            },
            notes="OpenAI-compatible vLLM endpoint; reachable only from the owning app's namespace.",
        )

    @driver_op(cloud="k8s_native", driver="model_endpoint_vllm")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise UnsupportedOperationError("managed_service.snapshot(vLLM) is unsupported; weights are re-downloadable")

    @driver_op(cloud="k8s_native", driver="model_endpoint_vllm")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise UnsupportedOperationError("managed_service.restore(vLLM) is unsupported; provision a new endpoint")

    @driver_op(cloud="k8s_native", driver="model_endpoint_vllm", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        quantity = {"type": "string", "pattern": _QUANTITY_RE.pattern}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["model"],
            "properties": {
                "model": {"type": "string", "description": "Hugging Face model id, e.g. Qwen/Qwen3-8B"},
                "task": {"type": "string", "enum": list(TASKS), "default": "generate"},
                "frontend": {"type": "string", "enum": list(FRONTENDS)},
                "gpu": {"type": "integer", "minimum": 0, "maximum": 16, "default": 1},
                "gpu_type": {"type": "string"},
                "mig_profile": {"type": "string", "pattern": _MIG_RE.pattern},
                "tensor_parallel_size": {"type": "integer", "minimum": 1, "maximum": 16},
                "max_model_len": {"type": "integer", "minimum": 256},
                "gpu_memory_utilization": {"type": "number", "minimum": 0.1, "maximum": 0.95, "default": 0.85},
                "dtype": {"type": "string", "enum": list(_DTYPES), "default": "auto"},
                "max_num_seqs": {"type": "integer", "minimum": 1, "maximum": 4096},
                "enable_prefix_caching": {"type": "boolean", "default": True},
                "tool_call_parser": {"type": "string"},
                "reasoning_parser": {"type": "string"},
                "replicas": {"type": "integer", "minimum": 0, "maximum": 32, "default": 1},
                "cpu": quantity,
                "memory": quantity,
                "ephemeral_storage": quantity,
                "cache_size": {**quantity, "default": "50Gi"},
                "hf_token_secret_ref": {"type": "string", "description": "Platform secret ref to a Hugging Face token"},
            },
        }

    @driver_op(cloud="k8s_native", driver="model_endpoint_vllm", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MODEL_ENDPOINT_URL": "OpenAI-compatible base URL (in-cluster)",
                "MODEL_API_KEY": "API key the server requires (secret)",
                "MODEL_DEPLOYMENT_NAME": "Served model id",
                "MODEL_API_STYLE": "Client protocol: 'openai'",
                "MODEL_AUTH_MODE": "Credential kind: 'api_key'",
                "MODEL_REGION": "Always kubernetes",
            },
        )

    def editable_fields(self) -> list[str]:
        return sorted(_CONFIG_FIELDS - {"model"})

    # ---- config ---------------------------------------------------------

    def _normalize(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        cfg = dict(raw or {})
        unknown = set(cfg) - _CONFIG_FIELDS
        if unknown:
            raise ValueError("unsupported vLLM config fields: " + ", ".join(sorted(unknown)))
        model = cfg.get("model")
        if not isinstance(model, str) or not _MODEL_RE.fullmatch(model) or ".." in model:
            raise ValueError("model must be a Hugging Face model id such as 'Qwen/Qwen3-8B'")
        task = cfg.setdefault("task", "generate")
        if task not in TASKS:
            raise ValueError(f"task must be one of {list(TASKS)}")
        gpu = cfg.setdefault("gpu", 1)
        if isinstance(gpu, bool) or not isinstance(gpu, int) or not 0 <= gpu <= 16:
            raise ValueError("gpu must be an int from 0 to 16")
        if cfg.get("mig_profile") is not None and not _MIG_RE.fullmatch(str(cfg["mig_profile"])):
            raise ValueError("mig_profile must look like '3g.47gb'")
        tp = cfg.get("tensor_parallel_size")
        if tp is not None and (not isinstance(tp, int) or isinstance(tp, bool) or tp < 1 or (gpu and tp > gpu)):
            raise ValueError("tensor_parallel_size must be 1..gpu")
        util = cfg.setdefault("gpu_memory_utilization", 0.85)
        if isinstance(util, bool) or not isinstance(util, (int, float)) or not 0.1 <= float(util) <= 0.95:
            raise ValueError("gpu_memory_utilization must be between 0.1 and 0.95")
        if cfg.setdefault("dtype", "auto") not in _DTYPES:
            raise ValueError(f"dtype must be one of {list(_DTYPES)}")
        for key in ("tool_call_parser", "reasoning_parser"):
            if cfg.get(key) is not None and not _PARSER_RE.fullmatch(str(cfg[key])):
                raise ValueError(f"{key} must be a vLLM parser name")
        replicas = cfg.setdefault("replicas", 1)
        if isinstance(replicas, bool) or not isinstance(replicas, int) or not 0 <= replicas <= 32:
            raise ValueError("replicas must be an int from 0 to 32")
        for key in ("cpu", "memory", "ephemeral_storage", "cache_size"):
            if cfg.get(key) is not None and not _QUANTITY_RE.fullmatch(str(cfg[key])):
                raise ValueError(f"{key} must be a Kubernetes quantity")
        cfg.setdefault("cache_size", "50Gi")
        if cfg.get("frontend") is not None and cfg["frontend"] not in FRONTENDS:
            raise ValueError(f"frontend must be one of {list(FRONTENDS)}")
        return cfg

    def resolve_frontend(self, cfg: dict[str, Any]) -> tuple[str, str]:
        """``(frontend, layer that decided)``: service, model, cluster, then install."""
        if cfg.get("frontend"):
            return str(cfg["frontend"]), "service"
        for pattern, defaults in sorted((self._config.model_defaults or {}).items()):
            if fnmatch.fnmatch(str(cfg["model"]), pattern) and (defaults or {}).get("frontend") in FRONTENDS:
                return str(defaults["frontend"]), "model"
        if self._config.cluster_frontend in FRONTENDS:
            return self._config.cluster_frontend, "cluster"
        return (self._config.frontend_default if self._config.frontend_default in FRONTENDS else "python"), "install"

    def _frontend_refusal(self, cfg: dict[str, Any], frontend: str, source: str) -> str:
        if frontend != "rust":
            return ""
        where = {"service": "this service", "model": "the model default", "cluster": "the cluster setting"}.get(
            source, "the install default (VLLM_FRONTEND_DEFAULT)"
        )
        problems = []
        if cfg["task"] not in RUST_TASKS:
            problems.append(f"task {cfg['task']!r}")
        if cfg.get("tool_call_parser") and cfg["tool_call_parser"] not in RUST_TOOL_PARSERS:
            problems.append(f"tool_call_parser {cfg['tool_call_parser']!r}")
        if cfg.get("reasoning_parser") and cfg["reasoning_parser"] not in RUST_REASONING_PARSERS:
            problems.append(f"reasoning_parser {cfg['reasoning_parser']!r}")
        if not problems:
            return ""
        return (
            f"the vLLM Rust frontend (chosen by {where}) does not serve {', '.join(problems)} yet; "
            'set frontend = "python" for this service (see vllm-project/vllm#44280)'
        )

    # ---- rendering ------------------------------------------------------

    def _manifests(self, *, spec: ProvisionSpec, namespace: str, name: str, cfg: dict[str, Any]) -> list[dict]:
        if not self._config.image:
            raise ValueError("vLLM needs a pinned image: set vllm_image in the cluster's provider config")
        frontend, source = self.resolve_frontend(cfg)
        refusal = self._frontend_refusal(cfg, frontend, source)
        if refusal:
            raise ValueError(refusal)
        labels = {
            _OWNER: "astrolift",
            _OWNER_ID: dns_label(spec.managed_service_id or "", max_length=63),
            "astrolift.io/organization": spec.organization_slug,
            "astrolift.io/app": spec.app_slug,
            "app.kubernetes.io/name": "vllm",
            "app.kubernetes.io/instance": name,
        }
        api_key = self._api_key(spec.tenant_cluster_id, namespace, name)
        secret_data = {"api_key": api_key}
        hf_ref = cfg.get("hf_token_secret_ref")
        if hf_ref:
            token = self._read_secret_ref(str(hf_ref))
            if not token:
                raise ValueError("hf_token_secret_ref does not resolve to a value in the secrets backend")
            secret_data["hf_token"] = token
        manifests: list[dict[str, Any]] = [
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": self._secret_name(name), "namespace": namespace, "labels": labels},
                "type": "Opaque",
                "stringData": secret_data,
            },
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": self._cache_name(name), "namespace": namespace, "labels": labels},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": str(cfg["cache_size"])}},
                    **({"storageClassName": self._config.storage_class} if self._config.storage_class else {}),
                },
            },
            self._deployment_manifest(
                namespace, name, labels, cfg, frontend, source, has_hf_token="hf_token" in secret_data
            ),
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": name, "namespace": namespace, "labels": labels},
                "spec": {
                    "type": "ClusterIP",
                    "selector": {"app.kubernetes.io/instance": name},
                    "ports": [{"name": "http", "port": PORT, "targetPort": PORT}],
                },
            },
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": name, "namespace": namespace, "labels": labels},
                "spec": {
                    "podSelector": {"matchLabels": {"app.kubernetes.io/instance": name}},
                    "policyTypes": ["Ingress"],
                    # Only pods in this (the owning app's) namespace reach the server.
                    "ingress": [{"from": [{"podSelector": {}}], "ports": [{"port": PORT, "protocol": "TCP"}]}],
                },
            },
        ]
        return manifests

    def _deployment_manifest(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        cfg: dict[str, Any],
        frontend: str,
        source: str,
        *,
        has_hf_token: bool,
    ) -> dict[str, Any]:
        gpu = int(cfg["gpu"])
        args = [
            "--model",
            str(cfg["model"]),
            "--host",
            "0.0.0.0",
            "--port",
            str(PORT),
            "--gpu-memory-utilization",
            str(cfg["gpu_memory_utilization"]),
            "--dtype",
            str(cfg["dtype"]),
        ]
        if gpu and not cfg.get("mig_profile"):
            args += ["--tensor-parallel-size", str(cfg.get("tensor_parallel_size") or gpu)]
        if cfg.get("max_model_len"):
            args += ["--max-model-len", str(cfg["max_model_len"])]
        if cfg.get("max_num_seqs"):
            args += ["--max-num-seqs", str(cfg["max_num_seqs"])]
        if cfg.get("enable_prefix_caching", True):
            args.append("--enable-prefix-caching")
        if cfg["task"] != "generate":
            args += ["--task", "embed" if cfg["task"] == "embed" else "score"]
        for key, flag in (("tool_call_parser", "--tool-call-parser"), ("reasoning_parser", "--reasoning-parser")):
            if cfg.get(key):
                args += [flag, str(cfg[key])]
        if cfg.get("tool_call_parser"):
            args.append("--enable-auto-tool-choice")

        secret = self._secret_name(name)
        env = [{"name": "VLLM_API_KEY", "valueFrom": {"secretKeyRef": {"name": secret, "key": "api_key"}}}]
        if has_hf_token:
            env.append({"name": "HF_TOKEN", "valueFrom": {"secretKeyRef": {"name": secret, "key": "hf_token"}}})
        if frontend == "rust":
            env.append({"name": "VLLM_USE_RUST_FRONTEND", "value": "1"})

        requests: dict[str, str] = {}
        limits: dict[str, str] = {}
        for key, resource in (("cpu", "cpu"), ("memory", "memory"), ("ephemeral_storage", "ephemeral-storage")):
            if cfg.get(key):
                requests[resource] = str(cfg[key])
        if gpu:
            resource = f"nvidia.com/mig-{cfg['mig_profile']}" if cfg.get("mig_profile") else "nvidia.com/gpu"
            limits[resource] = str(gpu)
        health = {"httpGet": {"path": "/health", "port": PORT}}
        pod: dict[str, Any] = {
            "containers": [
                {
                    "name": "vllm",
                    "image": self._config.image,
                    "args": args,
                    "env": env,
                    "ports": [{"name": "http", "containerPort": PORT}],
                    "resources": {k: v for k, v in (("requests", requests), ("limits", limits)) if v},
                    "volumeMounts": [
                        {"name": "cache", "mountPath": "/root/.cache/huggingface"},
                        {"name": "shm", "mountPath": "/dev/shm"},
                    ],
                    # Weights download and engine start can take many minutes;
                    # readiness gates on /health, never on the port alone.
                    "startupProbe": {**health, "periodSeconds": 10, "failureThreshold": 180},
                    "readinessProbe": {**health, "periodSeconds": 10},
                    "livenessProbe": {**health, "periodSeconds": 20, "failureThreshold": 6},
                }
            ],
            "volumes": [
                {"name": "cache", "persistentVolumeClaim": {"claimName": self._cache_name(name)}},
                {"name": "shm", "emptyDir": {"medium": "Memory"}},
            ],
        }
        if gpu:
            pod["tolerations"] = [
                {"key": key, "operator": "Exists", "effect": "NoSchedule"} for key in _GPU_POOL_TAINT_KEYS
            ]
            if cfg.get("gpu_type"):
                pod["affinity"] = {
                    "nodeAffinity": {
                        "requiredDuringSchedulingIgnoredDuringExecution": {
                            "nodeSelectorTerms": [
                                {"matchExpressions": [{"key": key, "operator": "In", "values": [str(cfg["gpu_type"])]}]}
                                for key in ("nvidia.com/gpu.product", "cloud.google.com/gke-accelerator")
                            ]
                        }
                    }
                }
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": {
                    _FRONTEND: frontend,
                    _FRONTEND_SOURCE: source,
                    "astrolift.io/vllm-model": str(cfg["model"]),
                },
            },
            "spec": {
                "replicas": int(cfg["replicas"]),
                # One replica holds the GPUs; a rolling surge would need a second set.
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {"app.kubernetes.io/instance": name}},
                "template": {"metadata": {"labels": labels}, "spec": pod},
            },
        }

    # ---- helpers --------------------------------------------------------

    def _require(self) -> None:
        if self._config.cluster_driver is None or self._config.secrets_backend is None:
            raise ValueError("vLLM requires a cluster driver and a secrets backend")

    def _namespace(self, spec: ProvisionSpec) -> str:
        if self._config.namespace:
            raise ValueError("vLLM runs in the owning app's namespace; a shared vllm namespace is not supported")
        return app_namespace(organization_slug=spec.organization_slug, app_slug=spec.app_slug)

    def _deployment(self, cluster_id: str, namespace: str, name: str) -> dict[str, Any] | None:
        return self._config.cluster_driver.get_manifest(cluster_id, namespace, "apps/v1/Deployment", name)

    def _assert_adoptable(self, cluster_id: str, namespace: str, name: str, managed_service_id: str) -> None:
        existing = self._deployment(cluster_id, namespace, name)
        if existing is not None and self._owner_id(existing) != dns_label(managed_service_id, max_length=63):
            raise ValueError(f"Deployment {namespace}/{name} belongs to another managed service; refusing to adopt it")

    def _owner_id(self, deployment: dict[str, Any]) -> str:
        labels = (deployment.get("metadata") or {}).get("labels") or {}
        if labels.get(_OWNER) != "astrolift" or not labels.get(_OWNER_ID):
            raise ValueError("Deployment is not an Astrolift-managed vLLM server")
        return str(labels[_OWNER_ID])

    @staticmethod
    def _label(obj: dict[str, Any], key: str) -> str:
        return str(((obj.get("metadata") or {}).get("labels") or {}).get(key) or "")

    @staticmethod
    def _annotation(obj: dict[str, Any], key: str) -> str:
        return str(((obj.get("metadata") or {}).get("annotations") or {}).get(key) or "")

    def _api_key(self, cluster_id: str, namespace: str, name: str) -> str:
        path = self._credential_path(cluster_id, namespace, name)
        current = self._config.secrets_backend.get(path)
        if isinstance(current, dict) and current.get("api_key"):
            return str(current["api_key"])
        key = secrets.token_urlsafe(32)
        self._config.secrets_backend.upsert(path, {"api_key": key})
        return key

    def _read_secret_ref(self, ref: str) -> str:
        path, _, key = ref.partition("#")
        value = self._config.secrets_backend.get(path)
        if isinstance(value, dict):
            return str(value.get(key or "value") or "")
        return str(value or "")

    def _credential_path(self, cluster_id: str, namespace: str, name: str) -> str:
        return f"{self._config.credential_path_prefix.strip('/')}/{cluster_id}/{namespace}/{name}/credentials"

    @staticmethod
    def _secret_name(name: str) -> str:
        return dns_label(name, "vllm", max_length=63)

    @staticmethod
    def _cache_name(name: str) -> str:
        return dns_label(name, "cache", max_length=63)

    @staticmethod
    def _stub(api_version: str, kind: str, namespace: str, name: str) -> dict[str, Any]:
        return {"apiVersion": api_version, "kind": kind, "metadata": {"name": name, "namespace": namespace}}
