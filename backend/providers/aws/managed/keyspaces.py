"""Amazon Keyspaces Cassandra-compatible table driver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
from aws.managed._base import ManagedServiceError, handle_for, parse_handle, tags_for

KIND = "wide_column"
_SIZE_TO_CAPACITY = {
    "small": (5, 5),
    "medium": (25, 25),
    "large": (100, 100),
    "xlarge": (500, 500),
}
_STATE = {
    "ACTIVE": "available",
    "CREATING": "provisioning",
    "RESTORING": "provisioning",
    "UPDATING": "updating",
    "DELETING": "deprovisioning",
    "DELETED": "deprovisioned",
    "INACCESSIBLE_ENCRYPTION_CREDENTIALS": "error",
}


@dataclass(frozen=True)
class KeyspacesConfig:
    region: str
    account_id: str
    name_prefix: str = "astrolift"
    throughput_mode_default: str = "PAY_PER_REQUEST"
    point_in_time_recovery_default: bool = True
    vpc_endpoint_id: str = ""


class KeyspacesDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: KeyspacesConfig,
        keyspaces_client: Any | None = None,
    ) -> None:
        self._config = config
        if keyspaces_client is None:
            import boto3

            keyspaces_client = boto3.client("keyspaces", region_name=config.region)
        self._keyspaces = keyspaces_client

    @driver_op(
        cloud="aws",
        driver="keyspaces",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_keyspaces_config"])
        keyspace_name, table_name = self._coordinates(spec)
        handle = self._handle(keyspace_name, table_name)
        try:
            if self._get_keyspace(keyspace_name) is None:
                self._create_keyspace(keyspace_name, spec, cfg)
            table = self._get_table(keyspace_name, table_name)
            if table is None:
                self._create_table(keyspace_name, table_name, spec, cfg)
                return ProvisionResult(
                    True,
                    handle,
                    f"Amazon Keyspaces table {keyspace_name}.{table_name} provisioning",
                )
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Amazon Keyspaces: {exc}", [str(exc)])
        updated = self.update(UpdateSpec(handle=handle, size=spec.size, config=cfg))
        return ProvisionResult(updated.ok, handle, updated.message, updated.errors)

    @driver_op(cloud="aws", driver="keyspaces")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        keyspace_name, table_name = self._parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg, update=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_keyspaces_config"])
        existing = self._get_table(keyspace_name, table_name)
        if existing is None:
            return UpdateResult(False, spec.handle, "Amazon Keyspaces table not found", ["not_found"])
        kwargs: dict[str, Any] = {
            "keyspaceName": keyspace_name,
            "tableName": table_name,
        }
        if spec.size or any(key in cfg for key in ("throughput_mode", "read_capacity", "write_capacity")):
            desired_capacity = self._capacity(
                spec.size or "",
                cfg,
                current=existing.get("capacitySpecification") or {},
            )
            current_capacity = existing.get("capacitySpecification") or {}
            comparable_current = {
                key: current_capacity[key]
                for key in ("throughputMode", "readCapacityUnits", "writeCapacityUnits")
                if key in current_capacity
            }
            if desired_capacity != comparable_current:
                kwargs["capacitySpecification"] = desired_capacity
        if "kms_key_arn" in cfg:
            kwargs["encryptionSpecification"] = self._encryption(cfg)
        if "point_in_time_recovery" in cfg:
            kwargs["pointInTimeRecovery"] = {
                "status": "ENABLED" if cfg["point_in_time_recovery"] else "DISABLED",
            }
        if cfg.get("ttl_enabled"):
            kwargs["ttl"] = {"status": "ENABLED"}
        if "default_time_to_live" in cfg:
            kwargs["defaultTimeToLive"] = int(cfg["default_time_to_live"])
        if cfg.get("client_side_timestamps"):
            kwargs["clientSideTimestamps"] = {"status": "ENABLED"}
        for key, aws_key in (
            ("add_columns", "addColumns"),
            ("auto_scaling", "autoScalingSpecification"),
            ("replica_specifications", "replicaSpecifications"),
            ("cdc_specification", "cdcSpecification"),
            ("warm_throughput", "warmThroughputSpecification"),
        ):
            if key in cfg:
                kwargs[aws_key] = cfg[key]
        if len(kwargs) == 2:
            return UpdateResult(True, spec.handle, "no Amazon Keyspaces changes requested")
        try:
            self._keyspaces.update_table(**kwargs)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update_table: {exc}", [str(exc)])
        return UpdateResult(
            True,
            spec.handle,
            f"Amazon Keyspaces table {keyspace_name}.{table_name} update queued",
        )

    @driver_op(
        cloud="aws",
        driver="keyspaces",
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
        del force_destroy
        keyspace_name, table_name = self._parse_handle(spec.handle)
        table = self._get_table(keyspace_name, table_name)
        if table is not None:
            if table.get("status") == "DELETING":
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Amazon Keyspaces table {keyspace_name}.{table_name} is deleting",
                    ["table_deletion_in_progress"],
                    retryable=True,
                )
            pitr = (table.get("pointInTimeRecovery") or {}).get("status") == "ENABLED"
            if not delete_data and not pitr:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Amazon Keyspaces PITR is disabled; enable it or set delete_data=true",
                    ["data_retention_not_available"],
                    retryable=False,
                )
            if delete_data and pitr:
                try:
                    self._keyspaces.update_table(
                        keyspaceName=keyspace_name,
                        tableName=table_name,
                        pointInTimeRecovery={"status": "DISABLED"},
                    )
                except Exception as exc:
                    return DeprovisionResult(
                        False,
                        spec.handle,
                        f"disable point-in-time recovery: {exc}",
                        [str(exc)],
                        retryable=_retryable_cloud_error(exc),
                    )
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Amazon Keyspaces PITR disable queued before destructive deletion",
                    ["pitr_disable_in_progress"],
                    retryable=True,
                )
            try:
                self._keyspaces.delete_table(
                    keyspaceName=keyspace_name,
                    tableName=table_name,
                )
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"delete_table: {exc}",
                    [str(exc)],
                    retryable=_retryable_cloud_error(exc),
                )
            retention = "PITR retained for 35 days" if pitr else "PITR disabled"
            return DeprovisionResult(
                False,
                spec.handle,
                f"Amazon Keyspaces table deletion queued ({retention})",
                ["table_deletion_in_progress"],
                retryable=True,
            )
        if self._get_keyspace(keyspace_name) is not None:
            if not delete_data:
                return DeprovisionResult(
                    True,
                    spec.handle,
                    (
                        f"Amazon Keyspaces table is gone; empty keyspace {keyspace_name} retained "
                        "for the 35-day PITR recovery window"
                    ),
                )
            try:
                self._keyspaces.delete_keyspace(keyspaceName=keyspace_name)
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"delete_keyspace: {exc}",
                    [str(exc)],
                    retryable=_retryable_cloud_error(exc),
                )
            return DeprovisionResult(
                False,
                spec.handle,
                f"Amazon Keyspaces keyspace {keyspace_name} deletion queued",
                ["keyspace_deletion_in_progress"],
                retryable=True,
            )
        return DeprovisionResult(True, spec.handle, "Amazon Keyspaces table and keyspace are gone")

    @driver_op(cloud="aws", driver="keyspaces")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        keyspace_name, table_name = self._parse_handle(handle.handle)
        table = self._get_table(keyspace_name, table_name)
        if table is None:
            return ServiceStatus(handle.handle, "deprovisioned", "Amazon Keyspaces table not found")
        state = str(table.get("status", "UNKNOWN"))
        return ServiceStatus(handle.handle, _STATE.get(state, "updating"), f"Amazon Keyspaces reports {state}")

    @driver_op(cloud="aws", driver="keyspaces")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        del config
        keyspace_name, table_name = self._parse_handle(handle.handle)
        table = self._get_table(keyspace_name, table_name)
        if table is None:
            raise ManagedServiceError("binding requested for missing Amazon Keyspaces table")
        arn = str(table.get("resourceArn") or self._table_arn(keyspace_name, table_name))
        endpoint = f"cassandra.{self._config.region}.amazonaws.com"
        return Binding(
            env_vars={
                "WIDE_COLUMN_ENDPOINT": ValueRef(literal=endpoint),
                "WIDE_COLUMN_KEYSPACE": ValueRef(literal=keyspace_name),
                "WIDE_COLUMN_TABLE": ValueRef(literal=table_name),
                "WIDE_COLUMN_REGION": ValueRef(literal=self._config.region),
                "WIDE_COLUMN_PORT": ValueRef(literal="9142"),
                "WIDE_COLUMN_AUTH_MODE": ValueRef(literal="AWS_SIGV4"),
                "WIDE_COLUMN_RESOURCE_ARN": ValueRef(literal=arn),
            },
            iam_grants=[
                Grant(
                    arn,
                    [
                        "cassandra:Select",
                        "cassandra:Modify",
                    ],
                ),
            ],
            notes=(
                "TLS Cassandra endpoint using workload IAM SigV4 authentication"
                + (
                    f" through private VPC endpoint {self._config.vpc_endpoint_id}"
                    if self._config.vpc_endpoint_id
                    else ""
                )
            ),
        )

    @driver_op(cloud="aws", driver="keyspaces")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        keyspace_name, table_name = self._parse_handle(handle.handle)
        table = self._get_table(keyspace_name, table_name)
        if table is None:
            raise ManagedServiceError("snapshot requested for missing Amazon Keyspaces table")
        if (table.get("pointInTimeRecovery") or {}).get("status") != "ENABLED":
            raise ManagedServiceError("Amazon Keyspaces point-in-time recovery is not enabled")
        now = datetime.now(UTC).replace(microsecond=0)
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=now.isoformat(),
            created_at=now.isoformat(),
        )

    @driver_op(cloud="aws", driver="keyspaces")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        source_keyspace, source_table = self._parse_handle(snapshot.handle)
        target_keyspace, target_table = self._coordinates(target)
        cfg = target.config or {}
        try:
            if self._get_keyspace(target_keyspace) is None:
                self._create_keyspace(target_keyspace, target, cfg)
            kwargs: dict[str, Any] = {
                "sourceKeyspaceName": source_keyspace,
                "sourceTableName": source_table,
                "targetKeyspaceName": target_keyspace,
                "targetTableName": target_table,
                "restoreTimestamp": datetime.fromisoformat(snapshot.snapshot_id),
                "tagsOverride": _keyspaces_tags(target),
            }
            if any(key in cfg for key in ("throughput_mode", "read_capacity", "write_capacity")):
                kwargs["capacitySpecificationOverride"] = self._capacity(target.size, cfg)
            if "kms_key_arn" in cfg:
                kwargs["encryptionSpecificationOverride"] = self._encryption(cfg)
            if "point_in_time_recovery" in cfg:
                kwargs["pointInTimeRecoveryOverride"] = {
                    "status": "ENABLED" if cfg["point_in_time_recovery"] else "DISABLED",
                }
            for key, aws_key in (
                ("auto_scaling", "autoScalingSpecification"),
                ("replica_specifications", "replicaSpecifications"),
            ):
                if key in cfg:
                    kwargs[aws_key] = cfg[key]
            self._keyspaces.restore_table(**kwargs)
        except Exception as exc:
            return ProvisionResult(False, "", f"restore_table: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            self._handle(target_keyspace, target_table),
            f"Amazon Keyspaces restore queued into {target_keyspace}.{target_table}",
        )

    @driver_op(cloud="aws", driver="keyspaces", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "throughput_mode": {
                    "type": "string",
                    "enum": ["PAY_PER_REQUEST", "PROVISIONED"],
                },
                "read_capacity": {"type": "integer", "minimum": 1},
                "write_capacity": {"type": "integer", "minimum": 1},
                "point_in_time_recovery": {"type": "boolean"},
                "kms_key_arn": {"type": "string"},
                "all_columns": {"type": "array"},
                "partition_keys": {"type": "array"},
                "clustering_keys": {"type": "array"},
                "static_columns": {"type": "array"},
                "replication_regions": {"type": "array", "items": {"type": "string"}},
                "ttl_enabled": {"type": "boolean"},
                "default_time_to_live": {"type": "integer", "minimum": 0},
                "client_side_timestamps": {"type": "boolean"},
                "auto_scaling": {"type": "object"},
                "replica_specifications": {"type": "array"},
                "cdc_specification": {"type": "object"},
                "warm_throughput": {"type": "object"},
                "add_columns": {"type": "array"},
                "comment": {"type": "string"},
            },
        }

    @driver_op(cloud="aws", driver="keyspaces", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "WIDE_COLUMN_ENDPOINT": "Regional Cassandra endpoint",
                "WIDE_COLUMN_KEYSPACE": "Managed keyspace name",
                "WIDE_COLUMN_TABLE": "Managed table name",
                "WIDE_COLUMN_REGION": "AWS region",
                "WIDE_COLUMN_PORT": "TLS port 9142",
                "WIDE_COLUMN_AUTH_MODE": "AWS_SIGV4",
                "WIDE_COLUMN_RESOURCE_ARN": "Amazon Keyspaces table ARN",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "throughput_mode",
            "read_capacity",
            "write_capacity",
            "point_in_time_recovery",
            "kms_key_arn",
            "ttl_enabled",
            "default_time_to_live",
            "client_side_timestamps",
            "auto_scaling",
            "replica_specifications",
            "cdc_specification",
            "warm_throughput",
            "add_columns",
        ]

    def _create_keyspace(
        self,
        keyspace_name: str,
        spec: ProvisionSpec,
        cfg: dict[str, Any],
    ) -> None:
        regions = list(cfg.get("replication_regions") or [])
        replication: dict[str, Any] = {
            "replicationStrategy": "MULTI_REGION" if regions else "SINGLE_REGION",
        }
        if regions:
            replication["regionList"] = regions
        self._keyspaces.create_keyspace(
            keyspaceName=keyspace_name,
            replicationSpecification=replication,
            tags=_keyspaces_tags(spec),
        )

    def _create_table(
        self,
        keyspace_name: str,
        table_name: str,
        spec: ProvisionSpec,
        cfg: dict[str, Any],
    ) -> None:
        kwargs: dict[str, Any] = {
            "keyspaceName": keyspace_name,
            "tableName": table_name,
            "schemaDefinition": {
                "allColumns": list(
                    cfg.get("all_columns") or [{"name": "partition_key", "type": "text"}],
                ),
                "partitionKeys": list(
                    cfg.get("partition_keys") or [{"name": "partition_key"}],
                ),
                "clusteringKeys": list(cfg.get("clustering_keys") or []),
                "staticColumns": list(cfg.get("static_columns") or []),
            },
            "capacitySpecification": self._capacity(spec.size, cfg),
            "encryptionSpecification": self._encryption(cfg),
            "pointInTimeRecovery": {
                "status": (
                    "ENABLED"
                    if cfg.get(
                        "point_in_time_recovery",
                        self._config.point_in_time_recovery_default,
                    )
                    else "DISABLED"
                ),
            },
            "tags": _keyspaces_tags(spec),
        }
        if cfg.get("comment"):
            kwargs["comment"] = {"message": str(cfg["comment"])}
        if cfg.get("ttl_enabled"):
            kwargs["ttl"] = {"status": "ENABLED"}
        if "default_time_to_live" in cfg:
            kwargs["defaultTimeToLive"] = int(cfg["default_time_to_live"])
        if cfg.get("client_side_timestamps") or cfg.get("replication_regions"):
            kwargs["clientSideTimestamps"] = {"status": "ENABLED"}
        for key, aws_key in (
            ("auto_scaling", "autoScalingSpecification"),
            ("replica_specifications", "replicaSpecifications"),
            ("cdc_specification", "cdcSpecification"),
            ("warm_throughput", "warmThroughputSpecification"),
        ):
            if key in cfg:
                kwargs[aws_key] = cfg[key]
        self._keyspaces.create_table(**kwargs)

    def _capacity(
        self,
        size: str,
        cfg: dict[str, Any],
        *,
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = current or {}
        mode = str(
            cfg.get("throughput_mode") or current.get("throughputMode") or self._config.throughput_mode_default,
        )
        capacity: dict[str, Any] = {"throughputMode": mode}
        if mode == "PROVISIONED":
            default_read, default_write = _SIZE_TO_CAPACITY.get(size, (5, 5))
            capacity["readCapacityUnits"] = int(
                cfg.get("read_capacity", current.get("readCapacityUnits", default_read)),
            )
            capacity["writeCapacityUnits"] = int(
                cfg.get("write_capacity", current.get("writeCapacityUnits", default_write)),
            )
        return capacity

    @staticmethod
    def _encryption(cfg: dict[str, Any]) -> dict[str, Any]:
        kms_key = str(cfg.get("kms_key_arn") or "")
        if kms_key:
            return {
                "type": "CUSTOMER_MANAGED_KMS_KEY",
                "kmsKeyIdentifier": kms_key,
            }
        return {"type": "AWS_OWNED_KMS_KEY"}

    def _get_keyspace(self, keyspace_name: str) -> dict[str, Any] | None:
        try:
            return dict(self._keyspaces.get_keyspace(keyspaceName=keyspace_name))
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _get_table(self, keyspace_name: str, table_name: str) -> dict[str, Any] | None:
        try:
            return dict(
                self._keyspaces.get_table(
                    keyspaceName=keyspace_name,
                    tableName=table_name,
                ),
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _coordinates(self, spec: ProvisionSpec) -> tuple[str, str]:
        keyspace_name = _identifier(
            self._config.name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        )
        table_name = _identifier(spec.service_handle_hint or "data")
        return keyspace_name, table_name

    @staticmethod
    def _handle(keyspace_name: str, table_name: str) -> str:
        return handle_for(kind=KIND, resource_id=f"{keyspace_name}/{table_name}")

    @staticmethod
    def _parse_handle(handle: str) -> tuple[str, str]:
        _, resource_id = parse_handle(handle)
        parts = resource_id.split("/", 1)
        if len(parts) != 2 or not all(parts):
            raise ManagedServiceError(f"invalid Amazon Keyspaces handle {handle!r}")
        return parts[0], parts[1]

    def _table_arn(self, keyspace_name: str, table_name: str) -> str:
        return (
            f"arn:aws:cassandra:{self._config.region}:{self._config.account_id}:"
            f"/keyspace/{keyspace_name}/table/{table_name}"
        )

    def _validate_config(self, cfg: dict[str, Any], *, update: bool = False) -> str:
        mode = cfg.get("throughput_mode", self._config.throughput_mode_default)
        if mode not in {"PAY_PER_REQUEST", "PROVISIONED"}:
            return "throughput_mode must be PAY_PER_REQUEST or PROVISIONED"
        for key in ("read_capacity", "write_capacity"):
            if key in cfg:
                value = cfg[key]
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    return f"{key} must be a positive integer"
        for key in ("point_in_time_recovery", "ttl_enabled", "client_side_timestamps"):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        if update and cfg.get("ttl_enabled") is False:
            return "Amazon Keyspaces TTL can be enabled but not disabled after table creation"
        if update and cfg.get("client_side_timestamps") is False:
            return "Amazon Keyspaces client-side timestamps cannot be disabled after they are enabled"
        if "default_time_to_live" in cfg:
            value = cfg["default_time_to_live"]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return "default_time_to_live must be a non-negative integer"
        if not update:
            regions = cfg.get("replication_regions") or []
            if not isinstance(regions, list) or any(not isinstance(region, str) for region in regions):
                return "replication_regions must be a list of AWS region names"
            if regions and (len(set(regions)) < 2 or self._config.region not in regions):
                return f"multi-Region keyspaces require at least two unique regions including {self._config.region}"
        return ""


def _identifier(*parts: str) -> str:
    raw = "_".join(str(part).lower() for part in parts if part)
    clean = "".join(char if char.isalnum() or char == "_" else "_" for char in raw)
    while "__" in clean:
        clean = clean.replace("__", "_")
    clean = clean.strip("_")
    if not clean or not clean[0].isalpha():
        clean = f"a_{clean}"
    return clean[:48].rstrip("_")


def _keyspaces_tags(spec: ProvisionSpec) -> list[dict[str, str]]:
    return [{"key": row["Key"], "value": row["Value"]} for row in tags_for(spec)]


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    text = f"{code} {type(exc).__name__} {exc}".lower()
    return "resourcenotfound" in text or "not found" in text


def _retryable_cloud_error(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0) or 0)
    return (
        status >= 500
        or code.startswith("Throttl")
        or code
        in {
            "ConflictException",
            "InternalServerException",
            "ServiceUnavailableException",
        }
    )
