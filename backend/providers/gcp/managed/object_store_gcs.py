"""GCS ObjectStore managed-service driver (#363).

Implements ``ManagedServiceDriver`` for the canonical GCP managed-
object-store path. Symmetric to S3Driver (#35) — same lifecycle,
same Binding shape, just google-cloud-storage backed.

GCS has no provisioning lifecycle in the database sense; bucket
creation is synchronous. The driver still implements the full
protocol so the workflow layer doesn't special-case GCS.

Four-corner deprovision matrix:

  delete_data=False, force_destroy=False (default):
    Retain bucket + contents. Platform unbinds; bucket lives on
    for the operator to deal with manually.

  delete_data=False, force_destroy=True:
    Retain bucket + contents but bypass platform-side guards. At
    GCS this is essentially the same as the safe path — the
    bucket isn't touched.

  delete_data=True, force_destroy=False:
    Empty bucket then delete. Refuses cleanly when a retention
    policy is locked (the operator must wait it out or unlock
    manually) or when the bucket has object holds.

  delete_data=True, force_destroy=True:
    Disable platform-managed safeguards (clear unlocked retention
    policies; release event-based + temporary holds), then empty
    + delete. Locked retention policies CANNOT be bypassed by the
    GCS API and surface as an error.
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
    unsupported_update,
)
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from gcp.managed._ownership import label_adoption_refusal

KIND = "object_store"


@dataclass(frozen=True)
class GCSConfig:
    project_id: str
    bucket_name_prefix: str = "astrolift"
    versioning_enabled: bool = True
    uniform_bucket_level_access: bool = True
    """ON by default — enforces IAM-only auth (no per-object ACLs).
    Recommended for compliance frameworks."""

    location: str = "US"
    storage_class: str = "STANDARD"
    client: Any | None = None


class GCSDriver(ManagedServiceDriver):
    def __init__(self, *, config: GCSConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from google.cloud import storage

            self._client = storage.Client(project=config.project_id)

    @driver_op(
        cloud="gcp",
        driver="object_store_gcs",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        bucket_name = self._bucket_name_for(spec=spec)
        try:
            bucket = self._client.bucket(bucket_name)
            bucket.location = self._config.location
            bucket.storage_class = self._config.storage_class
            if self._config.uniform_bucket_level_access:
                bucket.iam_configuration.uniform_bucket_level_access_enabled = True
            try:
                self._client.create_bucket(
                    bucket,
                    location=self._config.location,
                )
            except Exception as exc:
                if type(exc).__name__ != "Conflict":
                    raise
                # Idempotent only for this service's bucket: the labels below
                # would otherwise take another service's bucket over (#1961).
                existing = self._client.get_bucket(bucket_name)
                refusal = label_adoption_refusal(
                    dict(existing.labels or {}), spec, resource=f"GCS bucket {bucket_name}"
                )
                if refusal is not None:
                    return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
                bucket = existing
            if self._config.versioning_enabled:
                bucket.versioning_enabled = True
                bucket.patch()
            # Apply labels (GCS uses lowercase k=v with dashes,
            # not the AWS Tag shape). #438: stamp per-binding +
            # managed-service ids so the cost collector can join
            # against billing-API tag breakdowns.
            labels: dict[str, str] = {
                "astrolift-io-managed-by": "platform",
                "astrolift-io-organization": spec.organization_slug,
                "astrolift-io-app": spec.app_slug,
                "astrolift-io-environment": spec.environment_name,
            }
            if spec.binding_id:
                labels["astrolift-io-binding"] = spec.binding_id
            if spec.managed_service_id:
                labels["astrolift-io-managed-service-id"] = spec.managed_service_id
                labels[MANAGED_SERVICE_ID_LABEL] = spec.managed_service_id
            bucket.labels = labels
            bucket.patch()
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"provision failed: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=f"{KIND}/{bucket_name}",
            message=f"GCS bucket {bucket_name} provisioned",
        )

    @driver_op(cloud="gcp", driver="object_store_gcs")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        return unsupported_update(
            spec.handle, "GCS bucket storage class and location reconcile on provision, not in place"
        )

    @driver_op(
        cloud="gcp",
        driver="object_store_gcs",
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
        _, _, bucket_name = spec.handle.partition("/")

        if not delete_data:
            # Safe path — bucket and contents stay. force_destroy here
            # is a no-op at the bucket level; the platform's binding
            # record is what gets cleaned up by the calling activity.
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"bucket {bucket_name} retained",
            )

        try:
            bucket = self._client.get_bucket(bucket_name)
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                return DeprovisionResult(
                    ok=True,
                    handle=spec.handle,
                    message="already gone",
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"get_bucket: {exc}",
                errors=[str(exc)],
            )

        retention_policy = getattr(bucket, "retention_policy", None) or {}
        # ``retention_policy`` may be a dict (REST mode) or an object
        # with ``.is_locked`` + ``.retention_period`` attrs. Normalize.
        if isinstance(retention_policy, dict):
            policy_locked = bool(retention_policy.get("isLocked"))
            policy_active = bool(retention_policy.get("retentionPeriod"))
        else:
            policy_locked = bool(
                getattr(retention_policy, "is_locked", False),
            )
            policy_active = bool(
                getattr(retention_policy, "retention_period", 0),
            )

        if policy_active and not force_destroy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"bucket {bucket_name} has an active retention "
                    f"policy — pass force_destroy=True to attempt "
                    f"clearing it (locked policies cannot be cleared)"
                ),
                errors=["retention_policy_active"],
            )
        if policy_active and force_destroy and policy_locked:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"bucket {bucket_name} has a LOCKED retention "
                    f"policy — GCS does not allow API-level bypass; "
                    f"the operator must wait for the policy to expire"
                ),
                errors=["retention_policy_locked"],
            )
        if policy_active and force_destroy:
            # Unlocked retention policy can be cleared.
            try:
                bucket.retention_policy = None
                bucket.patch()
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"failed to clear retention_policy: {exc}",
                    errors=[str(exc)],
                )

        try:
            for blob in bucket.list_blobs(versions=True):
                if force_destroy:
                    # Release event-based and temporary holds before
                    # delete. Both are object-level flags that block
                    # delete unless explicitly cleared.
                    cleared = False
                    if getattr(blob, "event_based_hold", False):
                        blob.event_based_hold = False
                        cleared = True
                    if getattr(blob, "temporary_hold", False):
                        blob.temporary_hold = False
                        cleared = True
                    if cleared and hasattr(blob, "patch"):
                        blob.patch()
                blob.delete()
            bucket.delete()
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                return DeprovisionResult(
                    ok=True,
                    handle=spec.handle,
                    message="already gone",
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete failed: {exc}",
                errors=[str(exc)],
            )
        suffix = " (force_destroy)" if force_destroy else ""
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"bucket {bucket_name} deleted with data{suffix}",
        )

    @driver_op(cloud="gcp", driver="object_store_gcs")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, _, bucket_name = handle.handle.partition("/")
        try:
            self._client.get_bucket(bucket_name)
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                return ServiceStatus(
                    handle=handle.handle,
                    state="deprovisioned",
                    message=f"bucket {bucket_name} does not exist",
                )
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message=str(exc),
            )
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message=f"bucket {bucket_name} reachable",
        )

    @driver_op(cloud="gcp", driver="object_store_gcs")
    def binding(self, handle: ServiceHandle) -> Binding:
        _, _, bucket_name = handle.handle.partition("/")
        return Binding(
            env_vars={
                "GCS_BUCKET_NAME": ValueRef(literal=bucket_name),
                "GCS_BUCKET_URI": ValueRef(literal=f"gs://{bucket_name}"),
                "GCP_PROJECT_ID": ValueRef(
                    literal=self._config.project_id,
                ),
            },
            iam_grants=[
                Grant(
                    resource=(f"//storage.googleapis.com/projects/_/buckets/{bucket_name}"),
                    actions=["roles/storage.objectAdmin"],
                ),
            ],
            notes=("GCS object IAM via roles/storage.objectAdmin role binding."),
        )

    @driver_op(cloud="gcp", driver="object_store_gcs")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=(f"v-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}"),
            created_at=datetime.now(tz=UTC).isoformat(),
        )

    @driver_op(cloud="gcp", driver="object_store_gcs")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        return ProvisionResult(
            ok=False,
            handle="",
            message=("GCS restore via cross-bucket copy not implemented in this driver"),
            errors=["not_implemented"],
        )

    @driver_op(cloud="gcp", driver="object_store_gcs", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "storage_class_override": {"type": "string"},
                "location_override": {"type": "string"},
            },
        }

    @driver_op(cloud="gcp", driver="object_store_gcs", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "GCS_BUCKET_NAME": "Bucket name",
                "GCS_BUCKET_URI": "gs:// URI",
                "GCP_PROJECT_ID": "Project ID",
            }
        )

    def editable_fields(self) -> list[str]:
        """No config key can be applied without a reprovision (#1376)."""
        # storage_class_override / location_override are applied while creating
        # the bucket; location is immutable afterwards either way.
        return []

    def _bucket_name_for(self, *, spec: ProvisionSpec) -> str:
        # GCS bucket names: 3-63 chars, lowercase letters / digits /
        # dashes / underscores / dots; must be globally unique
        # (project-scoped naming via prefix).
        parts = [
            self._config.bucket_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:63]
