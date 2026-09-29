"""OpenSearch Operator managed search and vector indexes on Kubernetes.

The driver owns an operator-managed OpenSearch cluster plus tenant-scoped
application credentials and OpenSearch security CRs.  The external Astrolift
secrets backend is the credential source of truth; Kubernetes Secrets are
runtime projections used by the operator and can repair a missing external
bundle after a data-retaining teardown.

Variant keys: ``('search', 'opensearch_operator')`` and
``('vector_index', 'opensearch_operator_vector')``.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import re
import secrets
import string
import time
from dataclasses import dataclass
from typing import Any

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op, maybe_heartbeat
from _sdk.k8s_naming import agent_namespace, app_namespace, dns_label
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

SEARCH_KIND = "search"
VECTOR_KIND = "vector_index"
SEARCH_VARIANT = "opensearch_operator"
VECTOR_VARIANT = "opensearch_operator_vector"
PORT = 9200
DEFAULT_VERSION = "3.8.0"
MINIMUM_OPERATOR_VERSION = "3.0.2"
DEFAULT_IMAGE = (
    "opensearchproject/opensearch:3.8.0@sha256:bcc1797519726ceb6d651d4a3e60b7c30da91793914a8dfe75fd441d4f641509"
)
DEFAULT_BOOTSTRAP_IMAGE = (
    "curlimages/curl:8.16.0@sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6"
)
LEGACY_API_VERSION = "opensearch.opster.io/v1"
CURRENT_API_VERSION = "opensearch.org/v1"

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
_QUANTITY = re.compile(r"^[1-9][0-9]*(?:[EPTGMK]i?)?$")
_CPU = re.compile(r"^(?:[1-9][0-9]*m|[1-9][0-9]*(?:\.[0-9]+)?)$")
_INDEX_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_PASSWORD = re.compile(r"^[A-Za-z0-9!#%+,\-.:=@_]{24,256}$")
_SECRET_KEYS = {
    "admin_password",
    "username",
    "password",
    "endpoint",
    "index_prefix",
    "tls_verify",
}
_RESERVED_CONFIG_PREFIXES = (
    "cluster.",
    "discovery.",
    "http.",
    "network.",
    "node.",
    "path.",
    "plugins.security.",
    "transport.",
)
_CONFIG_FIELDS = {
    "additional_config",
    "affinity",
    "cpu_limit",
    "cpu_request",
    "data_replicas",
    "deletion_protection",
    "disk_size",
    "image",
    "image_pull_policy",
    "image_pull_secrets",
    "index_replicas",
    "index_name",
    "index_prefix",
    "manager_cpu_limit",
    "manager_cpu_request",
    "manager_disk_size",
    "manager_memory_limit",
    "manager_memory_request",
    "manager_replicas",
    "memory_limit",
    "memory_request",
    "network_policy_mode",
    "node_annotations",
    "node_selector",
    "plugins_list",
    "priority_class_name",
    "replicas",
    "shards",
    "space_type",
    "storage_class_name",
    "tolerations",
    "topology",
    "vector_dimension",
    "vector_ef_construction",
    "vector_ef_search",
    "vector_engine",
    "vector_field",
    "vector_m",
    "version",
}

_VECTOR_CONFIG_FIELDS = (
    "index_name",
    "index_replicas",
    "shards",
    "space_type",
    "vector_dimension",
    "vector_ef_construction",
    "vector_ef_search",
    "vector_engine",
    "vector_field",
    "vector_m",
)

_SIZE_DEFAULTS: dict[str, dict[str, str | int]] = {
    "small": {
        "topology": "combined",
        "replicas": 3,
        "cpu_request": "500m",
        "cpu_limit": "1",
        "memory_request": "2Gi",
        "memory_limit": "4Gi",
        "disk_size": "20Gi",
    },
    "medium": {
        "topology": "combined",
        "replicas": 3,
        "cpu_request": "1",
        "cpu_limit": "2",
        "memory_request": "4Gi",
        "memory_limit": "8Gi",
        "disk_size": "100Gi",
    },
    "large": {
        "topology": "dedicated",
        "manager_replicas": 3,
        "data_replicas": 3,
        "manager_cpu_request": "500m",
        "manager_cpu_limit": "1",
        "manager_memory_request": "2Gi",
        "manager_memory_limit": "4Gi",
        "manager_disk_size": "20Gi",
        "cpu_request": "2",
        "cpu_limit": "4",
        "memory_request": "8Gi",
        "memory_limit": "16Gi",
        "disk_size": "500Gi",
    },
    "xlarge": {
        "topology": "dedicated",
        "manager_replicas": 3,
        "data_replicas": 5,
        "manager_cpu_request": "1",
        "manager_cpu_limit": "2",
        "manager_memory_request": "4Gi",
        "manager_memory_limit": "8Gi",
        "manager_disk_size": "50Gi",
        "cpu_request": "4",
        "cpu_limit": "8",
        "memory_request": "16Gi",
        "memory_limit": "32Gi",
        "disk_size": "1Ti",
    },
}


@dataclass(frozen=True)
class OpenSearchOperatorConfig:
    """Install-level OpenSearch Operator policy and infrastructure defaults."""

    cluster_driver: Any = None
    secrets_backend: Any = None
    namespace: str | None = None
    storage_class_name: str = ""
    api_version: str = CURRENT_API_VERSION
    operator_namespace: str = "opensearch-operator-system"
    version: str = DEFAULT_VERSION
    image: str = DEFAULT_IMAGE
    bootstrap_image: str = DEFAULT_BOOTSTRAP_IMAGE
    credential_path_prefix: str = "managed/opensearch"
    allow_custom_versions: bool = False
    allow_custom_images: bool = False
    allow_custom_plugins: bool = False
    allow_custom_bootstrap_images: bool = False
    allow_single_node: bool = False
    allow_network_policy_disable: bool = False
    http_tls_secret_name: str = ""
    http_tls_ca_secret_name: str = ""
    http_tls_admin_secret_name: str = ""
    http_tls_admin_dns: tuple[str, ...] = ()
    http_tls_verify: bool = False
    deletion_timeout_seconds: float = 180.0
    deletion_poll_seconds: float = 2.0


class _OpenSearchOperatorDriver(ManagedServiceDriver):
    kind = SEARCH_KIND
    variant = SEARCH_VARIANT

    def __init__(self, *, config: OpenSearchOperatorConfig) -> None:
        self._config = config

    @driver_op(
        cloud="k8s_native",
        driver="opensearch_operator",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._validate_dependencies()
            namespace = self._namespace(spec)
            name = self._name(spec)
            cfg = self._service_config(spec.size, spec.config, spec=spec)
            storage_class = self._storage_class(spec.tenant_cluster_id, cfg)
            existing = self._validate_existing_ownership(spec, namespace, name)
            if existing is not None:
                live = self._live_config(existing)
                self._validate_update(live, cfg)
            bundle = self._credentials_for(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
                index_prefix=str(cfg["index_prefix"]),
            )
        except ValueError as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_opensearch_config"])
        except Exception as exc:
            return ProvisionResult(
                False,
                "",
                f"OpenSearch credential preparation failed: {type(exc).__name__}",
                ["credential_error"],
            )

        handle = _pack_handle(
            kind=self.kind,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=namespace,
            name=name,
        )
        self._ensure_namespace(spec, namespace)
        manifests = self._manifests(
            spec=spec,
            namespace=namespace,
            name=name,
            cfg=cfg,
            storage_class=storage_class,
            bundle=bundle,
        )
        if self.kind == VECTOR_KIND:
            previous_job = self._config.cluster_driver.get_manifest(
                spec.tenant_cluster_id,
                namespace,
                "batch/v1/Job",
                self._bootstrap_job_name(name),
            )
            if previous_job is not None and int((previous_job.get("status", {}) or {}).get("failed") or 0) > 0:
                deleted = self._config.cluster_driver.delete_manifests(
                    spec.tenant_cluster_id,
                    namespace,
                    [self._stub("batch/v1", "Job", self._bootstrap_job_name(name), namespace)],
                )
                if deleted.errors:
                    return ProvisionResult(
                        False,
                        handle,
                        "failed to replace the unsuccessful vector index bootstrap Job",
                        deleted.summary(),
                    )
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            manifests,
        )
        if not result.ok:
            return ProvisionResult(
                False,
                handle,
                "OpenSearch Operator manifests were rejected",
                result.summary(),
            )
        return ProvisionResult(
            True,
            handle,
            f"OpenSearch {self.kind} cluster {name} submitted to the operator",
            ready=False,
        )

    @driver_op(cloud="k8s_native", driver="opensearch_operator")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._validate_dependencies()
            parsed = self._parsed(spec.handle)
            cluster = self._get_cluster(parsed.cluster_id, parsed.namespace, parsed.name)
            if cluster is None:
                raise ValueError("OpenSearch cluster does not exist")
            self._assert_owned(cluster, parsed.name)
            live = self._live_config(cluster)
            requested_size = spec.size or "custom"
            for key in (
                "topology",
                "storage_class_name",
                "index_prefix",
                "index_name",
                "network_policy_mode",
                *_VECTOR_CONFIG_FIELDS,
            ):
                if key in spec.config and str(spec.config[key]) != str(live.get(key)):
                    raise ValueError(f"OpenSearch {key} is immutable and requires replacement")
            raw = {**live, **dict(spec.config or {})}
            cfg = self._service_config(requested_size, raw, spec=None)
            self._validate_update(live, cfg)
            updated = copy.deepcopy(cluster)
            updated["spec"] = self._cluster_spec(
                name=parsed.name,
                cfg=cfg,
                storage_class=str(cfg["storage_class_name"]),
                admin_secret_name=self._admin_secret_name(parsed.name),
            )
            updated.setdefault("metadata", {}).setdefault("annotations", {}).update(self._config_annotations(cfg))
            result = self._config.cluster_driver.apply_manifests(
                parsed.cluster_id,
                parsed.namespace,
                [updated],
            )
            if not result.ok:
                return UpdateResult(False, spec.handle, "OpenSearch update was rejected", result.summary())
            bundle = self._read_valid_bundle(
                self._credential_path(parsed),
                expected_endpoint=self._endpoint(parsed.namespace, parsed.name),
            )
            self._reconcile_bundle_tls(self._credential_path(parsed), bundle)
            return UpdateResult(True, spec.handle, "OpenSearch update submitted to the operator")
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_opensearch_update"])

    @driver_op(
        cloud="k8s_native",
        driver="opensearch_operator",
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
        try:
            self._validate_dependencies()
            parsed = self._parsed(spec.handle)
            cluster = self._get_cluster(parsed.cluster_id, parsed.namespace, parsed.name)
            protection = True
            if cluster is not None:
                self._assert_owned(cluster, parsed.name)
                protection = (
                    str(
                        ((cluster.get("metadata", {}) or {}).get("annotations", {}) or {}).get(
                            "astrolift.io/deletion-protection",
                            "true",
                        )
                    ).lower()
                    == "true"
                )
            elif "deletion_protection" in spec.config:
                protection = bool(spec.config["deletion_protection"])
            if delete_data and protection and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "destructive OpenSearch deletion requires force_destroy while deletion protection is enabled",
                    ["deletion_protection_enabled"],
                    retryable=False,
                )

            stubs = self._owned_stubs(parsed.namespace, parsed.name, cluster)
            if cluster is not None:
                stubs.append(self._stub(str(cluster["apiVersion"]), "OpenSearchCluster", parsed.name, parsed.namespace))
            result = self._config.cluster_driver.delete_manifests(
                parsed.cluster_id,
                parsed.namespace,
                stubs,
            )
            if result.errors:
                return DeprovisionResult(False, spec.handle, "OpenSearch teardown failed", result.summary())
            if cluster is not None and not self._wait_absent(parsed.cluster_id, parsed.namespace, parsed.name):
                return DeprovisionResult(False, spec.handle, "OpenSearchCluster deletion is still in progress")

            if delete_data:
                pvc_stubs = self._pvc_stubs(parsed.namespace, parsed.name, cluster)
                secret_stubs = [
                    self._stub("v1", "Secret", self._admin_secret_name(parsed.name), parsed.namespace),
                    self._stub("v1", "Secret", self._app_secret_name(parsed.name), parsed.namespace),
                ]
                cleanup = self._config.cluster_driver.delete_manifests(
                    parsed.cluster_id,
                    parsed.namespace,
                    [*pvc_stubs, *secret_stubs],
                )
                if cleanup.errors:
                    return DeprovisionResult(False, spec.handle, "OpenSearch data cleanup failed", cleanup.summary())
                self._config.secrets_backend.delete(self._credential_path(parsed))
                return DeprovisionResult(True, spec.handle, "OpenSearch workload, data, and credentials deleted")

            return DeprovisionResult(
                True,
                spec.handle,
                "OpenSearch workload deleted; PVCs and credential copies retained",
            )
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_opensearch_teardown"], retryable=False)

    @driver_op(cloud="k8s_native", driver="opensearch_operator")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._validate_dependencies()
            parsed = self._parsed(handle.handle)
            cluster = self._get_cluster(parsed.cluster_id, parsed.namespace, parsed.name)
            if cluster is None:
                retained = self._config.cluster_driver.get_manifest(
                    parsed.cluster_id,
                    parsed.namespace,
                    "v1/Secret",
                    self._app_secret_name(parsed.name),
                )
                message = "OpenSearch cluster is absent"
                if retained is not None:
                    message += "; data and credentials may be retained"
                return ServiceStatus(handle.handle, "deprovisioned", message)
            self._assert_owned(cluster, parsed.name)
            status = cluster.get("status", {}) or {}
            phase = str(status.get("phase") or "").lower()
            health = str(status.get("health") or "").lower()
            initialized = status.get("initialized") is True
            if phase in {"error", "failed"} or health == "red":
                return ServiceStatus(
                    handle.handle,
                    "error",
                    f"operator phase={phase or 'unknown'} health={health or 'unknown'}",
                )
            if initialized and health in {"green", "yellow"}:
                access_status = self._application_access_status(parsed)
                if access_status is not None:
                    state, message = access_status
                    return ServiceStatus(handle.handle, state, message)
                if parsed.kind == VECTOR_KIND:
                    bootstrap = self._config.cluster_driver.get_manifest(
                        parsed.cluster_id,
                        parsed.namespace,
                        "batch/v1/Job",
                        self._bootstrap_job_name(parsed.name),
                    )
                    if bootstrap is None:
                        return ServiceStatus(handle.handle, "error", "vector index bootstrap Job is absent")
                    job_status = bootstrap.get("status", {}) or {}
                    if int(job_status.get("failed") or 0) > 0:
                        return ServiceStatus(handle.handle, "error", "vector index bootstrap Job failed")
                    if int(job_status.get("succeeded") or 0) < 1:
                        return ServiceStatus(handle.handle, "provisioning", "vector index bootstrap is pending")
                return ServiceStatus(handle.handle, "available", f"OpenSearch health is {health}")
            if phase in {"upgrading", "rollingrestart", "scaling"}:
                return ServiceStatus(handle.handle, "updating", f"operator phase={phase}")
            return ServiceStatus(handle.handle, "provisioning", f"operator phase={phase or 'pending'}")
        except ValueError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))

    def _application_access_status(self, parsed: Any) -> tuple[str, str] | None:
        resources = (
            ("OpensearchRole", self._role_name(parsed.name)),
            ("OpensearchUser", self._username(parsed.name)),
            ("OpensearchUserRoleBinding", self._binding_name(parsed.name)),
        )
        pending: list[str] = []
        for kind, name in resources:
            manifest = self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                f"{self._config.api_version}/{kind}",
                name,
            )
            if manifest is None:
                return "error", f"OpenSearch application access resource {kind}/{name} is absent"
            status = manifest.get("status", {}) or {}
            state = str(status.get("state") or "PENDING").upper()
            if state == "ERROR":
                reason = " ".join(str(status.get("reason") or "").split())[:256]
                suffix = f": {reason}" if reason else ""
                return "error", f"OpenSearch application access resource {kind}/{name} failed{suffix}"
            if state != "CREATED":
                pending.append(f"{kind}/{name}")
        if pending:
            return "provisioning", f"OpenSearch application access is pending ({', '.join(pending)})"
        return None

    @driver_op(cloud="k8s_native", driver="opensearch_operator")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        del config
        self._validate_dependencies()
        parsed = self._parsed(handle.handle)
        bundle = self._read_valid_bundle(
            self._credential_path(parsed),
            expected_endpoint=self._endpoint(parsed.namespace, parsed.name),
        )
        expected_tls_verify = str(bool(self._config.http_tls_verify)).lower()
        if bundle["tls_verify"] != expected_tls_verify:
            raise ValueError(
                "OpenSearch credential bundle TLS policy is stale; reconcile the managed service before binding"
            )
        path = self._credential_path(parsed)
        if parsed.kind == VECTOR_KIND:
            return Binding(
                env_vars={
                    "VECTOR_ENDPOINT": ValueRef(literal=bundle["endpoint"]),
                    "VECTOR_INDEX_NAME": ValueRef(literal=bundle["index_prefix"].removesuffix("-")),
                    "VECTOR_USERNAME": ValueRef(literal=bundle["username"]),
                    "VECTOR_PASSWORD": ValueRef(secret_ref=f"{path}#password"),
                    "VECTOR_TLS_VERIFY": ValueRef(literal=bundle["tls_verify"]),
                },
                notes="OpenSearch k-NN endpoint; basic-auth password is an external secret reference.",
            )
        return Binding(
            env_vars={
                "SEARCH_ENDPOINT": ValueRef(literal=bundle["endpoint"]),
                "SEARCH_USER": ValueRef(literal=bundle["username"]),
                "SEARCH_PASSWORD": ValueRef(secret_ref=f"{path}#password"),
                "SEARCH_INDEX_PREFIX": ValueRef(literal=bundle["index_prefix"]),
                "SEARCH_TLS_VERIFY": ValueRef(literal=bundle["tls_verify"]),
            },
            notes="OpenSearch HTTPS endpoint; password is an external secret reference.",
        )

    @driver_op(cloud="k8s_native", driver="opensearch_operator")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise UnsupportedOperationError(
            "OpenSearch snapshots require an explicitly configured repository and are not yet "
            "exposed by this preview driver"
        )

    @driver_op(cloud="k8s_native", driver="opensearch_operator")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        return ProvisionResult(
            False,
            "",
            "OpenSearch restore is unavailable until repository-backed snapshots are implemented",
            ["unsupported_operation"],
        )

    @driver_op(cloud="k8s_native", driver="opensearch_operator", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "topology": {"type": "string", "enum": ["combined", "dedicated"]},
            "replicas": {"type": "integer", "minimum": 1, "maximum": 9},
            "manager_replicas": {"type": "integer", "minimum": 3, "maximum": 9},
            "data_replicas": {"type": "integer", "minimum": 1, "maximum": 50},
            "version": {"type": "string"},
            "image": {"type": "string"},
            "image_pull_policy": {"type": "string", "enum": ["Always", "IfNotPresent", "Never"]},
            "image_pull_secrets": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "storage_class_name": {"type": "string"},
            "disk_size": {"type": "string"},
            "cpu_request": {"type": "string"},
            "cpu_limit": {"type": "string"},
            "memory_request": {"type": "string"},
            "memory_limit": {"type": "string"},
            "manager_disk_size": {"type": "string"},
            "manager_cpu_request": {"type": "string"},
            "manager_cpu_limit": {"type": "string"},
            "manager_memory_request": {"type": "string"},
            "manager_memory_limit": {"type": "string"},
            "index_prefix": {"type": "string"},
            "index_name": {"type": "string"},
            "plugins_list": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "additional_config": {"type": "object", "additionalProperties": {"type": "string"}},
            "node_selector": {"type": "object", "additionalProperties": {"type": "string"}},
            "tolerations": {"type": "array", "items": {"type": "object"}},
            "affinity": {"type": "object"},
            "node_annotations": {"type": "object", "additionalProperties": {"type": "string"}},
            "priority_class_name": {"type": "string"},
            "network_policy_mode": {
                "type": "string",
                "enum": ["managed_namespaces", "same_namespace", "disabled"],
                "default": "managed_namespaces",
            },
            "deletion_protection": {"type": "boolean", "default": True},
        }
        required: list[str] = []
        if self.kind == VECTOR_KIND:
            properties.update(
                {
                    "vector_dimension": {"type": "integer", "minimum": 1, "maximum": 16000},
                    "vector_field": {"type": "string", "default": "vector"},
                    "vector_engine": {"type": "string", "enum": ["lucene", "faiss"], "default": "lucene"},
                    "space_type": {
                        "type": "string",
                        "enum": ["l2", "cosinesimil", "innerproduct"],
                        "default": "cosinesimil",
                    },
                    "vector_m": {"type": "integer", "minimum": 2, "maximum": 100, "default": 16},
                    "vector_ef_construction": {
                        "type": "integer",
                        "minimum": 2,
                        "maximum": 1000,
                        "default": 100,
                    },
                    "vector_ef_search": {
                        "type": "integer",
                        "minimum": 2,
                        "maximum": 1000,
                        "default": 100,
                    },
                    "shards": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 1},
                    "index_replicas": {"type": "integer", "minimum": 0, "maximum": 20, "default": 1},
                }
            )
            required.append("vector_dimension")
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": required,
        }

    @driver_op(cloud="k8s_native", driver="opensearch_operator", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        if self.kind == VECTOR_KIND:
            return BindingSchema(
                env_vars={
                    "VECTOR_ENDPOINT": "OpenSearch HTTPS endpoint",
                    "VECTOR_INDEX_NAME": "Owned k-NN index name",
                    "VECTOR_USERNAME": "Least-privilege OpenSearch user",
                    "VECTOR_PASSWORD": "External secret reference to the application password",
                    "VECTOR_TLS_VERIFY": "Whether clients must verify the HTTP certificate",
                }
            )
        return BindingSchema(
            env_vars={
                "SEARCH_ENDPOINT": "OpenSearch HTTPS endpoint",
                "SEARCH_USER": "Least-privilege OpenSearch user",
                "SEARCH_PASSWORD": "External secret reference to the application password",
                "SEARCH_INDEX_PREFIX": "Owned index-name prefix",
                "SEARCH_TLS_VERIFY": "Whether clients must verify the HTTP certificate",
            }
        )

    def editable_fields(self) -> list[str]:
        return [
            "version",
            "image",
            "image_pull_policy",
            "image_pull_secrets",
            "replicas",
            "manager_replicas",
            "data_replicas",
            "cpu_request",
            "cpu_limit",
            "memory_request",
            "memory_limit",
            "manager_cpu_request",
            "manager_cpu_limit",
            "manager_memory_request",
            "manager_memory_limit",
            "disk_size",
            "manager_disk_size",
            "node_selector",
            "tolerations",
            "affinity",
            "node_annotations",
            "priority_class_name",
            "additional_config",
            "deletion_protection",
        ]

    def _validate_dependencies(self) -> None:
        if self._config.cluster_driver is None:
            raise ValueError("OpenSearch Operator requires a cluster_driver")
        if self._config.secrets_backend is None:
            raise ValueError("OpenSearch Operator requires an external secrets backend")
        if self._config.api_version != CURRENT_API_VERSION:
            raise ValueError("OpenSearch Operator 3.x requires the opensearch.org/v1 API group")
        if "@sha256:" not in self._config.bootstrap_image:
            raise ValueError("opensearch_bootstrap_image must be pinned by sha256 digest")
        if self._config.bootstrap_image != DEFAULT_BOOTSTRAP_IMAGE and not self._config.allow_custom_bootstrap_images:
            raise ValueError("custom OpenSearch bootstrap images require opensearch_allow_custom_bootstrap_images=true")
        if self._config.http_tls_ca_secret_name and not self._config.http_tls_secret_name:
            raise ValueError("opensearch_http_tls_ca_secret_name requires opensearch_http_tls_secret_name")
        if (
            self._config.http_tls_admin_secret_name or self._config.http_tls_admin_dns
        ) and not self._config.http_tls_secret_name:
            raise ValueError("OpenSearch HTTP TLS admin certificate settings require a custom HTTP TLS secret")
        if self._config.http_tls_verify and not self._config.http_tls_secret_name:
            raise ValueError("verified OpenSearch HTTP TLS requires an operator-provided certificate secret")
        for value, field in (
            (self._config.operator_namespace, "operator_namespace"),
            (self._config.http_tls_secret_name, "http_tls_secret_name"),
            (self._config.http_tls_ca_secret_name, "http_tls_ca_secret_name"),
            (self._config.http_tls_admin_secret_name, "http_tls_admin_secret_name"),
        ):
            if value and (len(value) > 63 or not _DNS_LABEL.fullmatch(value)):
                raise ValueError(f"OpenSearch {field} must be a Kubernetes DNS label")
        prefix = self._config.credential_path_prefix.strip("/")
        if not prefix or "#" in prefix or any(part in {"", ".", ".."} for part in prefix.split("/")):
            raise ValueError("opensearch_credential_path_prefix must be a safe non-empty secret path")
        if self._config.http_tls_secret_name and not self._config.http_tls_admin_secret_name:
            raise ValueError("custom OpenSearch HTTP TLS requires opensearch_http_tls_admin_secret_name")
        if self._config.http_tls_secret_name and not self._config.http_tls_admin_dns:
            raise ValueError("custom OpenSearch HTTP TLS requires at least one admin certificate DN")
        if any(
            not value or len(value) > 512 or any(char in value for char in ("\x00", "\r", "\n"))
            for value in self._config.http_tls_admin_dns
        ):
            raise ValueError("opensearch_http_tls_admin_dns contains an invalid certificate DN")

    def _service_config(
        self,
        size: str,
        raw: dict[str, Any],
        *,
        spec: ProvisionSpec | None,
    ) -> dict[str, Any]:
        if size not in {*_SIZE_DEFAULTS, "custom"}:
            raise ValueError(f"unsupported OpenSearch size {size!r}")
        values = dict(raw or {})
        declared_size = values.pop("size", None)
        if declared_size is not None and str(declared_size) != size:
            raise ValueError("config.size disagrees with ProvisionSpec size")
        cfg = {**dict(_SIZE_DEFAULTS.get(size) or {}), **values}
        if size == "custom":
            topology = str(cfg.get("topology") or "")
            base = {"cpu_request", "cpu_limit", "memory_request", "memory_limit", "disk_size"}
            required = base | ({"replicas"} if topology == "combined" else {"manager_replicas", "data_replicas"})
            if topology == "dedicated":
                required |= {
                    "manager_cpu_request",
                    "manager_cpu_limit",
                    "manager_memory_request",
                    "manager_memory_limit",
                    "manager_disk_size",
                }
            missing = sorted(required - cfg.keys())
            if missing:
                raise ValueError(f"custom OpenSearch size requires {', '.join(missing)}")
        cfg.setdefault("version", self._config.version)
        cfg.setdefault("image", self._config.image)
        cfg.setdefault("image_pull_policy", "IfNotPresent")
        cfg.setdefault("storage_class_name", self._config.storage_class_name)
        cfg.setdefault("plugins_list", [])
        cfg.setdefault("additional_config", {})
        cfg.setdefault("node_selector", {})
        cfg.setdefault("tolerations", [])
        cfg.setdefault("affinity", {})
        cfg.setdefault("node_annotations", {})
        cfg.setdefault("image_pull_secrets", [])
        # A pull secret is a registry credential; in a namespace shared between
        # tenants its name can resolve to another tenant's (#1959, #2087).
        refuse_shared_namespace_secrets(
            self._config.namespace,
            None,
            extra=[str(name) for name in cfg["image_pull_secrets"] or []],
        )
        cfg.setdefault("priority_class_name", "")
        cfg.setdefault("network_policy_mode", "managed_namespaces")
        cfg.setdefault("deletion_protection", True)
        if "index_prefix" not in cfg:
            if spec is None:
                raise ValueError("live OpenSearch config is missing index_prefix")
            cfg["index_prefix"] = self._default_index_prefix(spec)
        if self.kind == VECTOR_KIND:
            cfg.setdefault("index_name", str(cfg["index_prefix"]).removesuffix("-"))
            cfg["index_prefix"] = f"{str(cfg['index_name']).removesuffix('-')}-"
            cfg.setdefault("vector_field", "vector")
            cfg.setdefault("vector_engine", "lucene")
            cfg.setdefault("space_type", "cosinesimil")
            cfg.setdefault("vector_m", 16)
            cfg.setdefault("vector_ef_construction", 100)
            cfg.setdefault("vector_ef_search", 100)
            cfg.setdefault("shards", 1)
            cfg.setdefault("index_replicas", 1)
        self._validate_cfg(cfg)
        return cfg

    def _validate_cfg(self, cfg: dict[str, Any]) -> None:
        unknown = sorted(set(cfg) - _CONFIG_FIELDS)
        if unknown:
            raise ValueError(f"unsupported OpenSearch config fields: {', '.join(unknown)}")
        topology = str(cfg.get("topology"))
        if topology not in {"combined", "dedicated"}:
            raise ValueError("OpenSearch topology must be combined or dedicated")
        if topology == "combined":
            replicas = int(cfg.get("replicas", 0))
            if replicas == 1 and not self._config.allow_single_node:
                raise ValueError("single-node OpenSearch requires opensearch_allow_single_node=true")
            if replicas not in {1, 3, 5, 7, 9}:
                raise ValueError("combined OpenSearch replicas must be 1 or an odd value from 3 through 9")
        else:
            managers = int(cfg.get("manager_replicas", 0))
            data = int(cfg.get("data_replicas", 0))
            if managers not in {3, 5, 7, 9} or not 1 <= data <= 50:
                raise ValueError("dedicated OpenSearch requires 3/5/7/9 managers and 1-50 data nodes")
        version = str(cfg.get("version"))
        if not _VERSION.fullmatch(version):
            raise ValueError("OpenSearch version must be major.minor.patch")
        if version != self._config.version and not self._config.allow_custom_versions:
            raise ValueError("custom OpenSearch versions require opensearch_allow_custom_versions=true")
        image = str(cfg.get("image"))
        if image != self._config.image and not self._config.allow_custom_images:
            raise ValueError("custom OpenSearch images require opensearch_allow_custom_images=true")
        if "@sha256:" not in image:
            raise ValueError("OpenSearch image must be pinned by sha256 digest")
        if str(cfg.get("image_pull_policy")) not in {"Always", "IfNotPresent", "Never"}:
            raise ValueError("OpenSearch image_pull_policy is invalid")
        for key in (
            "disk_size",
            "memory_request",
            "memory_limit",
        ):
            if not _QUANTITY.fullmatch(str(cfg.get(key, ""))):
                raise ValueError(f"OpenSearch {key} must be a positive Kubernetes quantity")
        for key in ("cpu_request", "cpu_limit"):
            if not _CPU.fullmatch(str(cfg.get(key, ""))):
                raise ValueError(f"OpenSearch {key} must be a positive Kubernetes CPU quantity")
        if topology == "dedicated":
            for key in ("manager_disk_size", "manager_memory_request", "manager_memory_limit"):
                if not _QUANTITY.fullmatch(str(cfg.get(key, ""))):
                    raise ValueError(f"OpenSearch {key} must be a positive Kubernetes quantity")
            for key in ("manager_cpu_request", "manager_cpu_limit"):
                if not _CPU.fullmatch(str(cfg.get(key, ""))):
                    raise ValueError(f"OpenSearch {key} must be a positive Kubernetes CPU quantity")
        if self._bytes(str(cfg["memory_request"])) < 2 * 1024**3:
            raise ValueError("OpenSearch data nodes require at least 2Gi memory request")
        if self._bytes(str(cfg["memory_limit"])) < self._bytes(str(cfg["memory_request"])):
            raise ValueError("OpenSearch memory_limit cannot be smaller than memory_request")
        storage_class = str(cfg.get("storage_class_name") or "")
        if storage_class and (len(storage_class) > 253 or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", storage_class)):
            raise ValueError("OpenSearch storage_class_name is invalid")
        index_prefix = str(cfg.get("index_prefix") or "").removesuffix("-")
        if not _INDEX_TOKEN.fullmatch(index_prefix):
            raise ValueError("OpenSearch index_prefix is invalid")
        if self.kind == VECTOR_KIND and not _INDEX_TOKEN.fullmatch(str(cfg.get("index_name") or "")):
            raise ValueError("OpenSearch vector index_name is invalid")
        if self.kind == VECTOR_KIND:
            dimension = cfg.get("vector_dimension")
            if isinstance(dimension, bool) or not isinstance(dimension, int) or not 1 <= dimension <= 16000:
                raise ValueError("OpenSearch vector_dimension must be an integer from 1 through 16000")
            if not _INDEX_TOKEN.fullmatch(str(cfg.get("vector_field") or "")):
                raise ValueError("OpenSearch vector_field is invalid")
            if str(cfg.get("vector_engine")) not in {"lucene", "faiss"}:
                raise ValueError("OpenSearch vector_engine must be lucene or faiss")
            if str(cfg.get("space_type")) not in {"l2", "cosinesimil", "innerproduct"}:
                raise ValueError("OpenSearch space_type is unsupported")
            for key, minimum, maximum in (
                ("vector_m", 2, 100),
                ("vector_ef_construction", 2, 1000),
                ("vector_ef_search", 2, 1000),
                ("shards", 1, 1000),
                ("index_replicas", 0, 20),
            ):
                value = cfg.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                    raise ValueError(f"OpenSearch {key} must be an integer from {minimum} through {maximum}")
        plugins = list(cfg.get("plugins_list") or [])
        if plugins and not self._config.allow_custom_plugins:
            raise ValueError("custom OpenSearch plugins require opensearch_allow_custom_plugins=true")
        if any(not isinstance(item, str) or not item or "\x00" in item or "\n" in item for item in plugins):
            raise ValueError("OpenSearch plugins_list contains an invalid entry")
        additional = dict(cfg.get("additional_config") or {})
        for key, value in additional.items():
            lowered = str(key).lower()
            if any(lowered.startswith(prefix) for prefix in _RESERVED_CONFIG_PREFIXES):
                raise ValueError(f"OpenSearch additional_config cannot override reserved setting {key!r}")
            if not key or any(char in str(value) for char in ("\x00", "\r", "\n")) or len(str(value)) > 1024:
                raise ValueError(f"OpenSearch additional_config value for {key!r} is invalid")
        mode = str(cfg.get("network_policy_mode"))
        if mode not in {"managed_namespaces", "same_namespace", "disabled"}:
            raise ValueError("OpenSearch network_policy_mode is invalid")
        if mode == "disabled" and not self._config.allow_network_policy_disable:
            raise ValueError("disabling NetworkPolicy requires opensearch_allow_network_policy_disable=true")

    def _storage_class(self, cluster_id: str, cfg: dict[str, Any]) -> str:
        requested = str(cfg.get("storage_class_name") or "")
        classes = self._config.cluster_driver.list_storage_classes(cluster_id)
        if requested:
            if requested not in {str(row.name) for row in classes}:
                raise ValueError(f"StorageClass {requested!r} is not installed on cluster {cluster_id!r}")
            cfg["storage_class_name"] = requested
            return requested
        defaults = [str(row.name) for row in classes if row.is_default]
        if len(defaults) != 1:
            raise ValueError("OpenSearch requires storage_class_name when the cluster lacks one unambiguous default")
        cfg["storage_class_name"] = defaults[0]
        return defaults[0]

    def _manifests(
        self,
        *,
        spec: ProvisionSpec,
        namespace: str,
        name: str,
        cfg: dict[str, Any],
        storage_class: str,
        bundle: dict[str, str],
    ) -> list[dict[str, Any]]:
        labels = self._labels(spec, name)
        admin_secret = self._admin_secret(namespace, name, labels, bundle)
        app_secret = self._app_secret(namespace, name, labels, bundle)
        cluster = {
            "apiVersion": self._config.api_version,
            "kind": "OpenSearchCluster",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": {
                    "astrolift.io/deletion-protection": str(bool(cfg["deletion_protection"])).lower(),
                    "astrolift.io/index-prefix": str(cfg["index_prefix"]),
                    **self._config_annotations(cfg),
                },
            },
            "spec": self._cluster_spec(
                name=name,
                cfg=cfg,
                storage_class=storage_class,
                admin_secret_name=self._admin_secret_name(name),
            ),
        }
        security = self._security_resources(namespace, name, labels, bundle)
        policy = self._network_policy(namespace, name, labels, cfg)
        rows = [admin_secret, app_secret, cluster, *security]
        if policy is not None:
            rows.append(policy)
        if self.kind == VECTOR_KIND:
            rows.append(self._vector_bootstrap_job(namespace, name, labels, cfg, bundle))
        return rows

    def _cluster_spec(
        self,
        *,
        name: str,
        cfg: dict[str, Any],
        storage_class: str,
        admin_secret_name: str,
    ) -> dict[str, Any]:
        http_tls: dict[str, Any]
        if self._config.http_tls_secret_name:
            http_tls = {
                "generate": False,
                "secret": {"name": self._config.http_tls_secret_name},
                "adminDn": list(self._config.http_tls_admin_dns),
            }
            if self._config.http_tls_ca_secret_name:
                http_tls["caSecret"] = {"name": self._config.http_tls_ca_secret_name}
        else:
            http_tls = {"generate": True, "rotateDaysBeforeExpiry": 30}
        security_config: dict[str, Any] = {"adminCredentialsSecret": {"name": admin_secret_name}}
        if self._config.http_tls_admin_secret_name:
            security_config["adminSecret"] = {"name": self._config.http_tls_admin_secret_name}
        general: dict[str, Any] = {
            "serviceName": name,
            "version": str(cfg["version"]),
            "image": str(cfg["image"]),
            "imagePullPolicy": str(cfg["image_pull_policy"]),
            "httpPort": PORT,
            "drainDataNodes": True,
            "setVMMaxMapCount": False,
            "additionalConfig": dict(cfg.get("additional_config") or {}),
            "podSecurityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "privileged": False,
                "capabilities": {"drop": ["ALL"]},
            },
        }
        if cfg.get("plugins_list"):
            general["pluginsList"] = list(cfg["plugins_list"])
        if cfg.get("image_pull_secrets"):
            general["imagePullSecrets"] = [{"name": str(value)} for value in cfg["image_pull_secrets"]]
        return {
            "general": general,
            "security": {
                "config": security_config,
                "tls": {
                    "http": http_tls,
                    "transport": {
                        "generate": True,
                        "perNode": True,
                        "rotateDaysBeforeExpiry": 30,
                    },
                },
            },
            "dashboards": {"enable": False},
            "nodePools": self._node_pools(name, cfg, storage_class),
        }

    def _node_pools(
        self,
        name: str,
        cfg: dict[str, Any],
        storage_class: str,
    ) -> list[dict[str, Any]]:
        common = {
            "nodeSelector": dict(cfg.get("node_selector") or {}),
            "tolerations": copy.deepcopy(cfg.get("tolerations") or []),
            "affinity": copy.deepcopy(cfg.get("affinity") or {}),
            "annotations": dict(cfg.get("node_annotations") or {}),
            "priorityClassName": str(cfg.get("priority_class_name") or ""),
            "pdb": {"enable": True, "maxUnavailable": 1},
            "persistence": {"pvc": {"storageClass": storage_class, "accessModes": ["ReadWriteOnce"]}},
            "labels": {
                "astrolift.io/managed-by": "astrolift",
                "astrolift.io/resource": name,
            },
        }
        data = {
            **copy.deepcopy(common),
            "component": "nodes" if cfg["topology"] == "combined" else "data",
            "replicas": int(cfg["replicas"] if cfg["topology"] == "combined" else cfg["data_replicas"]),
            "roles": ["cluster_manager", "data", "ingest"] if cfg["topology"] == "combined" else ["data", "ingest"],
            "diskSize": str(cfg["disk_size"]),
            "resources": {
                "requests": {"cpu": str(cfg["cpu_request"]), "memory": str(cfg["memory_request"])},
                "limits": {"cpu": str(cfg["cpu_limit"]), "memory": str(cfg["memory_limit"])},
            },
        }
        if cfg["topology"] == "combined":
            return [data]
        managers = {
            **copy.deepcopy(common),
            "component": "managers",
            "replicas": int(cfg["manager_replicas"]),
            "roles": ["cluster_manager"],
            "diskSize": str(cfg["manager_disk_size"]),
            "resources": {
                "requests": {
                    "cpu": str(cfg["manager_cpu_request"]),
                    "memory": str(cfg["manager_memory_request"]),
                },
                "limits": {
                    "cpu": str(cfg["manager_cpu_limit"]),
                    "memory": str(cfg["manager_memory_limit"]),
                },
            },
        }
        return [managers, data]

    def _security_resources(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        bundle: dict[str, str],
    ) -> list[dict[str, Any]]:
        api_version = self._config.api_version
        role_name = self._role_name(name)
        username = bundle["username"]
        index_pattern = (
            bundle["index_prefix"].removesuffix("-") if self.kind == VECTOR_KIND else f"{bundle['index_prefix']}*"
        )
        return [
            {
                "apiVersion": api_version,
                "kind": "OpensearchRole",
                "metadata": {"name": role_name, "namespace": namespace, "labels": labels},
                "spec": {
                    "opensearchCluster": {"name": name},
                    "clusterPermissions": [
                        "indices:data/write/bulk*",
                        "indices:data/read/mget",
                        "indices:data/read/msearch",
                        "indices:data/read/mtv",
                        "indices:data/read/scroll",
                    ],
                    "indexPermissions": [
                        {
                            "indexPatterns": [index_pattern],
                            "allowedActions": [
                                "crud",
                                "create_index",
                                "manage_aliases",
                                "indices:admin/exists",
                            ],
                        }
                    ],
                },
            },
            {
                "apiVersion": api_version,
                "kind": "OpensearchUser",
                "metadata": {"name": username, "namespace": namespace, "labels": labels},
                "spec": {
                    "opensearchCluster": {"name": name},
                    "passwordFrom": {"name": self._app_secret_name(name), "key": username},
                },
            },
            {
                "apiVersion": api_version,
                "kind": "OpensearchUserRoleBinding",
                "metadata": {"name": self._binding_name(name), "namespace": namespace, "labels": labels},
                "spec": {
                    "opensearchCluster": {"name": name},
                    "users": [username],
                    "roles": [role_name],
                },
            },
        ]

    def _network_policy(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        cfg: dict[str, Any],
    ) -> dict[str, Any] | None:
        mode = str(cfg["network_policy_mode"])
        if mode == "disabled":
            return None
        organization = labels["astrolift.io/organization"]
        clients: list[dict[str, Any]] = [{"podSelector": {}}]
        if mode == "managed_namespaces":
            clients.extend(
                [
                    {
                        "namespaceSelector": {
                            "matchLabels": {
                                "astrolift.io/managed-by": "astrolift",
                                "astrolift.io/organization": organization,
                            }
                        }
                    },
                    {
                        "namespaceSelector": {
                            "matchLabels": {
                                "kubernetes.io/metadata.name": agent_namespace(organization),
                            }
                        }
                    },
                ]
            )
        clients.append(
            {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": self._config.operator_namespace}}}
        )
        cluster_label = (
            "opster.io/opensearch-cluster"
            if self._config.api_version == LEGACY_API_VERSION
            else "opensearch.org/opensearch-cluster"
        )
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": self._policy_name(name), "namespace": namespace, "labels": labels},
            "spec": {
                "podSelector": {"matchLabels": {cluster_label: name}},
                "policyTypes": ["Ingress"],
                "ingress": [
                    {"from": clients, "ports": [{"protocol": "TCP", "port": PORT}]},
                    {
                        "from": [{"podSelector": {"matchLabels": {cluster_label: name}}}],
                        "ports": [{"protocol": "TCP", "port": 9300}],
                    },
                ],
            },
        }

    def _credentials_for(
        self,
        *,
        cluster_id: str,
        namespace: str,
        name: str,
        index_prefix: str,
    ) -> dict[str, str]:
        path = self._credential_path_parts(cluster_id, namespace, name)
        current = self._config.secrets_backend.get(path)
        endpoint = self._endpoint(namespace, name)
        if current is not None:
            bundle = self._read_valid_bundle(path, expected_endpoint=endpoint)
            if bundle["index_prefix"] != index_prefix:
                raise ValueError("existing OpenSearch credentials disagree with immutable index prefix")
            return self._reconcile_bundle_tls(path, bundle)
        admin_secret = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            "v1/Secret",
            self._admin_secret_name(name),
        )
        app_secret = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            "v1/Secret",
            self._app_secret_name(name),
        )
        if admin_secret is not None or app_secret is not None:
            if admin_secret is None or app_secret is None:
                raise ValueError("retained OpenSearch credential Secrets are incomplete")
            username = self._username(name)
            bundle = {
                "admin_password": self._decode_secret_field(admin_secret, "password"),
                "username": username,
                "password": self._decode_secret_field(app_secret, username),
                "endpoint": endpoint,
                "index_prefix": index_prefix,
                "tls_verify": str(bool(self._config.http_tls_verify)).lower(),
            }
            bundle = self._validate_bundle(bundle, expected_endpoint=endpoint)
            self._config.secrets_backend.upsert(path, bundle)
            return bundle
        bundle = {
            "admin_password": self._password(),
            "username": self._username(name),
            "password": self._password(),
            "endpoint": endpoint,
            "index_prefix": index_prefix,
            "tls_verify": str(bool(self._config.http_tls_verify)).lower(),
        }
        bundle = self._validate_bundle(bundle, expected_endpoint=endpoint)
        self._config.secrets_backend.upsert(path, bundle)
        return self._read_valid_bundle(path, expected_endpoint=endpoint)

    def _read_valid_bundle(self, path: str, *, expected_endpoint: str) -> dict[str, str]:
        value = self._config.secrets_backend.get(path)
        if value is None:
            raise ValueError(f"OpenSearch credential bundle {path!r} does not exist")
        return self._validate_bundle(value, expected_endpoint=expected_endpoint)

    def _reconcile_bundle_tls(self, path: str, bundle: dict[str, str]) -> dict[str, str]:
        expected_tls_verify = str(bool(self._config.http_tls_verify)).lower()
        if bundle["tls_verify"] == expected_tls_verify:
            return bundle
        updated = {**bundle, "tls_verify": expected_tls_verify}
        self._config.secrets_backend.upsert(path, updated)
        return self._read_valid_bundle(path, expected_endpoint=bundle["endpoint"])

    def _validate_bundle(self, raw: Any, *, expected_endpoint: str) -> dict[str, str]:
        if not isinstance(raw, dict):
            raise ValueError("OpenSearch credential bundle must be an object")
        missing = sorted(_SECRET_KEYS - raw.keys())
        unknown = sorted(set(raw) - _SECRET_KEYS)
        if missing:
            raise ValueError(f"OpenSearch credential bundle is missing {', '.join(missing)}")
        if unknown:
            raise ValueError(f"OpenSearch credential bundle has unsupported keys: {', '.join(unknown)}")
        bundle = {str(key): str(value) for key, value in raw.items()}
        if bundle["endpoint"] != expected_endpoint:
            raise ValueError("OpenSearch credential bundle endpoint disagrees with its owned Service")
        if not _DNS_LABEL.fullmatch(bundle["username"]):
            raise ValueError("OpenSearch credential bundle username is invalid")
        if not _PASSWORD.fullmatch(bundle["admin_password"]) or not _PASSWORD.fullmatch(bundle["password"]):
            raise ValueError("OpenSearch credential bundle password contains unsupported characters or length")
        if not _INDEX_TOKEN.fullmatch(bundle["index_prefix"].removesuffix("-")):
            raise ValueError("OpenSearch credential bundle index prefix is invalid")
        if bundle["tls_verify"] not in {"true", "false"}:
            raise ValueError("OpenSearch credential bundle TLS verification flag is invalid")
        return bundle

    def _admin_secret(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        bundle: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": self._admin_secret_name(name), "namespace": namespace, "labels": labels},
            "type": "Opaque",
            "stringData": {"username": "admin", "password": bundle["admin_password"]},
        }

    def _app_secret(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        bundle: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": self._app_secret_name(name), "namespace": namespace, "labels": labels},
            "type": "Opaque",
            "stringData": {bundle["username"]: bundle["password"]},
        }

    def _vector_bootstrap_job(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        cfg: dict[str, Any],
        bundle: dict[str, str],
    ) -> dict[str, Any]:
        payload = json.dumps(
            {
                "settings": {
                    "index": {
                        "knn": True,
                        "knn.algo_param.ef_search": int(cfg["vector_ef_search"]),
                        "number_of_shards": int(cfg["shards"]),
                        "number_of_replicas": int(cfg["index_replicas"]),
                    }
                },
                "mappings": {
                    "properties": {
                        str(cfg["vector_field"]): {
                            "type": "knn_vector",
                            "dimension": int(cfg["vector_dimension"]),
                            "method": {
                                "name": "hnsw",
                                "engine": str(cfg["vector_engine"]),
                                "space_type": str(cfg["space_type"]),
                                "parameters": {
                                    "ef_construction": int(cfg["vector_ef_construction"]),
                                    "m": int(cfg["vector_m"]),
                                },
                            },
                        }
                    }
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        tls_args = "--insecure"
        volumes: list[dict[str, Any]] = []
        mounts: list[dict[str, Any]] = []
        if self._config.http_tls_verify:
            tls_args = ""
            if self._config.http_tls_ca_secret_name:
                tls_args = "--cacert /var/run/astrolift-opensearch-ca/ca.crt"
                volumes.append(
                    {
                        "name": "http-ca",
                        "secret": {"secretName": self._config.http_tls_ca_secret_name},
                    }
                )
                mounts.append(
                    {
                        "name": "http-ca",
                        "mountPath": "/var/run/astrolift-opensearch-ca",
                        "readOnly": True,
                    }
                )
        container: dict[str, Any] = {
            "name": "create-vector-index",
            "image": self._config.bootstrap_image,
            "imagePullPolicy": "IfNotPresent",
            "command": ["/bin/sh", "-ceu"],
            "args": [
                """
