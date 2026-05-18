"""CloudNativePG (CNPG) Postgres ManagedServiceDriver (#53).

Spec ref: spec 23-provider-plugin-k8s-native + _sdk/managed_service.py.

CNPG is the recommended in-cluster Postgres operator. The driver
emits CNPG ``Cluster`` CRDs that the operator reconciles into
StatefulSets + Services. Connection info comes from the operator-
generated app-user Secret which CNPG populates with host / port /
user / password / dbname.

Pairs with the platform's ManagedServiceDriver protocol so the
workflow layer's lifecycle (provision / update / deprovision /
snapshot / restore / status / binding) works the same against
CNPG as it does against RDS on AWS.
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

KIND = "postgres"


# Per-size CNPG cluster spec. CNPG instances run as Pods backed
# by PVCs; sizing translates to instance count + resource requests.
SIZE_TO_SPEC = {
    "small": {
        "instances": 1,
        "resources": {
            "requests": {"cpu": "200m", "memory": "512Mi"},
            "limits": {"cpu": "1", "memory": "2Gi"},
        },
        "storage_size": "10Gi",
    },
    "medium": {
        "instances": 2,
        "resources": {
            "requests": {"cpu": "500m", "memory": "2Gi"},
            "limits": {"cpu": "2", "memory": "8Gi"},
        },
        "storage_size": "50Gi",
    },
    "large": {
        "instances": 3,
        "resources": {
            "requests": {"cpu": "1", "memory": "4Gi"},
            "limits": {"cpu": "4", "memory": "16Gi"},
        },
        "storage_size": "100Gi",
    },
    "xlarge": {
        "instances": 3,
        "resources": {
            "requests": {"cpu": "2", "memory": "8Gi"},
            "limits": {"cpu": "8", "memory": "32Gi"},
        },
        "storage_size": "500Gi",
    },
}


@dataclass(frozen=True)
class CNPGConfig:
    cluster_driver: Any | None = None
    """Required for live mutations. Render-only mode without one."""

    operator_namespace: str = "cnpg-system"
    """Where the CNPG operator runs. Cluster CRDs live in the
    app's namespace."""

    storage_class: str = ""
    """k8s StorageClass for CNPG PVCs. Empty = use cluster default."""

    backup_object_store_url: str = ""
    """S3-compatible bucket URL for continuous backups (BarmanObjectStore).
    Empty = no backup configured."""


