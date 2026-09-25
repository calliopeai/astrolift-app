"""Bigtable managed-service driver (#371).

Implements ``ManagedServiceDriver`` for the canonical GCP managed
key-value store (Bigtable). Same four-corner deprovision semantic
as the AWS DynamoDB driver; Bigtable's API surface is split across
two clients:

  - ``BigtableInstanceAdminClient`` -- instance + cluster lifecycle
  - ``BigtableTableAdminClient`` -- table + column-family + backup

The instance is the multi-tenant resource (clusters live inside);
the table is the per-binding addressable thing. Astrolift provisions
one instance per binding (single-node SSD cluster by default) so
the workload-facing handle is the instance_id; the table is created
inside it as a fixed name (``data``) so the binding envelope stays
simple.

Four-corner deprovision matrix:

  delete_data=False, force_destroy=False (default):
    snapshot taken via the table-level backup API
    (``create_backup``); instance with more than one cluster is
    refused -- destroying replicas accidentally is the classic
    Bigtable footgun.

  delete_data=True, force_destroy=False:
    skip the backup; still refuse if cluster_count > 1.

  delete_data=False, force_destroy=True:
    take the backup; bypass the >1-cluster safety guard. The
    operator has already accepted that replicas will be deleted
    (or has manually drained them).

  delete_data=True, force_destroy=True:
    skip backup, bypass guards, delete. The --atomic path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
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
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from gcp.managed._ownership import label_adoption_refusal

KIND = "kv_store"


# Size -> node count for the single cluster. Bigtable's cost model
# is per-node (each node serves ~10K QPS and ~2.5 TB SSD); we start
# at 1 node and bump linearly with size.
_SIZE_TO_NODES = {
    "small": 1,
    "medium": 2,
    "large": 4,
    "xlarge": 8,
}

# Fixed table name inside the instance. Keeping it constant means
# operator-facing app code doesn't need to thread the table id
# through config; everything keyed on the instance_id.
_DEFAULT_TABLE_NAME = "data"


class _BigtableError(Exception):
    """Internal -- surfaced as ProvisionResult.errors / DeprovisionResult.errors."""


@dataclass(frozen=True)
class BigtableConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    project_id: str
    region: str
    """GCP region. Bigtable clusters are zonal under the hood; the
    driver lets ``spec.config.zone`` pick the AZ, otherwise it
    derives one as ``<region>-b``."""

    instance_name_prefix: str = "astrolift"

    storage_type_default: str = "SSD"
    """``SSD`` (default, low-latency) or ``HDD`` (cheaper, batch
    workloads). Operators override via ``spec.config.storage_type``."""

    cluster_count_default: int = 1
    """Number of clusters per instance. 1 = single-region, no
    replication. Operators bump for multi-region failover."""

    column_family_default: str = "cf1"
    """Default column family created on the table. App code can add
    more via Bigtable's online CF API; this just ensures there's a
    target so writes work out of the box."""


class BigtableDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: BigtableConfig,
        instance_admin_client: Any | None = None,
        table_admin_client: Any | None = None,
    ) -> None:
        self._config = config
        if instance_admin_client is not None:
            self._iadmin = instance_admin_client
        else:
            from google.cloud import bigtable_admin_v2

            self._iadmin = bigtable_admin_v2.BigtableInstanceAdminClient()
        if table_admin_client is not None:
            self._tadmin = table_admin_client
        else:
            from google.cloud import bigtable_admin_v2

            self._tadmin = bigtable_admin_v2.BigtableTableAdminClient()

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="gcp",
        driver="bigtable",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        instance_id = self._instance_id_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe_instance(instance_id)
        if existing is not None:
            refusal = label_adoption_refusal(
                dict(_get(existing, "labels", None) or {}), spec, resource=f"bigtable instance {instance_id}"
            )
            if refusal is not None:
                return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
            return ProvisionResult(
                ok=True,
                handle=_handle_for(instance_id),
                message=(f"bigtable instance {instance_id} already exists (state={_get(existing, 'state', '?')})"),
            )

        node_count = int(
            cfg.get("node_count", _SIZE_TO_NODES.get(spec.size, 1)),
        )
        storage_type = cfg.get(
            "storage_type",
            self._config.storage_type_default,
        )
        zone = cfg.get("zone") or f"{self._config.region}-b"
        cluster_id = f"{instance_id}-c1"

        instance_body: dict[str, Any] = {
            "display_name": instance_id,
            "type_": "PRODUCTION",
            "labels": _labels_for(spec),
        }
        clusters = {
            cluster_id: {
                "location": (f"projects/{self._config.project_id}/locations/{zone}"),
                "serve_nodes": node_count,
                "default_storage_type": storage_type,
            },
        }

        try:
            self._iadmin.create_instance(
                parent=f"projects/{self._config.project_id}",
                instance_id=instance_id,
                instance=instance_body,
                clusters=clusters,
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_instance: {exc}",
                errors=[str(exc)],
            )

        # Create the default table inside the instance so writes work
        # without an extra step. The column family handles the same.
        try:
            self._tadmin.create_table(
                parent=(f"projects/{self._config.project_id}/instances/{instance_id}"),
                table_id=_DEFAULT_TABLE_NAME,
                table={
                    "column_families": {
                        self._config.column_family_default: {},
                    },
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_table: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=_handle_for(instance_id),
            message=(f"bigtable instance {instance_id} provisioning (table {_DEFAULT_TABLE_NAME} created)"),
        )

    @driver_op(cloud="gcp", driver="bigtable")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        instance_id = _parse_handle(spec.handle)
        cfg = spec.config or {}

        new_nodes: int | None = None
        if spec.size:
            new_nodes = cfg.get(
                "node_count",
                _SIZE_TO_NODES.get(spec.size),
            )
        elif "node_count" in cfg:
            new_nodes = int(cfg["node_count"])

        if new_nodes is None:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        # Bigtable resize: list clusters, update serve_nodes on each.
        try:
            clusters = list(
                self._iadmin.list_clusters(
                    parent=(f"projects/{self._config.project_id}/instances/{instance_id}"),
                )
            )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"list_clusters: {exc}",
                errors=[str(exc)],
            )

        try:
            for cluster in clusters:
                cluster_name = _get(cluster, "name", "")
                self._iadmin.update_cluster(
                    name=cluster_name,
                    serve_nodes=int(new_nodes),
                )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"update_cluster: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"bigtable instance {instance_id} update queued",
        )

    @driver_op(
        cloud="gcp",
        driver="bigtable",
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
        instance_id = _parse_handle(spec.handle)

        existing = self._describe_instance(instance_id)
        if existing is None:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"bigtable instance {instance_id} already gone",
            )

        # Replica-safety guard: > 1 cluster means there are replicas;
        # delete_instance wipes them all. Refuse unless force_destroy.
        cluster_count = self._cluster_count(instance_id)
        if cluster_count > 1 and not force_destroy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"bigtable instance {instance_id} has "
                    f"{cluster_count} clusters (replicas); pass "
                    f"force_destroy=True to delete all of them"
                ),
                errors=["multi_cluster_replica_guard"],
            )

        backup_taken = False
        if not delete_data:
            try:
                self._tadmin.create_backup(
                    parent=(f"projects/{self._config.project_id}/instances/{instance_id}/clusters/{instance_id}-c1"),
                    backup_id=_final_backup_id(instance_id=instance_id),
                    backup={
                        "source_table": (
                            f"projects/{self._config.project_id}/instances/{instance_id}/tables/{_DEFAULT_TABLE_NAME}"
                        ),
                    },
                )
                backup_taken = True
            except Exception:
                # Best-effort: don't block delete on backup failure
                # (table may already be gone, cluster id may not match
                # operator-managed deviations). Surface via message.
                backup_taken = False

        try:
            self._iadmin.delete_instance(
                name=(f"projects/{self._config.project_id}/instances/{instance_id}"),
            )
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_instance: {exc}",
                errors=[str(exc)],
            )

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"bigtable instance {instance_id} delete queued "
                f"(backup={'taken' if backup_taken else 'skipped'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="gcp", driver="bigtable")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        instance_id = _parse_handle(handle.handle)
        existing = self._describe_instance(instance_id)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"bigtable instance {instance_id} not found",
            )
        state = _get(existing, "state", "STATE_NOT_KNOWN")
        return ServiceStatus(
            handle=handle.handle,
            state=_BT_STATE_TO_PROTOCOL.get(str(state), "updating"),
            message=f"bigtable reports {state}",
        )

    @driver_op(cloud="gcp", driver="bigtable")
    def binding(self, handle: ServiceHandle) -> Binding:
        instance_id = _parse_handle(handle.handle)
        existing = self._describe_instance(instance_id)
        if existing is None:
            raise _BigtableError(
                f"binding requested for missing instance {instance_id}",
            )
        return Binding(
            env_vars={
                "BIGTABLE_PROJECT_ID": ValueRef(
                    literal=self._config.project_id,
                ),
                "BIGTABLE_INSTANCE_ID": ValueRef(literal=instance_id),
                "BIGTABLE_TABLE_NAME": ValueRef(
                    literal=_DEFAULT_TABLE_NAME,
                ),
                "BIGTABLE_COLUMN_FAMILY": ValueRef(
                    literal=self._config.column_family_default,
                ),
            },
            iam_grants=[
                Grant(
                    resource=(f"projects/{self._config.project_id}/instances/{instance_id}"),
                    actions=["roles/bigtable.user"],
                ),
            ],
            notes=(
                "Workload identity binds the GSA to the workload's "
                "KSA; the GSA is granted bigtable.user on the "
                "instance scope."
            ),
        )

    @driver_op(cloud="gcp", driver="bigtable")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        instance_id = _parse_handle(handle.handle)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_id = f"{instance_id}-snap-{stamp}"[:50]
        try:
            self._tadmin.create_backup(
                parent=(f"projects/{self._config.project_id}/instances/{instance_id}/clusters/{instance_id}-c1"),
                backup_id=backup_id,
                backup={
                    "source_table": (
                        f"projects/{self._config.project_id}/instances/{instance_id}/tables/{_DEFAULT_TABLE_NAME}"
                    ),
                },
            )
        except Exception as exc:
            raise _BigtableError(
                f"create_backup: {exc}",
            ) from exc
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=backup_id,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="gcp", driver="bigtable")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        target_instance = self._instance_id_for(spec=target)
        source_instance = _parse_handle(snapshot.handle)
        try:
            # restore_table targets a NEW table in the target instance;
            # we ship into the canonical _DEFAULT_TABLE_NAME so the
            # binding envelope still resolves.
            self._tadmin.restore_table(
                parent=(f"projects/{self._config.project_id}/instances/{target_instance}"),
                table_id=_DEFAULT_TABLE_NAME,
                backup=(
                    f"projects/{self._config.project_id}"
                    f"/instances/{source_instance}"
                    f"/clusters/{source_instance}-c1"
                    f"/backups/{snapshot.snapshot_id}"
                ),
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"restore_table: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=_handle_for(target_instance),
            message=f"bigtable restore from {snapshot.snapshot_id} queued",
        )

    @driver_op(cloud="gcp", driver="bigtable", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "node_count": {"type": "integer", "minimum": 1},
                "storage_type": {
                    "type": "string",
                    "enum": ["SSD", "HDD"],
                },
                "zone": {"type": "string"},
            },
        }

    @driver_op(cloud="gcp", driver="bigtable", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "BIGTABLE_PROJECT_ID": "GCP project hosting the instance",
                "BIGTABLE_INSTANCE_ID": "Bigtable instance id",
                "BIGTABLE_TABLE_NAME": (f"Fixed table name ({_DEFAULT_TABLE_NAME})"),
                "BIGTABLE_COLUMN_FAMILY": ("Default column family on the table"),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe_instance(self, instance_id: str) -> Any | None:
        try:
            return self._iadmin.get_instance(
                name=(f"projects/{self._config.project_id}/instances/{instance_id}"),
            )
        except Exception as exc:
            if type(exc).__name__ in {"NotFound", "ResourceNotFound"}:
                return None
            msg = str(exc).lower()
            if "notfound" in msg or "not found" in msg or "404" in msg:
                return None
            raise

    def _cluster_count(self, instance_id: str) -> int:
        try:
            clusters = list(
                self._iadmin.list_clusters(
                    parent=(f"projects/{self._config.project_id}/instances/{instance_id}"),
                )
            )
        except Exception:
            return 1
        return len(clusters)

    def _instance_id_for(self, *, spec: ProvisionSpec) -> str:
        # Bigtable instance ids: 6-33 chars, lowercase letters, digits,
        # hyphens; must start with a letter, cannot end with hyphen.
        parts = [
            self._config.instance_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "kv",
        ]
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")[:33]
        # Bigtable requires the id to start with a letter; if the
        # first char isn't one (because the prefix got eaten), prefix
        # 'i' and re-trim.
        if clean and not clean[0].isalpha():
            clean = f"i{clean}"[:33]
        return clean


# ----- module-level helpers --------------------------------------------


def _handle_for(instance_id: str) -> str:
    return f"{KIND}/{instance_id}"


def _parse_handle(handle: str) -> str:
    if "/" not in handle:
        raise _BigtableError(
            f"handle {handle!r} must be '<kind>/<instance>'",
        )
    _, _, instance_id = handle.partition("/")
    if not instance_id:
        raise _BigtableError(
            f"handle {handle!r} has empty instance part",
        )
    return instance_id


def _labels_for(spec: ProvisionSpec) -> dict[str, str]:
    """Bigtable labels: lowercase, [a-z0-9_-]; max 64 chars per key
    and value; max 64 labels per resource."""

    def _sanitize(s: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in s.lower())[:63]

    base = {
        "astrolift-managed-by": "platform",
        "astrolift-organization": _sanitize(spec.organization_slug),
        "astrolift-app": _sanitize(spec.app_slug),
        "astrolift-environment": _sanitize(spec.environment_name),
        "astrolift-cluster": _sanitize(spec.tenant_cluster_id),
        "astrolift-isolation": _sanitize(spec.isolation),
    }
    # Per-binding cost-attribution keys (#438). GCP labels are
    # lowercase + [a-z0-9_-], so the dotted/slash form
    # ``astrolift.io/binding`` becomes ``astrolift-binding``.
    if spec.binding_id:
        base["astrolift-binding"] = _sanitize(spec.binding_id)
    if spec.managed_service_id:
        base["astrolift-managed-service-id"] = _sanitize(spec.managed_service_id)
        base[MANAGED_SERVICE_ID_LABEL] = _sanitize(spec.managed_service_id)
    for k, v in (spec.tags or {}).items():
        base[f"astrolift-extra-{_sanitize(k)}"] = _sanitize(str(v))
    return base


def _final_backup_id(*, instance_id: str) -> str:
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    # Bigtable backup ids: 50-char limit; same charset as instance ids.
    return f"{instance_id}-final-{stamp}"[:50]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Dual-mode getter for proto-Message and dict -- google-cloud
    SDKs return both depending on client variant."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_BT_STATE_TO_PROTOCOL = {
    "READY": "available",
    "STATE_NOT_KNOWN": "updating",
    "CREATING": "provisioning",
}