os_curl() {
  printf 'user = \"%s:%s\"\\n' "$OS_USER" "$OS_PASSWORD" | \\
    curl --silent --show-error --fail-with-body --config - \\
    $TLS_ARGS --connect-timeout 5 --max-time 30 "$@"
}
os_curl --retry 120 --retry-delay 5 --retry-all-errors "$OS_ENDPOINT/" >/dev/null
if os_curl --head "$OS_ENDPOINT/$OS_INDEX" >/dev/null 2>&1; then
  exit 0
fi
os_curl --header 'Content-Type: application/json' --request PUT \\
  "$OS_ENDPOINT/$OS_INDEX" --data "$OS_INDEX_PAYLOAD"
""".strip()
            ],
            "env": [
                {"name": "OS_ENDPOINT", "value": bundle["endpoint"]},
                {"name": "OS_INDEX", "value": str(cfg["index_name"])},
                {"name": "OS_INDEX_PAYLOAD", "value": payload},
                {"name": "TLS_ARGS", "value": tls_args},
                {"name": "OS_USER", "value": bundle["username"]},
                {
                    "name": "OS_PASSWORD",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": self._app_secret_name(name),
                            "key": bundle["username"],
                        }
                    },
                },
            ],
            "resources": {
                "requests": {"cpu": "10m", "memory": "16Mi"},
                "limits": {"cpu": "100m", "memory": "64Mi"},
            },
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
                "readOnlyRootFilesystem": True,
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "runAsGroup": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
        }
        if mounts:
            container["volumeMounts"] = mounts
        pod_spec: dict[str, Any] = {
            "restartPolicy": "OnFailure",
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "runAsGroup": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [container],
        }
        if volumes:
            pod_spec["volumes"] = volumes
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": self._bootstrap_job_name(name),
                "namespace": namespace,
                "labels": labels,
            },
            "spec": {
                "activeDeadlineSeconds": 900,
                "backoffLimit": 3,
                "template": {"metadata": {"labels": labels}, "spec": pod_spec},
            },
        }

    def _live_config(self, cluster: dict[str, Any]) -> dict[str, Any]:
        spec = cluster.get("spec", {}) or {}
        general = spec.get("general", {}) or {}
        pools = list(spec.get("nodePools") or [])
        if not pools:
            raise ValueError("live OpenSearch cluster has no node pools")
        data = next((row for row in pools if "data" in (row.get("roles") or [])), None)
        if data is None:
            raise ValueError("live OpenSearch cluster has no data node pool")
        managers = next(
            (
                row
                for row in pools
                if "cluster_manager" in (row.get("roles") or []) and "data" not in (row.get("roles") or [])
            ),
            None,
        )
        resources = data.get("resources", {}) or {}
        requests = resources.get("requests", {}) or {}
        limits = resources.get("limits", {}) or {}
        annotations = (cluster.get("metadata", {}) or {}).get("annotations", {}) or {}
        cfg: dict[str, Any] = {
            "topology": "dedicated" if managers is not None else "combined",
            "replicas": int(data.get("replicas") or 0),
            "data_replicas": int(data.get("replicas") or 0),
            "cpu_request": str(requests.get("cpu") or ""),
            "cpu_limit": str(limits.get("cpu") or ""),
            "memory_request": str(requests.get("memory") or ""),
            "memory_limit": str(limits.get("memory") or ""),
            "disk_size": str(data.get("diskSize") or ""),
            "storage_class_name": str(((data.get("persistence") or {}).get("pvc") or {}).get("storageClass") or ""),
            "version": str(general.get("version") or ""),
            "image": str(general.get("image") or ""),
            "image_pull_policy": str(general.get("imagePullPolicy") or "IfNotPresent"),
            "image_pull_secrets": [
                str(row.get("name")) for row in general.get("imagePullSecrets") or [] if row.get("name")
            ],
            "plugins_list": list(general.get("pluginsList") or []),
            "additional_config": dict(general.get("additionalConfig") or {}),
            "node_selector": dict(data.get("nodeSelector") or {}),
            "tolerations": copy.deepcopy(data.get("tolerations") or []),
            "affinity": copy.deepcopy(data.get("affinity") or {}),
            "node_annotations": dict(data.get("annotations") or {}),
            "priority_class_name": str(data.get("priorityClassName") or ""),
            "network_policy_mode": str(annotations.get("astrolift.io/network-policy-mode", "managed_namespaces")),
            "deletion_protection": str(annotations.get("astrolift.io/deletion-protection", "true")).lower() == "true",
            "index_prefix": str(annotations.get("astrolift.io/index-prefix") or ""),
        }
        if self.kind == VECTOR_KIND:
            cfg["index_name"] = str(cfg["index_prefix"]).removesuffix("-")
            for key in _VECTOR_CONFIG_FIELDS:
                if key in {"index_name"}:
                    continue
                annotation = annotations.get(f"astrolift.io/{key.replace('_', '-')}")
                if annotation is None:
                    raise ValueError(f"live OpenSearch vector config is missing {key}")
                cfg[key] = (
                    int(annotation)
                    if key
                    in {
                        "index_replicas",
                        "shards",
                        "vector_dimension",
                        "vector_ef_construction",
                        "vector_ef_search",
                        "vector_m",
                    }
                    else str(annotation)
                )
        if managers is not None:
            manager_resources = managers.get("resources", {}) or {}
            manager_requests = manager_resources.get("requests", {}) or {}
            manager_limits = manager_resources.get("limits", {}) or {}
            cfg.update(
                {
                    "manager_replicas": int(managers.get("replicas") or 0),
                    "manager_cpu_request": str(manager_requests.get("cpu") or ""),
                    "manager_cpu_limit": str(manager_limits.get("cpu") or ""),
                    "manager_memory_request": str(manager_requests.get("memory") or ""),
                    "manager_memory_limit": str(manager_limits.get("memory") or ""),
                    "manager_disk_size": str(managers.get("diskSize") or ""),
                }
            )
        return cfg

    def _validate_update(self, live: dict[str, Any], cfg: dict[str, Any]) -> None:
        for key in (
            "topology",
            "storage_class_name",
            "index_prefix",
            "index_name",
            "network_policy_mode",
            *_VECTOR_CONFIG_FIELDS,
        ):
            if key in live and str(live.get(key)) != str(cfg.get(key)):
                raise ValueError(f"OpenSearch {key} is immutable and requires replacement")
        if self._bytes(str(cfg["disk_size"])) < self._bytes(str(live["disk_size"])):
            raise ValueError("OpenSearch disk_size cannot shrink")
        if live.get("manager_disk_size") and self._bytes(str(cfg["manager_disk_size"])) < self._bytes(
            str(live["manager_disk_size"])
        ):
            raise ValueError("OpenSearch manager_disk_size cannot shrink")
        if self._version_tuple(str(cfg["version"])) < self._version_tuple(str(live["version"])):
            raise ValueError("OpenSearch version downgrade is not supported")

    def _validate_existing_ownership(
        self,
        spec: ProvisionSpec,
        namespace: str,
        name: str,
    ) -> dict[str, Any] | None:
        existing = self._get_cluster(spec.tenant_cluster_id, namespace, name)
        if existing is None:
            return None
        labels = (existing.get("metadata", {}) or {}).get("labels", {}) or {}
        if (
            labels.get("astrolift.io/managed-by") != "astrolift"
            or labels.get("astrolift.io/organization") != self._label_value(spec.organization_slug)
            or labels.get("astrolift.io/app") != self._label_value(spec.app_slug)
            or labels.get("astrolift.io/service-kind") != self.kind
            or (
                spec.managed_service_id
                and labels.get("astrolift.io/managed-service") != self._label_value(spec.managed_service_id)
            )
        ):
            raise ValueError(f"refusing to adopt foreign OpenSearchCluster {namespace}/{name}")
        return existing

    def _assert_owned(self, cluster: dict[str, Any], name: str) -> None:
        labels = (cluster.get("metadata", {}) or {}).get("labels", {}) or {}
        if (
            labels.get("astrolift.io/managed-by") != "astrolift"
            or labels.get("astrolift.io/resource") != name
            or labels.get("astrolift.io/service-kind") != self.kind
        ):
            raise ValueError(f"refusing to operate foreign OpenSearchCluster {name}")

    def _get_cluster(self, cluster_id: str, namespace: str, name: str) -> dict[str, Any] | None:
        versions = [self._config.api_version]
        versions.extend(v for v in (LEGACY_API_VERSION, CURRENT_API_VERSION) if v not in versions)
        for version in versions:
            obj = self._config.cluster_driver.get_manifest(
                cluster_id,
                namespace,
                f"{version}/OpenSearchCluster",
                name,
            )
            if obj is not None:
                return dict(obj)
        return None

    def _owned_stubs(
        self,
        namespace: str,
        name: str,
        cluster: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        api_version = str(cluster.get("apiVersion")) if cluster is not None else self._config.api_version
        rows = [
            self._stub(api_version, "OpensearchUserRoleBinding", self._binding_name(name), namespace),
            self._stub(api_version, "OpensearchUser", self._username(name), namespace),
            self._stub(api_version, "OpensearchRole", self._role_name(name), namespace),
            self._stub("networking.k8s.io/v1", "NetworkPolicy", self._policy_name(name), namespace),
        ]
        if self.kind == VECTOR_KIND:
            rows.insert(0, self._stub("batch/v1", "Job", self._bootstrap_job_name(name), namespace))
        return rows

    def _pvc_stubs(
        self,
        namespace: str,
        name: str,
        cluster: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if cluster is None:
            return []
        rows: list[dict[str, Any]] = []
        for pool in (cluster.get("spec", {}) or {}).get("nodePools") or []:
            component = str(pool.get("component") or "")
            replicas = int(pool.get("replicas") or 0)
            if not component or replicas < 0:
                continue
            rows.extend(
                self._stub("v1", "PersistentVolumeClaim", f"data-{name}-{component}-{index}", namespace)
                for index in range(replicas)
            )
        return rows

    def _wait_absent(self, cluster_id: str, namespace: str, name: str) -> bool:
        deadline = time.monotonic() + max(self._config.deletion_timeout_seconds, 0)
        while True:
            maybe_heartbeat(f"opensearch.delete:{namespace}/{name}")
            if self._get_cluster(cluster_id, namespace, name) is None:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(self._config.deletion_poll_seconds, 0))

    def _ensure_namespace(self, spec: ProvisionSpec, namespace: str) -> None:
        ensure_namespace = getattr(self._config.cluster_driver, "ensure_namespace", None)
        if callable(ensure_namespace):
            ensure_namespace(
                spec.tenant_cluster_id,
                namespace,
                {
                    "astrolift.io/managed-by": "astrolift",
                    "astrolift.io/organization": self._label_value(spec.organization_slug),
                    "astrolift.io/app": self._label_value(spec.app_slug),
                },
                {},
            )

    def _labels(self, spec: ProvisionSpec, name: str) -> dict[str, str]:
        labels = {
            "app.kubernetes.io/name": "opensearch",
            "app.kubernetes.io/instance": name,
            "app.kubernetes.io/managed-by": "astrolift",
            "astrolift.io/managed-by": "astrolift",
            "astrolift.io/organization": self._label_value(spec.organization_slug),
            "astrolift.io/app": self._label_value(spec.app_slug),
            "astrolift.io/environment": self._label_value(spec.environment_name),
            "astrolift.io/resource": name,
            "astrolift.io/service-kind": self.kind,
        }
        if spec.managed_service_id:
            labels["astrolift.io/managed-service"] = self._label_value(spec.managed_service_id)
        return labels

    def _config_annotations(self, cfg: dict[str, Any]) -> dict[str, str]:
        values = {
            "astrolift.io/network-policy-mode": str(cfg["network_policy_mode"]),
        }
        if self.kind == VECTOR_KIND:
            values.update(
                {
                    f"astrolift.io/{key.replace('_', '-')}": str(cfg[key])
                    for key in _VECTOR_CONFIG_FIELDS
                    if key != "index_name"
                }
            )
        return values

    def _name(self, spec: ProvisionSpec) -> str:
        return dns_label(
            "os",
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
            max_length=48,
        )

    def _namespace(self, spec: ProvisionSpec) -> str:
        if self._config.namespace:
            value = self._config.namespace
        else:
            value = app_namespace(
                organization_slug=spec.organization_slug,
                app_slug=spec.app_slug,
            )
        if len(value) > 63 or not _DNS_LABEL.fullmatch(value):
            raise ValueError("OpenSearch namespace must be a Kubernetes DNS label")
        return value

    def _parsed(self, handle: str) -> Any:
        parsed = _unpack_handle(handle)
        if parsed.is_legacy:
            raise ValueError("legacy OpenSearch handle lacks cluster and namespace locators")
        if parsed.kind not in {SEARCH_KIND, VECTOR_KIND}:
            raise ValueError(f"handle kind {parsed.kind!r} is not OpenSearch search/vector")
        return parsed

    def _credential_path(self, parsed: Any) -> str:
        return self._credential_path_parts(parsed.cluster_id, parsed.namespace, parsed.name)

    def _credential_path_parts(self, cluster_id: str, namespace: str, name: str) -> str:
        return f"{self._config.credential_path_prefix.strip('/')}/{cluster_id}/{namespace}/{name}/credentials"

    @staticmethod
    def _endpoint(namespace: str, name: str) -> str:
        return f"https://{name}.{namespace}.svc.cluster.local:{PORT}"

    @staticmethod
    def _default_index_prefix(spec: ProvisionSpec) -> str:
        value = re.sub(
            r"-+",
            "-",
            re.sub(
                r"[^a-z0-9._-]+", "-", f"{spec.app_slug}-{spec.environment_name}-{spec.service_handle_hint}".lower()
            ),
        ).strip("-._")
        value = value[:120].rstrip("-._")
        if not value:
            raise ValueError("OpenSearch index prefix could not be normalized")
        return f"{value}-"

    @staticmethod
    def _password() -> str:
        alphabet = string.ascii_letters + string.digits + "!#%+,-.:=@_"
        chars = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice("!#%+,-.:=@_"),
            *(secrets.choice(alphabet) for _ in range(36)),
        ]
        secrets.SystemRandom().shuffle(chars)
        return "".join(chars)

    @staticmethod
    def _decode_secret_field(secret: dict[str, Any], key: str) -> str:
        value = (secret.get("data", {}) or {}).get(key)
        if not isinstance(value, str):
            raise ValueError(f"retained OpenSearch Secret is missing data.{key}")
        try:
            return base64.b64decode(value, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError(f"retained OpenSearch Secret data.{key} is malformed") from exc

    @staticmethod
    def _bytes(value: str) -> int:
        match = re.fullmatch(r"([1-9][0-9]*)([EPTGMK]i?)?", value)
        if not match:
            raise ValueError(f"unsupported Kubernetes quantity {value!r}")
        amount = int(match.group(1))
        unit = match.group(2) or ""
        binary = unit.endswith("i")
        symbol = unit[:-1] if binary else unit
        exponent = {"": 0, "K": 1, "M": 2, "G": 3, "T": 4, "P": 5, "E": 6}.get(symbol)
        if exponent is None:
            raise ValueError(f"unsupported Kubernetes quantity {value!r}")
        return int(amount * ((1024 if binary else 1000) ** exponent))

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, int, int]:
        if not _VERSION.fullmatch(value):
            raise ValueError(f"invalid OpenSearch version {value!r}")
        major, minor, patch = value.split(".")
        return int(major), int(minor), int(patch)

    @staticmethod
    def _label_value(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value))[:63].strip("-_.")
        return cleaned or "unknown"

    @staticmethod
    def _username(name: str) -> str:
        return f"{name}-app"[:63].rstrip("-")

    @staticmethod
    def _admin_secret_name(name: str) -> str:
        return f"{name}-admin"

    @staticmethod
    def _app_secret_name(name: str) -> str:
        return f"{name}-app-user"

    @staticmethod
    def _role_name(name: str) -> str:
        return f"{name}-client"

    @staticmethod
    def _binding_name(name: str) -> str:
        return f"{name}-client-binding"

    @staticmethod
    def _policy_name(name: str) -> str:
        return f"{name}-ingress"

    @staticmethod
    def _bootstrap_job_name(name: str) -> str:
        return f"{name}-create-index"

    @staticmethod
    def _stub(api_version: str, kind: str, name: str, namespace: str) -> dict[str, Any]:
        return {"apiVersion": api_version, "kind": kind, "metadata": {"name": name, "namespace": namespace}}


class OpenSearchSearchDriver(_OpenSearchOperatorDriver):
    kind = SEARCH_KIND
    variant = SEARCH_VARIANT


class OpenSearchVectorDriver(_OpenSearchOperatorDriver):
    kind = VECTOR_KIND
    variant = VECTOR_VARIANT


__all__ = [
    "CURRENT_API_VERSION",
    "DEFAULT_IMAGE",
    "DEFAULT_VERSION",
    "LEGACY_API_VERSION",
    "MINIMUM_OPERATOR_VERSION",
    "OpenSearchOperatorConfig",
    "OpenSearchSearchDriver",
    "OpenSearchVectorDriver",
]
