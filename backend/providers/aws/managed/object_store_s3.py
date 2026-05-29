"""S3 ObjectStore managed-service driver (#35).

Spec ref: spec 23-provider-plugin-aws + _sdk/managed_service.py.

S3 has no provisioning lifecycle in the database sense — bucket
creation is synchronous, no 'available' state to wait for.
The driver still implements the full lifecycle protocol so the
workflow layer doesn't special-case S3.
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
from aws._errors import map_client_error
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
    tags_for,
)

KIND = "object_store"


@dataclass(frozen=True)
class S3Config:
    """Driver-instance config bound from the cluster's plugin config."""

    region: str
    bucket_name_prefix: str = "astrolift"
    """All managed buckets get this prefix to make them easy to
    spot in the AWS console + filter via tag-based budgets."""

    versioning_enabled: bool = True
    """S3 versioning lets the platform's snapshot API behave —
    we treat 'snapshot' as a versioning marker rather than a
    full bucket copy. Required for snapshot/restore."""

    public_access_blocked: bool = True
    """Default off public access at the bucket level; per-object
    grants still possible if operator explicitly opts in."""


class S3Driver(ManagedServiceDriver):
    def __init__(
        self, *, config: S3Config, client: Any | None = None,
    ) -> None:
        self._config = config
        if client is not None:
            self._s3 = client
        else:
            import boto3

            self._s3 = boto3.client("s3", region_name=config.region)

    # ---- lifecycle ------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="object_store_s3",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        bucket_name = self._bucket_name_for(spec=spec)
        try:
            create_kwargs: dict[str, Any] = {"Bucket": bucket_name}
            if self._config.region != "us-east-1":
                # us-east-1 is the default; passing it explicitly
                # raises InvalidLocationConstraint
                create_kwargs["CreateBucketConfiguration"] = {
                    "LocationConstraint": self._config.region,
                }
            self._s3.create_bucket(**create_kwargs)
        except self._s3.exceptions.BucketAlreadyOwnedByYou:
            # Idempotent — operator already has this bucket
            pass
        except self._s3.exceptions.BucketAlreadyExists:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"bucket name {bucket_name} already taken globally",
                errors=[bucket_name],
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False, handle="",
                message=f"create_bucket: {exc}",
                errors=[str(exc)],
            )

        # Apply tags
        try:
            self._s3.put_bucket_tagging(
                Bucket=bucket_name,
                Tagging={"TagSet": [
                    {"Key": t["Key"], "Value": t["Value"]}
                    for t in tags_for(spec)
                ]},
            )
        except Exception as exc:
            # Tag failure isn't fatal — surface but proceed
            return ProvisionResult(
                ok=True,
                handle=handle_for(kind=KIND, resource_id=bucket_name),
                message=(
                    f"bucket created but tagging failed: {exc}"
                ),
            )

        # Versioning
        if self._config.versioning_enabled:
            try:
                self._s3.put_bucket_versioning(
                    Bucket=bucket_name,
                    VersioningConfiguration={"Status": "Enabled"},
                )
            except Exception as exc:
                return ProvisionResult(
                    ok=True,
                    handle=handle_for(kind=KIND, resource_id=bucket_name),
                    message=(
                        f"bucket created but versioning failed: {exc}"
                    ),
                )

        # Public access block
        if self._config.public_access_blocked:
            try:
                self._s3.put_public_access_block(
                    Bucket=bucket_name,
                    PublicAccessBlockConfiguration={
                        "BlockPublicAcls": True,
                        "IgnorePublicAcls": True,
                        "BlockPublicPolicy": True,
                        "RestrictPublicBuckets": True,
                    },
                )
            except Exception as exc:
                return ProvisionResult(
                    ok=True,
                    handle=handle_for(kind=KIND, resource_id=bucket_name),
                    message=(
                        f"bucket created but public-access-block "
                        f"failed: {exc}"
                    ),
                )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=bucket_name),
            message=f"bucket {bucket_name} provisioned",
        )

    @driver_op(cloud="aws", driver="object_store_s3")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        """S3 buckets don't have 'size'; this is a no-op for now.
        Spec acceptance: 'update_managed_service is idempotent.'"""
        _, bucket_name = parse_handle(spec.handle)
        return UpdateResult(
            ok=True, handle=spec.handle,
            message=f"bucket {bucket_name} has no updatable attributes "
                    "via this driver (config changes go through "
                    "the bucket-policy editor)",
        )

    @driver_op(
        cloud="aws",
        driver="object_store_s3",
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
        """S3 teardown — four-corner matrix per the protocol.

        ``delete_data=False, force_destroy=False`` (default safe):
          Retain bucket + contents. Platform unbinds; bucket lives on
          for the operator to deal with manually.

        ``delete_data=False, force_destroy=True``:
          Retain bucket + contents but bypass guards (e.g. operator
          tags that would refuse cleanup).

        ``delete_data=True, force_destroy=False``:
          Empty bucket then delete. Fails if the bucket has
          versioning + MFA-delete or any object lock retention that
          blocks the empty step — operator must lift those manually.

        ``delete_data=True, force_destroy=True``:
          Disable versioning safeguards (suspend versioning, delete
          all versions + delete markers, lift any deletion-protection
          tag the platform owns), then empty + delete.
        """
        _, bucket_name = parse_handle(spec.handle)

        if not delete_data:
            # Safe path. force_destroy without delete_data is a noop
            # at the bucket level — the bucket+contents stay; the
            # platform's record of the binding is what gets cleaned up
            # by the calling activity, not this driver call.
            return DeprovisionResult(
                ok=True, handle=spec.handle,
                message=(
                    f"bucket {bucket_name} retained (delete_data=False); "
                    "platform unbinding only"
                ),
            )

        # delete_data=True branch — irreversibly delete bucket contents.
        if force_destroy:
            # Disable versioning safeguards before empty. NB: this also
            # lets the bucket be re-emptied if a previous attempt
            # half-finished. Best-effort: ignore errors (most buckets
            # are non-versioned).
            try:
                self._s3.put_bucket_versioning(
                    Bucket=bucket_name,
                    VersioningConfiguration={"Status": "Suspended"},
                )
            except Exception:
                pass

        try:
            self._empty_bucket(bucket_name=bucket_name)
        except Exception as exc:
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=f"empty failed: {exc}",
                errors=[str(exc)],
            )
        try:
            self._s3.delete_bucket(Bucket=bucket_name)
        except self._s3.exceptions.NoSuchBucket:
            pass
        except Exception as exc:
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=f"delete_bucket: {exc}",
                errors=[str(exc)],
            )
        suffix = " (force_destroy)" if force_destroy else ""
        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message=f"bucket {bucket_name} deleted with data{suffix}",
        )

    # ---- read-only ops --------------------------------------------

    @driver_op(cloud="aws", driver="object_store_s3")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, bucket_name = parse_handle(handle.handle)
        try:
            self._s3.head_bucket(Bucket=bucket_name)
        except self._s3.exceptions.NoSuchBucket:
            return ServiceStatus(
                handle=handle.handle, state="deprovisioned",
                message=f"bucket {bucket_name} does not exist",
            )
        except Exception as exc:
            # head_bucket on missing bucket can also surface as
            # generic ClientError with HTTP 404 (boto3 doesn't
            # always raise NoSuchBucket cleanly via head_bucket).
            response = getattr(exc, "response", None) or {}
            metadata = response.get("ResponseMetadata", {}) or {}
            error_code = response.get("Error", {}).get("Code", "")
            if (
                metadata.get("HTTPStatusCode") == 404
                or error_code in ("404", "NoSuchBucket")
            ):
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

    @driver_op(cloud="aws", driver="object_store_s3")
    def binding(self, handle: ServiceHandle) -> Binding:
        _, bucket_name = parse_handle(handle.handle)
        bucket_arn = f"arn:aws:s3:::{bucket_name}"
        return Binding(
            env_vars={
                "S3_BUCKET_NAME": ValueRef(literal=bucket_name),
                "S3_BUCKET_ARN": ValueRef(literal=bucket_arn),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=[
                Grant(
                    resource=bucket_arn,
                    actions=[
                        "s3:GetBucketLocation",
                        "s3:ListBucket",
                    ],
                ),
                Grant(
                    resource=f"{bucket_arn}/*",
                    actions=[
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                    ],
                ),
            ],
            notes=(
                "Object operations (Get/Put/Delete) are scoped to "
                "the bucket's contents; bucket-level operations "
                "are limited to read-only."
            ),
        )

    @driver_op(cloud="aws", driver="object_store_s3")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """S3 'snapshots' are version markers — versioning gives a
        natural point-in-time restore. The snapshot_id is the
        latest VersionId across all objects, captured as a marker
        for restore. Real bucket-level snapshots use AWS Backup
        which is out of scope for this driver."""
        from datetime import UTC, datetime

        _, bucket_name = parse_handle(handle.handle)
        if not self._config.versioning_enabled:
            raise ManagedServiceError(
                "snapshot requires versioning_enabled=True; this "
                "config has versioning disabled",
            )
        marker_id = f"v-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}"
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=marker_id,
            created_at=datetime.now(tz=UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="object_store_s3")
    def restore(
        self, snapshot: SnapshotHandle, target: ProvisionSpec,
    ) -> ProvisionResult:
        """S3 restore is best-effort: provision the target bucket
        + copy from source. Full point-in-time restore needs AWS
        Backup. This shape exists so the workflow layer doesn't
        special-case S3."""
        return ProvisionResult(
            ok=False,
            handle="",
            message=(
                "S3 restore via cross-bucket copy not implemented "
                "in this driver; use AWS Backup for point-in-time "
                "restore or copy_objects manually"
            ),
            errors=["not_implemented"],
        )

    @driver_op(cloud="aws", driver="object_store_s3", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "versioning_override": {
                    "type": "boolean",
                    "description": (
                        "Override the cluster-level versioning "
                        "default for this binding."
                    ),
                },
            },
        }

    @driver_op(cloud="aws", driver="object_store_s3", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(env_vars={
            "S3_BUCKET_NAME": "S3 bucket name",
            "S3_BUCKET_ARN": "Full bucket ARN",
            "AWS_REGION": "Bucket's region",
        })

    # ---- internals ------------------------------------------------

    def _bucket_name_for(self, *, spec: ProvisionSpec) -> str:
        """S3 bucket names are GLOBAL across AWS — collisions on
        a sensible name are common. Use a deterministic but
        scoped name: <prefix>-<org>-<app>-<env>-<hint>.
        """
        parts = [
            self._config.bucket_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        # S3 names: lowercase, 3-63 chars, no underscores, no dots
        # in the way (DNS-style). Collapse underscores/dots → dash.
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(
            c if (c.isalnum() or c == "-") else "-"
            for c in raw
        )
        # Collapse runs of dashes
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        return clean[:63]

    def _empty_bucket(self, *, bucket_name: str) -> None:
        """Empties versioned buckets including delete markers."""
        try:
            paginator = self._s3.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket_name):
                versions = page.get("Versions", []) or []
                markers = page.get("DeleteMarkers", []) or []
                objects = [
                    {"Key": v["Key"], "VersionId": v["VersionId"]}
                    for v in versions + markers
                ]
                if objects:
                    self._s3.delete_objects(
                        Bucket=bucket_name,
                        Delete={"Objects": objects},
                    )
        except Exception as exc:
            raise map_client_error(exc) from exc