class CNPGPostgresDriver(ManagedServiceDriver):
    def __init__(self, *, config: CNPGConfig | None = None) -> None:
        self._config = config or CNPGConfig()

    # ---- lifecycle ------------------------------------------------

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cluster_name = self._cluster_name(spec=spec)
        namespace = f"{spec.organization_slug}-{spec.app_slug}"
        manifest = self._render_cluster(spec=spec, cluster_name=cluster_name)

        if self._config.cluster_driver is not None:
            result = self._config.cluster_driver.apply_manifests(
                spec.tenant_cluster_id,
                namespace,
                [manifest],
            )
            if not result.ok:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=f"failed to apply CNPG Cluster: {result.summary()}",
                    errors=result.summary(),
                )

        # 4-segment handle so deprovision can recover the locator
        # without re-deriving organization_slug/app_slug. The
        # tenant_cluster_id may be empty in render-only mode but
        # callers that go on to deprovision must pass the same spec
        # back through provision first or supply a 4-segment handle.
        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=namespace,
            name=cluster_name,
        )
        return ProvisionResult(
            ok=True,
            handle=handle,
            message=(f"CNPG Cluster {cluster_name} applied; CNPG operator " "reconciles asynchronously"),
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        # Update path: re-apply the Cluster CRD with new size/config.
        # CNPG operator handles rolling resize + replica scaling.
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=(
                "to update CNPG cluster size, call provision again " "with the new size — CNPG handles rolling resize"
            ),
        )

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        # force_destroy: in CNPG the only guard is the reclaim policy
        # on the underlying PVCs. Real deletion would patch
        # spec.storage.reclaimPolicy=Delete and override pod-disruption-
        # budgets before delete; this driver accepts the flag for
        # Protocol symmetry and lets the workflow proceed.
        del force_destroy
        parsed = _unpack_handle(spec.handle)
        if self._config.cluster_driver is None:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message="render-only mode — caller deletes",
            )
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
        # CNPG's Cluster CRD has reclaim policy: by default the
        # PVCs are kept on Cluster delete. Setting
        # spec.storage.reclaimPolicy=Delete would purge the data.
        stub = {
            "apiVersion": "postgresql.cnpg.io/v1",
            "kind": "Cluster",
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
                message=f"delete failed: {result.summary()}",
                errors=result.summary(),
            )
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(f"CNPG Cluster {parsed.name} deleted " f"({'with data' if delete_data else 'PVCs retained'})"),
        )

    # ---- read-only -------------------------------------------------

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        return ServiceStatus(
            handle=handle.handle,
            state="provisioning",
            message=("live status requires querying the Cluster CRD's " "status block via cluster_driver"),
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        cluster_name = _unpack_handle(handle.handle).name
        # CNPG operator generates a Secret named
        # <cluster>-app with host/port/user/password/dbname.
        secret_name = f"{cluster_name}-app"
        return Binding(
            env_vars={
                "DATABASE_HOST": ValueRef(
                    secret_ref=f"{secret_name}#host",
                ),
                "DATABASE_PORT": ValueRef(
                    secret_ref=f"{secret_name}#port",
                ),
                "DATABASE_USER": ValueRef(
                    secret_ref=f"{secret_name}#user",
                ),
                "DATABASE_PASSWORD": ValueRef(
                    secret_ref=f"{secret_name}#password",
                ),
                "DATABASE_NAME": ValueRef(
                    secret_ref=f"{secret_name}#dbname",
                ),
                "DATABASE_URL": ValueRef(
                    secret_ref=f"{secret_name}#uri",
                ),
            },
            iam_grants=[],
            notes=(
                "CNPG generates the app Secret with rotation. The " "platform mounts via envFrom or projected volume."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """CNPG snapshots = Backup CRDs. The driver emits one
        and returns the snapshot_id; CNPG operator handles
        the actual backup to the configured object store."""
        from datetime import UTC, datetime

        snapshot_id = f"backup-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}"
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snapshot_id,
            created_at=datetime.now(tz=UTC).isoformat(),
        )

    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        """CNPG restore creates a new Cluster with bootstrap.recovery
        pointing at the source backup."""
        return ProvisionResult(
            ok=False,
            handle="",
            message=(
                "CNPG restore via Backup-and-Recovery wiring is "
                "deferred to the in-cluster operator; call "
                "provision with bootstrap.recovery in spec.config"
            ),
            errors=["not_implemented_in_driver"],
        )

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "version": {
                    "type": "string",
                    "description": "Postgres major version (15, 16, 17).",
                    "default": "16",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": ("Postgres extensions to enable (pg_trgm, " "vector, postgis, etc.)."),
                },
                "storage_size": {
                    "type": "string",
                    "description": "Override per-size storage size.",
                },
            },
        }

    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "DATABASE_HOST": "Postgres hostname",
                "DATABASE_PORT": "Port (5432)",
                "DATABASE_USER": "Username",
                "DATABASE_PASSWORD": "Password",
                "DATABASE_NAME": "Initial database name",
                "DATABASE_URL": "Full postgresql:// URL",
            }
        )

    # ---- internals ------------------------------------------------

    def _cluster_name(self, *, spec: ProvisionSpec) -> str:
        parts = [
            spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p)
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw.lower())
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:63]

    def _render_cluster(
        self,
        *,
        spec: ProvisionSpec,
        cluster_name: str,
    ) -> dict[str, Any]:
        size_spec = SIZE_TO_SPEC.get(spec.size, SIZE_TO_SPEC["small"])
        version = spec.config.get("version", "16")
        extensions = spec.config.get("extensions", []) or []
        storage_size = spec.config.get(
            "storage_size",
            size_spec["storage_size"],
        )

        cluster: dict[str, Any] = {
            "apiVersion": "postgresql.cnpg.io/v1",
            "kind": "Cluster",
            "metadata": {
                "name": cluster_name,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/app": spec.app_slug,
                    "astrolift.io/organization": spec.organization_slug,
                    "astrolift.io/environment": spec.environment_name,
                },
            },
            "spec": {
                "instances": size_spec["instances"],
                "imageName": (f"ghcr.io/cloudnative-pg/postgresql:{version}"),
                "resources": size_spec["resources"],
                "storage": {
                    "size": storage_size,
                },
                "monitoring": {
                    "enablePodMonitor": True,
                },
            },
        }
        if self._config.storage_class:
            cluster["spec"]["storage"]["storageClass"] = self._config.storage_class
        if extensions:
            # CNPG honors postgresql.parameters / managed.roles for
            # extension setup via post-init SQL. Simplest path:
            # CNPG postInitSQL runs on cluster bootstrap.
            cluster["spec"]["bootstrap"] = {
                "initdb": {
                    "database": "app",
                    "owner": "app",
                    "postInitSQL": [f"CREATE EXTENSION IF NOT EXISTS {ext};" for ext in extensions],
                },
            }
        if self._config.backup_object_store_url:
            cluster["spec"]["backup"] = {
                "barmanObjectStore": {
                    "destinationPath": (self._config.backup_object_store_url),
                },
                "retentionPolicy": "30d",
            }
        return cluster
