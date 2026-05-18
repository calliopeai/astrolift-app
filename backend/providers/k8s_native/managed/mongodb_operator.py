"""MongoDB operator-backed ManagedServiceDriver (#71).

Targets the Percona Server for MongoDB operator (PSMDB) by default
— it ships replica-set + sharded-cluster topologies + ops-manager
backup. The driver emits PerconaServerMongoDB CRDs.

Variant key: ('document_db', 'mongodb_operator').
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

KIND = "document_db"


SIZE_TO_SPEC = {
    "small": {
        "replicas": 3,
        "resources": {
            "requests": {"cpu": "200m", "memory": "512Mi"},
            "limits": {"cpu": "1", "memory": "2Gi"},
        },
        "storage_size": "10Gi",
    },
    "medium": {
        "replicas": 3,
        "resources": {
            "requests": {"cpu": "500m", "memory": "2Gi"},
            "limits": {"cpu": "2", "memory": "8Gi"},
        },
        "storage_size": "50Gi",
    },
    "large": {
        "replicas": 3,
        "resources": {
            "requests": {"cpu": "1", "memory": "4Gi"},
            "limits": {"cpu": "4", "memory": "16Gi"},
        },
        "storage_size": "100Gi",
    },
    "xlarge": {
        "replicas": 5,
        "resources": {
            "requests": {"cpu": "2", "memory": "8Gi"},
            "limits": {"cpu": "8", "memory": "32Gi"},
        },
        "storage_size": "500Gi",
    },
}


@dataclass(frozen=True)
class MongoDBOperatorConfig:
    storage_class: str | None = None
    namespace: str | None = None
    backup_url: str | None = None
    cluster_driver: Any | None = None


class MongoDBOperatorDriver(ManagedServiceDriver):
    def __init__(self, *, config: MongoDBOperatorConfig) -> None:
        self._config = config

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cluster_name = self._cluster_name(spec=spec)
        namespace = self._namespace_for(spec=spec)
        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=namespace,
            name=cluster_name,
        )
        manifest = self._render_cluster_crd(spec=spec, name=cluster_name)
        if self._config.cluster_driver is None:
            return ProvisionResult(
                ok=True,
                handle=handle,
                message="MongoDB CRD rendered (no cluster_driver injected)",
            )
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            [manifest],
        )
        if not result.ok:
            return ProvisionResult(
                ok=False,
                handle="",
                message="apply_manifests failed",
                errors=result.summary(),
            )
        return ProvisionResult(
            ok=True,
            handle=handle,
            message=f"MongoDB cluster {cluster_name} provisioned",
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message="operator reconciles via re-applied CRD",
        )

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        del delete_data, force_destroy
        if self._config.cluster_driver is None:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message="no cluster_driver — manifest deletion skipped",
            )
        parsed = _unpack_handle(spec.handle)
        if parsed.is_legacy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    "legacy 2-segment handle cannot be deprovisioned: "
                    "re-provision to refresh the handle, or pass a "
                    "4-segment handle (<kind>/<cluster>/<ns>/<name>)"
                ),
                errors=["legacy_handle_missing_locator"],
            )
        stub = {
            "apiVersion": "psmdb.percona.com/v1",
            "kind": "PerconaServerMongoDB",
            "metadata": {
                "name": parsed.name,
                "namespace": parsed.namespace,
            },
        }
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [stub],
        )
        if result.errors:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(result.summary()),
                errors=result.summary(),
            )
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"MongoDB cluster {parsed.name} deleted",
        )

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message="status delegated to operator reconciliation",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        name = _unpack_handle(handle.handle).name
        return Binding(
            env_vars={
                "DOCDB_URI": ValueRef(
                    literal=(f"mongodb+srv://{name}-rs0/?replicaSet={name}-rs0"),
                ),
                "DOCDB_DB": ValueRef(literal="app"),
                "DOCDB_USER": ValueRef(
                    secret_ref=f"{name}-secrets#MONGODB_USER_ADMIN_USER",
                ),
                "DOCDB_PASSWORD": ValueRef(
                    secret_ref=f"{name}-secrets#MONGODB_USER_ADMIN_PASSWORD",
                ),
            },
            iam_grants=[],
            notes=("MongoDB connection via operator-managed replica-set " "Service. Credentials in <cluster>-secrets."),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"backup-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}",
            created_at=datetime.now(tz=UTC).isoformat(),
        )

    def restore(self, snapshot, target):
        return ProvisionResult(
            ok=False,
            handle="",
            message="MongoDB restore via operator's restore CRD",
            errors=["not_implemented"],
        )

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "backup_url": {"type": "string"},
                "storage_class": {"type": "string"},
            },
        }

    def binding_schema(self):
        return BindingSchema(
            env_vars={
                "DOCDB_URI": "mongodb+srv URI to the replica set",
                "DOCDB_DB": "Database name",
                "DOCDB_USER": "Admin user (from Secret)",
                "DOCDB_PASSWORD": "Admin password (from Secret)",
            }
        )

    def _render_cluster_crd(
        self,
        *,
        spec: ProvisionSpec,
        name: str,
    ) -> dict[str, Any]:
        size_spec = SIZE_TO_SPEC.get(spec.size, SIZE_TO_SPEC["small"])
        return {
            "apiVersion": "psmdb.percona.com/v1",
            "kind": "PerconaServerMongoDB",
            "metadata": {
                "name": name,
                "namespace": self._namespace_for(spec=spec),
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/organization": spec.organization_slug,
                    "astrolift.io/app": spec.app_slug,
                },
            },
            "spec": {
                "crVersion": "1.16.0",
                "image": "percona/percona-server-mongodb:7.0",
                "secrets": {"users": f"{name}-secrets"},
                "replsets": [
                    {
                        "name": "rs0",
                        "size": size_spec["replicas"],
                        "resources": size_spec["resources"],
                        "volumeSpec": {
                            "persistentVolumeClaim": {
                                "storageClassName": self._config.storage_class,
                                "resources": {
                                    "requests": {
                                        "storage": size_spec["storage_size"],
                                    },
                                },
                            },
                        },
                    }
                ],
                **(
                    {
                        "backup": {
                            "enabled": True,
                            "storages": {
                                "default": {
                                    "type": "s3",
                                    "s3": {
                                        "bucket": self._config.backup_url,
                                    },
                                },
                            },
                        }
                    }
                    if self._config.backup_url
                    else {}
                ),
            },
        }

    def _cluster_name(self, *, spec: ProvisionSpec) -> str:
        parts = [spec.app_slug, spec.environment_name]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        clean = "-".join(p for p in parts if p).lower()
        clean = "".join(c if c.isalnum() or c == "-" else "-" for c in clean)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:50]

    def _namespace_for(self, *, spec: ProvisionSpec) -> str:
        if self._config.namespace:
            return self._config.namespace
        return f"{spec.organization_slug}-{spec.app_slug}".lower()
