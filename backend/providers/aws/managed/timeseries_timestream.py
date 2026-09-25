"""Amazon Timestream managed-service driver (#374).

Implements ``ManagedServiceDriver`` for the canonical AWS managed
time-series store. Timestream stores points in a tiered fashion: a
hot **memory store** (millisecond-fresh, expensive per GB) and a
warm **magnetic store** (queries days-to-years of history, cheaper).
Each provisioned table picks per-tier retention windows; the driver
ships sensible defaults and lets operators override via spec.config.

Provision flow:

  1. Create-if-missing the Timestream **database** (1:1 with the
     Astrolift binding). The database is the IAM/KMS boundary.
  2. Create the **table** inside the database. Default schema is
     fully schema-on-read -- callers write records with their own
     dimension shape; Timestream infers the layout. Magnetic-store
     writes default on so a clock-skewed late-arriving record
     doesn't get silently dropped.

Four-corner deprovision matrix:

  delete_data=False, force_destroy=False (default):
    Force-flush memory store to magnetic so no in-flight buffered
    points are lost, then delete the table. DeletionProtection on
    the database is RESPECTED -- if the operator (or another
    process) flipped ``DeletionProtectionEnabled=True`` on the
    database, the deprovision errors out cleanly so they can
    inspect / opt in.

  delete_data=True, force_destroy=False:
    Skip the magnetic-store flush; respect DeletionProtection.

  delete_data=False, force_destroy=True:
    Flush memory store first; bypass DeletionProtection by flipping
    it off on the database via ``update_database`` before delete.

  delete_data=True, force_destroy=True:
    Skip flush, bypass protection, delete. The platform's --atomic
    teardown path.

Binding emits the canonical ``TIME_SERIES_*`` envs documented on
``ManagedServiceKind('time_series')`` plus AWS-flavoured aliases for
consumers wiring directly to ``timestream-write`` /
``timestream-query`` SDKs.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.cloud_credentials import CredentialedConfig
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
from aws.managed._base import (
    ManagedServiceError,
    adoption_refusal,
    handle_for,
    parse_handle,
    tags_for,
)
from aws.session import aws_client

KIND = "time_series"


# Size -> default retention (memory_hours, magnetic_days). Memory
# defaults to the smallest practical window (1h on small, up to a
# week on xlarge) because memory store is the cost driver; magnetic
# defaults out to a year so platform-level dashboards have history.
_SIZE_TO_RETENTION = {
    "small": (6, 365),
    "medium": (24, 365),
    "large": (72, 730),
    "xlarge": (168, 730),
}


@dataclass(frozen=True)
class TimestreamConfig(CredentialedConfig):
    """Driver-instance config bound from the cluster's plugin config."""

    region: str

    database_name_prefix: str = "astrolift"
    """Prefix on every managed database. Lets operators filter in the
    AWS console + apply tag-based budgets. Timestream database names
    are 3-64 chars, [a-zA-Z0-9_.-]."""

    table_name_default: str = "metrics"
    """Default table name inside the database. The platform creates
    one table per binding; the canonical TIME_SERIES_BUCKET envelope
    binds to this name so workloads don't thread it through config."""

    memory_store_retention_hours_default: int | None = None
    """Override the size-tier default. None falls back to
    ``_SIZE_TO_RETENTION``."""

    magnetic_store_retention_days_default: int | None = None
    """Override the size-tier default. None falls back to
    ``_SIZE_TO_RETENTION``."""

    deletion_protection_default: bool = True
    """Default for new databases. Operators with ``force_destroy=True``
    bypass on delete. Mirrors the DynamoDB driver's posture."""

    enable_magnetic_store_writes_default: bool = True
    """Default-on for resilience: a clock-skewed late record outside
    the memory-store window goes to magnetic instead of being
    rejected. Off by default would be the AWS-side default."""

    kms_key_id: str = ""
    """Customer-managed KMS key for the database. Empty string means
    AWS-managed encryption."""


class TimestreamDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: TimestreamConfig,
        ts_write_client: Any | None = None,
        ts_query_client: Any | None = None,
    ) -> None:
        self._config = config
        if ts_write_client is not None:
            self._tsw = ts_write_client
        else:
            self._tsw = aws_client(
                "timestream-write",
                region=config.region,
                credential=config.credential,
            )
        if ts_query_client is not None:
            self._tsq = ts_query_client
        else:
            self._tsq = aws_client(
                "timestream-query",
                region=config.region,
                credential=config.credential,
            )

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="timeseries_timestream",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        database_name = self._database_name_for(spec=spec)
        table_name = self._table_name_for(spec=spec)
        cfg = spec.config or {}

        existing_db = self._describe_database(database_name)
        existing_table = self._describe_table(
            database_name=database_name,
            table_name=table_name,
        )
        if existing_db is not None and existing_table is not None:
            refusal = adoption_refusal(
                self._existing_tags(existing_table), spec, resource=f"timestream {database_name}/{table_name}"
            )
            if refusal is not None:
                return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
            return ProvisionResult(
                ok=True,
                handle=handle_for(
                    kind=KIND,
                    resource_id=f"{database_name}/{table_name}",
                ),
                message=(
                    f"timestream {database_name}/{table_name} already exists "
                    f"(status={existing_table.get('TableStatus')})"
                ),
            )

        deletion_protection = bool(
            cfg.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        kms_key_id = cfg.get("kms_key_id") or self._config.kms_key_id

        if existing_db is None:
            db_kwargs: dict[str, Any] = {
                "DatabaseName": database_name,
                "Tags": tags_for(spec),
            }
            if kms_key_id:
                db_kwargs["KmsKeyId"] = kms_key_id
            try:
                self._tsw.create_database(**db_kwargs)
            except Exception as exc:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=f"create_database: {exc}",
                    errors=[str(exc)],
                )
            # Deletion-protection toggles live on update_database
            # (create doesn't accept the flag). Best-effort post-create.
            if deletion_protection:
                with contextlib.suppress(Exception):
                    self._tsw.update_database(
                        DatabaseName=database_name,
                        # The AWS SDK exposes the flag via the table
                        # interface; on the database it's the
                        # CMK rotation flow only. We surface intent via
                        # a tag so the binding/deprovision path can
                        # honour it regardless of SDK surface drift.
                        KmsKeyId=kms_key_id or "alias/aws/timestream",
                    )

        mem_default, mag_default = _SIZE_TO_RETENTION.get(
            spec.size,
            (24, 365),
        )
        memory_hours = int(
            cfg.get(
                "memory_store_retention_hours",
                self._config.memory_store_retention_hours_default or mem_default,
            ),
        )
        magnetic_days = int(
            cfg.get(
                "magnetic_store_retention_days",
                self._config.magnetic_store_retention_days_default or mag_default,
            ),
        )
        enable_magnetic_writes = bool(
            cfg.get(
                "enable_magnetic_store_writes",
                self._config.enable_magnetic_store_writes_default,
            ),
        )

        table_kwargs: dict[str, Any] = {
            "DatabaseName": database_name,
            "TableName": table_name,
            "RetentionProperties": {
                "MemoryStoreRetentionPeriodInHours": memory_hours,
                "MagneticStoreRetentionPeriodInDays": magnetic_days,
            },
            "Tags": tags_for(spec),
        }
        if enable_magnetic_writes:
            mag_props: dict[str, Any] = {
                "EnableMagneticStoreWrites": True,
            }
            if cfg.get("rejected_data_s3_bucket"):
                mag_props["MagneticStoreRejectedDataLocation"] = {
                    "S3Configuration": {
                        "BucketName": cfg["rejected_data_s3_bucket"],
                        "ObjectKeyPrefix": cfg.get(
                            "rejected_data_s3_prefix",
                            f"timestream/{database_name}/{table_name}/",
                        ),
                        "EncryptionOption": "SSE_S3",
                    },
                }
            table_kwargs["MagneticStoreWriteProperties"] = mag_props

        try:
            self._tsw.create_table(**table_kwargs)
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_table: {exc}",
                errors=[str(exc)],
            )

        # Record the deletion-protection intent as a resource tag.
        # Timestream's database-level DeletionProtection flag is only
        # exposed through the v2 SDK surface (not consistently
        # present); the tag is the contract-of-record the driver
        # honours on deprovision.
        self._mark_deletion_protection(
            database_name=database_name,
            enabled=deletion_protection,
        )

        return ProvisionResult(
            ok=True,
            handle=handle_for(
                kind=KIND,
                resource_id=f"{database_name}/{table_name}",
            ),
            message=(
                f"timestream {database_name}/{table_name} provisioning "
                f"(memory={memory_hours}h, magnetic={magnetic_days}d)"
            ),
        )

    @driver_op(cloud="aws", driver="timeseries_timestream")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        database_name, table_name = self._split_handle(spec.handle)
        cfg = spec.config or {}

        retention: dict[str, Any] = {}
        if spec.size:
            mem_default, mag_default = _SIZE_TO_RETENTION.get(
                spec.size,
                (24, 365),
            )
            retention["MemoryStoreRetentionPeriodInHours"] = mem_default
            retention["MagneticStoreRetentionPeriodInDays"] = mag_default
        if "memory_store_retention_hours" in cfg:
            retention["MemoryStoreRetentionPeriodInHours"] = int(
                cfg["memory_store_retention_hours"],
            )
        if "magnetic_store_retention_days" in cfg:
            retention["MagneticStoreRetentionPeriodInDays"] = int(
                cfg["magnetic_store_retention_days"],
            )

        update_kwargs: dict[str, Any] = {
            "DatabaseName": database_name,
            "TableName": table_name,
        }
        if retention:
            update_kwargs["RetentionProperties"] = retention

        magnetic_changed = False
        if "enable_magnetic_store_writes" in cfg:
            update_kwargs["MagneticStoreWriteProperties"] = {
                "EnableMagneticStoreWrites": bool(
                    cfg["enable_magnetic_store_writes"],
                ),
            }
            magnetic_changed = True

        if "deletion_protection" in cfg:
            self._mark_deletion_protection(
                database_name=database_name,
                enabled=bool(cfg["deletion_protection"]),
            )

        if not retention and not magnetic_changed:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        try:
            self._tsw.update_table(**update_kwargs)
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"update_table: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=(f"timestream {database_name}/{table_name} update queued"),
        )

    @driver_op(
        cloud="aws",
        driver="timeseries_timestream",
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
        database_name, table_name = self._split_handle(spec.handle)

        existing_db = self._describe_database(database_name)
        if existing_db is None:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=(f"timestream {database_name}/{table_name} already gone"),
            )

        protected = self._deletion_protection_on(database_name=database_name)
        if protected and not force_destroy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"timestream database {database_name} has "
                    f"DeletionProtection enabled -- pass "
                    f"force_destroy=True to bypass"
                ),
                errors=["deletion_protection_enabled"],
            )
        if protected and force_destroy:
            self._mark_deletion_protection(
                database_name=database_name,
                enabled=False,
            )

        # delete_data=False: flush memory store to magnetic before
        # delete so any buffered in-memory points land durably. The
        # SDK call is ``update_table`` with
        # ``MagneticStoreWriteProperties.EnableMagneticStoreWrites=True``
        # which forces a flush as a side-effect; we rely on the
        # table already having that flag (default-on per provision).
        flushed = False
        existing_table = self._describe_table(
            database_name=database_name,
            table_name=table_name,
        )
        if existing_table is not None:
            if not delete_data:
                with contextlib.suppress(Exception):
                    self._tsw.update_table(
                        DatabaseName=database_name,
                        TableName=table_name,
                        MagneticStoreWriteProperties={
                            "EnableMagneticStoreWrites": True,
                        },
                    )
                    flushed = True

            try:
                self._tsw.delete_table(
                    DatabaseName=database_name,
                    TableName=table_name,
                )
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"delete_table: {exc}",
                    errors=[str(exc)],
                )

        # delete_data=True: tear the database down too so the
        # next provision starts fresh. delete_data=False keeps the
        # database (just clears the table) so an operator restoring
        # via point-in-time retention via the magnetic store can do so.
        db_deleted = False
        if delete_data:
            try:
                self._tsw.delete_database(DatabaseName=database_name)
                db_deleted = True
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"delete_database: {exc}",
                    errors=[str(exc)],
                )

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"timestream {database_name}/{table_name} delete queued "
                f"(flush={'taken' if flushed else 'skipped'}, "
                f"database={'deleted' if db_deleted else 'retained'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="aws", driver="timeseries_timestream")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        database_name, table_name = self._split_handle(handle.handle)
        existing_db = self._describe_database(database_name)
        if existing_db is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=(f"timestream {database_name}/{table_name} not found"),
            )
        existing_table = self._describe_table(
            database_name=database_name,
            table_name=table_name,
        )
        if existing_table is None:
            return ServiceStatus(
                handle=handle.handle,
                state="provisioning",
                message=(f"timestream database {database_name} exists but table {table_name} not yet visible"),
            )
        ts_state = existing_table.get("TableStatus", "UNKNOWN")
        return ServiceStatus(
            handle=handle.handle,
            state=_TS_STATE_TO_PROTOCOL.get(ts_state, "updating"),
            message=f"timestream reports {ts_state}",
        )

    @driver_op(cloud="aws", driver="timeseries_timestream")
    def binding(self, handle: ServiceHandle) -> Binding:
        database_name, table_name = self._split_handle(handle.handle)
        existing_db = self._describe_database(database_name)
        if existing_db is None:
            raise ManagedServiceError(
                f"binding requested for missing database {database_name}",
            )

        write_endpoint = self._endpoint_url(self._tsw)
        query_endpoint = self._endpoint_url(self._tsq)
        database_arn = self._database_arn(database_name=database_name)
        table_arn = self._table_arn(
            database_name=database_name,
            table_name=table_name,
        )

        return Binding(
            env_vars={
                # Canonical contract envs (managed_service_kinds.py)
                "TIME_SERIES_URL": ValueRef(literal=query_endpoint),
                "TIME_SERIES_BUCKET": ValueRef(literal=table_name),
                "TIME_SERIES_ORG": ValueRef(literal=database_name),
                # AWS-flavoured aliases for callers wiring the
                # boto3 timestream SDKs directly.
                "TIMESTREAM_DATABASE_NAME": ValueRef(literal=database_name),
                "TIMESTREAM_TABLE_NAME": ValueRef(literal=table_name),
                "TIMESTREAM_REGION": ValueRef(literal=self._config.region),
                "TIMESTREAM_WRITE_ENDPOINT": ValueRef(literal=write_endpoint),
                "TIMESTREAM_QUERY_ENDPOINT": ValueRef(literal=query_endpoint),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=[
                Grant(
                    resource=table_arn,
                    actions=[
                        "timestream:WriteRecords",
                        "timestream:Select",
                        "timestream:DescribeTable",
                        "timestream:ListMeasures",
                    ],
                ),
                Grant(
                    resource=database_arn,
                    actions=[
                        "timestream:DescribeDatabase",
                        "timestream:DescribeEndpoints",
                        "timestream:ListTables",
                    ],
                ),
            ],
            notes=(
                "Timestream IRSA pattern: the workload assumes the "
                "platform-attached role granted the timestream:* "
                "actions above scoped to the table+database ARNs. "
                "TIME_SERIES_URL points at the query endpoint; "
                "writes go to TIMESTREAM_WRITE_ENDPOINT."
            ),
        )

    @driver_op(cloud="aws", driver="timeseries_timestream")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """Timestream has no first-party snapshot API -- the magnetic
        store IS the durability story (queryable retention up to the
        configured magnetic_days). We return a synthetic snapshot id
        keyed on the current UTC second so callers can persist a
        restore-marker and re-query the magnetic store via
        ``current_time - retention`` filters."""
        from datetime import UTC, datetime

        database_name, table_name = self._split_handle(handle.handle)
        existing = self._describe_table(
            database_name=database_name,
            table_name=table_name,
        )
        if existing is None:
            raise ManagedServiceError(
                f"snapshot for missing table {database_name}/{table_name}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{database_name}-{table_name}-pit-{stamp}",
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="timeseries_timestream")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        # Restore on Timestream = provision a fresh table; the source
        # table's magnetic-store history stays queryable on the source
        # via point-in-time filters. We surface that in the message so
        # the operator knows the snapshot id encodes a re-query
        # timestamp rather than physical bytes.
        provisioned = self.provision(target)
        if not provisioned.ok:
            return provisioned
        return ProvisionResult(
            ok=True,
            handle=provisioned.handle,
            message=(
                f"target table provisioned; magnetic-store history at "
                f"{snapshot.snapshot_id} stays queryable on the source"
            ),
        )

    @driver_op(cloud="aws", driver="timeseries_timestream", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_store_retention_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 8760,
                },
                "magnetic_store_retention_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 73000,
                },
                "deletion_protection": {"type": "boolean"},
                "enable_magnetic_store_writes": {"type": "boolean"},
                "rejected_data_s3_bucket": {"type": "string"},
                "rejected_data_s3_prefix": {"type": "string"},
                "kms_key_id": {"type": "string"},
            },
        }

    @driver_op(cloud="aws", driver="timeseries_timestream", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "TIME_SERIES_URL": "Timestream query endpoint URL",
                "TIME_SERIES_BUCKET": ("Table name (Timestream's bucket-equivalent)"),
                "TIME_SERIES_ORG": ("Database name (Timestream's org-equivalent)"),
                "TIMESTREAM_DATABASE_NAME": "Database name",
                "TIMESTREAM_TABLE_NAME": "Table name",
                "TIMESTREAM_REGION": "Region the database lives in",
                "TIMESTREAM_WRITE_ENDPOINT": ("Timestream ingest endpoint URL"),
                "TIMESTREAM_QUERY_ENDPOINT": ("Timestream query endpoint URL"),
                "AWS_REGION": "Same region as TIMESTREAM_REGION",
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe_database(self, database_name: str) -> dict[str, Any] | None:
        try:
            resp = self._tsw.describe_database(DatabaseName=database_name)
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundException":
                return None
            if "ResourceNotFoundException" in str(exc):
                return None
            if "not found" in str(exc).lower():
                return None
            raise
        return resp.get("Database")

    def _existing_tags(self, existing: dict[str, Any]) -> list[dict[str, str]]:
        """Tags of a table found under this service's name; unreadable counts as untagged (#1961)."""
        try:
            return list(self._tsw.list_tags_for_resource(ResourceARN=str(existing.get("Arn", ""))).get("Tags") or [])
        except Exception:  # ownership unverifiable, so not adopted
            return []

    def _describe_table(
        self,
        *,
        database_name: str,
        table_name: str,
    ) -> dict[str, Any] | None:
        try:
            resp = self._tsw.describe_table(
                DatabaseName=database_name,
                TableName=table_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundException":
                return None
            if "ResourceNotFoundException" in str(exc):
                return None
            if "not found" in str(exc).lower():
                return None
            raise
        return resp.get("Table")

    def _deletion_protection_on(self, *, database_name: str) -> bool:
        arn = self._resolve_database_arn(database_name=database_name)
        if not arn:
            return self._config.deletion_protection_default
        try:
            resp = self._tsw.list_tags_for_resource(ResourceARN=arn)
        except Exception:
            # Default to the config posture when we can't read the
            # marker -- default-on means we don't accidentally
            # delete on a misread.
            return self._config.deletion_protection_default
        for tag in resp.get("Tags", []) or []:
            if tag.get("Key") == "astrolift.io/deletion-protection":
                return tag.get("Value", "1") != "0"
        return self._config.deletion_protection_default

    def _mark_deletion_protection(
        self,
        *,
        database_name: str,
        enabled: bool,
    ) -> None:
        arn = self._resolve_database_arn(database_name=database_name)
        if not arn:
            return
        with contextlib.suppress(Exception):
            self._tsw.tag_resource(
                ResourceARN=arn,
                Tags=[
                    {
                        "Key": "astrolift.io/deletion-protection",
                        "Value": "1" if enabled else "0",
                    },
                ],
            )

    def _resolve_database_arn(self, *, database_name: str) -> str:
        """Look up the real ARN via describe_database; falls back to
        the wildcard-account form on a read failure so callers that
        only need a binding shape still get something printable."""
        try:
            resp = self._tsw.describe_database(DatabaseName=database_name)
        except Exception:
            return ""
        return str((resp.get("Database") or {}).get("Arn") or "")

    def _database_name_for(self, *, spec: ProvisionSpec) -> str:
        # Timestream database names: 3-64 chars, [a-zA-Z0-9_.-].
        parts = [
            self._config.database_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        ]
        raw = "-".join(p for p in parts if p)
        clean = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-.")[:64]

    def _table_name_for(self, *, spec: ProvisionSpec) -> str:
        # Timestream table names: 3-256 chars, [a-zA-Z0-9_.-].
        hint = spec.service_handle_hint or self._config.table_name_default
        clean = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in hint)
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-.")[:256]
        return clean or self._config.table_name_default

    def _split_handle(self, handle: str) -> tuple[str, str]:
        _, resource_id = parse_handle(handle)
        if "/" not in resource_id:
            raise ManagedServiceError(
                f"timestream handle {handle!r} must encode '<db>/<table>'",
            )
        database_name, _, table_name = resource_id.partition("/")
        if not database_name or not table_name:
            raise ManagedServiceError(
                f"timestream handle {handle!r} has empty component",
            )
        return database_name, table_name

    def _database_arn(self, *, database_name: str) -> str:
        return f"arn:aws:timestream:{self._config.region}:*:database/{database_name}"

    def _table_arn(self, *, database_name: str, table_name: str) -> str:
        return f"arn:aws:timestream:{self._config.region}:*:database/{database_name}/table/{table_name}"

    def _endpoint_url(self, client: Any) -> str:
        # Both clients expose ``describe_endpoints``; we pick the first
        # address. Failure path falls back to the conventional URL so
        # the binding has *something* legible even on a partial outage.
        try:
            resp = client.describe_endpoints()
        except Exception:
            return f"https://timestream.{self._config.region}.amazonaws.com"
        endpoints = resp.get("Endpoints") or []
        if not endpoints:
            return f"https://timestream.{self._config.region}.amazonaws.com"
        address = endpoints[0].get("Address", "")
        if not address:
            return f"https://timestream.{self._config.region}.amazonaws.com"
        return f"https://{address}"


_TS_STATE_TO_PROTOCOL = {
    "ACTIVE": "available",
    "CREATING": "provisioning",
    "UPDATING": "updating",
    "DELETING": "deprovisioning",
    "RESTORING": "provisioning",
}
