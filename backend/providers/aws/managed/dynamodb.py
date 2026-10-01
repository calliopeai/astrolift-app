"""DynamoDB managed-service driver (#371).

Implements ``ManagedServiceDriver`` for the canonical AWS managed
key-value store. Default billing mode is ``PAY_PER_REQUEST`` to
keep the operator-facing cost model legible (no provisioned IOPS
forecasting); operators bump to ``PROVISIONED`` via spec.config
when the workload's traffic shape warrants it.

Four-corner deprovision matrix:

  delete_data=False, force_destroy=False (default):
    final on-demand backup taken via ``create_backup``;
    ``DeletionProtectionEnabled`` is respected. Refuses cleanly
    when protection is on so the operator must pass
    ``force_destroy=True``.

  delete_data=True, force_destroy=False:
    skip the final backup; respect protection.

  delete_data=False, force_destroy=True:
    take the final backup; flip ``DeletionProtectionEnabled``
    off via ``update_table`` before delete.

  delete_data=True, force_destroy=True:
    skip backup, flip protection off, delete. The platform's
    --atomic teardown path.

Binding envelope follows the IRSA pattern proven by object_store
(#1011): the workload assumes an IRSA-backed role that the platform
folds ``dynamodb:*Item`` + ``dynamodb:Query`` permissions into,
scoped at the table ARN. We bind ``DYNAMODB_TABLE_NAME`` /
``DYNAMODB_REGION`` / ``DYNAMODB_ENDPOINT`` as literals only — no
static access-key refs, since the bindings-Secret render hard-fails
on any secret_ref the backend can't resolve and the SDK resolves
credentials from the projected service-account token.
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
    LiveOwnershipError,
    ManagedServiceError,
    adoption_refusal,
    assert_resource_arn,
    handle_for,
    live_ownership_refusal,
    parse_handle,
    tags_for,
)
from aws.session import aws_client

KIND = "kv_store"


# astrolift's small/medium/large/xlarge maps onto on-demand by default
# (DynamoDB scales automatically), so the size axis only matters for
# the optional PROVISIONED billing mode. The numbers below are the
# starting read/write capacity per tier; operators can override via
# spec.config.read_capacity / write_capacity.
_SIZE_TO_PROVISIONED_CAPACITY = {
    "small": (5, 5),
    "medium": (10, 10),
    "large": (50, 50),
    "xlarge": (200, 200),
}


@dataclass(frozen=True)
class DynamoDBConfig(CredentialedConfig):
    """Driver-instance config bound from the cluster's plugin config."""

    region: str
    table_name_prefix: str = "astrolift"
    """Prefix on every managed table. Lets operators filter in the
    AWS console + apply tag-based budgets."""

    billing_mode_default: str = "PAY_PER_REQUEST"
    """``PAY_PER_REQUEST`` (default, on-demand) or ``PROVISIONED``.
    On-demand is the lowest-friction default; operators with a stable
    read/write shape can pin PROVISIONED for cost predictability."""

    deletion_protection_default: bool = True
    """Default for new tables. Operators with ``force_destroy=True``
    bypass on delete."""

    point_in_time_recovery_default: bool = True
    """PITR keeps a continuous 35-day backup. Default on so the
    platform's restore story is always live."""


class DynamoDBDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: DynamoDBConfig,
        ddb_client: Any | None = None,
    ) -> None:
        self._config = config
        if ddb_client is not None:
            self._ddb = ddb_client
        else:
            self._ddb = aws_client("dynamodb", region=config.region, credential=config.credential)

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="kv_store_dynamodb",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        table_name = self._table_name_for(spec=spec)
        cfg = spec.config or {}

        # Probe existing -- provision is idempotent.
        existing = self._describe(table_name)
        if existing is not None:
            refusal = adoption_refusal(self._existing_tags(existing), spec, resource=f"dynamodb table {table_name}")
            if refusal is not None:
                return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
            return ProvisionResult(
                ok=True,
                handle=handle_for(kind=KIND, resource_id=table_name),
                message=(f"dynamodb table {table_name} already exists (status={existing.get('TableStatus')})"),
            )

        billing_mode = cfg.get(
            "billing_mode",
            self._config.billing_mode_default,
        )
        deletion_protection = bool(
            cfg.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        pitr = bool(
            cfg.get(
                "point_in_time_recovery",
                self._config.point_in_time_recovery_default,
            ),
        )

        # Default key schema: HASH partition key on ``pk`` (string).
        # Operators with a composite key model override via
        # spec.config.key_schema + attribute_definitions.
        attribute_definitions = list(
            cfg.get(
                "attribute_definitions",
                [{"AttributeName": "pk", "AttributeType": "S"}],
            )
        )
        key_schema = list(
            cfg.get(
                "key_schema",
                [{"AttributeName": "pk", "KeyType": "HASH"}],
            )
        )

        create_kwargs: dict[str, Any] = {
            "TableName": table_name,
            "AttributeDefinitions": attribute_definitions,
            "KeySchema": key_schema,
            "BillingMode": billing_mode,
            "DeletionProtectionEnabled": deletion_protection,
            "Tags": tags_for(spec),
        }
        if billing_mode == "PROVISIONED":
            read_cap, write_cap = _SIZE_TO_PROVISIONED_CAPACITY.get(
                spec.size,
                (5, 5),
            )
            create_kwargs["ProvisionedThroughput"] = {
                "ReadCapacityUnits": int(
                    cfg.get("read_capacity", read_cap),
                ),
                "WriteCapacityUnits": int(
                    cfg.get("write_capacity", write_cap),
                ),
            }
        if cfg.get("global_secondary_indexes"):
            create_kwargs["GlobalSecondaryIndexes"] = list(
                cfg["global_secondary_indexes"],
            )
        if cfg.get("local_secondary_indexes"):
            create_kwargs["LocalSecondaryIndexes"] = list(
                cfg["local_secondary_indexes"],
            )
        if cfg.get("stream_enabled"):
            create_kwargs["StreamSpecification"] = {
                "StreamEnabled": True,
                "StreamViewType": cfg.get(
                    "stream_view_type",
                    "NEW_AND_OLD_IMAGES",
                ),
            }
        if cfg.get("sse_kms_key_id"):
            create_kwargs["SSESpecification"] = {
                "Enabled": True,
                "SSEType": "KMS",
                "KMSMasterKeyId": cfg["sse_kms_key_id"],
            }

        try:
            self._ddb.create_table(**create_kwargs)
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_table: {exc}",
                errors=[str(exc)],
            )

        # PITR is set out-of-band post-create -- AWS doesn't accept
        # it on create_table. Best-effort: surface failures via the
        # message but don't block.
        if pitr:
            with contextlib.suppress(Exception):
                self._ddb.update_continuous_backups(
                    TableName=table_name,
                    PointInTimeRecoverySpecification={
                        "PointInTimeRecoveryEnabled": True,
                    },
                )

        # TTL is similarly post-create; only emit the call when the
        # operator explicitly asked for it.
        ttl_attr = cfg.get("ttl_attribute")
        if ttl_attr:
            with contextlib.suppress(Exception):
                self._ddb.update_time_to_live(
                    TableName=table_name,
                    TimeToLiveSpecification={
                        "Enabled": True,
                        "AttributeName": ttl_attr,
                    },
                )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=table_name),
            message=f"dynamodb table {table_name} provisioning",
        )

    @driver_op(cloud="aws", driver="kv_store_dynamodb")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        table_name = self._recorded_table_name(spec.handle)
        try:
            existing = self._live_table(table_name, spec.managed_service_id)
        except ManagedServiceError as exc:
            return UpdateResult(
                False, spec.handle, str(exc), [getattr(exc, "code", "ownership_unknown")], retryable=False
            )
        if existing is None:
            return UpdateResult(
                False, spec.handle, "DynamoDB table does not exist", ["resource_missing"], retryable=False
            )
        cfg = spec.config or {}

        update_kwargs: dict[str, Any] = {"TableName": table_name}
        modified = False

        if "billing_mode" in cfg:
            update_kwargs["BillingMode"] = cfg["billing_mode"]
            modified = True

        if (
            spec.size
            and cfg.get(
                "billing_mode",
                self._config.billing_mode_default,
            )
            == "PROVISIONED"
        ):
            read_cap, write_cap = _SIZE_TO_PROVISIONED_CAPACITY.get(
                spec.size,
                (5, 5),
            )
            update_kwargs["ProvisionedThroughput"] = {
                "ReadCapacityUnits": int(
                    cfg.get("read_capacity", read_cap),
                ),
                "WriteCapacityUnits": int(
                    cfg.get("write_capacity", write_cap),
                ),
            }
            modified = True
        elif "read_capacity" in cfg or "write_capacity" in cfg:
            current = existing.get("ProvisionedThroughput") or {}
            if any(
                key not in cfg and not isinstance(current.get(observed), int)
                for key, observed in (("read_capacity", "ReadCapacityUnits"), ("write_capacity", "WriteCapacityUnits"))
            ):
                return UpdateResult(
                    False,
                    spec.handle,
                    "DynamoDB current throughput is unavailable",
                    ["throughput_unknown"],
                    retryable=False,
                )
            update_kwargs["ProvisionedThroughput"] = {
                "ReadCapacityUnits": int(
                    cfg.get(
                        "read_capacity",
                        current.get("ReadCapacityUnits"),
                    ),
                ),
                "WriteCapacityUnits": int(
                    cfg.get(
                        "write_capacity",
                        current.get("WriteCapacityUnits"),
                    ),
                ),
            }
            modified = True

        if cfg.get("stream_enabled") is not None:
            update_kwargs["StreamSpecification"] = {
                "StreamEnabled": bool(cfg["stream_enabled"]),
            }
            if cfg.get("stream_enabled"):
                update_kwargs["StreamSpecification"]["StreamViewType"] = cfg.get(
                    "stream_view_type", "NEW_AND_OLD_IMAGES"
                )
            modified = True

        if "deletion_protection" in cfg:
            update_kwargs["DeletionProtectionEnabled"] = bool(
                cfg["deletion_protection"],
            )
            modified = True

        if not modified:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        try:
            self._ddb.update_table(**update_kwargs)
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
            message=f"dynamodb table {table_name} update queued",
        )

    @driver_op(
        cloud="aws",
        driver="kv_store_dynamodb",
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
        table_name = self._recorded_table_name(spec.handle)

        try:
            existing = self._live_table(table_name, spec.managed_service_id)
        except ManagedServiceError as exc:
            return DeprovisionResult(
                False, spec.handle, str(exc), [getattr(exc, "code", "ownership_unknown")], retryable=False
            )
        if existing is None:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"dynamodb table {table_name} already gone",
            )

        # DeletionProtectionEnabled bites the same way RDS's flag does:
        # delete_table refuses while the flag is set. force_destroy
        # must flip it off via update_table first.
        protected = bool(existing.get("DeletionProtectionEnabled"))
        if protected and force_destroy:
            try:
                self._ddb.update_table(
                    TableName=table_name,
                    DeletionProtectionEnabled=False,
                )
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=(f"failed to clear DeletionProtectionEnabled: {exc}"),
                    errors=[str(exc)],
                )
        elif protected:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"dynamodb table {table_name} has DeletionProtectionEnabled -- pass force_destroy=True to bypass"
                ),
                errors=["deletion_protection_enabled"],
            )

        backup_taken = False
        if not delete_data:
            try:
                self._ddb.create_backup(
                    TableName=table_name,
                    BackupName=_final_backup_name(table_name=table_name),
                )
                backup_taken = True
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"create_backup: {exc}",
                    errors=[str(exc)],
                )

        try:
            self._ddb.delete_table(TableName=table_name)
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_table: {exc}",
                errors=[str(exc)],
            )

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"dynamodb table {table_name} delete queued "
                f"(backup={'taken' if backup_taken else 'skipped'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="aws", driver="kv_store_dynamodb")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        table_name = self._recorded_table_name(handle.handle)
        try:
            existing = self._live_table(table_name, handle.managed_service_id)
        except ManagedServiceError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"dynamodb table {table_name} not found",
            )
        ddb_state = existing.get("TableStatus", "UNKNOWN")
        return ServiceStatus(
            handle=handle.handle,
            state=_DDB_STATE_TO_PROTOCOL.get(ddb_state, "updating"),
            message=f"dynamodb reports {ddb_state}",
        )

    @driver_op(cloud="aws", driver="kv_store_dynamodb")
    def binding(self, handle: ServiceHandle) -> Binding:
        table_name = self._recorded_table_name(handle.handle)
        existing = self._live_table(table_name, handle.managed_service_id)
        if existing is None:
            raise ManagedServiceError("binding requested for missing DynamoDB table")
        table_arn = existing["TableArn"]

        # IRSA pattern (mirrors object_store #1011): the workload assumes
        # an IRSA-backed role we fold the iam_grants below into. We bind
        # only the table coordinates as literals — no static access-key
        # refs. Emitting empty secret refs here would be a deploy-time
        # trap: the bindings-Secret render hard-fails on any secret_ref
        # the backend can't resolve, and nothing mints per-table keys for
        # an IRSA workload. SDK clients (boto3 etc.) pick up credentials
        # from the projected service-account token automatically.
        return Binding(
            env_vars={
                "DYNAMODB_TABLE_NAME": ValueRef(literal=table_name),
                "DYNAMODB_TABLE_ARN": ValueRef(literal=table_arn),
                "DYNAMODB_REGION": ValueRef(literal=self._config.region),
                "DYNAMODB_ENDPOINT": ValueRef(
                    literal=(f"https://dynamodb.{self._config.region}.amazonaws.com"),
                ),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=[
                Grant(
                    resource=table_arn,
                    actions=[
                        "dynamodb:GetItem",
                        "dynamodb:PutItem",
                        "dynamodb:UpdateItem",
                        "dynamodb:DeleteItem",
                        "dynamodb:BatchGetItem",
                        "dynamodb:BatchWriteItem",
                        "dynamodb:Query",
                        "dynamodb:Scan",
                        "dynamodb:DescribeTable",
                    ],
                ),
            ],
            notes=(
                "IRSA role-binding path: the platform attaches the "
                "dynamodb item/query actions above to the workload's "
                "service-account role; the SDK resolves credentials from "
                "the projected token. No static access keys are injected."
            ),
        )

    @driver_op(cloud="aws", driver="kv_store_dynamodb")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        table_name = self._recorded_table_name(handle.handle)
        existing = self._live_table(table_name, handle.managed_service_id)
        if existing is None:
            raise ManagedServiceError("snapshot requested for missing DynamoDB table")
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_name = f"{table_name}-snap-{stamp}"
        try:
            resp = self._ddb.create_backup(
                TableName=table_name,
                BackupName=backup_name,
            )
        except Exception as exc:
            raise ManagedServiceError(
                f"create_backup: {exc}",
            ) from exc
        details = resp.get("BackupDetails") or {}
        snapshot_id = details.get("BackupArn")
        prefix = existing["TableArn"] + "/backup/"
        if not isinstance(snapshot_id, str) or not snapshot_id.startswith(prefix) or not snapshot_id[len(prefix) :]:
            raise ManagedServiceError("DynamoDB backup identity did not match the recorded source")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snapshot_id,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="kv_store_dynamodb")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        target_table = self._table_name_for(spec=target)
        try:
            self._ddb.restore_table_from_backup(
                TargetTableName=target_table,
                BackupArn=snapshot.snapshot_id,
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"restore_table_from_backup: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=target_table),
            message=(f"dynamodb restore from {snapshot.snapshot_id} queued into {target_table}"),
        )

    @driver_op(cloud="aws", driver="kv_store_dynamodb", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "billing_mode": {
                    "type": "string",
                    "enum": ["PAY_PER_REQUEST", "PROVISIONED"],
                },
                "read_capacity": {"type": "integer", "minimum": 1},
                "write_capacity": {"type": "integer", "minimum": 1},
                "deletion_protection": {"type": "boolean"},
                "point_in_time_recovery": {"type": "boolean"},
                "stream_enabled": {"type": "boolean"},
                "stream_view_type": {
                    "type": "string",
                    "enum": [
                        "KEYS_ONLY",
                        "NEW_IMAGE",
                        "OLD_IMAGE",
                        "NEW_AND_OLD_IMAGES",
                    ],
                },
                "ttl_attribute": {"type": "string"},
                "sse_kms_key_id": {"type": "string"},
                "key_schema": {"type": "array"},
                "attribute_definitions": {"type": "array"},
                "global_secondary_indexes": {"type": "array"},
                "local_secondary_indexes": {"type": "array"},
            },
        }

    @driver_op(cloud="aws", driver="kv_store_dynamodb", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "DYNAMODB_TABLE_NAME": "Table name",
                "DYNAMODB_TABLE_ARN": "Full table ARN",
                "DYNAMODB_REGION": "Table's region",
                "DYNAMODB_ENDPOINT": "DynamoDB HTTPS endpoint URL",
                "AWS_REGION": "Same region as DYNAMODB_REGION",
            },
        )

    # ---- internals ----------------------------------------------------

    @staticmethod
    def _recorded_table_name(handle: str) -> str:
        kind, name = parse_handle(handle)
        if (
            kind != KIND
            or not 3 <= len(name) <= 255
            or any(not c.isascii() or not (c.isalnum() or c in "-_.") for c in name)
        ):
            raise LiveOwnershipError("recorded DynamoDB target is invalid", code="ownership_refused")
        return name

    def _live_table(self, table_name: str, managed_service_id: str) -> dict[str, Any] | None:
        existing = self._describe(table_name)
        if existing is None:
            return None
        if existing.get("TableName") != table_name:
            raise LiveOwnershipError("live DynamoDB table does not match the recorded target", code="ownership_refused")
        table_arn = existing.get("TableArn")
        try:
            assert_resource_arn(
                table_arn, service="dynamodb", region=self._config.region, resource=f"table/{table_name}"
            )
        except ManagedServiceError:
            raise LiveOwnershipError(
                "live DynamoDB resource does not match the recorded target", code="ownership_refused"
            ) from None
        tags = self._live_tags(table_arn)
        refusal = live_ownership_refusal(tags, managed_service_id=managed_service_id, resource="DynamoDB table")
        if refusal:
            raise LiveOwnershipError(refusal, code="ownership_refused")
        return existing

    def _live_tags(self, table_arn: str) -> list[dict[str, str]]:
        """Complete bounded tags before granting access, with no partial proof."""
        rows = []
        token = ""
        seen = set()
        for _ in range(10):
            kwargs = {"ResourceArn": table_arn}
            if token:
                kwargs["NextToken"] = token
            try:
                response = self._ddb.list_tags_of_resource(**kwargs)
            except Exception:
                raise LiveOwnershipError("DynamoDB live ownership tags could not be verified") from None
            if not isinstance(response, dict):
                raise LiveOwnershipError("DynamoDB ownership tag response is unknown")
            page = response.get("Tags")
            if not isinstance(page, list) or len(rows) + len(page) > 1000:
                raise LiveOwnershipError("DynamoDB ownership tag inventory is unknown")
            rows.extend(page)
            token = response.get("NextToken", "")
            if not isinstance(token, str) or token in seen:
                raise LiveOwnershipError("DynamoDB ownership tag pagination is unknown")
            if not token:
                return rows
            seen.add(token)
        raise LiveOwnershipError("DynamoDB ownership tag inventory limit exceeded")

    def _existing_tags(self, existing: dict[str, Any]) -> list[dict[str, str]]:
        """Tags of a resource found under this service's name; unreadable counts as untagged (#1961)."""
        try:
            return list(
                self._ddb.list_tags_of_resource(ResourceArn=str(existing.get("TableArn", ""))).get("Tags", []) or []
            )
        except Exception:  # ownership unverifiable, so not adopted
            return []

    def _describe(self, table_name: str) -> dict[str, Any] | None:
        try:
            resp = self._ddb.describe_table(TableName=table_name)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code == "ResourceNotFoundException" or type(exc).__name__ == "ResourceNotFoundException":
                return None
            raise LiveOwnershipError("DynamoDB live table lookup could not be verified") from None
        if not isinstance(resp, dict):
            raise LiveOwnershipError("DynamoDB live table identity is unavailable")
        table = resp.get("Table")
        if not isinstance(table, dict) or not table:
            raise LiveOwnershipError("DynamoDB live table identity is unavailable")
        return table

    def _table_name_for(self, *, spec: ProvisionSpec) -> str:
        # DynamoDB table names: 3-255 chars, [a-zA-Z0-9_.-].
        parts = [
            self._config.table_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "kv",
        ]
        raw = "-".join(p for p in parts if p)
        clean = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-.")[:255]


# ----- module-level helpers --------------------------------------------


def _final_backup_name(*, table_name: str) -> str:
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    # DynamoDB backup names: 3-255 chars, [a-zA-Z0-9_.-].
    return f"{table_name}-final-{stamp}"[:255]


_DDB_STATE_TO_PROTOCOL = {
    "ACTIVE": "available",
    "CREATING": "provisioning",
    "UPDATING": "updating",
    "DELETING": "deprovisioning",
    "ARCHIVING": "updating",
    "ARCHIVED": "available",
    "INACCESSIBLE_ENCRYPTION_CREDENTIALS": "error",
}
