"""Microsoft SQL Server Express on Kubernetes.

This driver owns one non-root SQL Server 2025 Express instance, its PVC,
services, credential Secret, and ingress policy.  The external Astrolift
secrets backend is the credential source of truth; the Kubernetes Secret is a
runtime projection of that bundle.

Variant key: ``('mssql', 'sqlserver_express')``.
"""

from __future__ import annotations

import base64
import binascii
import copy
import ipaddress
import re
import secrets
import string
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import uuid4

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

KIND = "mssql"
VARIANT = "sqlserver_express"
PORT = 1433
DEFAULT_IMAGE = (
    "mcr.microsoft.com/mssql/server:2025-CU7-ubuntu-22.04"
    "@sha256:fa0dcf206087759fe6dad4cc02bfa88d97439085e548fbca9039330519c0cf1d"
)

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#@]{0,127}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_BYTE_QUANTITY = re.compile(r"^[1-9][0-9]*(?:[EPTGMK]i?)?$")
_CPU_QUANTITY = re.compile(r"^(?:[1-9][0-9]*m|[1-9][0-9]*(?:\.[0-9]+)?)$")
_SECRET_KEYS = {"sa_password", "username", "password", "database", "database_url"}
_RESERVED_ENV = {
    "ACCEPT_EULA",
    "MSSQL_DB",
    "MSSQL_AGENT_ENABLED",
    "MSSQL_COLLATION",
    "MSSQL_LCID",
    "MSSQL_MEMORY_LIMIT_MB",
    "MSSQL_PASSWORD",
    "MSSQL_PID",
    "MSSQL_SA_PASSWORD",
    "MSSQL_TCP_PORT",
    "MSSQL_USER",
}
_RESERVED_CONF = {
    ("network", "forceencryption"),
    ("network", "tcpport"),
    ("network", "tlscert"),
    ("network", "tlskey"),
    ("network", "tlsprotocols"),
    ("memory", "memorylimitmb"),
}
_CONFIG_FIELDS = {
    "affinity",
    "collation",
    "cpu_limit",
    "cpu_request",
    "database",
    "deletion_protection",
    "extra_env",
    "image",
    "image_pull_policy",
    "image_pull_secrets",
    "lcid",
    "load_balancer_source_ranges",
    "memory_limit",
    "memory_limit_mb",
    "memory_request",
    "mssql_conf",
    "network_policy_mode",
    "node_selector",
    "pod_annotations",
    "priority_class_name",
    "runtime_class_name",
    "service_annotations",
    "service_type",
    "storage_access_mode",
    "storage_class_name",
    "storage_size",
    "termination_grace_period_seconds",
    "tls_secret_name",
    "tolerations",
    "username",
}

_SIZE_DEFAULTS: dict[str, dict[str, str | int]] = {
    "small": {
        "cpu_request": "500m",
        "cpu_limit": "1",
        "memory_request": "2Gi",
        "memory_limit": "3Gi",
        "memory_limit_mb": 1408,
        "storage_size": "20Gi",
    },
    "medium": {
        "cpu_request": "1",
        "cpu_limit": "2",
        "memory_request": "3Gi",
        "memory_limit": "4Gi",
        "memory_limit_mb": 1408,
        "storage_size": "50Gi",
    },
    "large": {
        "cpu_request": "2",
        "cpu_limit": "4",
        "memory_request": "4Gi",
        "memory_limit": "6Gi",
        "memory_limit_mb": 1408,
        "storage_size": "100Gi",
    },
    "xlarge": {
        "cpu_request": "2",
        "cpu_limit": "4",
        "memory_request": "4Gi",
        "memory_limit": "8Gi",
        "memory_limit_mb": 1408,
        "storage_size": "200Gi",
    },
}


@dataclass(frozen=True)
class SQLServerExpressConfig:
    """Install-level SQL Server Express policy and infrastructure defaults."""

    cluster_driver: Any = None
    secrets_backend: Any = None
    namespace: str | None = None
    storage_class_name: str = ""
    image: str = DEFAULT_IMAGE
    credential_path_prefix: str = "managed/mssql"
    allow_custom_images: bool = False
    allow_load_balancer: bool = False
    allow_network_policy_disable: bool = False
    volume_snapshot_class: str = ""
    allow_crash_consistent_snapshots: bool = False
    deletion_timeout_seconds: float = 120.0
    deletion_poll_seconds: float = 2.0


