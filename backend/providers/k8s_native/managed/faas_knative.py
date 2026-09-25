"""Knative Serving implementation of the portable ``faas`` contract."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
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
from k8s_native.managed._secret_refs import refuse_shared_namespace_secrets

KIND = "faas"
VARIANT = "knative_service"
API_VERSION = "serving.knative.dev/v1"
RESOURCE_KIND = "Service"
REQUIRED_CRDS = ("services.serving.knative.dev",)

_DIGEST_IMAGE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LABEL_KEY = re.compile(
    r"^(?:[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?$",
)
_LABEL_VALUE = re.compile(r"^(?:[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?)?$")
_RESERVED_ENV = {"K_CONFIGURATION", "K_REVISION", "K_SERVICE", "PORT"}
_RESERVED_LABELS = {
    "app.kubernetes.io/managed-by",
    "networking.knative.dev/visibility",
}
_RESERVED_REVISION_ANNOTATIONS = {
    "autoscaling.knative.dev/min-scale",
    "autoscaling.knative.dev/max-scale",
    "autoscaling.knative.dev/target",
}

_SIZE_DEFAULTS: dict[str, dict[str, Any]] = {
    "small": {
        "cpu_request": "100m",
        "memory_request": "128Mi",
        "cpu_limit": "500m",
        "memory_limit": "512Mi",
        "min_scale": 0,
        "max_scale": 10,
    },
    "medium": {
        "cpu_request": "250m",
        "memory_request": "256Mi",
        "cpu_limit": "1",
        "memory_limit": "1Gi",
        "min_scale": 0,
        "max_scale": 50,
    },
    "large": {
        "cpu_request": "500m",
        "memory_request": "512Mi",
        "cpu_limit": "2",
        "memory_limit": "2Gi",
        "min_scale": 1,
        "max_scale": 100,
    },
    "xlarge": {
        "cpu_request": "1",
        "memory_request": "1Gi",
        "cpu_limit": "4",
        "memory_limit": "4Gi",
        "min_scale": 1,
        "max_scale": 250,
    },
}


@dataclass(frozen=True)
class KnativeServiceConfig:
    cluster_driver: Any = None
    namespace: str | None = None
    allow_public: bool = False
    allow_tagged_images: bool = False
    allow_unsafe_pod_spec: bool = False
    default_port: int = 8080
    default_timeout_seconds: int = 300
    default_container_concurrency: int = 0


class KnativeServiceDriver(ManagedServiceDriver):
    """Own one Knative Service and its immutable revision history."""

    def __init__(self, *, config: KnativeServiceConfig) -> None:
        self._config = config

    @driver_op(
        cloud="k8s_native",
        driver="faas_knative",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._require_driver()
            namespace = self._namespace(spec)
            name = self._name(spec)
            cfg = self._normalized_config(spec.size, spec.config, update=False)
            self._assert_adoptable(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
                managed_service_id=spec.managed_service_id,
                cfg=cfg,
            )
            manifest = self._manifest(
                namespace=namespace,
                name=name,
                cfg=cfg,
                owner={
                    "managed_service_id": spec.managed_service_id,
                    "organization": spec.organization_slug,
                    "app": spec.app_slug,
                    "environment": spec.environment_name,
                },
            )
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_knative_config"])

        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=namespace,
            name=name,
        )
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            [manifest],
        )
        if not result.ok:
            return ProvisionResult(False, handle, "Knative Service was rejected", result.summary())
        return ProvisionResult(
            True,
            handle,
            f"Knative Service {namespace}/{name} submitted for reconciliation",
            ready=False,
        )

    @driver_op(cloud="k8s_native", driver="faas_knative")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._require_driver()
            parsed = _unpack_handle(spec.handle)
            if parsed.is_legacy:
                raise ValueError("legacy Knative handle has no cluster locator")
            current = self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                f"{API_VERSION}/{RESOURCE_KIND}",
                parsed.name,
            )
            if current is None:
                return UpdateResult(
                    False,
                    spec.handle,
                    "Knative Service does not exist",
                    ["resource_not_found"],
                    retryable=False,
                )
            cfg = self._normalized_config(spec.size or "custom", spec.config, update=True)
            labels = dict((current.get("metadata", {}) or {}).get("labels", {}) or {})
            if labels.get("app.kubernetes.io/managed-by") != "astrolift" or not labels.get(
                "astrolift.io/managed-service-id"
            ):
                raise ValueError("existing Knative Service is not owned by Astrolift")
            manifest = self._manifest(
                namespace=parsed.namespace,
                name=parsed.name,
                cfg=cfg,
                owner={
                    "managed_service_id": labels.get("astrolift.io/managed-service-id", ""),
                    "organization": labels.get("astrolift.io/organization", ""),
                    "app": labels.get("astrolift.io/app", ""),
                    "environment": labels.get("astrolift.io/environment", ""),
                },
            )
        except (TypeError, ValueError) as exc:
            return UpdateResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_knative_update"],
                retryable=False,
            )

        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [manifest],
        )
        if not result.ok:
            return UpdateResult(False, spec.handle, "Knative Service update was rejected", result.summary())
        return UpdateResult(True, spec.handle, f"Knative Service {parsed.name} update submitted")

    @driver_op(
        cloud="k8s_native",
        driver="faas_knative",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        del delete_data
        try:
            self._require_driver()
            parsed = _unpack_handle(spec.handle)
            if parsed.is_legacy:
                raise ValueError("legacy Knative handle has no cluster locator")
        except ValueError as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_handle"],
                retryable=False,
            )
        if bool(spec.config.get("deletion_protection", False)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Knative Service deletion protection is enabled; pass force_destroy to delete it",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        current = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            f"{API_VERSION}/{RESOURCE_KIND}",
            parsed.name,
        )
        if current is None:
            return DeprovisionResult(True, spec.handle, "Knative Service already absent")
        labels = dict((current.get("metadata", {}) or {}).get("labels", {}) or {})
        if labels.get("app.kubernetes.io/managed-by") != "astrolift" or not labels.get(
            "astrolift.io/managed-service-id"
        ):
            return DeprovisionResult(
                False,
                spec.handle,
                "refusing to delete a Knative Service not owned by Astrolift",
                ["ownership_mismatch"],
                retryable=False,
            )
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [self._stub(parsed.namespace, parsed.name)],
        )
        if not result.ok:
            return DeprovisionResult(False, spec.handle, "Knative Service deletion failed", result.summary())
        return DeprovisionResult(True, spec.handle, f"Knative Service {parsed.name} deletion submitted")

    @driver_op(cloud="k8s_native", driver="faas_knative")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._require_driver()
            parsed = _unpack_handle(handle.handle)
            if parsed.is_legacy:
                raise ValueError("legacy Knative handle has no cluster locator")
        except ValueError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        service = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            f"{API_VERSION}/{RESOURCE_KIND}",
            parsed.name,
        )
        if service is None:
            return ServiceStatus(handle.handle, "deprovisioned", "Knative Service not found")
        status = dict(service.get("status", {}) or {})
        ready = self._condition(status, "Ready")
        url = str(status.get("url") or "")
        if ready and str(ready.get("status", "")).lower() == "true" and url:
            return ServiceStatus(handle.handle, "available", f"Knative Service ready at {url}")
        if ready and str(ready.get("status", "")).lower() == "false":
            detail = str(ready.get("message") or ready.get("reason") or "reconciliation failed")
            return ServiceStatus(handle.handle, "error", detail)
        detail = "Knative Service reconciliation in progress"
        if ready:
            detail = str(ready.get("message") or ready.get("reason") or detail)
        return ServiceStatus(handle.handle, "provisioning", detail)

    @driver_op(cloud="k8s_native", driver="faas_knative")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        self._require_driver()
        parsed = _unpack_handle(handle.handle)
        if parsed.is_legacy:
            raise ValueError("legacy Knative handle has no cluster locator")
        service = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            f"{API_VERSION}/{RESOURCE_KIND}",
            parsed.name,
        )
        if service is None:
            raise ValueError("Knative Service does not exist")
        status = dict(service.get("status", {}) or {})
        url = str(status.get("url") or "")
        if not url:
            raise ValueError("Knative Service has no ready route URL")
        return Binding(
            env_vars={
                "FUNCTION_NAME": ValueRef(literal=parsed.name),
                "FUNCTION_ARN": ValueRef(
                    literal=f"k8s://{parsed.cluster_id}/{parsed.namespace}/{parsed.name}",
                ),
                "FUNCTION_URL": ValueRef(literal=url),
                "FUNCTION_REGION": ValueRef(literal="kubernetes"),
            },
            notes="Knative Serving route; private services resolve only inside the cluster.",
        )

    @driver_op(cloud="k8s_native", driver="faas_knative")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise UnsupportedOperationError(
            "managed_service.snapshot(Knative Service) is unsupported; immutable revisions are not data snapshots",
        )

    @driver_op(cloud="k8s_native", driver="faas_knative")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise UnsupportedOperationError(
            "managed_service.restore(Knative Service) is unsupported; redeploy a pinned image revision instead",
        )

    @driver_op(cloud="k8s_native", driver="faas_knative", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        scalar_env = {"type": "object", "additionalProperties": {"type": "string"}}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["image"],
            "properties": {
                "image": {"type": "string", "minLength": 1, "description": "Digest-pinned OCI image"},
                "port": {"type": "integer", "minimum": 1, "maximum": 65535, "default": 8080},
                "public": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": False},
                "adopt_existing": {"type": "boolean", "default": False},
                "expected_existing_uid": {"type": "string"},
                "command": {"type": "array", "items": {"type": "string"}},
                "args": {"type": "array", "items": {"type": "string"}},
                "working_dir": {"type": "string"},
                "env": scalar_env,
                "secret_env": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["secret_name", "key"],
                        "properties": {
                            "secret_name": {"type": "string"},
                            "key": {"type": "string"},
                            "optional": {"type": "boolean", "default": False},
                        },
                    },
                },
                "min_scale": {"type": "integer", "minimum": 0},
                "max_scale": {"type": "integer", "minimum": 1},
                "target_concurrency": {"type": "integer", "minimum": 1},
                "container_concurrency": {"type": "integer", "minimum": 0},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
                "service_account_name": {"type": "string"},
                "image_pull_secrets": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                "cpu_request": {"type": "string"},
                "cpu_limit": {"type": "string"},
                "memory_request": {"type": "string"},
                "memory_limit": {"type": "string"},
                "node_selector": scalar_env,
                "tolerations": {"type": "array", "items": {"type": "object"}},
                "affinity": {"type": "object"},
                "runtime_class_name": {"type": "string"},
                "priority_class_name": {"type": "string"},
                "service_annotations": scalar_env,
                "revision_annotations": scalar_env,
                "labels": scalar_env,
                "security_context": {"type": "object"},
                "startup_probe": {"type": "object"},
                "liveness_probe": {"type": "object"},
                "readiness_probe": {"type": "object"},
                "volumes": {"type": "array", "items": {"type": "object"}},
                "volume_mounts": {"type": "array", "items": {"type": "object"}},
                "traffic": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "latestRevision": {"type": "boolean"},
                            "revisionName": {"type": "string"},
                            "configurationName": {"type": "string"},
                            "percent": {"type": "integer", "minimum": 0, "maximum": 100},
                            "tag": {"type": "string"},
                        },
                    },
                },
            },
        }

    @driver_op(cloud="k8s_native", driver="faas_knative", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "FUNCTION_NAME": "Knative Service name",
                "FUNCTION_ARN": "Portable Kubernetes resource locator",
                "FUNCTION_URL": "Knative Route URL",
                "FUNCTION_REGION": "Always kubernetes",
            },
        )

    def editable_fields(self) -> list[str]:
        return sorted(
            set(self.config_schema()["properties"]) - {"adopt_existing", "expected_existing_uid"},
        )

    def _require_driver(self) -> None:
        if self._config.cluster_driver is None:
            raise ValueError("Knative Service requires a live cluster driver")

    def _namespace(self, spec: ProvisionSpec) -> str:
        return self._config.namespace or app_namespace(
            organization_slug=spec.organization_slug,
            app_slug=spec.app_slug,
        )

    @staticmethod
    def _name(spec: ProvisionSpec) -> str:
        return dns_label(
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "function",
            max_length=63,
        )

    def _normalized_config(self, size: str, raw: dict[str, Any], *, update: bool) -> dict[str, Any]:
        cfg = copy.deepcopy(_SIZE_DEFAULTS.get(size, {}))
        cfg.update(copy.deepcopy(raw or {}))
        image = str(cfg.get("image") or "")
        if not image:
            raise ValueError("Knative image is required")
        if not self._config.allow_tagged_images and not _DIGEST_IMAGE.fullmatch(image):
            raise ValueError("Knative image must be pinned by sha256 digest")
        if bool(cfg.get("public", False)) and not self._config.allow_public:
            raise ValueError("public Knative routes are disabled by cluster policy")
        env = cfg.get("env") or {}
        secret_env = cfg.get("secret_env") or {}
        if not isinstance(env, dict) or not isinstance(secret_env, dict):
            raise ValueError("env and secret_env must be objects")
        for key, value in env.items():
            if not _ENV_NAME.fullmatch(str(key)) or not isinstance(value, str):
                raise ValueError("env must map valid environment names to strings")
            if str(key) in _RESERVED_ENV:
                raise ValueError(f"Knative runtime owns environment variable {key!r}")
        for key, value in secret_env.items():
            if not _ENV_NAME.fullmatch(str(key)) or not isinstance(value, dict):
                raise ValueError("secret_env must map valid environment names to secret references")
            if not value.get("secret_name") or not value.get("key"):
                raise ValueError("secret_env entries require secret_name and key")
            if str(key) in _RESERVED_ENV:
                raise ValueError(f"Knative runtime owns environment variable {key!r}")
        if set(env) & set(secret_env):
            raise ValueError("env and secret_env cannot define the same variable")
        refuse_shared_namespace_secrets(
            self._config.namespace,
            {"image_pull_secrets": cfg.get("image_pull_secrets") or [], "volumes": cfg.get("volumes") or []},
            extra=[str(value.get("secret_name")) for value in secret_env.values()],
        )
        labels = cfg.get("labels") or {}
        if not isinstance(labels, dict):
            raise ValueError("labels must be an object")
        for key, value in labels.items():
            key = str(key)
            value = str(value)
            if key in _RESERVED_LABELS or key.startswith("astrolift.io/"):
                raise ValueError(f"label {key!r} is reserved by Astrolift")
            if len(key) > 253 or not _LABEL_KEY.fullmatch(key):
                raise ValueError(f"invalid Kubernetes label key {key!r}")
            if len(value) > 63 or not _LABEL_VALUE.fullmatch(value):
                raise ValueError(f"invalid Kubernetes label value for {key!r}")
        service_annotations = cfg.get("service_annotations") or {}
        revision_annotations = cfg.get("revision_annotations") or {}
        for field_name, annotation_map in (
            ("service_annotations", service_annotations),
            ("revision_annotations", revision_annotations),
        ):
            if not isinstance(annotation_map, dict):
                raise ValueError(f"{field_name} must be an object")
            for key, value in annotation_map.items():
                if len(str(key)) > 253 or not _LABEL_KEY.fullmatch(str(key)):
                    raise ValueError(f"invalid Kubernetes annotation key {key!r}")
                if not isinstance(value, str):
                    raise ValueError(f"{field_name} must map annotation keys to strings")
        reserved_annotations = _RESERVED_REVISION_ANNOTATIONS & set(revision_annotations)
        if reserved_annotations:
            raise ValueError(
                "use the typed scaling fields instead of reserved annotations: "
                + ", ".join(sorted(reserved_annotations)),
            )
        if not self._config.allow_unsafe_pod_spec:
            security_context = cfg.get("security_context") or {}
            if security_context.get("privileged") is True:
                raise ValueError("privileged Knative containers require allow_unsafe_pod_spec")
            if security_context.get("allowPrivilegeEscalation") is True:
                raise ValueError("privilege escalation requires allow_unsafe_pod_spec")
            if any("hostPath" in volume for volume in cfg.get("volumes") or []):
                raise ValueError("hostPath volumes require allow_unsafe_pod_spec")
        min_scale = int(cfg.get("min_scale", 0))
        max_scale = int(cfg.get("max_scale", 10))
        if min_scale < 0 or max_scale < 1 or min_scale > max_scale:
            raise ValueError("Knative scale bounds must satisfy 0 <= min_scale <= max_scale")
        target_concurrency = cfg.get("target_concurrency")
        if target_concurrency is not None:
            target_concurrency = int(target_concurrency)
            if target_concurrency < 1:
                raise ValueError("Knative target_concurrency must be positive")
        port = int(cfg.get("port", self._config.default_port))
        if not 1 <= port <= 65535:
            raise ValueError("Knative container port must be between 1 and 65535")
        timeout = int(cfg.get("timeout_seconds", self._config.default_timeout_seconds))
        if not 1 <= timeout <= 3600:
            raise ValueError("Knative timeout_seconds must be between 1 and 3600")
        concurrency = int(cfg.get("container_concurrency", self._config.default_container_concurrency))
        if concurrency < 0:
            raise ValueError("Knative container_concurrency cannot be negative")
        traffic = cfg.get("traffic") or [{"latestRevision": True, "percent": 100}]
        if not isinstance(traffic, list) or not traffic:
            raise ValueError("Knative traffic must be a non-empty array")
        allowed_traffic_fields = {
            "latestRevision",
            "revisionName",
            "configurationName",
            "percent",
            "tag",
        }
        total_percent = 0
        for row in traffic:
            if not isinstance(row, dict):
                raise ValueError("Knative traffic entries must be objects")
            unknown = set(row) - allowed_traffic_fields
            if unknown:
                raise ValueError("unsupported Knative traffic fields: " + ", ".join(sorted(unknown)))
            percent = int(row.get("percent", 0))
            if not 0 <= percent <= 100:
                raise ValueError("Knative traffic percentages must be between 0 and 100")
            total_percent += percent
            selectors = [bool(row.get(key)) for key in ("latestRevision", "revisionName", "configurationName")]
            if sum(selectors) != 1:
                raise ValueError("each Knative traffic entry requires exactly one revision selector")
        if total_percent != 100:
            raise ValueError("Knative traffic percentages must sum to 100")
        cfg["min_scale"] = min_scale
        cfg["max_scale"] = max_scale
        cfg["port"] = port
        cfg["timeout_seconds"] = timeout
        cfg["container_concurrency"] = concurrency
        if target_concurrency is not None:
            cfg["target_concurrency"] = target_concurrency
        cfg["traffic"] = traffic
        del update
        return cfg

    def _assert_adoptable(
        self,
        *,
        cluster_id: str,
        namespace: str,
        name: str,
        managed_service_id: str,
        cfg: dict[str, Any],
    ) -> None:
        current = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            f"{API_VERSION}/{RESOURCE_KIND}",
            name,
        )
        if current is None:
            return
        metadata = dict(current.get("metadata", {}) or {})
        labels = dict(metadata.get("labels", {}) or {})
        owner = str(labels.get("astrolift.io/managed-service-id") or "")
        if labels.get("app.kubernetes.io/managed-by") == "astrolift" and owner == managed_service_id:
            return
        if labels.get("app.kubernetes.io/managed-by") == "astrolift" and owner:
            raise ValueError("Knative Service belongs to another Astrolift managed resource")
        if not bool(cfg.get("adopt_existing", False)):
            raise ValueError("Knative Service already exists and is not owned by this managed resource")
        expected_uid = str(cfg.get("expected_existing_uid") or "")
        if not expected_uid or expected_uid != str(metadata.get("uid") or ""):
            raise ValueError("adopting a Knative Service requires its exact expected_existing_uid")

    def _manifest(
        self,
        *,
        namespace: str,
        name: str,
        cfg: dict[str, Any],
        owner: dict[str, str],
    ) -> dict[str, Any]:
        labels = {
            **{str(key): str(value) for key, value in (cfg.get("labels") or {}).items()},
            "app.kubernetes.io/name": name,
            "app.kubernetes.io/managed-by": "astrolift",
        }
        for key, label in (
            ("managed_service_id", "astrolift.io/managed-service-id"),
            ("organization", "astrolift.io/organization"),
            ("app", "astrolift.io/app"),
            ("environment", "astrolift.io/environment"),
        ):
            if owner.get(key):
                labels[label] = dns_label(owner[key], max_length=63)
        if not bool(cfg.get("public", False)):
            labels["networking.knative.dev/visibility"] = "cluster-local"

        revision_annotations = {
            "autoscaling.knative.dev/min-scale": str(cfg["min_scale"]),
            "autoscaling.knative.dev/max-scale": str(cfg["max_scale"]),
            **{str(key): str(value) for key, value in (cfg.get("revision_annotations") or {}).items()},
        }
        if cfg.get("target_concurrency") is not None:
            revision_annotations["autoscaling.knative.dev/target"] = str(cfg["target_concurrency"])
        env: list[dict[str, Any]] = [
            {"name": str(key), "value": str(value)} for key, value in sorted((cfg.get("env") or {}).items())
        ]
        for key, ref in sorted((cfg.get("secret_env") or {}).items()):
            env.append(
                {
                    "name": str(key),
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": str(ref["secret_name"]),
                            "key": str(ref["key"]),
                            "optional": bool(ref.get("optional", False)),
                        },
                    },
                },
            )
        container: dict[str, Any] = {
            "name": "function",
            "image": str(cfg["image"]),
            "ports": [{"name": "http1", "containerPort": int(cfg["port"])}],
            "env": env,
            "resources": {
                "requests": {
                    "cpu": str(cfg.get("cpu_request", "100m")),
                    "memory": str(cfg.get("memory_request", "128Mi")),
                },
                "limits": {
                    "cpu": str(cfg.get("cpu_limit", "500m")),
                    "memory": str(cfg.get("memory_limit", "512Mi")),
                },
            },
        }
        for source, target in (
            ("command", "command"),
            ("args", "args"),
            ("working_dir", "workingDir"),
            ("security_context", "securityContext"),
            ("startup_probe", "startupProbe"),
            ("liveness_probe", "livenessProbe"),
            ("readiness_probe", "readinessProbe"),
            ("volume_mounts", "volumeMounts"),
        ):
            if cfg.get(source) not in (None, "", []):
                container[target] = copy.deepcopy(cfg[source])
        pod_spec: dict[str, Any] = {
            "containerConcurrency": int(cfg["container_concurrency"]),
            "timeoutSeconds": int(cfg["timeout_seconds"]),
            "containers": [container],
        }
        for source, target in (
            ("service_account_name", "serviceAccountName"),
            ("node_selector", "nodeSelector"),
            ("tolerations", "tolerations"),
            ("affinity", "affinity"),
            ("runtime_class_name", "runtimeClassName"),
            ("priority_class_name", "priorityClassName"),
            ("volumes", "volumes"),
        ):
            if cfg.get(source) not in (None, "", [], {}):
                pod_spec[target] = copy.deepcopy(cfg[source])
        if cfg.get("image_pull_secrets"):
            pod_spec["imagePullSecrets"] = [{"name": str(value)} for value in cfg["image_pull_secrets"]]
        return {
            "apiVersion": API_VERSION,
            "kind": RESOURCE_KIND,
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": {str(key): str(value) for key, value in (cfg.get("service_annotations") or {}).items()},
            },
            "spec": {
                "template": {
                    "metadata": {"labels": labels, "annotations": revision_annotations},
                    "spec": pod_spec,
                },
                "traffic": copy.deepcopy(cfg["traffic"]),
            },
        }

    @staticmethod
    def _condition(status: dict[str, Any], condition_type: str) -> dict[str, Any] | None:
        return next(
            (row for row in status.get("conditions", []) or [] if row.get("type") == condition_type),
            None,
        )

    @staticmethod
    def _stub(namespace: str, name: str) -> dict[str, Any]:
        return {
            "apiVersion": API_VERSION,
            "kind": RESOURCE_KIND,
            "metadata": {"namespace": namespace, "name": name},
        }
