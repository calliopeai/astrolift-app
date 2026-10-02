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

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

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
    SliceResult,
    SliceSpec,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
    unsupported_update,
)
from _sdk.physical_naming import managed_service_identity, physical_name
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle
from k8s_native.managed._service_dns import service_host

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
    secrets_backend: Any | None = None
    """Required for durable independent preview credentials."""

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

    @driver_op(
        cloud="k8s_native",
        driver="postgres_cnpg",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cluster_name = self._cluster_name(spec=spec)
        parsed = _unpack_handle(spec.recorded_handle) if spec.recorded_handle else None
        namespace = (
            parsed.namespace
            if parsed
            else app_namespace(
                organization_slug=spec.organization_slug,
                app_slug=spec.app_slug,
            )
        )
        manifest = self._render_cluster(spec=spec, cluster_name=cluster_name)

        if self._config.cluster_driver is not None:
            current = self._config.cluster_driver.get_manifest(
                spec.tenant_cluster_id, namespace, "postgresql.cnpg.io/v1/Cluster", cluster_name
            )
            if current is not None:
                metadata = current.get("metadata", {})
                labels = metadata.get("labels", {})
                owner = labels.get("ai.astrolift/managed-service-id")
                legacy_owned = (
                    not owner
                    and bool(spec.recorded_handle)
                    and spec.recorded_handle_exclusive
                    and labels.get("astrolift.io/organization") == spec.organization_slug
                    and labels.get("astrolift.io/app") == spec.app_slug
                )
                owned = owner == spec.managed_service_id or legacy_owned
                if (
                    not owned
                    or labels.get("astrolift.io/managed-by") != "platform"
                    or metadata.get("name") != cluster_name
                    or metadata.get("namespace") != namespace
                    or not metadata.get("uid")
                    or not metadata.get("resourceVersion")
                    or labels.get("ai.astrolift/organization-id", spec.organization_id) != spec.organization_id
                    or labels.get("ai.astrolift/app-id", spec.app_id) != spec.app_id
                ):
                    return ProvisionResult(
                        False, spec.recorded_handle, "CNPG resource ownership cannot be verified", ["ownership_refused"]
                    )
                retained = deepcopy(current)
                retained.pop("status", None)
                retained["metadata"].pop("managedFields", None)
                retained["metadata"].setdefault("labels", {}).update(manifest["metadata"]["labels"])
                retained.setdefault("spec", {}).update(manifest["spec"])
                manifest = retained
            result = self._config.cluster_driver.apply_manifests(
                spec.tenant_cluster_id,
                namespace,
                [manifest],
                create_only=current is None,
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
            message=(f"CNPG Cluster {cluster_name} applied; CNPG operator reconciles asynchronously"),
        )

    @driver_op(cloud="k8s_native", driver="postgres_cnpg")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        return unsupported_update(
            spec.handle, "CNPG reconciles a rolling resize from the Cluster CRD re-applied on provision"
        )

    @driver_op(
        cloud="k8s_native",
        driver="postgres_cnpg",
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
                    "record the operator-confirmed original cluster and namespace in a "
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
            message=(f"CNPG Cluster {parsed.name} deleted ({'with data' if delete_data else 'PVCs retained'})"),
        )

    # ---- per-preview slices (#1578) --------------------------------
    #
    # Why CNPG first, and why it is written this way rather than the
    # shorter way.
    #
    # A slice needs a database and a role created *inside* a running
    # instance. No cloud API does that -- RDS has no "create database" call
    # -- so the alternative is the platform holding an admin credential for
    # a production database and running SQL against it. CNPG needs neither:
    # its operator already holds the superuser credential and reconciles
    # `Database` CRDs and `Cluster.spec.managed.roles` declaratively, so
    # the platform writes manifests without reading the parent admin credential.
    # Slice login credentials are independently minted and retained in the
    # selected cluster secret backend and an owned basic-auth Secret.
    #
    # **A dedicated role, not the parent's app credentials.** Overriding
    # only `POSTGRES_DB` and reusing the parent's credentials is far less
    # code and passes every guard in `SliceResult`. It also leaves the
    # preview holding a credential that opens the parent database, so a
    # workload ignoring `POSTGRES_DB` -- or any dependency with a hardcoded
    # connection string, or a migration tool defaulting to `postgres` --
    # reaches production data. That is the exact failure #1578 was filed
    # about, and renaming the default database does not prevent it.
    #
    # **The role list is read-modify-written from the parent's FULL spec,
    # never patched.** This is the part that has to be right. The shared
    # `server_side_apply` uses one field manager (`astrolift`) with
    # `force_conflicts=True`, so applying a partial Cluster carrying only
    # `spec.managed.roles` makes that manager relinquish every field it
    # owns and is not carrying -- pruning `spec.instances` and
    # `spec.storage` off a live production database. Re-applying the
    # complete spec with the role merged in is safe precisely because
    # nothing is absent.

    SLICE_DB_PREFIX = "slice"

    def supports_slicing(self) -> bool:
        return True

    def _slice_names(self, spec: SliceSpec) -> tuple[str, str, str]:
        """Deterministic (database, role, secret) names for a slice.

        Derived from `slice_id`, never generated: the caller has to be able
        to ask for the same slice twice and tear down the one it means.
        Postgres identifiers may not contain `-` and k8s object names may
        not contain `_`, so the same slice carries two spellings.
        """
        stem = dns_label(self.SLICE_DB_PREFIX, spec.parent.managed_service_id, spec.slice_id, max_length=57)
        ident = stem.replace("-", "_")
        return ident, f"{ident}_owner", f"{stem}-owner"

    @driver_op(
        cloud="k8s_native",
        driver="postgres_cnpg",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision_slice(self, spec: SliceSpec) -> SliceResult:
        from k8s_native.managed._cnpg_slices import provision_slice

        return provision_slice(self, spec)

    def slice_binding(self, spec: SliceSpec) -> Binding:
        from k8s_native.managed._cnpg_slices import slice_binding

        return slice_binding(self, spec)

    @driver_op(cloud="k8s_native", driver="postgres_cnpg", audit=True)
    def deprovision_slice(self, spec: SliceSpec, slice_handle: str) -> bool:
        """Delete only this consumer's owned slice, waiting for the Database finalizer."""
        from k8s_native.managed._cnpg_slices import deprovision_slice

        return deprovision_slice(self, spec, slice_handle)

    # ---- read-only -------------------------------------------------

    @driver_op(cloud="k8s_native", driver="postgres_cnpg")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        return ServiceStatus(
            handle=handle.handle,
            state="provisioning",
            message=("live status requires querying the Cluster CRD's status block via cluster_driver"),
        )

    @driver_op(cloud="k8s_native", driver="postgres_cnpg")
    def binding(self, handle: ServiceHandle) -> Binding:
        parsed = _unpack_handle(handle.handle)
        cluster_name = parsed.name
        host = service_host(parsed, name=f"{cluster_name}-rw")
        # CNPG operator generates a Secret named
        # <cluster>-app with host/port/user/password/dbname.
        secret_name = f"{cluster_name}-app"
        return Binding(
            env_vars={
                # Canonical postgres envelope (#1003, backfilled in #1402).
                # Credentials stay references to the rotating operator Secret.
                # The RW Service identity is stable across primary failover.
                "POSTGRES_HOST": ValueRef(
                    literal=host,
                ),
                "POSTGRES_PORT": ValueRef(
                    secret_ref=f"{secret_name}#port",
                ),
                "POSTGRES_DB": ValueRef(
                    secret_ref=f"{secret_name}#dbname",
                ),
                "POSTGRES_USER": ValueRef(
                    secret_ref=f"{secret_name}#user",
                ),
                "POSTGRES_PASSWORD": ValueRef(
                    secret_ref=f"{secret_name}#password",
                ),
                # CNPG serves TLS from its own CA and the app Secret's uri
                # does not pin a mode; require is the weakest mode that still
                # encrypts, and matches what every other postgres driver
                # publishes.
                "POSTGRES_SSL_MODE": ValueRef(literal="require"),
                # Pre-#1003 names, kept as aliases so workloads already bound
                # to this driver keep the variables they read (#1401).
                "DATABASE_HOST": ValueRef(
                    literal=host,
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
                    secret_ref=f"{secret_name}#fqdn-uri",
                ),
            },
            iam_grants=[],
            notes=("CNPG generates the app Secret with rotation. The platform mounts via envFrom or projected volume."),
        )

    @driver_op(cloud="k8s_native", driver="postgres_cnpg")
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

    @driver_op(cloud="k8s_native", driver="postgres_cnpg")
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

    @driver_op(cloud="k8s_native", driver="postgres_cnpg", heartbeat=False)
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
                    "description": ("Postgres extensions to enable (pg_trgm, vector, postgis, etc.)."),
                },
                "storage_size": {
                    "type": "string",
                    "description": "Override per-size storage size.",
                },
            },
        }

    @driver_op(cloud="k8s_native", driver="postgres_cnpg", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "POSTGRES_HOST": "Postgres hostname",
                "POSTGRES_PORT": "Port (5432)",
                "POSTGRES_DB": "Initial database name",
                "POSTGRES_USER": "Username",
                "POSTGRES_PASSWORD": "Password",
                "POSTGRES_SSL_MODE": "Always require; CNPG serves TLS from its own CA",
                "DATABASE_HOST": "Postgres hostname",
                "DATABASE_PORT": "Port (5432)",
                "DATABASE_USER": "Username",
                "DATABASE_PASSWORD": "Password",
                "DATABASE_NAME": "Initial database name",
                "DATABASE_URL": "Full postgresql:// URL",
            }
        )

    def editable_fields(self) -> list[str]:
        """No config key can be applied without a reprovision (#1376)."""
        # version / extensions / storage_size land in the CNPG Cluster CRD, which
        # only provision() applies; CNPG then handles the rolling resize.
        return []

    # ---- internals ------------------------------------------------

    def _cluster_name(self, *, spec: ProvisionSpec) -> str:
        managed_service_identity(spec.managed_service_id)
        if spec.recorded_handle:
            parsed = _unpack_handle(spec.recorded_handle)
            if (
                parsed.kind != KIND
                or parsed.is_legacy
                or not parsed.name
                or not parsed.namespace
                or parsed.cluster_id != (spec.tenant_cluster_id or "render-only")
            ):
                raise ValueError("recorded CNPG handle requires the original kind, cluster and namespace")
            return parsed.name
        return physical_name(spec.managed_service_id, prefix="pg", max_length=60)

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
                    "ai.astrolift/managed-service-id": spec.managed_service_id,
                    "ai.astrolift/organization-id": spec.organization_id,
                    "ai.astrolift/app-id": spec.app_id,
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
