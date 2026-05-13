"""GCS ObjectStore managed-service driver (#41).

Spec ref: spec 23-provider-plugin-gcp + _sdk/managed_service.py.

Symmetric to S3Driver — same lifecycle, same Binding shape, just
google-cloud-storage backed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
from gcp._errors import map_api_error


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
                    bucket, location=self._config.location,
                )
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ == "Conflict":
                    pass  # idempotent
                else:
                    raise
            if self._config.versioning_enabled:
                bucket.versioning_enabled = True
                bucket.patch()
            # Apply labels (GCS uses lowercase k=v with dashes,
            # not the AWS Tag shape)
            bucket.labels = {
                "astrolift-io-managed-by": "platform",
                "astrolift-io-organization": spec.organization_slug,
                "astrolift-io-app": spec.app_slug,
                "astrolift-io-environment": spec.environment_name,
            }
            bucket.patch()
        except Exception as exc:  # noqa: BLE001
            return ProvisionResult(
                ok=False, handle="",
                message=f"provision failed: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=f"{KIND}/{bucket_name}",
            message=f"GCS bucket {bucket_name} provisioned",
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True, handle=spec.handle,
            message="GCS update via lifecycle/storage_class is operator-managed",
        )

    def deprovision(
        self, spec: DeprovisionSpec, *, delete_data: bool = False,
    ) -> DeprovisionResult:
        _, _, bucket_name = spec.handle.partition("/")
        if not delete_data:
            return DeprovisionResult(
                ok=True, handle=spec.handle,
                message=f"bucket {bucket_name} retained",
            )
        try:
            bucket = self._client.get_bucket(bucket_name)
            for blob in bucket.list_blobs(versions=True):
                blob.delete()
            bucket.delete()
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "NotFound":
                return DeprovisionResult(
                    ok=True, handle=spec.handle,
                    message="already gone",
                )
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=f"delete failed: {exc}",
                errors=[str(exc)],
            )
        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message=f"bucket {bucket_name} deleted with data",
        )

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, _, bucket_name = handle.handle.partition("/")
        try:
            self._client.get_bucket(bucket_name)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "NotFound":
                return ServiceStatus(
                    handle=handle.handle, state="deprovisioned",
                    message=f"bucket {bucket_name} does not exist",
                )
            return ServiceStatus(
                handle=handle.handle, state="error",
                message=str(exc),
            )
        return ServiceStatus(
            handle=handle.handle, state="available",
            message=f"bucket {bucket_name} reachable",
        )

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
                    resource=f"//storage.googleapis.com/projects/_/buckets/{bucket_name}",
                    actions=["roles/storage.objectAdmin"],
                ),
            ],
            notes="GCS object IAM via roles/storage.objectAdmin role binding.",
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"v-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}",
            created_at=datetime.now(tz=UTC).isoformat(),
        )

    def restore(self, snapshot, target):
        return ProvisionResult(
            ok=False, handle="",
            message="GCS restore via cross-bucket copy not implemented in this driver",
            errors=["not_implemented"],
        )

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "storage_class_override": {"type": "string"},
                "location_override": {"type": "string"},
            },
        }

    def binding_schema(self):
        return BindingSchema(env_vars={
            "GCS_BUCKET_NAME": "Bucket name",
            "GCS_BUCKET_URI": "gs:// URI",
            "GCP_PROJECT_ID": "Project ID",
        })

    def _bucket_name_for(self, *, spec: ProvisionSpec) -> str:
        # GCS bucket names: 3-63 chars, lowercase letters / digits /
        # dashes / underscores / dots; must be globally unique
        # (project-scoped naming via prefix).
        parts = [
            self._config.bucket_name_prefix,
            spec.organization_slug, spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(
            c if (c.isalnum() or c == "-") else "-"
            for c in raw
        )
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:63]