class SQLServerExpressDriver(ManagedServiceDriver):
    """Provision a single SQL Server Express instance with durable storage."""

    def __init__(self, *, config: SQLServerExpressConfig) -> None:
        self._config = config

    @driver_op(
        cloud="k8s_native",
        driver="mssql_express",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._validate_dependencies()
            name = self._name(spec)
            namespace = self._namespace(spec)
            cfg = self._service_config(spec.size, spec.config)
            storage_class = self._storage_class(spec.tenant_cluster_id, cfg)
            self._validate_existing_ownership(spec, namespace, name)
            credentials = self._credentials_for(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
                database=str(cfg["database"]),
                username=str(cfg["username"]),
                trust_server_certificate=not bool(cfg.get("tls_secret_name")),
            )
        except ValueError as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_mssql_config"])
        except Exception as exc:  # Redact provider secret errors.
            return ProvisionResult(
                False, "", f"SQL Server credential preparation failed: {type(exc).__name__}", ["credential_error"]
            )

        handle = _pack_handle(
            kind=KIND,
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
            credentials=credentials,
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
                "SQL Server Express manifests were rejected",
                result.summary(),
            )
        return ProvisionResult(
            True,
            handle,
            f"SQL Server 2025 Express {namespace}/{name} submitted for reconciliation",
            ready=False,
        )

    @driver_op(cloud="k8s_native", driver="mssql_express")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._validate_dependencies()
            parsed = _unpack_handle(spec.handle)
            if parsed.is_legacy:
                raise ValueError("legacy SQL Server handle has no cluster locator")
            statefulset = self._required_live(parsed, "apps/v1/StatefulSet", parsed.name)
            pvc = self._required_live(parsed, "v1/PersistentVolumeClaim", self._pvc_name(parsed.name))
            config_map = self._required_live(parsed, "v1/ConfigMap", self._config_name(parsed.name))
            current = self._current_runtime_config(parsed, statefulset, pvc, config_map)
            for immutable in (
                "database",
                "username",
                "storage_class_name",
                "storage_access_mode",
                "tls_secret_name",
                "collation",
                "lcid",
            ):
                if immutable in spec.config and spec.config[immutable] != current.get(immutable):
                    raise ValueError(f"{immutable} is immutable; replace the SQL Server resource")
            requested = dict(spec.config)
            config_size = requested.pop("size", None)
            requested_size = spec.size or (str(config_size) if config_size else "")
            if spec.size and config_size and str(config_size) != spec.size:
                raise ValueError("UpdateSpec size disagrees with config.size")
            desired = dict(current)
            if requested_size:
                if requested_size not in {*_SIZE_DEFAULTS, "custom"}:
                    raise ValueError(f"unsupported SQL Server size {requested_size!r}")
                desired.update(_SIZE_DEFAULTS.get(requested_size) or {})
            desired.update(requested)
            cfg = self._service_config("custom", desired)
            self._apply_runtime_update(statefulset, config_map, cfg)
            self._apply_storage_update(parsed, pvc, str(cfg["storage_size"]))
            manifests = [self._clean_live(config_map), self._clean_live(pvc), self._clean_live(statefulset)]
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_mssql_update"])

        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            manifests,
        )
        if not result.ok:
            return UpdateResult(False, spec.handle, "SQL Server Express update was rejected", result.summary())
        return UpdateResult(True, spec.handle, f"SQL Server Express {parsed.name} update submitted")

    @driver_op(
        cloud="k8s_native",
        driver="mssql_express",
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
            parsed = _unpack_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        if parsed.is_legacy:
            return DeprovisionResult(
                False,
                spec.handle,
                "legacy SQL Server handle has no cluster locator",
                ["legacy_handle_missing_locator"],
                retryable=False,
            )
        if delete_data and bool(spec.config.get("deletion_protection", True)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "SQL Server deletion protection is enabled; pass force_destroy to delete the PVC",
                ["deletion_protection_enabled"],
                retryable=False,
            )

        workload_stubs = [
            self._stub("apps/v1", "StatefulSet", parsed.name, parsed.namespace),
            self._stub("networking.k8s.io/v1", "NetworkPolicy", self._policy_name(parsed.name), parsed.namespace),
            self._stub("v1", "Service", parsed.name, parsed.namespace),
            self._stub("v1", "Service", self._headless_name(parsed.name), parsed.namespace),
        ]
        deleted = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            workload_stubs,
        )
        if not deleted.ok:
            return DeprovisionResult(False, spec.handle, "could not stop SQL Server Express", deleted.summary())
        if not self._wait_absent(parsed, "apps/v1/StatefulSet", parsed.name):
            return DeprovisionResult(
                False, spec.handle, "timed out waiting for SQL Server shutdown", ["shutdown_timeout"]
            )

        if not delete_data:
            return DeprovisionResult(
                True,
                spec.handle,
                f"SQL Server workload stopped; PVC and credential bundles for {parsed.name} retained",
            )

        data_stubs = [
            self._stub("v1", "PersistentVolumeClaim", self._pvc_name(parsed.name), parsed.namespace),
            self._stub("v1", "Secret", self._secret_name(parsed.name), parsed.namespace),
            self._stub("v1", "ConfigMap", self._config_name(parsed.name), parsed.namespace),
        ]
        removed = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            data_stubs,
        )
        if not removed.ok:
            return DeprovisionResult(
                False, spec.handle, "could not delete SQL Server data resources", removed.summary()
            )
        if not self._wait_absent(parsed, "v1/PersistentVolumeClaim", self._pvc_name(parsed.name)):
            return DeprovisionResult(
                False, spec.handle, "timed out waiting for SQL Server PVC deletion", ["pvc_delete_timeout"]
            )
        try:
            self._config.secrets_backend.delete(self._credential_path(parsed))
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"data deleted but external credential cleanup failed: {type(exc).__name__}",
                ["credential_cleanup_failed"],
            )
        return DeprovisionResult(True, spec.handle, f"SQL Server Express {parsed.name} and its PVC were deleted")

    @driver_op(cloud="k8s_native", driver="mssql_express")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._validate_dependencies()
            parsed = _unpack_handle(handle.handle)
        except ValueError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        if parsed.is_legacy:
            return ServiceStatus(handle.handle, "error", "legacy SQL Server handle has no cluster locator")
        workload = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            "apps/v1/StatefulSet",
            parsed.name,
        )
        if workload is None:
            pvc = self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                "v1/PersistentVolumeClaim",
                self._pvc_name(parsed.name),
            )
            retained = " (data PVC retained)" if pvc is not None else ""
            return ServiceStatus(handle.handle, "deprovisioned", f"StatefulSet not found{retained}")
        status = workload.get("status", {}) or {}
        conditions = status.get("conditions", []) or []
        failure = next(
            (
                row
                for row in conditions
                if row.get("type") in {"ReplicaFailure", "Failed"} and str(row.get("status", "")).lower() == "true"
            ),
            None,
        )
        if failure:
            return ServiceStatus(
                handle.handle,
                "error",
                str(failure.get("message") or failure.get("reason") or "StatefulSet reconciliation failed"),
            )
        ready = int(status.get("readyReplicas", 0) or 0)
        current = int(status.get("currentReplicas", 0) or 0)
        desired = int((workload.get("spec", {}) or {}).get("replicas", 1) or 1)
        if ready == desired and current == desired:
            return ServiceStatus(handle.handle, "available", f"SQL Server Express ready ({ready}/{desired})")
        return ServiceStatus(handle.handle, "provisioning", f"SQL Server Express starting ({ready}/{desired} ready)")

    @driver_op(cloud="k8s_native", driver="mssql_express")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        self._validate_dependencies()
        parsed = _unpack_handle(handle.handle)
        if parsed.is_legacy:
            raise ValueError("legacy SQL Server handle has no cluster locator")
        path = self._credential_path(parsed)
        bundle = self._read_valid_bundle(path, host=self._host(parsed.namespace, parsed.name))
        trust_server_certificate = str(bundle.get("trust_server_certificate", "true")).lower()
        return Binding(
            env_vars={
                "MSSQL_HOST": ValueRef(literal=self._host(parsed.namespace, parsed.name)),
                "MSSQL_PORT": ValueRef(literal=str(PORT)),
                "MSSQL_DB": ValueRef(literal=bundle["database"]),
                "MSSQL_USER": ValueRef(literal=bundle["username"]),
                "MSSQL_PASSWORD": ValueRef(secret_ref=f"{path}#password"),
                "MSSQL_ENCRYPT": ValueRef(literal="true"),
                "MSSQL_TRUST_SERVER_CERTIFICATE": ValueRef(literal=trust_server_certificate),
                "DATABASE_URL": ValueRef(secret_ref=f"{path}#database_url"),
            },
            notes=(
                "SQL Server 2025 Express is limited to one instance, four cores, and a 50 GB relational database; "
                "credentials are stored in the install secrets backend."
            ),
        )

    @driver_op(cloud="k8s_native", driver="mssql_express")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        self._validate_dependencies()
        if not self._config.allow_crash_consistent_snapshots:
            raise UnsupportedOperationError(
                "Kubernetes SQL Server snapshots are crash-consistent and require "
                "mssql_allow_crash_consistent_snapshots=true",
            )
        if not self._config.volume_snapshot_class:
            raise UnsupportedOperationError("mssql_volume_snapshot_class is required for SQL Server snapshots")
        parsed = _unpack_handle(handle.handle)
        if parsed.is_legacy:
            raise ValueError("legacy SQL Server handle has no cluster locator")
        self._preflight_snapshot(parsed.cluster_id)
        suffix = f"{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid4().hex[:6]}"
        name = f"{parsed.name}-snap-{suffix}"[:63].rstrip("-")
        manifest = {
            "apiVersion": "snapshot.storage.k8s.io/v1",
            "kind": "VolumeSnapshot",
            "metadata": {
                "name": name,
                "namespace": parsed.namespace,
                "labels": {"app.kubernetes.io/managed-by": "astrolift", "astrolift.io/source": parsed.name},
            },
            "spec": {
                "volumeSnapshotClassName": self._config.volume_snapshot_class,
                "source": {"persistentVolumeClaimName": self._pvc_name(parsed.name)},
            },
        }
        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [manifest],
        )
        if not result.ok:
            raise RuntimeError("VolumeSnapshot apply failed: " + "; ".join(result.summary()))
        snapshot = self._wait_snapshot_ready(parsed.cluster_id, parsed.namespace, name)
        created = str((snapshot.get("status", {}) or {}).get("creationTime") or datetime.now(UTC).isoformat())
        return SnapshotHandle(handle.handle, f"{parsed.cluster_id}/{parsed.namespace}/{name}", created)

    @driver_op(cloud="k8s_native", driver="mssql_express")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        try:
            self._validate_dependencies()
            source = _unpack_handle(snapshot.handle)
            if source.is_legacy:
                raise ValueError("legacy source handle cannot be restored")
            snap_cluster, snap_namespace, snap_name = self._parse_snapshot_id(snapshot.snapshot_id)
            if snap_cluster != target.tenant_cluster_id:
                raise ValueError("cross-cluster VolumeSnapshot restore is not supported")
            namespace = self._namespace(target)
            if namespace != snap_namespace:
                raise ValueError("Kubernetes VolumeSnapshots can only restore into their source namespace")
            snapshot_obj = self._config.cluster_driver.get_manifest(
                snap_cluster,
                snap_namespace,
                "snapshot.storage.k8s.io/v1/VolumeSnapshot",
                snap_name,
            )
            if snapshot_obj is None or not bool((snapshot_obj.get("status", {}) or {}).get("readyToUse")):
                raise ValueError(f"VolumeSnapshot {snap_name} is not ready")
            name = self._name(target)
            cfg = self._service_config(target.size, target.config)
            storage_class = self._storage_class(target.tenant_cluster_id, cfg)
            self._validate_existing_ownership(target, namespace, name)
            source_bundle = self._read_valid_bundle(
                self._credential_path(source),
                host=self._host(source.namespace, source.name),
            )
            if str(cfg["database"]) != source_bundle["database"] or str(cfg["username"]) != source_bundle["username"]:
                raise ValueError("snapshot restore must retain the source database and username")
            credentials = dict(source_bundle)
            trust_server_certificate = not bool(cfg.get("tls_secret_name"))
            credentials["trust_server_certificate"] = str(trust_server_certificate).lower()
            credentials["database_url"] = self._database_url(
                self._host(namespace, name),
                credentials["database"],
                credentials["username"],
                credentials["password"],
                trust_server_certificate=trust_server_certificate,
            )
            target_path = self._credential_path_parts(target.tenant_cluster_id, namespace, name)
            current = self._config.secrets_backend.get(target_path)
            if current is not None and current != credentials:
                raise ValueError("restore target credential path already contains different values")
            self._config.secrets_backend.upsert(target_path, credentials)
            manifests = self._manifests(
                spec=target,
                namespace=namespace,
                name=name,
                cfg=cfg,
                storage_class=storage_class,
                credentials=credentials,
                snapshot_name=snap_name,
            )
            self._ensure_namespace(target, namespace)
        except ValueError as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_mssql_restore"])
        result = self._config.cluster_driver.apply_manifests(
            target.tenant_cluster_id,
            namespace,
            manifests,
        )
        handle = _pack_handle(kind=KIND, cluster_id=target.tenant_cluster_id, namespace=namespace, name=name)
        if not result.ok:
            return ProvisionResult(False, handle, "SQL Server restore manifests were rejected", result.summary())
        return ProvisionResult(True, handle, f"SQL Server restore from {snap_name} submitted", ready=False)

    @driver_op(cloud="k8s_native", driver="mssql_express", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        quantity = {"type": "string", "pattern": _BYTE_QUANTITY.pattern}
        cpu = {"type": "string", "pattern": _CPU_QUANTITY.pattern}
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "database": {"type": "string", "pattern": _SQL_IDENTIFIER.pattern, "default": "astrolift"},
                "username": {"type": "string", "pattern": _SQL_IDENTIFIER.pattern, "default": "astrolift_app"},
                "storage_class_name": {"type": "string"},
                "storage_size": quantity,
                "storage_access_mode": {
                    "type": "string",
                    "enum": ["ReadWriteOnce", "ReadWriteOncePod"],
                    "default": "ReadWriteOnce",
                },
                "image": {"type": "string", "minLength": 1, "default": DEFAULT_IMAGE},
                "image_pull_policy": {
                    "type": "string",
                    "enum": ["Always", "IfNotPresent", "Never"],
                    "default": "IfNotPresent",
                },
                "image_pull_secrets": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                "cpu_request": cpu,
                "cpu_limit": cpu,
                "memory_request": quantity,
                "memory_limit": quantity,
                "memory_limit_mb": {"type": "integer", "minimum": 512, "maximum": 1410},
                "collation": {"type": "string"},
                "lcid": {"type": "integer", "minimum": 1},
                "tls_secret_name": {"type": "string"},
                "service_type": {
                    "type": "string",
                    "enum": ["ClusterIP", "LoadBalancer"],
                    "default": "ClusterIP",
                },
                "load_balancer_source_ranges": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string"},
                },
                "network_policy_mode": {
                    "type": "string",
                    "enum": ["managed_namespaces", "same_namespace", "disabled"],
                    "default": "managed_namespaces",
                },
                "deletion_protection": {"type": "boolean", "default": True},
                "node_selector": {"type": "object", "additionalProperties": {"type": "string"}},
                "tolerations": {"type": "array", "items": {"type": "object"}},
                "affinity": {"type": "object"},
                "priority_class_name": {"type": "string"},
                "runtime_class_name": {"type": "string"},
                "pod_annotations": {"type": "object", "additionalProperties": {"type": "string"}},
                "service_annotations": {"type": "object", "additionalProperties": {"type": "string"}},
                "extra_env": {"type": "object", "additionalProperties": {"type": "string"}},
                "mssql_conf": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "additionalProperties": {"type": ["string", "integer", "number", "boolean"]},
                    },
                },
                "termination_grace_period_seconds": {"type": "integer", "minimum": 30, "maximum": 900},
            },
        }

    @driver_op(cloud="k8s_native", driver="mssql_express", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MSSQL_HOST": "In-cluster SQL Server service DNS name",
                "MSSQL_PORT": "SQL Server TDS port (1433)",
                "MSSQL_DB": "Initial application database",
                "MSSQL_USER": "Application login",
                "MSSQL_PASSWORD": "External secret reference to the application login password",
                "MSSQL_ENCRYPT": "Require encrypted client transport",
                "MSSQL_TRUST_SERVER_CERTIFICATE": "Whether the server certificate is self-signed",
                "DATABASE_URL": "External secret reference to the encrypted sqlserver:// URL",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "image",
            "image_pull_policy",
            "image_pull_secrets",
            "cpu_request",
            "cpu_limit",
            "memory_request",
            "memory_limit",
            "memory_limit_mb",
            "storage_size",
            "node_selector",
            "tolerations",
            "affinity",
            "priority_class_name",
            "runtime_class_name",
            "pod_annotations",
            "extra_env",
            "mssql_conf",
            "termination_grace_period_seconds",
            "deletion_protection",
        ]

    def _validate_dependencies(self) -> None:
        if self._config.cluster_driver is None:
            raise ValueError("SQL Server Express requires a cluster_driver")
        if self._config.secrets_backend is None:
            raise ValueError("SQL Server Express requires an external secrets backend")
        prefix = self._config.credential_path_prefix.strip("/")
        if not prefix or "#" in prefix or any(part in {"", ".", ".."} for part in prefix.split("/")):
            raise ValueError("mssql_credential_path_prefix must be a safe non-empty secret path")

    def _service_config(self, size: str, raw: dict[str, Any]) -> dict[str, Any]:
        if size not in {*_SIZE_DEFAULTS, "custom"}:
            raise ValueError(f"unsupported SQL Server size {size!r}")
        values = dict(raw or {})
        declared_size = values.pop("size", None)
        if declared_size is not None and str(declared_size) != size:
            raise ValueError("config.size disagrees with ProvisionSpec size")
        defaults = dict(_SIZE_DEFAULTS.get(size) or {})
        cfg = {**defaults, **values}
        if size == "custom":
            required = {"cpu_request", "cpu_limit", "memory_request", "memory_limit", "memory_limit_mb", "storage_size"}
            missing = sorted(required - cfg.keys())
            if missing:
                raise ValueError(f"custom SQL Server size requires {', '.join(missing)}")
        cfg.setdefault("database", "astrolift")
        cfg.setdefault("username", "astrolift_app")
        cfg.setdefault("storage_access_mode", "ReadWriteOnce")
        cfg.setdefault("image", self._config.image)
        cfg.setdefault("image_pull_policy", "IfNotPresent")
        cfg.setdefault("network_policy_mode", "managed_namespaces")
        cfg.setdefault("service_type", "ClusterIP")
        cfg.setdefault("deletion_protection", True)
        cfg.setdefault("termination_grace_period_seconds", 120)
        self._validate_cfg(cfg)
        return cfg

    def _validate_cfg(self, cfg: dict[str, Any]) -> None:
        unknown = sorted(set(cfg) - _CONFIG_FIELDS)
        if unknown:
            raise ValueError(f"unsupported SQL Server config fields: {', '.join(unknown)}")
        for key in ("database", "username"):
            if not _SQL_IDENTIFIER.fullmatch(str(cfg[key])):
                raise ValueError(f"{key} must be a valid SQL Server identifier")
        if str(cfg["database"]).lower() in {"master", "model", "msdb", "tempdb"}:
            raise ValueError("database cannot use a SQL Server system database name")
        if str(cfg["username"]).lower() == "sa":
            raise ValueError("username cannot replace the SQL Server sa login")
        for key in ("memory_request", "memory_limit", "storage_size"):
            if not _BYTE_QUANTITY.fullmatch(str(cfg[key])):
                raise ValueError(f"{key} must be a positive Kubernetes quantity")
        for key in ("cpu_request", "cpu_limit"):
            if not _CPU_QUANTITY.fullmatch(str(cfg[key])):
                raise ValueError(f"{key} must be a positive Kubernetes CPU quantity")
        if self._cpu_millis(str(cfg["cpu_limit"])) > 4000:
            raise ValueError("SQL Server Express cannot use more than four CPU cores")
        if self._cpu_millis(str(cfg["cpu_request"])) > self._cpu_millis(str(cfg["cpu_limit"])):
            raise ValueError("cpu_request cannot exceed cpu_limit")
        if self._bytes(str(cfg["memory_request"])) > self._bytes(str(cfg["memory_limit"])):
            raise ValueError("memory_request cannot exceed memory_limit")
        if self._bytes(str(cfg["memory_limit"])) < 2 * 1024**3:
            raise ValueError("memory_limit must be at least 2Gi for SQL Server")
        if self._bytes(str(cfg["storage_size"])) < 10 * 1024**3:
            raise ValueError("storage_size must be at least 10Gi for SQL Server")
        memory_limit_mb = int(cfg["memory_limit_mb"])
        if memory_limit_mb < 512 or memory_limit_mb > 1410:
            raise ValueError("memory_limit_mb must be between 512 and the Express limit of 1410")
        if memory_limit_mb * 1024**2 > self._bytes(str(cfg["memory_limit"])):
            raise ValueError("memory_limit_mb cannot exceed the container memory_limit")
        image = str(cfg["image"])
        if not self._config.allow_custom_images and image != self._config.image:
            raise ValueError("custom SQL Server images require mssql_allow_custom_images=true")
        if str(cfg["storage_access_mode"]) not in {"ReadWriteOnce", "ReadWriteOncePod"}:
            raise ValueError("storage_access_mode must be ReadWriteOnce or ReadWriteOncePod")
        if str(cfg["image_pull_policy"]) not in {"Always", "IfNotPresent", "Never"}:
            raise ValueError("image_pull_policy must be Always, IfNotPresent, or Never")
        if str(cfg["service_type"]) not in {"ClusterIP", "LoadBalancer"}:
            raise ValueError("service_type must be ClusterIP or LoadBalancer")
        policy_mode = str(cfg["network_policy_mode"])
        if policy_mode not in {"managed_namespaces", "same_namespace", "disabled"}:
            raise ValueError("network_policy_mode is invalid")
        if policy_mode == "disabled" and not self._config.allow_network_policy_disable:
            raise ValueError("disabling NetworkPolicy requires mssql_allow_network_policy_disable=true")
        if str(cfg["service_type"]) == "LoadBalancer":
            if not self._config.allow_load_balancer:
                raise ValueError("LoadBalancer exposure requires mssql_allow_load_balancer=true")
            if not cfg.get("load_balancer_source_ranges"):
                raise ValueError("LoadBalancer exposure requires load_balancer_source_ranges")
            for cidr in cfg["load_balancer_source_ranges"]:
                try:
                    ipaddress.ip_network(str(cidr), strict=False)
                except ValueError as exc:
                    raise ValueError(f"invalid LoadBalancer source CIDR {cidr!r}") from exc
        tls_secret_name = str(cfg.get("tls_secret_name") or "")
        if tls_secret_name and (len(tls_secret_name) > 63 or not _DNS_LABEL.fullmatch(tls_secret_name)):
            raise ValueError("tls_secret_name must be a Kubernetes DNS label")
        # A pull secret is a registry credential like any other Secret (#1959, #2087).
        refuse_shared_namespace_secrets(
            self._config.namespace,
            None,
            extra=[tls_secret_name, *(str(name) for name in cfg.get("image_pull_secrets") or [])],
        )
        extra_env = dict(cfg.get("extra_env") or {})
        denied = sorted(_RESERVED_ENV & extra_env.keys())
        if denied:
            raise ValueError(f"extra_env cannot override reserved variables: {', '.join(denied)}")
        for key, value in extra_env.items():
            if not _ENV_NAME.fullmatch(str(key)):
                raise ValueError(f"extra_env name {key!r} is invalid")
            if "\x00" in str(value):
                raise ValueError(f"extra_env value for {key!r} contains a null byte")
        self._render_mssql_conf(cfg)

    def _storage_class(self, cluster_id: str, cfg: dict[str, Any]) -> str:
        requested = str(cfg.get("storage_class_name") or self._config.storage_class_name)
        classes = self._config.cluster_driver.list_storage_classes(cluster_id)
        if requested:
            if requested not in {row.name for row in classes}:
                raise ValueError(f"StorageClass {requested!r} is not installed on cluster {cluster_id!r}")
            return requested
        defaults = [row.name for row in classes if row.is_default]
        if len(defaults) != 1:
            raise ValueError("SQL Server requires storage_class_name when the cluster lacks one unambiguous default")
        return str(defaults[0])

    def _credentials_for(
        self,
        *,
        cluster_id: str,
        namespace: str,
        name: str,
        database: str,
        username: str,
        trust_server_certificate: bool,
    ) -> dict[str, str]:
        path = self._credential_path_parts(cluster_id, namespace, name)
        current = self._config.secrets_backend.get(path)
        if current is not None:
            bundle = self._validate_bundle(current)
            if bundle["database"] != database or bundle["username"] != username:
                raise ValueError("existing SQL Server credentials disagree with immutable database or username")
            if bundle.get("trust_server_certificate", "true") != str(trust_server_certificate).lower():
                raise ValueError("existing SQL Server credentials disagree with immutable TLS trust mode")
            return self._validate_endpoint_bundle(bundle, self._host(namespace, name))
        k8s_secret = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            "v1/Secret",
            self._secret_name(name),
        )
        if k8s_secret is not None:
            bundle = self._decode_k8s_secret(k8s_secret)
            if bundle["database"] != database or bundle["username"] != username:
                raise ValueError("retained Kubernetes credentials disagree with requested database or username")
            if bundle.get("trust_server_certificate", "true") != str(trust_server_certificate).lower():
                raise ValueError("retained Kubernetes credentials disagree with immutable TLS trust mode")
            bundle = self._validate_endpoint_bundle(bundle, self._host(namespace, name))
            self._config.secrets_backend.upsert(path, bundle)
            return bundle
        host = self._host(namespace, name)
        password = self._password()
        bundle = {
            "sa_password": self._password(),
            "username": username,
            "password": password,
            "database": database,
            "trust_server_certificate": str(trust_server_certificate).lower(),
            "database_url": self._database_url(
                host,
                database,
                username,
                password,
                trust_server_certificate=trust_server_certificate,
            ),
        }
        self._config.secrets_backend.upsert(path, bundle)
        return self._read_valid_bundle(path, host=host)

    def _read_valid_bundle(self, path: str, *, host: str = "") -> dict[str, str]:
        value = self._config.secrets_backend.get(path)
        if value is None:
            raise ValueError(f"SQL Server credential bundle {path!r} does not exist")
        bundle = self._validate_bundle(value)
        return self._validate_endpoint_bundle(bundle, host) if host else bundle

    def _validate_bundle(self, raw: Any) -> dict[str, str]:
        if not isinstance(raw, dict):
            raise ValueError("SQL Server credential bundle must be an object")
        missing = sorted(_SECRET_KEYS - raw.keys())
        if missing:
            raise ValueError(f"SQL Server credential bundle is missing {', '.join(missing)}")
        allowed = _SECRET_KEYS | {"trust_server_certificate"}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(f"SQL Server credential bundle has unsupported keys: {', '.join(unknown)}")
        bundle = {str(key): str(value) for key, value in raw.items()}
        if not _SQL_IDENTIFIER.fullmatch(bundle["database"]) or not _SQL_IDENTIFIER.fullmatch(bundle["username"]):
            raise ValueError("SQL Server credential bundle has invalid database or username")
        for key in ("sa_password", "password"):
            if len(bundle[key]) < 16:
                raise ValueError(f"SQL Server credential bundle {key} is too short")
        trust = bundle.get("trust_server_certificate", "true").lower()
        if trust not in {"true", "false"}:
            raise ValueError("SQL Server credential bundle has an invalid TLS trust flag")
        bundle["trust_server_certificate"] = trust
        return bundle

    def _validate_endpoint_bundle(self, bundle: dict[str, str], host: str) -> dict[str, str]:
        expected = self._database_url(
            host,
            bundle["database"],
            bundle["username"],
            bundle["password"],
            trust_server_certificate=bundle["trust_server_certificate"] == "true",
        )
        if bundle["database_url"] != expected:
            raise ValueError("SQL Server credential bundle database_url disagrees with its owned endpoint")
        return bundle

    def _decode_k8s_secret(self, secret: dict[str, Any]) -> dict[str, str]:
        data = secret.get("data", {}) or {}
        if not isinstance(data, dict):
            raise ValueError("retained SQL Server Secret data is malformed")
        decoded: dict[str, str] = {}
        for key in (*sorted(_SECRET_KEYS), "trust_server_certificate"):
            value = data.get(key)
            if value is None and key == "trust_server_certificate":
                decoded[key] = "true"
                continue
            if not isinstance(value, str):
                raise ValueError(f"retained SQL Server Secret is missing data.{key}")
            try:
                decoded[key] = base64.b64decode(value, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise ValueError(f"retained SQL Server Secret data.{key} is malformed") from exc
        return self._validate_bundle(decoded)

    def _manifests(
        self,
        *,
        spec: ProvisionSpec,
        namespace: str,
        name: str,
        cfg: dict[str, Any],
        storage_class: str,
        credentials: dict[str, str],
        snapshot_name: str = "",
    ) -> list[dict[str, Any]]:
        labels = self._labels(spec, name)
        manifests = [
            self._secret(namespace, name, labels, credentials),
            self._config_map(namespace, name, labels, cfg),
            self._headless_service(namespace, name, labels),
            self._service(namespace, name, labels, cfg),
            self._pvc(namespace, name, labels, cfg, storage_class, snapshot_name),
        ]
        policy = self._network_policy(namespace, name, labels, cfg)
        if policy is not None:
            manifests.append(policy)
        manifests.append(self._statefulset(namespace, name, labels, cfg))
        return manifests

    def _secret(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        credentials: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": self._secret_name(name), "namespace": namespace, "labels": labels},
            "type": "Opaque",
            "stringData": credentials,
        }

    def _config_map(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": self._config_name(name), "namespace": namespace, "labels": labels},
            "data": {"mssql.conf": self._render_mssql_conf(cfg)},
        }

    def _headless_service(self, namespace: str, name: str, labels: dict[str, str]) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": self._headless_name(name), "namespace": namespace, "labels": labels},
            "spec": {
                "clusterIP": "None",
                "publishNotReadyAddresses": True,
                "selector": {"astrolift.io/resource": name},
                "ports": [{"name": "tds", "port": PORT, "targetPort": "tds"}],
            },
        }

    def _service(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "type": str(cfg["service_type"]),
            "selector": {"astrolift.io/resource": name},
            "ports": [{"name": "tds", "port": PORT, "targetPort": "tds"}],
        }
        if cfg["service_type"] == "LoadBalancer":
            spec["loadBalancerSourceRanges"] = list(cfg["load_balancer_source_ranges"])
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": dict(cfg.get("service_annotations") or {}),
            },
            "spec": spec,
        }

    def _pvc(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        cfg: dict[str, Any],
        storage_class: str,
        snapshot_name: str,
    ) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "accessModes": [str(cfg["storage_access_mode"])],
            "storageClassName": storage_class,
            "resources": {"requests": {"storage": str(cfg["storage_size"])}},
        }
        if snapshot_name:
            spec["dataSource"] = {
                "apiGroup": "snapshot.storage.k8s.io",
                "kind": "VolumeSnapshot",
                "name": snapshot_name,
            }
        return {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": self._pvc_name(name), "namespace": namespace, "labels": labels},
            "spec": spec,
        }

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
        peers: list[dict[str, Any]] = [{"podSelector": {}}]
        if mode == "managed_namespaces":
            organization = labels["astrolift.io/organization"]
            peers.extend(
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
        if cfg["service_type"] == "LoadBalancer":
            peers.extend({"ipBlock": {"cidr": str(cidr)}} for cidr in cfg["load_balancer_source_ranges"])
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": self._policy_name(name), "namespace": namespace, "labels": labels},
            "spec": {
                "podSelector": {"matchLabels": {"astrolift.io/resource": name}},
                "policyTypes": ["Ingress"],
                "ingress": [{"from": peers, "ports": [{"protocol": "TCP", "port": PORT}]}],
            },
        }

    def _statefulset(
        self,
        namespace: str,
        name: str,
        labels: dict[str, str],
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        pod_labels = {**labels, "astrolift.io/resource": name}
        secret_name = self._secret_name(name)
        env = self._container_env(secret_name, cfg)
        volume_mounts = [
            {"name": "data", "mountPath": "/var/opt/mssql"},
            {"name": "config", "mountPath": "/var/opt/mssql/mssql.conf", "subPath": "mssql.conf", "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
        ]
        volumes: list[dict[str, Any]] = [
            {"name": "data", "persistentVolumeClaim": {"claimName": self._pvc_name(name)}},
            {"name": "config", "configMap": {"name": self._config_name(name), "defaultMode": 0o440}},
            {"name": "tmp", "emptyDir": {}},
        ]
        if cfg.get("tls_secret_name"):
            volume_mounts.append({"name": "tls", "mountPath": "/var/opt/mssql/tls", "readOnly": True})
            volumes.append(
                {
                    "name": "tls",
                    "secret": {"secretName": str(cfg["tls_secret_name"]), "defaultMode": 0o440},
                }
            )
        node_selector = {"kubernetes.io/arch": "amd64", **dict(cfg.get("node_selector") or {})}
        node_selector["kubernetes.io/arch"] = "amd64"
        pod_spec: dict[str, Any] = {
            "automountServiceAccountToken": False,
            "terminationGracePeriodSeconds": int(cfg["termination_grace_period_seconds"]),
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "runAsGroup": 0,
                "fsGroup": 10001,
                "fsGroupChangePolicy": "OnRootMismatch",
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "nodeSelector": node_selector,
            "containers": [
                {
                    "name": "mssql",
                    "image": str(cfg["image"]),
                    "imagePullPolicy": str(cfg["image_pull_policy"]),
                    "ports": [{"name": "tds", "containerPort": PORT, "protocol": "TCP"}],
                    "env": env,
                    "resources": {
                        "requests": {"cpu": str(cfg["cpu_request"]), "memory": str(cfg["memory_request"])},
                        "limits": {"cpu": str(cfg["cpu_limit"]), "memory": str(cfg["memory_limit"])},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "startupProbe": {
                        "tcpSocket": {"port": "tds"},
                        "periodSeconds": 5,
                        "failureThreshold": 60,
                        "timeoutSeconds": 2,
                    },
                    "readinessProbe": {
                        "tcpSocket": {"port": "tds"},
                        "periodSeconds": 5,
                        "failureThreshold": 6,
                        "timeoutSeconds": 2,
                    },
                    "livenessProbe": {
                        "tcpSocket": {"port": "tds"},
                        "periodSeconds": 10,
                        "failureThreshold": 6,
                        "timeoutSeconds": 2,
                    },
                    "volumeMounts": volume_mounts,
                }
            ],
            "volumes": volumes,
        }
        for source, target in (
            ("image_pull_secrets", "imagePullSecrets"),
            ("tolerations", "tolerations"),
            ("affinity", "affinity"),
            ("priority_class_name", "priorityClassName"),
            ("runtime_class_name", "runtimeClassName"),
        ):
            value = cfg.get(source)
            if value:
                pod_spec[target] = (
                    [{"name": str(item)} for item in value] if source == "image_pull_secrets" else copy.deepcopy(value)
                )
        return {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {"name": name, "namespace": namespace, "labels": labels},
            "spec": {
                "serviceName": self._headless_name(name),
                "replicas": 1,
                "podManagementPolicy": "OrderedReady",
                "updateStrategy": {"type": "RollingUpdate"},
                "selector": {"matchLabels": {"astrolift.io/resource": name}},
                "template": {
                    "metadata": {
                        "labels": pod_labels,
                        "annotations": dict(cfg.get("pod_annotations") or {}),
                    },
                    "spec": pod_spec,
                },
            },
        }

    @staticmethod
    def _container_env(secret_name: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        env: list[dict[str, Any]] = [
            {"name": "ACCEPT_EULA", "value": "Y"},
            {"name": "MSSQL_PID", "value": "Express"},
            {"name": "MSSQL_TCP_PORT", "value": str(PORT)},
            {"name": "MSSQL_SA_PASSWORD", "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "sa_password"}}},
            {"name": "MSSQL_DB", "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "database"}}},
            {"name": "MSSQL_USER", "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "username"}}},
            {"name": "MSSQL_PASSWORD", "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "password"}}},
            {"name": "MSSQL_MEMORY_LIMIT_MB", "value": str(cfg["memory_limit_mb"])},
        ]
        if cfg.get("collation"):
            env.append({"name": "MSSQL_COLLATION", "value": str(cfg["collation"])})
        if cfg.get("lcid"):
            env.append({"name": "MSSQL_LCID", "value": str(cfg["lcid"])})
        env.extend(
            {"name": str(key), "value": str(value)} for key, value in sorted(dict(cfg.get("extra_env") or {}).items())
        )
        return env

    def _render_mssql_conf(self, cfg: dict[str, Any]) -> str:
        raw = cfg.get("mssql_conf") or {}
        if not isinstance(raw, dict):
            raise ValueError("mssql_conf must be an object of sections")
        sections: dict[str, dict[str, str]] = {}
        for section, values in raw.items():
            section_key = str(section).lower()
            if not re.fullmatch(r"[a-z][a-z0-9_]*", section_key) or not isinstance(values, dict):
                raise ValueError("mssql_conf section names and values are invalid")
            rendered: dict[str, str] = {}
            for key, value in values.items():
                key_name = str(key).lower()
                if not re.fullmatch(r"[a-z][a-z0-9_]*", key_name):
                    raise ValueError("mssql_conf keys must be alphanumeric identifiers")
                if (section_key, key_name) in _RESERVED_CONF:
                    raise ValueError(f"mssql_conf cannot override reserved setting {section_key}.{key_name}")
                rendered_value = str(value).lower() if isinstance(value, bool) else str(value)
                if any(char in rendered_value for char in ("\r", "\n", "\x00")):
                    raise ValueError(f"mssql_conf value {section_key}.{key_name} contains a control character")
                if len(rendered_value) > 1024:
                    raise ValueError(f"mssql_conf value {section_key}.{key_name} is too long")
                rendered[key_name] = rendered_value
            sections[section_key] = rendered
        network = sections.setdefault("network", {})
        network.update({"tcpport": str(PORT), "forceencryption": "1", "tlsprotocols": "1.2"})
        if cfg.get("tls_secret_name"):
            network.update(
                {
                    "tlscert": "/var/opt/mssql/tls/tls.crt",
                    "tlskey": "/var/opt/mssql/tls/tls.key",
                }
            )
        sections.setdefault("memory", {})["memorylimitmb"] = str(cfg["memory_limit_mb"])
        lines: list[str] = []
        for section in sorted(sections):
            lines.append(f"[{section}]")
            lines.extend(f"{key} = {value}" for key, value in sorted(sections[section].items()))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _validate_existing_ownership(self, spec: ProvisionSpec, namespace: str, name: str) -> None:
        for kind, resource_name in (
            ("apps/v1/StatefulSet", name),
            ("v1/PersistentVolumeClaim", self._pvc_name(name)),
        ):
            current = self._config.cluster_driver.get_manifest(spec.tenant_cluster_id, namespace, kind, resource_name)
            if current is None:
                continue
            labels = (current.get("metadata", {}) or {}).get("labels", {}) or {}
            if labels.get("app.kubernetes.io/managed-by") != "astrolift":
                raise ValueError(f"refusing to adopt existing {kind}/{resource_name} without Astrolift ownership")
            expected = self._label_value(spec.managed_service_id) if spec.managed_service_id else ""
            actual = str(labels.get("astrolift.io/managed-service") or "")
            if expected and actual and actual != expected:
                raise ValueError(f"existing {kind}/{resource_name} belongs to another managed service")

    def _required_live(self, parsed: Any, kind: str, name: str) -> dict[str, Any]:
        obj = self._config.cluster_driver.get_manifest(parsed.cluster_id, parsed.namespace, kind, name)
        if obj is None:
            raise ValueError(f"{kind}/{name} does not exist")
        return copy.deepcopy(obj)

    def _current_runtime_config(
        self,
        parsed: Any,
        statefulset: dict[str, Any],
        pvc: dict[str, Any],
        config_map: dict[str, Any],
    ) -> dict[str, Any]:
        template = (statefulset.get("spec", {}) or {}).get("template", {}) or {}
        pod_spec = template.get("spec", {}) or {}
        containers = pod_spec.get("containers") or []
        container = next((row for row in containers if row.get("name") == "mssql"), None)
        if container is None:
            raise ValueError("live SQL Server StatefulSet has no mssql container")
        resources = container.get("resources", {}) or {}
        requests = resources.get("requests", {}) or {}
        limits = resources.get("limits", {}) or {}
        pvc_spec = pvc.get("spec", {}) or {}
        storage = ((pvc_spec.get("resources", {}) or {}).get("requests", {}) or {}).get("storage")
        required = {
            "cpu_request": requests.get("cpu"),
            "cpu_limit": limits.get("cpu"),
            "memory_request": requests.get("memory"),
            "memory_limit": limits.get("memory"),
            "storage_size": storage,
        }
        missing = sorted(key for key, value in required.items() if value in (None, ""))
        if missing:
            raise ValueError(f"live SQL Server manifests are missing {', '.join(missing)}")

        env_rows = container.get("env") or []
        literal_env = {
            str(row.get("name")): str(row.get("value"))
            for row in env_rows
            if row.get("name") and row.get("value") is not None
        }
        memory_limit_mb = literal_env.get("MSSQL_MEMORY_LIMIT_MB")
        if not memory_limit_mb:
            raise ValueError("live SQL Server StatefulSet has no MSSQL_MEMORY_LIMIT_MB")
        extra_env = {key: value for key, value in literal_env.items() if key not in _RESERVED_ENV}
        credentials = self._read_valid_bundle(
            self._credential_path(parsed),
            host=self._host(parsed.namespace, parsed.name),
        )
        volumes: list[dict[str, Any]] = list(pod_spec.get("volumes") or [])
        tls_volume: dict[str, Any] = next((row for row in volumes if row.get("name") == "tls"), {})
        tls_secret_name = str((tls_volume.get("secret") or {}).get("secretName") or "")
        node_selector = dict(pod_spec.get("nodeSelector") or {})
        node_selector.pop("kubernetes.io/arch", None)
        cfg: dict[str, Any] = {
            **required,
            "memory_limit_mb": int(memory_limit_mb),
            "database": credentials["database"],
            "username": credentials["username"],
            "storage_class_name": str(pvc_spec.get("storageClassName") or ""),
            "storage_access_mode": str((pvc_spec.get("accessModes") or ["ReadWriteOnce"])[0]),
            "image": str(container.get("image") or ""),
            "image_pull_policy": str(container.get("imagePullPolicy") or "IfNotPresent"),
            "image_pull_secrets": [
                str(row["name"])
                for row in pod_spec.get("imagePullSecrets") or []
                if isinstance(row, dict) and row.get("name")
            ],
            "node_selector": node_selector,
            "tolerations": copy.deepcopy(pod_spec.get("tolerations") or []),
            "affinity": copy.deepcopy(pod_spec.get("affinity") or {}),
            "priority_class_name": str(pod_spec.get("priorityClassName") or ""),
            "runtime_class_name": str(pod_spec.get("runtimeClassName") or ""),
            "pod_annotations": dict((template.get("metadata", {}) or {}).get("annotations") or {}),
            "extra_env": extra_env,
            "mssql_conf": self._parse_mssql_conf(str((config_map.get("data", {}) or {}).get("mssql.conf") or "")),
            "termination_grace_period_seconds": int(pod_spec.get("terminationGracePeriodSeconds") or 120),
            "tls_secret_name": tls_secret_name,
        }
        if literal_env.get("MSSQL_COLLATION"):
            cfg["collation"] = literal_env["MSSQL_COLLATION"]
        if literal_env.get("MSSQL_LCID"):
            cfg["lcid"] = int(literal_env["MSSQL_LCID"])
        return cfg

    @staticmethod
    def _parse_mssql_conf(value: str) -> dict[str, dict[str, str]]:
        sections: dict[str, dict[str, str]] = {}
        section = ""
        for raw_line in value.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                continue
            if not section or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            key = key.strip().lower()
            if (section, key) not in _RESERVED_CONF:
                sections.setdefault(section, {})[key] = raw.strip()
        return sections

    def _apply_runtime_update(
        self,
        statefulset: dict[str, Any],
        config_map: dict[str, Any],
        cfg: dict[str, Any],
    ) -> None:
        template = statefulset.setdefault("spec", {}).setdefault("template", {})
        pod_spec = template.setdefault("spec", {})
        containers = pod_spec.get("containers") or []
        container = next((row for row in containers if row.get("name") == "mssql"), None)
        if container is None:
            raise ValueError("live SQL Server StatefulSet has no mssql container")
        container["image"] = str(cfg["image"])
        container["imagePullPolicy"] = str(cfg["image_pull_policy"])
        container["resources"] = {
            "requests": {"cpu": str(cfg["cpu_request"]), "memory": str(cfg["memory_request"])},
            "limits": {"cpu": str(cfg["cpu_limit"]), "memory": str(cfg["memory_limit"])},
        }
        current_env: list[dict[str, Any]] = list(container.get("env") or [])
        sa_password: dict[str, Any] = next(
            (row for row in current_env if row.get("name") == "MSSQL_SA_PASSWORD"),
            {},
        )
        secret_name = str(((sa_password.get("valueFrom") or {}).get("secretKeyRef") or {}).get("name") or "")
        if not secret_name:
            raise ValueError("live SQL Server StatefulSet has no credential Secret reference")
        container["env"] = self._container_env(secret_name, cfg)
        pod_spec["terminationGracePeriodSeconds"] = int(cfg["termination_grace_period_seconds"])
        pod_spec["nodeSelector"] = {"kubernetes.io/arch": "amd64", **dict(cfg.get("node_selector") or {})}
        pod_spec["nodeSelector"]["kubernetes.io/arch"] = "amd64"
        for source, target in (
            ("image_pull_secrets", "imagePullSecrets"),
            ("tolerations", "tolerations"),
            ("affinity", "affinity"),
            ("priority_class_name", "priorityClassName"),
            ("runtime_class_name", "runtimeClassName"),
        ):
            value = cfg.get(source)
            if value:
                pod_spec[target] = (
                    [{"name": str(item)} for item in value] if source == "image_pull_secrets" else copy.deepcopy(value)
                )
            else:
                pod_spec.pop(target, None)
        template.setdefault("metadata", {})["annotations"] = dict(cfg.get("pod_annotations") or {})
        config_map.setdefault("data", {})["mssql.conf"] = self._render_mssql_conf(cfg)

    def _apply_storage_update(self, parsed: Any, pvc: dict[str, Any], requested: str) -> None:
        current = str(
            (((pvc.get("spec", {}) or {}).get("resources", {}) or {}).get("requests", {}) or {}).get("storage") or ""
        )
        if not current:
            raise ValueError("live SQL Server PVC has no storage request")
        if self._bytes(requested) < self._bytes(current):
            raise ValueError(f"PVC storage cannot shrink ({current} -> {requested})")
        if requested != current:
            storage_class = str((pvc.get("spec", {}) or {}).get("storageClassName") or "")
            storage = self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                None,
                "storage.k8s.io/v1/StorageClass",
                storage_class,
            )
            if storage is None or storage.get("allowVolumeExpansion") is not True:
                raise ValueError(f"StorageClass {storage_class!r} does not allow volume expansion")
            pvc.setdefault("spec", {}).setdefault("resources", {}).setdefault("requests", {})["storage"] = requested

    def _clean_live(self, obj: dict[str, Any]) -> dict[str, Any]:
        obj = copy.deepcopy(obj)
        obj.pop("status", None)
        metadata = obj.setdefault("metadata", {})
        for key in ("creationTimestamp", "generation", "managedFields", "resourceVersion", "selfLink", "uid"):
            metadata.pop(key, None)
        return obj

    def _preflight_snapshot(self, cluster_id: str) -> None:
        crd = self._config.cluster_driver.get_manifest(
            cluster_id,
            None,
            "apiextensions.k8s.io/v1/CustomResourceDefinition",
            "volumesnapshots.snapshot.storage.k8s.io",
        )
        snapshot_class = self._config.cluster_driver.get_manifest(
            cluster_id,
            None,
            "snapshot.storage.k8s.io/v1/VolumeSnapshotClass",
            self._config.volume_snapshot_class,
        )
        if crd is None or snapshot_class is None:
            raise UnsupportedOperationError(
                "VolumeSnapshot CRDs and the configured VolumeSnapshotClass must be installed"
            )

    def _wait_snapshot_ready(self, cluster_id: str, namespace: str, name: str) -> dict[str, Any]:
        deadline = time.monotonic() + max(self._config.deletion_timeout_seconds, 1)
        while time.monotonic() < deadline:
            maybe_heartbeat(f"mssql.snapshot:{name}")
            obj = self._config.cluster_driver.get_manifest(
                cluster_id,
                namespace,
                "snapshot.storage.k8s.io/v1/VolumeSnapshot",
                name,
            )
            if obj is not None:
                status = obj.get("status", {}) or {}
                error = status.get("error")
                if error:
                    raise RuntimeError(f"VolumeSnapshot {name} failed: {error}")
                if status.get("readyToUse") is True:
                    return dict(obj)
            time.sleep(max(self._config.deletion_poll_seconds, 0))
        raise TimeoutError(f"VolumeSnapshot {name} did not become ready")

    def _wait_absent(self, parsed: Any, kind: str, name: str) -> bool:
        deadline = time.monotonic() + max(self._config.deletion_timeout_seconds, 0)
        while True:
            maybe_heartbeat(f"mssql.delete:{kind}/{name}")
            if self._config.cluster_driver.get_manifest(parsed.cluster_id, parsed.namespace, kind, name) is None:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(self._config.deletion_poll_seconds, 0))

    def _name(self, spec: ProvisionSpec) -> str:
        return dns_label(
            "mssql",
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
            max_length=50,
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
            raise ValueError("SQL Server namespace must be a Kubernetes DNS label")
        return value

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
            "app.kubernetes.io/name": "mssql-server",
            "app.kubernetes.io/instance": name,
            "app.kubernetes.io/managed-by": "astrolift",
            "astrolift.io/organization": self._label_value(spec.organization_slug),
            "astrolift.io/app": self._label_value(spec.app_slug),
            "astrolift.io/environment": self._label_value(spec.environment_name),
            "astrolift.io/resource": name,
        }
        if spec.managed_service_id:
            labels["astrolift.io/managed-service"] = self._label_value(spec.managed_service_id)
        return labels

    def _credential_path(self, parsed: Any) -> str:
        return self._credential_path_parts(parsed.cluster_id, parsed.namespace, parsed.name)

    def _credential_path_parts(self, cluster_id: str, namespace: str, name: str) -> str:
        return f"{self._config.credential_path_prefix.strip('/')}/{cluster_id}/{namespace}/{name}/credentials"

    @staticmethod
    def _host(namespace: str, name: str) -> str:
        return f"{name}.{namespace}.svc.cluster.local"

    @staticmethod
    def _database_url(
        host: str,
        database: str,
        username: str,
        password: str,
        *,
        trust_server_certificate: bool,
    ) -> str:
        trust = "true" if trust_server_certificate else "false"
        return (
            f"sqlserver://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{PORT}/"
            f"{quote(database, safe='')}?encrypt=true&trustServerCertificate={trust}"
        )

    @staticmethod
    def _password() -> str:
        alphabet = string.ascii_letters + string.digits + "!#%+,-.:=@_"
        chars = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice("!#%+,-.:=@_"),
            *(secrets.choice(alphabet) for _ in range(28)),
        ]
        secrets.SystemRandom().shuffle(chars)
        return "".join(chars)

    @staticmethod
    def _label_value(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value))[:63].strip("-_.")
        return cleaned or "unknown"

    @staticmethod
    def _cpu_millis(value: str) -> int:
        if value.endswith("m"):
            return int(value[:-1])
        return int(float(value) * 1000)

    @staticmethod
    def _bytes(value: str) -> int:
        match = re.fullmatch(r"([1-9][0-9]*)([EPTGMK]i?|m)?", value)
        if not match:
            raise ValueError(f"unsupported Kubernetes quantity {value!r}")
        amount = int(match.group(1))
        unit = match.group(2) or ""
        if unit == "m":
            return amount // 1000
        binary = unit.endswith("i")
        symbol = unit[:-1] if binary else unit
        exponent = {"": 0, "K": 1, "M": 2, "G": 3, "T": 4, "P": 5, "E": 6}.get(symbol)
        if exponent is None:
            raise ValueError(f"unsupported Kubernetes quantity {value!r}")
        return int(amount * ((1024 if binary else 1000) ** exponent))

    @staticmethod
    def _parse_snapshot_id(value: str) -> tuple[str, str, str]:
        parts = value.split("/")
        if len(parts) != 3 or any(not part for part in parts):
            raise ValueError("snapshot_id must be <cluster>/<namespace>/<name>")
        return parts[0], parts[1], parts[2]

    @staticmethod
    def _secret_name(name: str) -> str:
        return f"{name}-credentials"

    @staticmethod
    def _config_name(name: str) -> str:
        return f"{name}-config"

    @staticmethod
    def _pvc_name(name: str) -> str:
        return f"{name}-data"

    @staticmethod
    def _headless_name(name: str) -> str:
        return f"{name}-headless"

    @staticmethod
    def _policy_name(name: str) -> str:
        return f"{name}-ingress"

    @staticmethod
    def _stub(api_version: str, kind: str, name: str, namespace: str) -> dict[str, Any]:
        return {"apiVersion": api_version, "kind": kind, "metadata": {"name": name, "namespace": namespace}}


__all__ = ["DEFAULT_IMAGE", "SQLServerExpressConfig", "SQLServerExpressDriver"]
