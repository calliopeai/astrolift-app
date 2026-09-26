"""KServe InferenceService implementation of the portable model-endpoint contract."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

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
from k8s_native.managed._handle import ParsedHandle
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle
from k8s_native.managed._secret_refs import refuse_shared_namespace_secrets

KIND = "model_endpoint"
VARIANT = "kserve"
API_VERSION = "serving.kserve.io/v1beta1"
RESOURCE_KIND = "InferenceService"
REQUIRED_CRDS = (
    "inferenceservices.serving.kserve.io",
    "servingruntimes.serving.kserve.io",
    "clusterservingruntimes.serving.kserve.io",
    "clusterstoragecontainers.serving.kserve.io",
)

_OWNER = "app.kubernetes.io/managed-by"
_OWNER_ID = "astrolift.io/managed-service-id"
_DELETION_PROTECTION = "astrolift.io/deletion-protection"
_DEPLOYMENT_MODE = "serving.kserve.io/deploymentMode"
_VISIBILITY = "networking.kserve.io/visibility"
_KNATIVE_VISIBILITY = "networking.knative.dev/visibility"
_STORAGE_READONLY = "storage.kserve.io/readonly"
_AUTOSCALER_CLASS = "serving.kserve.io/autoscalerClass"
_PROMETHEUS_SCRAPING = "serving.kserve.io/enable-prometheus-scraping"
_DISABLE_LOCAL_MODEL = "serving.kserve.io/disable-localmodel"
_COMPONENTS = ("predictor", "transformer", "explainer")
_LEGACY_MODEL_FORMATS = {
    "huggingface",
    "lightgbm",
    "onnx",
    "paddle",
    "pmml",
    "pytorch",
    "sklearn",
    "tensorflow",
    "triton",
    "xgboost",
}
_CONFIG_FIELDS = {
    "inference_spec",
    "deployment_mode",
    "public",
    "storage_read_only",
    "autoscaler_class",
    "enable_prometheus_scraping",
    "use_local_model_cache",
    "labels",
    "annotations",
    "deletion_protection",
    "adopt_existing",
    "expected_existing_uid",
}
_RESERVED_LABELS = {_OWNER, _OWNER_ID, _VISIBILITY, _KNATIVE_VISIBILITY}
_RESERVED_ANNOTATIONS = {
    _DELETION_PROTECTION,
    _DEPLOYMENT_MODE,
    _STORAGE_READONLY,
    _AUTOSCALER_CLASS,
    _PROMETHEUS_SCRAPING,
    _DISABLE_LOCAL_MODEL,
}
_LABEL_KEY = re.compile(
    r"^(?:[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?$",
)
_LABEL_VALUE = re.compile(r"^(?:[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?)?$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_DIGEST_IMAGE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
_SENSITIVE_ENV = re.compile(r"(?:PASSWORD|PASSWD|TOKEN|SECRET|API_KEY|PRIVATE_KEY|CREDENTIAL)", re.I)
_WORKLOAD_IDENTITY_ANNOTATIONS = {
    "azure.workload.identity/client-id",
    "eks.amazonaws.com/role-arn",
    "iam.gke.io/gcp-service-account",
}
_UNSAFE_TRUE_FIELDS = {
    "hostNetwork",
    "hostPID",
    "hostIPC",
    "hostProcess",
    "privileged",
    "allowPrivilegeEscalation",
    "shareProcessNamespace",
}


@dataclass(frozen=True)
class KServeConfig:
    cluster_driver: Any = None
    namespace: str | None = None
    default_deployment_mode: str = "Standard"
    allowed_deployment_modes: tuple[str, ...] = ("Standard",)
    service_account_name: str = "kserve-model"
    allow_service_account_override: bool = False
    allowed_service_accounts: tuple[str, ...] = ()
    allow_service_account_token: bool = False
    allow_public: bool = False
    allow_writable_storage: bool = False
    allow_custom_containers: bool = False
    allow_tagged_images: bool = False
    allowed_image_prefixes: tuple[str, ...] = ()
    allowed_storage_uri_schemes: tuple[str, ...] = (
        "s3",
        "gs",
        "hf",
        "pvc",
        "oci",
        "oci+native",
    )
    allow_external_storage_urls: bool = False
    allowed_external_storage_hosts: tuple[str, ...] = ()
    allow_external_logger_urls: bool = False
    allowed_external_logger_hosts: tuple[str, ...] = ()
    allow_privileged_pods: bool = False
    allow_host_access: bool = False
    allow_local_model_cache: bool = False
    allowed_model_formats: tuple[str, ...] = ()
    allowed_serving_runtimes: tuple[str, ...] = ()
    allowed_autoscaler_classes: tuple[str, ...] = ("hpa", "none")
    max_replicas: int = 100


class KServeDriver(ManagedServiceDriver):
    """Own one namespace-local KServe ``InferenceService``."""

    def __init__(self, *, config: KServeConfig) -> None:
        self._config = config

    @driver_op(
        cloud="k8s_native",
        driver="model_endpoint_kserve",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._require_driver()
            if not spec.managed_service_id:
                raise ValueError("KServe requires a managed_service_id for safe ownership")
            namespace = self._config.namespace or app_namespace(
                organization_slug=spec.organization_slug,
                app_slug=spec.app_slug,
            )
            name = dns_label(
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "model",
            )
            cfg = self._normalize(spec.config)
            self._assert_adoptable(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
                managed_service_id=spec.managed_service_id,
                cfg=cfg,
            )
            service_accounts = self._validate_service_accounts(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                inference_spec=cfg["inference_spec"],
            )
            owner = {
                "managed_service_id": spec.managed_service_id,
                "organization": spec.organization_slug,
                "app": spec.app_slug,
                "environment": spec.environment_name,
            }
            manifests = [
                *self._service_account_manifests(
                    cluster_id=spec.tenant_cluster_id,
                    namespace=namespace,
                    owner=owner,
                    service_accounts=service_accounts,
                ),
                self._manifest(namespace=namespace, name=name, cfg=cfg, owner=owner),
            ]
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_kserve_config"])

        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id,
            namespace=namespace,
            name=name,
        )
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            manifests,
        )
        if not result.ok:
            return ProvisionResult(False, handle, "KServe InferenceService was rejected", result.summary())
        return ProvisionResult(
            True,
            handle,
            f"KServe InferenceService {namespace}/{name} submitted for reconciliation",
            ready=False,
        )

    @driver_op(cloud="k8s_native", driver="model_endpoint_kserve")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._require_driver()
            parsed = self._parsed(spec.handle)
            current = self._resource(parsed)
            if current is None:
                return UpdateResult(
                    False,
                    spec.handle,
                    "KServe InferenceService does not exist",
                    ["resource_not_found"],
                    retryable=False,
                )
            self._assert_owned(current)
            cfg = self._normalize(spec.config)
            service_accounts = self._validate_service_accounts(
                cluster_id=parsed.cluster_id,
                namespace=parsed.namespace,
                inference_spec=cfg["inference_spec"],
            )
            owner = self._owner_from_labels(current)
            manifests = [
                *self._service_account_manifests(
                    cluster_id=parsed.cluster_id,
                    namespace=parsed.namespace,
                    owner=owner,
                    service_accounts=service_accounts,
                ),
                self._manifest(
                    namespace=parsed.namespace,
                    name=parsed.name,
                    cfg=cfg,
                    owner=owner,
                ),
            ]
        except (TypeError, ValueError) as exc:
            return UpdateResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_kserve_update"],
                retryable=False,
            )
        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            manifests,
        )
        if not result.ok:
            return UpdateResult(False, spec.handle, "KServe update was rejected", result.summary())
        return UpdateResult(True, spec.handle, f"KServe InferenceService {parsed.name} reconciled")

    @driver_op(
        cloud="k8s_native",
        driver="model_endpoint_kserve",
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
            parsed = self._parsed(spec.handle)
            resource = self._resource(parsed)
            if resource is None:
                return DeprovisionResult(True, spec.handle, "KServe InferenceService already absent")
            self._assert_owned(resource)
            annotations = dict((resource.get("metadata", {}) or {}).get("annotations", {}) or {})
        except ValueError as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                str(exc),
                ["ownership_mismatch"],
                retryable=False,
            )
        protected = annotations.get(_DELETION_PROTECTION) == "true" or bool(
            spec.config.get("deletion_protection", False)
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "KServe InferenceService deletion protection is enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [self._stub(parsed.namespace, parsed.name)],
        )
        if not result.ok:
            return DeprovisionResult(False, spec.handle, "KServe deletion failed", result.summary())
        return DeprovisionResult(
            True,
            spec.handle,
            f"KServe InferenceService {parsed.name} deletion submitted; model artifacts were retained",
        )

    @driver_op(cloud="k8s_native", driver="model_endpoint_kserve")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._require_driver()
            parsed = self._parsed(handle.handle)
            resource = self._resource(parsed)
            if resource is None:
                return ServiceStatus(handle.handle, "deprovisioned", "KServe InferenceService not found")
            self._assert_owned(resource)
            identity_error = self._service_account_error(parsed, resource)
            if identity_error:
                return ServiceStatus(handle.handle, "error", identity_error)
        except ValueError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        status = dict(resource.get("status", {}) or {})
        ready = self._condition(status, "Ready")
        model_status = dict(status.get("modelStatus", {}) or {})
        failure = dict(model_status.get("lastFailureInfo", {}) or {})
        if ready and str(ready.get("status", "")).lower() == "true":
            url = self._endpoint_url(status)
            runtime = str(status.get("servingRuntimeName") or "")
            suffix = f" using {runtime}" if runtime else ""
            endpoint = f" at {url}" if url else ""
            return ServiceStatus(handle.handle, "available", f"KServe model ready{suffix}{endpoint}")
        if failure:
            detail = str(failure.get("message") or failure.get("reason") or "model load failed")
            return ServiceStatus(handle.handle, "error", detail)
        if ready and str(ready.get("status", "")).lower() == "false":
            detail = str(ready.get("message") or ready.get("reason") or "KServe reconciliation failed")
            return ServiceStatus(handle.handle, "error", detail)
        transition = str(model_status.get("transitionStatus") or "")
        detail = transition if transition and transition != "UpToDate" else "KServe reconciliation in progress"
        return ServiceStatus(handle.handle, "provisioning", detail)

    @driver_op(cloud="k8s_native", driver="model_endpoint_kserve")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        self._require_driver()
        parsed = self._parsed(handle.handle)
        resource = self._resource(parsed)
        if resource is None:
            raise ValueError("KServe InferenceService does not exist")
        self._assert_owned(resource)
        status = dict(resource.get("status", {}) or {})
        ready = self._condition(status, "Ready")
        if not ready or str(ready.get("status", "")).lower() != "true":
            raise ValueError("KServe InferenceService is not ready")
        url = self._endpoint_url(status)
        if not url:
            raise ValueError("KServe InferenceService has no ready endpoint URL")
        inference_spec = dict(resource.get("spec", {}) or {})
        predictor = dict(inference_spec.get("predictor", {}) or {})
        model = dict(predictor.get("model", {}) or {})
        model_format = dict(model.get("modelFormat", {}) or {})
        model_format_name = str(model_format.get("name") or "")
        if not model_format_name:
            model_format_name = next(iter(sorted(_LEGACY_MODEL_FORMATS & predictor.keys())), "")
        protocol = str(model.get("protocolVersion") or predictor.get("protocolVersion") or "")
        runtime = str(status.get("servingRuntimeName") or model.get("runtime") or predictor.get("runtime") or "")
        env_vars = {
            "MODEL_ENDPOINT_URL": ValueRef(literal=url),
            "MODEL_DEPLOYMENT_NAME": ValueRef(literal=parsed.name),
            "MODEL_REGION": ValueRef(literal="kubernetes"),
            "MODEL_API_STYLE": ValueRef(literal="kserve"),
            "MODEL_AUTH_MODE": ValueRef(literal="none"),
            "KSERVE_INFERENCE_SERVICE": ValueRef(literal=parsed.name),
            "KSERVE_NAMESPACE": ValueRef(literal=parsed.namespace),
        }
        grpc_url = str(status.get("grpcUrl") or "")
        if grpc_url:
            env_vars["MODEL_ENDPOINT_GRPC_URL"] = ValueRef(literal=grpc_url)
        if protocol:
            env_vars["MODEL_PROTOCOL_VERSION"] = ValueRef(literal=protocol)
        if runtime:
            env_vars["MODEL_SERVING_RUNTIME"] = ValueRef(literal=runtime)
        if model_format_name:
            env_vars["MODEL_FORMAT"] = ValueRef(literal=model_format_name)
        return Binding(
            env_vars=env_vars,
            notes="KServe InferenceService endpoint; cluster-local services require in-cluster network access.",
        )

    @driver_op(cloud="k8s_native", driver="model_endpoint_kserve")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise UnsupportedOperationError(
            "managed_service.snapshot(KServe) is unsupported; model artifacts live in external immutable storage",
        )

    @driver_op(cloud="k8s_native", driver="model_endpoint_kserve")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise UnsupportedOperationError(
            "managed_service.restore(KServe) is unsupported; provision a new endpoint from a pinned model artifact",
        )

    @driver_op(cloud="k8s_native", driver="model_endpoint_kserve", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        scalar_map = {"type": "object", "additionalProperties": {"type": "string"}}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["inference_spec"],
            "properties": {
                "inference_spec": {
                    "type": "object",
                    "description": (
                        "Native serving.kserve.io/v1beta1 InferenceService spec, including predictor, "
                        "transformer, explainer, canary, runtime, storage, accelerator, scheduling, "
                        "and scaling controls."
                    ),
                },
                "deployment_mode": {
                    "type": "string",
                    "enum": ["Standard", "Knative", "ModelMesh"],
                    "default": self._config.default_deployment_mode,
                },
                "public": {"type": "boolean", "default": False},
                "storage_read_only": {"type": "boolean", "default": True},
                "autoscaler_class": {
                    "type": "string",
                    "enum": ["hpa", "keda", "external", "none"],
                    "default": "hpa",
                },
                "enable_prometheus_scraping": {"type": "boolean", "default": True},
                "use_local_model_cache": {"type": "boolean", "default": False},
                "labels": scalar_map,
                "annotations": scalar_map,
                "deletion_protection": {"type": "boolean", "default": False},
                "adopt_existing": {"type": "boolean", "default": False},
                "expected_existing_uid": {"type": "string"},
            },
        }

    @driver_op(cloud="k8s_native", driver="model_endpoint_kserve", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MODEL_ENDPOINT_URL": "Ready KServe REST endpoint",
                "MODEL_DEPLOYMENT_NAME": "InferenceService name",
                "MODEL_API_STYLE": "Client protocol: 'kserve' (see MODEL_PROTOCOL_VERSION)",
                "MODEL_AUTH_MODE": "Credential kind: 'none' (cluster-internal)",
                "MODEL_REGION": "Always kubernetes",
                "KSERVE_INFERENCE_SERVICE": "InferenceService name",
                "KSERVE_NAMESPACE": "InferenceService namespace",
                "MODEL_ENDPOINT_GRPC_URL": "Optional KServe gRPC endpoint",
                "MODEL_PROTOCOL_VERSION": "Optional v1, v2, or runtime protocol",
                "MODEL_SERVING_RUNTIME": "Resolved KServe ServingRuntime",
                "MODEL_FORMAT": "Declared model format",
            },
        )

    def editable_fields(self) -> list[str]:
        return sorted(_CONFIG_FIELDS - {"adopt_existing", "expected_existing_uid"})

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        cfg = copy.deepcopy(raw or {})
        if (
            isinstance(self._config.max_replicas, bool)
            or not isinstance(self._config.max_replicas, int)
            or self._config.max_replicas < 0
        ):
            raise ValueError("KServe cluster policy max_replicas must be a non-negative integer")
        self._validate_service_account_name(
            self._config.service_account_name,
            path="cluster policy service_account_name",
        )
        unknown = set(cfg) - _CONFIG_FIELDS
        if unknown:
            raise ValueError("unsupported KServe config fields: " + ", ".join(sorted(unknown)))
        for field in (
            "public",
            "storage_read_only",
            "enable_prometheus_scraping",
            "use_local_model_cache",
            "deletion_protection",
            "adopt_existing",
        ):
            if field in cfg and not isinstance(cfg[field], bool):
                raise ValueError(f"KServe {field} must be a boolean")
        if "expected_existing_uid" in cfg and not isinstance(cfg["expected_existing_uid"], str):
            raise ValueError("KServe expected_existing_uid must be a string")
        inference_spec = cfg.get("inference_spec")
        if not isinstance(inference_spec, dict) or not inference_spec:
            raise ValueError("KServe requires a non-empty inference_spec object")
        predictor = inference_spec.get("predictor")
        if not isinstance(predictor, dict) or not predictor:
            raise ValueError("KServe inference_spec requires a predictor object")
        for component in _COMPONENTS:
            value = inference_spec.get(component)
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"KServe {component} must be an object")
        deployment_mode = str(cfg.get("deployment_mode") or self._config.default_deployment_mode)
        if deployment_mode not in {"Standard", "Knative", "ModelMesh"}:
            raise ValueError("KServe deployment_mode must be Standard, Knative, or ModelMesh")
        if deployment_mode not in self._config.allowed_deployment_modes:
            raise ValueError(f"KServe deployment mode {deployment_mode!r} is disabled by cluster policy")
        if bool(cfg.get("public", False)) and not self._config.allow_public:
            raise ValueError("public KServe endpoints are disabled by cluster policy")
        if not bool(cfg.get("storage_read_only", True)) and not self._config.allow_writable_storage:
            raise ValueError("writable KServe model storage is disabled by cluster policy")
        autoscaler_class = str(cfg.get("autoscaler_class") or "hpa")
        if autoscaler_class not in {"hpa", "keda", "external", "none"}:
            raise ValueError("KServe autoscaler_class must be hpa, keda, external, or none")
        if autoscaler_class not in self._config.allowed_autoscaler_classes:
            raise ValueError(f"KServe autoscaler class {autoscaler_class!r} is disabled by cluster policy")
        if bool(cfg.get("use_local_model_cache", False)) and not self._config.allow_local_model_cache:
            raise ValueError("KServe local model cache is disabled by cluster policy")
        self._validate_metadata(cfg.get("labels"), cfg.get("annotations"))
        self._validate_inference_spec(inference_spec)
        cfg["deployment_mode"] = deployment_mode
        cfg["autoscaler_class"] = autoscaler_class
        cfg["storage_read_only"] = bool(cfg.get("storage_read_only", True))
        cfg["enable_prometheus_scraping"] = bool(cfg.get("enable_prometheus_scraping", True))
        cfg["use_local_model_cache"] = bool(cfg.get("use_local_model_cache", False))
        refuse_shared_namespace_secrets(self._config.namespace, cfg)
        return cfg

    def _validate_inference_spec(self, inference_spec: dict[str, Any]) -> None:
        if "canaryTrafficPercent" in inference_spec:
            raise ValueError(
                "KServe 0.20 uses inference_spec.canary revision entries, not canaryTrafficPercent",
            )
        self._validate_predictor(
            inference_spec["predictor"],
            path="inference_spec.predictor",
            canary=False,
        )
        canary = inference_spec.get("canary", [])
        if not isinstance(canary, list):
            raise ValueError("KServe inference_spec.canary must be an array")
        total_traffic = 0
        for index, row in enumerate(canary):
            if not isinstance(row, dict) or not isinstance(row.get("predictor"), dict):
                raise ValueError(f"KServe inference_spec.canary[{index}] requires a predictor object")
            percent = row.get("trafficPercent")
            if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
                raise ValueError(
                    f"KServe inference_spec.canary[{index}].trafficPercent must be an integer between 0 and 100",
                )
            total_traffic += percent
            self._validate_predictor(
                row["predictor"],
                path=f"inference_spec.canary[{index}].predictor",
                canary=True,
            )
        if total_traffic > 100:
            raise ValueError("KServe canary traffic percentages cannot total more than 100")
        self._walk_security(inference_spec, path="inference_spec")

    def _validate_predictor(
        self,
        predictor: dict[str, Any],
        *,
        path: str,
        canary: bool,
    ) -> None:
        model = predictor.get("model") or {}
        if model and not isinstance(model, dict):
            raise ValueError(f"KServe {path}.model must be an object")
        declared_formats = sorted(_LEGACY_MODEL_FORMATS & predictor.keys())
        if canary:
            named_specs = [
                value for field in ("model", *declared_formats) if isinstance((value := predictor.get(field)), dict)
            ]
            if any(not str(value.get("name") or "") for value in named_specs):
                raise ValueError(f"KServe {path} model revisions require a name")
        model_format = model.get("modelFormat") or {}
        if model_format:
            if not isinstance(model_format, dict) or not str(model_format.get("name") or ""):
                raise ValueError(f"KServe {path}.model.modelFormat requires a name")
            declared_formats.append(str(model_format["name"]))
        if self._config.allowed_model_formats:
            for model_format_name in declared_formats:
                if model_format_name not in self._config.allowed_model_formats:
                    raise ValueError(
                        f"KServe model format {model_format_name!r} is disabled by cluster policy",
                    )

    def _walk_security(self, value: Any, *, path: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                self._walk_security(item, path=f"{path}[{index}]")
            return
        if not isinstance(value, dict):
            return
        for field in _UNSAFE_TRUE_FIELDS:
            if value.get(field) is True:
                host_field = field.startswith("host") or field == "shareProcessNamespace"
                if host_field and not self._config.allow_host_access:
                    raise ValueError(f"{path}.{field} is disabled by cluster policy")
                if not host_field and not self._config.allow_privileged_pods:
                    raise ValueError(f"{path}.{field} is disabled by cluster policy")
        if "hostPath" in value and not self._config.allow_host_access:
            raise ValueError(f"{path}.hostPath is disabled by cluster policy")
        if value.get("hostPort") not in (None, 0) and not self._config.allow_host_access:
            raise ValueError(f"{path}.hostPort is disabled by cluster policy")
        if value.get("runAsUser") == 0 and not self._config.allow_privileged_pods:
            raise ValueError(f"{path}.runAsUser=0 is disabled by cluster policy")
        if value.get("runAsNonRoot") is False and not self._config.allow_privileged_pods:
            raise ValueError(f"{path}.runAsNonRoot=false is disabled by cluster policy")
        capabilities = value.get("capabilities") or {}
        if isinstance(capabilities, dict) and capabilities.get("add") and not self._config.allow_privileged_pods:
            raise ValueError(f"{path}.capabilities.add is disabled by cluster policy")
        if value.get("procMount") not in (None, "Default") and not self._config.allow_privileged_pods:
            raise ValueError(f"{path}.procMount is disabled by cluster policy")
        seccomp = value.get("seccompProfile") or {}
        if isinstance(seccomp, dict) and seccomp.get("type") == "Unconfined" and not self._config.allow_privileged_pods:
            raise ValueError(f"{path}.seccompProfile is disabled by cluster policy")
        apparmor = value.get("appArmorProfile") or {}
        if (
            isinstance(apparmor, dict)
            and apparmor.get("type") == "Unconfined"
            and not self._config.allow_privileged_pods
        ):
            raise ValueError(f"{path}.appArmorProfile is disabled by cluster policy")
        if value.get("sysctls") and not self._config.allow_privileged_pods:
            raise ValueError(f"{path}.sysctls is disabled by cluster policy")
        if value.get("automountServiceAccountToken") is True and not self._config.allow_service_account_token:
            raise ValueError(f"{path}.automountServiceAccountToken is disabled by cluster policy")
        if "serviceAccountToken" in value and not self._config.allow_service_account_token:
            raise ValueError(f"{path}.serviceAccountToken is disabled by cluster policy")
        projected = value.get("projected") or {}
        projected_sources = projected.get("sources", []) if isinstance(projected, dict) else []
        if (
            isinstance(projected_sources, list)
            and any(isinstance(source, dict) and source.get("serviceAccountToken") for source in projected_sources)
            and not self._config.allow_service_account_token
        ):
            raise ValueError(f"{path}.projected service-account token is disabled by cluster policy")
        if {
            "containers",
            "initContainers",
            "ephemeralContainers",
        } & value.keys() and not self._config.allow_custom_containers:
            raise ValueError(f"{path} custom containers are disabled by cluster policy")
        if "image" in value:
            self._validate_image(value["image"], path=f"{path}.image")
        for key in ("serviceAccountName", "serviceAccount"):
            if key in value:
                self._validate_service_account_name(value[key], path=f"{path}.{key}")
        if "runtime" in value and value["runtime"] not in (None, ""):
            runtime = str(value["runtime"])
            if runtime not in self._config.allowed_serving_runtimes:
                raise ValueError(f"KServe ServingRuntime {runtime!r} is not allowlisted")
        if "storageUri" in value:
            self._validate_storage_uri(value["storageUri"], path=f"{path}.storageUri")
        if "storageUris" in value:
            self._validate_storage_uris(value["storageUris"], path=f"{path}.storageUris")
        logger = value.get("logger")
        if isinstance(logger, dict) and logger.get("url"):
            self._validate_logger_url(logger["url"], path=f"{path}.logger.url")
        if (
            isinstance(value.get("name"), str)
            and "value" in value
            and _SENSITIVE_ENV.search(value["name"])
            and value.get("value") not in (None, "")
        ):
            raise ValueError(f"{path} must reference sensitive environment values from a Secret")
        for key in ("minReplicas", "maxReplicas"):
            if key in value:
                count = value[key]
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ValueError(f"{path}.{key} must be a non-negative integer")
                if count > self._config.max_replicas:
                    raise ValueError(
                        f"{path}.{key} exceeds cluster policy maximum {self._config.max_replicas}",
                    )
        if (
            isinstance(value.get("minReplicas"), int)
            and isinstance(value.get("maxReplicas"), int)
            and value["minReplicas"] > value["maxReplicas"]
        ):
            raise ValueError(f"{path} replica bounds require minReplicas <= maxReplicas")
        for key, item in value.items():
            self._walk_security(item, path=f"{path}.{key}")

    def _validate_image(self, raw: Any, *, path: str) -> None:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{path} must be a non-empty string")
        image = raw.strip()
        image_path = image.split("@", 1)[0]
        allowed = any(
            image_path == prefix.rstrip("/") or image_path.startswith(f"{prefix.rstrip('/')}/")
            for prefix in self._config.allowed_image_prefixes
            if prefix.rstrip("/")
        )
        if self._config.allowed_image_prefixes and not allowed:
            raise ValueError(f"{path} is outside the cluster image allowlist")
        if not self._config.allow_tagged_images and not _DIGEST_IMAGE.fullmatch(image):
            raise ValueError(f"{path} must use an immutable sha256 digest")

    def _validate_storage_uri(self, raw: Any, *, path: str) -> None:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{path} must be a non-empty URI")
        parsed = urlsplit(raw.strip())
        scheme = parsed.scheme.lower()
        if scheme in {"http", "https"}:
            if not self._config.allow_external_storage_urls:
                raise ValueError(f"{path} external HTTP storage is disabled by cluster policy")
            self._assert_allowed_host(
                parsed.hostname,
                self._config.allowed_external_storage_hosts,
                path=path,
            )
        elif scheme not in self._config.allowed_storage_uri_schemes:
            raise ValueError(f"{path} scheme {scheme!r} is disabled by cluster policy")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"{path} cannot embed credentials, query strings, or fragments")
        if scheme == "pvc" and not parsed.netloc:
            raise ValueError(f"{path} pvc URI requires a claim name")
        if scheme in {"oci", "oci+native"} and "@sha256:" not in raw and not self._config.allow_tagged_images:
            raise ValueError(f"{path} OCI model artifact must use an immutable sha256 digest")

    def _validate_storage_uris(self, value: Any, *, path: str) -> None:
        if not isinstance(value, list) or not value:
            raise ValueError(f"{path} must be a non-empty array")
        roots: set[str] = set()
        for index, row in enumerate(value):
            if not isinstance(row, dict) or not row.get("uri"):
                raise ValueError(f"{path}[{index}] requires uri")
            self._validate_storage_uri(row["uri"], path=f"{path}[{index}].uri")
            mount_path = str(row.get("mountPath") or "/mnt/models")
            pure = PurePosixPath(mount_path)
            if not mount_path.startswith("/") or ".." in pure.parts:
                raise ValueError(f"{path}[{index}].mountPath must be absolute and cannot traverse parents")
            if len(pure.parts) < 2:
                raise ValueError(f"{path}[{index}].mountPath cannot be the filesystem root")
            roots.add(pure.parts[1])
        if len(roots) != 1:
            raise ValueError(f"{path} mount paths must share a common non-root directory")

    def _validate_logger_url(self, raw: Any, *, path: str) -> None:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{path} must be a non-empty URL")
        parsed = urlsplit(raw.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"{path} must use http or https")
        internal = parsed.hostname.endswith(".svc") or parsed.hostname.endswith(".svc.cluster.local")
        if not internal:
            if not self._config.allow_external_logger_urls:
                raise ValueError(f"{path} external logger destinations are disabled by cluster policy")
            self._assert_allowed_host(
                parsed.hostname,
                self._config.allowed_external_logger_hosts,
                path=path,
            )
        if parsed.username or parsed.password:
            raise ValueError(f"{path} cannot embed credentials")

    @staticmethod
    def _assert_allowed_host(host: str | None, allowed_hosts: tuple[str, ...], *, path: str) -> None:
        candidate = (host or "").lower().rstrip(".")
        allowed = any(
            candidate == entry.lower().lstrip(".").rstrip(".")
            or candidate.endswith(f".{entry.lower().lstrip('.').rstrip('.')}")
            for entry in allowed_hosts
            if entry.strip(".")
        )
        if not candidate or not allowed:
            raise ValueError(f"{path} host {host!r} is outside the cluster allowlist")

    def _validate_service_account_name(self, raw: Any, *, path: str) -> None:
        name = str(raw or "")
        if not name or len(name) > 63 or not _DNS_LABEL.fullmatch(name):
            raise ValueError(f"{path} must be a valid Kubernetes DNS-label ServiceAccount name")
        if name == self._config.service_account_name:
            return
        if not self._config.allow_service_account_override:
            raise ValueError(f"{path} override is disabled by cluster policy")
        if name not in self._config.allowed_service_accounts:
            raise ValueError(f"{path} {name!r} is not enabled for this cluster")

    def _validate_service_accounts(
        self,
        *,
        cluster_id: str,
        namespace: str,
        inference_spec: dict[str, Any],
    ) -> set[str]:
        accounts = self._service_accounts(inference_spec)
        if any(not self._component_service_account(component) for component in self._component_specs(inference_spec)):
            accounts.add(self._config.service_account_name)
        for account in sorted(accounts):
            self._validate_service_account_name(account, path="inference_spec.serviceAccountName")
            current = self._config.cluster_driver.get_manifest(
                cluster_id,
                namespace,
                "v1/ServiceAccount",
                account,
            )
            if current is None and account != self._config.service_account_name:
                raise ValueError(f"KServe ServiceAccount {namespace}/{account} does not exist")
        return accounts

    @staticmethod
    def _service_accounts(value: Any) -> set[str]:
        accounts: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item)
                return
            if not isinstance(node, dict):
                return
            for key, item in node.items():
                if key in {"serviceAccountName", "serviceAccount"} and item:
                    accounts.add(str(item))
                else:
                    walk(item)

        walk(value)
        return accounts

    @staticmethod
    def _component_specs(inference_spec: dict[str, Any]) -> list[dict[str, Any]]:
        components = [value for name in _COMPONENTS if isinstance((value := inference_spec.get(name)), dict)]
        canary = inference_spec.get("canary") or []
        if isinstance(canary, list):
            components.extend(
                row["predictor"] for row in canary if isinstance(row, dict) and isinstance(row.get("predictor"), dict)
            )
        return components

    @staticmethod
    def _component_service_account(component: dict[str, Any]) -> str:
        return str(component.get("serviceAccountName") or component.get("serviceAccount") or "")

    def _service_account_manifests(
        self,
        *,
        cluster_id: str,
        namespace: str,
        owner: dict[str, str],
        service_accounts: set[str],
    ) -> list[dict[str, Any]]:
        baseline = self._config.service_account_name
        if baseline not in service_accounts:
            return []
        current = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            "v1/ServiceAccount",
            baseline,
        )
        if current is not None:
            identity_error = self._baseline_identity_error(current, owner)
            if identity_error:
                raise ValueError(identity_error)
        project_owner = {key: value for key, value in owner.items() if key not in {"managed_service_id", "environment"}}
        labels = self._owner_labels(project_owner)
        labels["astrolift.io/component"] = "kserve-model-runtime"
        return [
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {"name": baseline, "namespace": namespace, "labels": labels},
                "automountServiceAccountToken": bool(self._config.allow_service_account_token),
            },
        ]

    def _manifest(
        self,
        *,
        namespace: str,
        name: str,
        cfg: dict[str, Any],
        owner: dict[str, str],
    ) -> dict[str, Any]:
        inference_spec = copy.deepcopy(cfg["inference_spec"])
        for component in self._component_specs(inference_spec):
            if not self._component_service_account(component):
                component["serviceAccountName"] = self._config.service_account_name
            component.setdefault("automountServiceAccountToken", False)
        labels = {
            **{str(key): str(value) for key, value in (cfg.get("labels") or {}).items()},
            **self._owner_labels(owner),
            "app.kubernetes.io/name": name,
        }
        if not bool(cfg.get("public", False)):
            labels[_VISIBILITY] = "cluster-local"
            labels[_KNATIVE_VISIBILITY] = "cluster-local"
        annotations = {
            **{str(key): str(value) for key, value in (cfg.get("annotations") or {}).items()},
            _DEPLOYMENT_MODE: cfg["deployment_mode"],
            _STORAGE_READONLY: "true" if cfg["storage_read_only"] else "false",
            _AUTOSCALER_CLASS: cfg["autoscaler_class"],
            _PROMETHEUS_SCRAPING: "true" if cfg["enable_prometheus_scraping"] else "false",
            _DISABLE_LOCAL_MODEL: "false" if cfg["use_local_model_cache"] else "true",
            _DELETION_PROTECTION: "true" if bool(cfg.get("deletion_protection", False)) else "false",
        }
        return {
            "apiVersion": API_VERSION,
            "kind": RESOURCE_KIND,
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": annotations,
            },
            "spec": inference_spec,
        }

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
        owner = str(labels.get(_OWNER_ID) or "")
        if labels.get(_OWNER) == "astrolift" and owner == dns_label(managed_service_id):
            return
        if labels.get(_OWNER) == "astrolift" and owner:
            raise ValueError("KServe InferenceService belongs to another Astrolift resource")
        if not cfg.get("adopt_existing") or str(cfg.get("expected_existing_uid") or "") != str(
            metadata.get("uid") or "",
        ):
            raise ValueError("adopting a KServe InferenceService requires its exact expected_existing_uid")

    @staticmethod
    def _validate_metadata(labels: Any, annotations: Any) -> None:
        for field, values, reserved, bounded in (
            ("labels", labels, _RESERVED_LABELS, True),
            ("annotations", annotations, _RESERVED_ANNOTATIONS, False),
        ):
            values = values or {}
            if not isinstance(values, dict):
                raise ValueError(f"{field} must be an object")
            for key, value in values.items():
                key = str(key)
                if key in reserved or key.startswith("astrolift.io/"):
                    raise ValueError(f"metadata key {key!r} is reserved by Astrolift")
                if field == "annotations" and key.startswith("internal.serving.kserve.io/"):
                    raise ValueError(f"metadata key {key!r} is reserved by the KServe controller")
                prefix, _, name = key.rpartition("/")
                if len(key) > 253 or len(name) > 63 or (prefix and len(prefix) > 253) or not _LABEL_KEY.fullmatch(key):
                    raise ValueError(f"invalid Kubernetes metadata key {key!r}")
                if not isinstance(value, str):
                    raise ValueError(f"{field} must map keys to strings")
                if bounded and (len(value) > 63 or not _LABEL_VALUE.fullmatch(value)):
                    raise ValueError(f"invalid Kubernetes label value for {key!r}")

    def _service_account_error(self, parsed: ParsedHandle, resource: dict[str, Any]) -> str:
        owner = self._owner_from_labels(resource)
        for account in sorted(self._service_accounts(resource.get("spec") or {})):
            current = self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                "v1/ServiceAccount",
                account,
            )
            if current is None:
                return f"KServe ServiceAccount {parsed.namespace}/{account} is missing"
            if account == self._config.service_account_name:
                identity_error = self._baseline_identity_error(current, owner)
                if identity_error:
                    return identity_error
        return ""

    def _baseline_identity_error(
        self,
        account: dict[str, Any],
        owner: dict[str, str],
    ) -> str:
        metadata = dict(account.get("metadata", {}) or {})
        labels = dict(metadata.get("labels", {}) or {})
        expected = {
            _OWNER: "astrolift",
            "astrolift.io/component": "kserve-model-runtime",
            "astrolift.io/organization": dns_label(owner["organization"]),
            "astrolift.io/app": dns_label(owner["app"]),
        }
        if any(labels.get(key) != value for key, value in expected.items()):
            return (
                f"KServe baseline ServiceAccount {metadata.get('name')!r} is not owned "
                "by this Astrolift project; use an explicitly allowlisted service-account override"
            )
        annotations = dict(metadata.get("annotations", {}) or {})
        ambient_identity = sorted(_WORKLOAD_IDENTITY_ANNOTATIONS & annotations.keys())
        if ambient_identity:
            return (
                f"KServe baseline ServiceAccount {metadata.get('name')!r} has ambient workload "
                f"identity annotation {ambient_identity[0]!r}; use an explicitly allowlisted override"
            )
        return ""

    @staticmethod
    def _condition(status: dict[str, Any], condition_type: str) -> dict[str, Any] | None:
        return next(
            (row for row in status.get("conditions", []) or [] if row.get("type") == condition_type),
            None,
        )

    @staticmethod
    def _endpoint_url(status: dict[str, Any]) -> str:
        address = status.get("address") or {}
        address_url = address.get("url") if isinstance(address, dict) else ""
        return str(status.get("url") or address_url or "")

    @staticmethod
    def _owner_labels(owner: dict[str, str]) -> dict[str, str]:
        labels = {_OWNER: "astrolift"}
        for source, target in (
            ("managed_service_id", _OWNER_ID),
            ("organization", "astrolift.io/organization"),
            ("app", "astrolift.io/app"),
            ("environment", "astrolift.io/environment"),
        ):
            if owner.get(source):
                labels[target] = dns_label(owner[source])
        return labels

    @staticmethod
    def _owner_from_labels(resource: dict[str, Any]) -> dict[str, str]:
        labels = dict((resource.get("metadata", {}) or {}).get("labels", {}) or {})
        return {
            "managed_service_id": str(labels.get(_OWNER_ID) or ""),
            "organization": str(labels.get("astrolift.io/organization") or ""),
            "app": str(labels.get("astrolift.io/app") or ""),
            "environment": str(labels.get("astrolift.io/environment") or ""),
        }

    @staticmethod
    def _assert_owned(resource: dict[str, Any]) -> str:
        labels = dict((resource.get("metadata", {}) or {}).get("labels", {}) or {})
        owner = str(labels.get(_OWNER_ID) or "")
        if labels.get(_OWNER) != "astrolift" or not owner:
            raise ValueError("KServe InferenceService is not owned by an Astrolift managed resource")
        return owner

    def _resource(self, parsed: ParsedHandle) -> dict[str, Any] | None:
        resource = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            f"{API_VERSION}/{RESOURCE_KIND}",
            parsed.name,
        )
        if resource is None:
            return None
        if not isinstance(resource, dict):
            raise ValueError("KServe InferenceService response is malformed")
        return resource

    @staticmethod
    def _stub(namespace: str, name: str) -> dict[str, Any]:
        return {
            "apiVersion": API_VERSION,
            "kind": RESOURCE_KIND,
            "metadata": {"name": name, "namespace": namespace},
        }

    def _require_driver(self) -> None:
        if self._config.cluster_driver is None:
            raise ValueError("KServe requires a live cluster driver")

    @staticmethod
    def _parsed(handle: str) -> ParsedHandle:
        parsed = _unpack_handle(handle)
        if parsed.kind != KIND:
            raise ValueError(f"KServe handle kind must be {KIND!r}, got {parsed.kind!r}")
        if parsed.is_legacy:
            raise ValueError("legacy KServe handle has no cluster locator")
        return parsed
