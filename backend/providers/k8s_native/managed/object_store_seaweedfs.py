"""S3-compatible object storage via the SeaweedFS Operator.

The operator-managed ``Seaweed`` cluster is an install-level shared service;
one Astrolift managed resource creates a bucket and a least-privilege IAM
identity inside that cluster.  It never deploys or deletes the shared storage
engine itself.

Variant key: ``('object_store', 'seaweedfs_operator')``.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op, maybe_heartbeat
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
    UpdateResult,
    UpdateSpec,
    ValueRef,
)
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle

KIND = "object_store"
_API_VERSION = "seaweed.seaweedfs.com/v1"
_SEAWEED_KIND = f"{_API_VERSION}/Seaweed"
_RESOURCE_KINDS = {
    "Bucket": f"{_API_VERSION}/Bucket",
    "S3Identity": f"{_API_VERSION}/S3Identity",
    "S3Credentials": f"{_API_VERSION}/S3Credentials",
    "S3Policy": f"{_API_VERSION}/S3Policy",
    "S3PolicyBinding": f"{_API_VERSION}/S3PolicyBinding",
}
_REQUIRED_CRDS = (
    "buckets.seaweed.seaweedfs.com",
    "s3identities.seaweed.seaweedfs.com",
    "s3credentials.seaweed.seaweedfs.com",
    "s3policies.seaweed.seaweedfs.com",
    "s3policybindings.seaweed.seaweedfs.com",
)
_VERSIONING_STATES = {"Off", "Enabled", "Suspended"}
_REPLICATION_RE = re.compile(r"^[0-9]{3}$")
_TTL_RE = re.compile(r"^[1-9][0-9]*[mhdwMy]$")
_QUANTITY_RE = re.compile(r"^[1-9][0-9]*(?:[EPTGMK]i?|m)?$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


@dataclass(frozen=True)
class SeaweedFSObjectStoreConfig:
    """Install-level location of a shared SeaweedFS storage cluster."""

    namespace: str = "astrolift-storage"
    seaweed_name: str = "astrolift-object-store"
    endpoint: str = ""
    endpoint_scheme: str = "http"
    endpoint_port: int = 8333
    region: str = "us-east-1"
    credential_path_prefix: str = "managed/object_store"
    verify_crds: bool = True
    deletion_timeout_seconds: float = 120.0
    deletion_poll_seconds: float = 2.0
    cluster_driver: Any | None = None
    secrets_backend: Any | None = None


class SeaweedFSObjectStoreDriver(ManagedServiceDriver):
    """Provision SeaweedFS ``Bucket`` and S3 IAM resources."""

    def __init__(self, *, config: SeaweedFSObjectStoreConfig) -> None:
        self._config = config

    @driver_op(
        cloud="k8s_native",
        driver="object_store_seaweedfs",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._validate_install_config()
            bucket_name = self._bucket_name(spec)
            bucket_config = self._bucket_config(spec.config)
        except ValueError as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=str(exc),
                errors=["invalid_object_store_config"],
            )

        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=self._config.namespace,
            name=bucket_name,
        )
        if self._config.cluster_driver is None:
            return ProvisionResult(
                ok=False,
                handle=handle,
                message="SeaweedFS object storage requires a cluster_driver",
                errors=["cluster_driver_missing"],
            )
        if self._config.secrets_backend is None:
            return ProvisionResult(
                ok=False,
                handle=handle,
                message="SeaweedFS object storage requires an external secrets backend",
                errors=["secrets_backend_missing"],
            )

        preflight_error = self._preflight(spec.tenant_cluster_id)
        if preflight_error:
            return ProvisionResult(
                ok=False,
                handle=handle,
                message=preflight_error,
                errors=["seaweedfs_preflight_failed"],
            )

        manifests = self._manifests(
            spec=spec,
            bucket_name=bucket_name,
            bucket_config=bucket_config,
            reclaim_policy="Retain",
        )
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            self._config.namespace,
            manifests,
        )
        if not result.ok:
            return ProvisionResult(
                ok=False,
                handle=handle,
                message="SeaweedFS object-store manifests were rejected",
                errors=result.summary(),
            )
        return ProvisionResult(
            ok=True,
            handle=handle,
            message=f"SeaweedFS bucket {bucket_name} submitted for reconciliation",
            ready=False,
        )

    @driver_op(cloud="k8s_native", driver="object_store_seaweedfs")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._validate_install_config()
            parsed = _unpack_handle(spec.handle)
            if parsed.is_legacy:
                raise ValueError("legacy object-store handle has no cluster locator")
            requested = self._bucket_config(spec.config)
        except ValueError as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=["invalid_object_store_config"],
            )
        if self._config.cluster_driver is None:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message="SeaweedFS object storage requires a cluster_driver",
                errors=["cluster_driver_missing"],
            )
        existing = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            _RESOURCE_KINDS["Bucket"],
            parsed.name,
        )
        if existing is None:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"SeaweedFS Bucket {parsed.name} does not exist",
                errors=["bucket_not_found"],
            )
        manifest = copy.deepcopy(existing)
        manifest.pop("status", None)
        metadata = manifest.setdefault("metadata", {})
        for key in ("creationTimestamp", "generation", "managedFields", "resourceVersion", "uid"):
            metadata.pop(key, None)
        current = manifest.setdefault("spec", {})
        current.update(self._bucket_spec_fields(requested))
        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [manifest],
        )
        if not result.ok:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message="SeaweedFS Bucket update was rejected",
                errors=result.summary(),
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"SeaweedFS Bucket {parsed.name} update submitted",
        )

    @driver_op(
        cloud="k8s_native",
        driver="object_store_seaweedfs",
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
        del force_destroy  # Object Lock retention cannot be bypassed safely.
        try:
            self._validate_install_config()
        except ValueError as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=["invalid_install_config"],
                retryable=False,
            )
        try:
            parsed = _unpack_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=["invalid_handle"],
                retryable=False,
            )
        if parsed.is_legacy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message="legacy object-store handle has no cluster locator",
                errors=["legacy_handle_missing_locator"],
                retryable=False,
            )
        if self._config.cluster_driver is None or self._config.secrets_backend is None:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message="object-store teardown requires cluster and secrets drivers",
                errors=["driver_dependency_missing"],
                retryable=False,
            )

        bucket = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            _RESOURCE_KINDS["Bucket"],
            parsed.name,
        )
        if bucket is not None:
            desired = copy.deepcopy(bucket)
            desired.pop("status", None)
            metadata = desired.setdefault("metadata", {})
            for key in ("creationTimestamp", "generation", "managedFields", "resourceVersion", "uid"):
                metadata.pop(key, None)
            desired.setdefault("spec", {})["reclaimPolicy"] = "Delete" if delete_data else "Retain"
            if not delete_data:
                # Retaining data also retains the Bucket CR so a later
                # provision can reconcile the same platform-owned object
                # without an unsafe adopt-existing opt-in.  Strip the
                # departing workload's ownership and anonymous access before
                # revoking its IAM resources.
                desired["spec"]["owner"] = ""
                desired["spec"]["access"] = []
                desired["spec"]["anonymousRead"] = False
            applied = self._config.cluster_driver.apply_manifests(
                parsed.cluster_id,
                parsed.namespace,
                [desired],
            )
            if not applied.ok:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message="could not set the SeaweedFS bucket reclaim policy",
                    errors=applied.summary(),
                )

        if delete_data:
            bucket_stub = self._stub("Bucket", parsed.name, parsed.namespace)
            deleted = self._config.cluster_driver.delete_manifests(
                parsed.cluster_id,
                parsed.namespace,
                [bucket_stub],
            )
            if not deleted.ok:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message="could not delete the SeaweedFS Bucket resource",
                    errors=deleted.summary(),
                )
            absent, last_bucket = self._wait_absent(
                parsed.cluster_id,
                parsed.namespace,
                "Bucket",
                parsed.name,
            )
            if not absent:
                blocked = self._retention_block(last_bucket)
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=(
                        f"SeaweedFS retained {parsed.name}: active Object Lock retention blocks deletion"
                        if blocked
                        else f"timed out waiting for SeaweedFS Bucket {parsed.name} deletion"
                    ),
                    errors=["object_lock_retention_active" if blocked else "bucket_delete_timeout"],
                    retryable=not blocked,
                )

        resource_names = self._resource_names(parsed.name)
        for kinds in (("S3PolicyBinding",), ("S3Credentials",), ("S3Policy", "S3Identity")):
            stubs = [self._stub(kind, resource_names[kind], parsed.namespace) for kind in kinds]
            result = self._config.cluster_driver.delete_manifests(
                parsed.cluster_id,
                parsed.namespace,
                stubs,
            )
            if not result.ok:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"could not delete SeaweedFS IAM resources: {', '.join(kinds)}",
                    errors=result.summary(),
                )
            for kind in kinds:
                absent, _ = self._wait_absent(
                    parsed.cluster_id,
                    parsed.namespace,
                    kind,
                    resource_names[kind],
                )
                if not absent:
                    return DeprovisionResult(
                        ok=False,
                        handle=spec.handle,
                        message=f"timed out waiting for {kind} {resource_names[kind]} deletion",
                        errors=["iam_resource_delete_timeout"],
                    )

        secret_stub = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": resource_names["Secret"], "namespace": parsed.namespace},
        }
        secret_delete = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [secret_stub],
        )
        if not secret_delete.ok:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message="could not delete the Kubernetes credential Secret",
                errors=secret_delete.summary(),
            )
        self._config.secrets_backend.delete(self._credential_path(parsed.namespace, parsed.name))
        disposition = "deleted" if delete_data else "retained in its operator-managed Bucket CR"
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"SeaweedFS bucket data {disposition}; IAM credentials revoked",
        )

    @driver_op(cloud="k8s_native", driver="object_store_seaweedfs")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._validate_install_config()
            parsed = _unpack_handle(handle.handle)
        except ValueError as exc:
            return ServiceStatus(handle=handle.handle, state="error", message=str(exc))
        if parsed.is_legacy or self._config.cluster_driver is None or self._config.secrets_backend is None:
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message="object-store status requires a cluster-locating handle, cluster driver, and secrets backend",
            )
        names = self._resource_names(parsed.name)
        phases: dict[str, str] = {}
        messages: list[str] = []
        for kind in ("Bucket", "S3Identity", "S3Credentials", "S3Policy", "S3PolicyBinding"):
            name = parsed.name if kind == "Bucket" else names[kind]
            obj = self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                _RESOURCE_KINDS[kind],
                name,
            )
            if obj is None:
                if kind == "Bucket":
                    return ServiceStatus(
                        handle=handle.handle,
                        state="deprovisioned",
                        message=f"SeaweedFS Bucket {parsed.name} does not exist",
                    )
                phases[kind] = "Missing"
                continue
            status = obj.get("status", {}) or {}
            phase = str(status.get("phase", "Pending") or "Pending")
            phases[kind] = phase
            if phase == "Failed":
                messages.extend(self._condition_messages(status))
        if any(phase == "Failed" for phase in phases.values()):
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message="; ".join(messages) or f"SeaweedFS reconciliation failed: {phases}",
            )
        if phases and all(phase == "Ready" for phase in phases.values()):
            credentials_status = self._sync_operator_credentials(
                cluster_id=parsed.cluster_id,
                namespace=parsed.namespace,
                bucket_name=parsed.name,
                secret_name=names["Secret"],
            )
            if credentials_status is not None:
                return ServiceStatus(
                    handle=handle.handle,
                    state=credentials_status[0],
                    message=credentials_status[1],
                )
            return ServiceStatus(
                handle=handle.handle,
                state="available",
                message=f"SeaweedFS bucket {parsed.name} and IAM bindings are ready",
            )
        return ServiceStatus(
            handle=handle.handle,
            state="provisioning",
            message=f"SeaweedFS reconciliation pending: {phases}",
        )

    @driver_op(cloud="k8s_native", driver="object_store_seaweedfs")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        self._validate_install_config()
        parsed = _unpack_handle(handle.handle)
        if parsed.is_legacy:
            raise ValueError("legacy object-store handle has no cluster locator")
        cfg = config or {}
        prefix = str(cfg.get("prefix", "")).strip("/")
        credentials = self._credential_path(parsed.namespace, parsed.name)
        endpoint = self._endpoint()
        bucket_arn = f"arn:aws:s3:::{parsed.name}"
        return Binding(
            env_vars={
                "BUCKET_NAME": ValueRef(literal=parsed.name),
                "BUCKET_REGION": ValueRef(literal=self._config.region),
                "BUCKET_ENDPOINT": ValueRef(literal=endpoint),
                "BUCKET_PREFIX": ValueRef(literal=prefix),
                "AWS_ACCESS_KEY_ID": ValueRef(secret_ref=f"{credentials}#accessKey"),
                "AWS_SECRET_ACCESS_KEY": ValueRef(secret_ref=f"{credentials}#secretKey"),
                "AWS_REGION": ValueRef(literal=self._config.region),
                "AWS_DEFAULT_REGION": ValueRef(literal=self._config.region),
                "AWS_ENDPOINT_URL_S3": ValueRef(literal=endpoint),
                "AWS_S3_FORCE_PATH_STYLE": ValueRef(literal="true"),
                "S3_ENDPOINT_URL": ValueRef(literal=endpoint),
                "S3_BUCKET_ARN": ValueRef(literal=bucket_arn),
            },
            notes=(
                "S3-compatible credentials are scoped to this bucket and stored in the install secrets backend; "
                "path-style requests are required."
            ),
        )

    @driver_op(cloud="k8s_native", driver="object_store_seaweedfs")
    def snapshot(self, handle):
        del handle
        raise UnsupportedOperationError(
            "managed_service.snapshot(SeaweedFS bucket) has no truthful point-in-time implementation (#1287)",
        )

    @driver_op(cloud="k8s_native", driver="object_store_seaweedfs")
    def restore(self, snapshot, target):
        del snapshot, target
        raise UnsupportedOperationError(
            "managed_service.restore(SeaweedFS bucket) requires an operator backup/restore workflow (#1287)",
        )

    @driver_op(cloud="k8s_native", driver="object_store_seaweedfs", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "bucket_name": {
                    "type": "string",
                    "minLength": 3,
                    "maxLength": 63,
                    "pattern": r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$",
                },
                "prefix": {"type": "string"},
                "versioning": {
                    "type": "string",
                    "enum": sorted(_VERSIONING_STATES),
                    "default": "Enabled",
                },
                "object_lock": {"type": "boolean", "default": False},
                "adopt_existing": {"type": "boolean", "default": False},
                "anonymous_read": {"type": "boolean", "default": False},
                "quota_size": {
                    "type": "string",
                    "pattern": r"^[1-9][0-9]*(?:[EPTGMK]i?|m)?$",
                },
                "quota_enforce": {"type": "boolean", "default": True},
                "placement": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "replication": {"type": "string", "pattern": r"^[0-9]{3}$"},
                        "disk_type": {"type": "string"},
                        "ttl": {"type": "string", "pattern": r"^[1-9][0-9]*[mhdwMy]$"},
                        "fsync": {"type": "boolean", "default": False},
                        "worm": {"type": "boolean", "default": False},
                        "read_only": {"type": "boolean", "default": False},
                        "data_center": {"type": "string"},
                        "rack": {"type": "string"},
                        "data_node": {"type": "string"},
                        "volume_growth_count": {"type": "integer", "minimum": 0},
                    },
                },
            },
            "allOf": [
                {
                    "if": {"properties": {"object_lock": {"const": True}}, "required": ["object_lock"]},
                    "then": {"properties": {"versioning": {"const": "Enabled"}}},
                }
            ],
        }

    @driver_op(cloud="k8s_native", driver="object_store_seaweedfs", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "BUCKET_NAME": "S3 bucket name",
                "BUCKET_REGION": "S3 signing region",
                "BUCKET_ENDPOINT": "S3-compatible endpoint",
                "BUCKET_PREFIX": "Optional object-key prefix",
                "AWS_ACCESS_KEY_ID": "Bucket-scoped access key from the secrets backend",
                "AWS_SECRET_ACCESS_KEY": "Bucket-scoped secret key from the secrets backend",
                "AWS_S3_FORCE_PATH_STYLE": "Always true for the in-cluster gateway",
            }
        )

    @driver_op(cloud="k8s_native", driver="object_store_seaweedfs", heartbeat=False)
    def editable_fields(self) -> list[str]:
        return [
            "prefix",
            "versioning",
            "object_lock",
            "anonymous_read",
            "quota_size",
            "quota_enforce",
            "placement",
        ]

    def _preflight(self, cluster_id: str) -> str:
        if self._config.verify_crds:
            for crd_name in _REQUIRED_CRDS:
                crd = self._config.cluster_driver.get_manifest(
                    cluster_id,
                    None,
                    "apiextensions.k8s.io/v1/CustomResourceDefinition",
                    crd_name,
                )
                if crd is None:
                    return f"SeaweedFS Operator CRD {crd_name} is not installed"
        seaweed = self._config.cluster_driver.get_manifest(
            cluster_id,
            self._config.namespace,
            _SEAWEED_KIND,
            self._config.seaweed_name,
        )
        if seaweed is None:
            return f"shared SeaweedFS cluster {self._config.namespace}/{self._config.seaweed_name} does not exist"
        s3 = (seaweed.get("spec", {}) or {}).get("s3")
        if not isinstance(s3, dict) or s3.get("enabled", True) is False:
            return "shared SeaweedFS cluster does not enable the standalone S3 gateway"
        return ""

    def _sync_operator_credentials(
        self,
        *,
        cluster_id: str,
        namespace: str,
        bucket_name: str,
        secret_name: str,
    ) -> tuple[str, str] | None:
        """Mirror the operator-owned key pair into the install secrets store.

        SeaweedFS is the sole credential issuer.  That keeps retries and
        concurrent provisions from racing two independent secret writes and
        leaving Kubernetes and the external store with different keys.
        ``None`` means the credentials are synchronized and ready.
        """

        secret = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            "v1/Secret",
            secret_name,
        )
        if secret is None:
            return (
                "provisioning",
                f"SeaweedFS credential Secret {namespace}/{secret_name} is not ready",
            )
        data = secret.get("data", {}) or {}
        try:
            credentials = {
                "accessKey": self._decode_secret_field(data, "accessKey"),
                "secretKey": self._decode_secret_field(data, "secretKey"),
            }
        except ValueError as exc:
            return ("error", str(exc))
        if not 12 <= len(credentials["accessKey"]) <= 64 or len(credentials["secretKey"]) < 32:
            return (
                "error",
                f"SeaweedFS credential Secret {namespace}/{secret_name} contains invalid key lengths",
            )

        path = self._credential_path(namespace, bucket_name)
        try:
            current = self._config.secrets_backend.get(path)
            if current != credentials:
                self._config.secrets_backend.upsert(path, credentials)
        except Exception:
            return (
                "error",
                "SeaweedFS credentials could not be synchronized to the external secrets backend",
            )
        return None

    @staticmethod
    def _decode_secret_field(data: Any, field: str) -> str:
        if not isinstance(data, dict) or not isinstance(data.get(field), str):
            raise ValueError(f"SeaweedFS credential Secret is missing data.{field}")
        try:
            decoded = base64.b64decode(data[field], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError(f"SeaweedFS credential Secret data.{field} is malformed") from exc
        if not decoded:
            raise ValueError(f"SeaweedFS credential Secret data.{field} is empty")
        return decoded

    def _manifests(
        self,
        *,
        spec: ProvisionSpec,
        bucket_name: str,
        bucket_config: dict[str, Any],
        reclaim_policy: str,
    ) -> list[dict[str, Any]]:
        names = self._resource_names(bucket_name)
        namespace = self._config.namespace
        labels = self._labels(spec)
        seaweed_ref = {"name": self._config.seaweed_name}
        bucket_spec = {
            "name": bucket_name,
            "clusterRef": seaweed_ref,
            "reclaimPolicy": reclaim_policy,
            "adoptExisting": bucket_config["adopt_existing"],
            "owner": names["S3Identity"],
            **self._bucket_spec_fields(bucket_config),
        }
        return [
            {
                "apiVersion": _API_VERSION,
                "kind": "S3Identity",
                "metadata": {"name": names["S3Identity"], "namespace": namespace, "labels": labels},
                "spec": {
                    "seaweedRef": seaweed_ref,
                    "name": names["S3Identity"],
                    "reclaimPolicy": "Delete",
                    "disabled": False,
                    "account": {"displayName": f"Astrolift {bucket_name}"},
                },
            },
            {
                "apiVersion": _API_VERSION,
                "kind": "S3Credentials",
                "metadata": {"name": names["S3Credentials"], "namespace": namespace, "labels": labels},
                "spec": {
                    "seaweedRef": seaweed_ref,
                    "identityRef": {"name": names["S3Identity"]},
                    "secretRef": {
                        "name": names["Secret"],
                        "accessKeyField": "accessKey",
                        "secretKeyField": "secretKey",
                    },
                    "reclaimPolicy": "Delete",
                },
            },
            {
                "apiVersion": _API_VERSION,
                "kind": "S3Policy",
                "metadata": {"name": names["S3Policy"], "namespace": namespace, "labels": labels},
                "spec": {
                    "seaweedRef": seaweed_ref,
                    "name": names["S3Policy"],
                    "reclaimPolicy": "Delete",
                    "statements": [
                        {
                            "sid": "BucketMetadata",
                            "effect": "Allow",
                            "actions": ["s3:GetBucketLocation", "s3:ListBucket"],
                            "resources": [bucket_name],
                        },
                        {
                            "sid": "BucketObjects",
                            "effect": "Allow",
                            "actions": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                            "resources": [f"{bucket_name}/*"],
                        },
                    ],
                },
            },
            {
                "apiVersion": _API_VERSION,
                "kind": "S3PolicyBinding",
                "metadata": {"name": names["S3PolicyBinding"], "namespace": namespace, "labels": labels},
                "spec": {
                    "seaweedRef": seaweed_ref,
                    "policyRef": {"name": names["S3Policy"]},
                    "subjects": [{"kind": "S3Identity", "name": names["S3Identity"]}],
                    "reclaimPolicy": "Delete",
                },
            },
            {
                "apiVersion": _API_VERSION,
                "kind": "Bucket",
                "metadata": {"name": bucket_name, "namespace": namespace, "labels": labels},
                "spec": bucket_spec,
            },
        ]

    def _bucket_spec_fields(self, config: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "versioning": config["versioning"],
            "objectLock": config["object_lock"],
            "anonymousRead": config["anonymous_read"],
        }
        if config["quota_size"]:
            fields["quota"] = {
                "size": config["quota_size"],
                "enforce": config["quota_enforce"],
            }
        else:
            fields["quota"] = None
        if config["placement"]:
            fields["placement"] = self._placement_manifest(config["placement"])
        else:
            fields["placement"] = None
        return fields

    def _bucket_config(self, config: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "bucket_name",
            "prefix",
            "versioning",
            "object_lock",
            "adopt_existing",
            "anonymous_read",
            "quota_size",
            "quota_enforce",
            "placement",
        }
        unknown_config = sorted(set(config) - allowed)
        if unknown_config:
            raise ValueError(f"object-store config contains unsupported fields: {unknown_config}")
        versioning = str(config.get("versioning", "Enabled"))
        if versioning not in _VERSIONING_STATES:
            raise ValueError(f"versioning must be one of {sorted(_VERSIONING_STATES)}")
        object_lock = bool(config.get("object_lock", False))
        if object_lock and versioning != "Enabled":
            raise ValueError("object_lock requires versioning='Enabled'")
        quota_size = str(config.get("quota_size", "")).strip()
        if quota_size and not _QUANTITY_RE.fullmatch(quota_size):
            raise ValueError("quota_size must be a positive canonical Kubernetes quantity")
        placement = config.get("placement") or {}
        if not isinstance(placement, dict):
            raise ValueError("placement must be an object")
        allowed_placement = {
            "replication",
            "disk_type",
            "ttl",
            "fsync",
            "worm",
            "read_only",
            "data_center",
            "rack",
            "data_node",
            "volume_growth_count",
        }
        unknown = sorted(set(placement) - allowed_placement)
        if unknown:
            raise ValueError(f"placement contains unsupported fields: {unknown}")
        replication = str(placement.get("replication", ""))
        if replication and not _REPLICATION_RE.fullmatch(replication):
            raise ValueError("placement.replication must be a three-digit SeaweedFS code")
        ttl = str(placement.get("ttl", ""))
        if ttl and not _TTL_RE.fullmatch(ttl):
            raise ValueError("placement.ttl must use SeaweedFS <integer><m|h|d|w|M|y> syntax")
        growth = placement.get("volume_growth_count")
        if growth is not None and (isinstance(growth, bool) or not isinstance(growth, int) or growth < 0):
            raise ValueError("placement.volume_growth_count must be a non-negative integer")
        bucket_name = str(config.get("bucket_name", "")).strip()
        if bucket_name:
            self._validate_bucket_name(bucket_name)
        return {
            "bucket_name": bucket_name,
            "prefix": str(config.get("prefix", "")).strip("/"),
            "versioning": versioning,
            "object_lock": object_lock,
            "adopt_existing": bool(config.get("adopt_existing", False)),
            "anonymous_read": bool(config.get("anonymous_read", False)),
            "quota_size": quota_size,
            "quota_enforce": bool(config.get("quota_enforce", True)),
            "placement": placement,
        }

    def _placement_manifest(self, placement: dict[str, Any]) -> dict[str, Any]:
        mapping = {
            "replication": "replication",
            "disk_type": "diskType",
            "ttl": "ttl",
            "fsync": "fsync",
            "worm": "worm",
            "read_only": "readOnly",
            "data_center": "dataCenter",
            "rack": "rack",
            "data_node": "dataNode",
            "volume_growth_count": "volumeGrowthCount",
        }
        return {target: placement[source] for source, target in mapping.items() if source in placement}

    def _bucket_name(self, spec: ProvisionSpec) -> str:
        configured = str(spec.config.get("bucket_name", "")).strip()
        if configured:
            self._validate_bucket_name(configured)
            return configured
        raw = "-".join(
            str(value)
            for value in (
                "astrolift",
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint,
            )
            if value
        ).lower()
        clean = re.sub(r"[^a-z0-9-]+", "-", raw)
        clean = re.sub(r"-+", "-", clean).strip("-")
        digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
        bucket_name = f"{clean[:54].rstrip('-')}-{digest}"
        self._validate_bucket_name(bucket_name)
        return bucket_name

    def _validate_bucket_name(self, value: str) -> None:
        if not _BUCKET_RE.fullmatch(value) or ".." in value or re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", value):
            raise ValueError("bucket_name must satisfy S3 DNS naming rules")

    def _resource_names(self, bucket_name: str) -> dict[str, str]:
        return {
            "Secret": f"{bucket_name}-credentials",
            "S3Identity": f"{bucket_name}-identity",
            "S3Credentials": f"{bucket_name}-credentials",
            "S3Policy": f"{bucket_name}-policy",
            "S3PolicyBinding": f"{bucket_name}-policy-binding",
        }

    def _labels(self, spec: ProvisionSpec) -> dict[str, str]:
        labels = {
            "app.kubernetes.io/managed-by": "astrolift",
            "astrolift.io/organization": self._label_value(spec.organization_slug),
            "astrolift.io/app": self._label_value(spec.app_slug),
            "astrolift.io/environment": self._label_value(spec.environment_name),
        }
        if spec.managed_service_id:
            labels["astrolift.io/managed-service"] = self._label_value(spec.managed_service_id)
        return labels

    def _label_value(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value))[:63]
        cleaned = cleaned.strip("-_.")
        return cleaned or "unknown"

    def _credential_path(self, namespace: str, bucket_name: str) -> str:
        prefix = self._config.credential_path_prefix.strip("/")
        return f"{prefix}/{namespace}/{bucket_name}/credentials"

    def _endpoint(self) -> str:
        if self._config.endpoint:
            return self._config.endpoint.rstrip("/")
        scheme = self._config.endpoint_scheme.lower()
        if scheme not in {"http", "https"}:
            raise ValueError("SeaweedFS endpoint_scheme must be http or https")
        return f"{scheme}://{self._config.seaweed_name}-s3.{self._config.namespace}.svc:{self._config.endpoint_port}"

    def _validate_install_config(self) -> None:
        dns_label = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
        for field, value in (
            ("namespace", self._config.namespace),
            ("seaweed_name", self._config.seaweed_name),
        ):
            if len(value) > 63 or not dns_label.fullmatch(value):
                raise ValueError(f"SeaweedFS {field} must be a Kubernetes DNS label")
        if self._config.endpoint_scheme.lower() not in {"http", "https"}:
            raise ValueError("SeaweedFS endpoint_scheme must be http or https")
        if not 1 <= self._config.endpoint_port <= 65535:
            raise ValueError("SeaweedFS endpoint_port must be between 1 and 65535")
        if not self._config.region.strip():
            raise ValueError("SeaweedFS S3 signing region cannot be empty")
        prefix = self._config.credential_path_prefix.strip("/")
        if not prefix or "#" in prefix or any(part in {"", ".", ".."} for part in prefix.split("/")):
            raise ValueError("SeaweedFS credential_path_prefix must be a safe non-empty secret path")
        if self._config.endpoint:
            endpoint = urlsplit(self._config.endpoint)
            if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
                raise ValueError("SeaweedFS endpoint must be an absolute HTTP(S) URL")
            if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError("SeaweedFS endpoint cannot contain credentials, query, or fragment")

    def _stub(self, kind: str, name: str, namespace: str) -> dict[str, Any]:
        return {
            "apiVersion": _API_VERSION,
            "kind": kind,
            "metadata": {"name": name, "namespace": namespace},
        }

    def _wait_absent(
        self,
        cluster_id: str,
        namespace: str,
        kind: str,
        name: str,
    ) -> tuple[bool, dict[str, Any] | None]:
        deadline = time.monotonic() + max(self._config.deletion_timeout_seconds, 0)
        last: dict[str, Any] | None = None
        while True:
            maybe_heartbeat(f"seaweedfs.delete:{kind}/{name}")
            last = self._config.cluster_driver.get_manifest(
                cluster_id,
                namespace,
                _RESOURCE_KINDS[kind],
                name,
            )
            if last is None:
                return True, None
            if time.monotonic() >= deadline:
                return False, last
            time.sleep(max(self._config.deletion_poll_seconds, 0))

    def _retention_block(self, obj: dict[str, Any] | None) -> bool:
        if obj is None:
            return False
        conditions = (obj.get("status", {}) or {}).get("conditions", []) or []
        return any(
            row.get("type") == "DeleteBlockedByRetention" and str(row.get("status", "")).lower() == "true"
            for row in conditions
        )

    def _condition_messages(self, status: dict[str, Any]) -> list[str]:
        return [str(row.get("message", "")) for row in status.get("conditions", []) or [] if row.get("message")]
