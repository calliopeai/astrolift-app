"""Amazon Kinesis Data Streams managed-service driver."""

from __future__ import annotations

import json
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
from aws.managed._base import ManagedServiceError, adoption_refusal, handle_for, parse_handle, tags_for
from aws.session import aws_client

KIND = "stream"
_MODES = {"ON_DEMAND", "PROVISIONED"}
_METRICS = {
    "ALL",
    "IncomingBytes",
    "IncomingRecords",
    "IteratorAgeMilliseconds",
    "OutgoingBytes",
    "OutgoingRecords",
    "ReadProvisionedThroughputExceeded",
    "WriteProvisionedThroughputExceeded",
}
_SIZE_SHARDS = {"small": 1, "medium": 2, "large": 4, "xlarge": 8}


@dataclass(frozen=True)
class KinesisConfig(CredentialedConfig):
    region: str
    account_id: str
    stream_name_prefix: str = "astrolift"
    kms_key_id: str = ""
    stream_mode_default: str = "ON_DEMAND"
    retention_hours_default: int = 24
    deletion_protection_default: bool = True


class KinesisDriver(ManagedServiceDriver):
    def __init__(self, *, config: KinesisConfig, client: Any | None = None) -> None:
        self._config = config
        if client is None:
            client = aws_client("kinesis", region=config.region, credential=config.credential)
        self._kinesis = client

    @driver_op(
        cloud="aws",
        driver="stream_kinesis",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg, size=spec.size)
        if error:
            return ProvisionResult(False, "", error, ["invalid_kinesis_config"])
        if not self._config.account_id:
            return ProvisionResult(
                False,
                "",
                "Kinesis requires the AWS account_id to construct a stable stream ARN",
                ["missing_account_id"],
            )
        name = self._stream_name(spec)
        arn = self._stream_arn(name)
        request = self._create_request(name, spec)
        created = False
        try:
            self._kinesis.create_stream(**request)
            created = True
        except Exception as exc:
            if not _already_exists(exc):
                return ProvisionResult(False, "", f"create Kinesis stream: {exc}", [str(exc)])
        handle = handle_for(kind=KIND, resource_id=arn)
        try:
            self._await_active(name)
            summary = self._summary(arn)
            arn = str(summary.get("StreamARN") or arn)
            if not created and not self._is_own_stream(arn, spec):
                raise ManagedServiceError(
                    f"stream {name} already exists outside this resource declaration",
                )
            self._reconcile(
                arn,
                summary,
                cfg,
                size=spec.size,
                spec=spec,
                newly_created=created,
            )
            self._await_active(name)
        except Exception as exc:
            action = "configure new" if created else "reconcile existing"
            return ProvisionResult(False, handle, f"{action} Kinesis stream: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=arn),
            f"Kinesis stream {name} available",
            ready=True,
        )

    @driver_op(cloud="aws", driver="stream_kinesis")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, arn = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg, size=spec.size or "", partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_kinesis_config"])
        try:
            summary = self._summary(arn)
            self._reconcile(arn, summary, cfg, size=spec.size or "")
            self._await_active(_stream_name_from_arn(arn))
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, "Kinesis stream not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update Kinesis stream: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Kinesis stream {_stream_name_from_arn(arn)} reconciled")

    @driver_op(
        cloud="aws",
        driver="stream_kinesis",
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
        _, arn = parse_handle(spec.handle)
        name = _stream_name_from_arn(arn)
        try:
            self._summary(arn)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"Kinesis stream {name} already gone")
            return _deprovision_error(spec.handle, "describe Kinesis stream", exc)
        protected = bool(
            spec.config.get("deletion_protection", self._config.deletion_protection_default),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Kinesis stream {name} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "Kinesis has no snapshot API; deleting the stream destroys retained records, so set delete_data=true",
                ["retained_stream_data_requires_delete_data"],
                retryable=False,
            )
        try:
            consumers = self._consumers(arn)
            external = [
                consumer
                for consumer in consumers
                if not self._is_managed_resource(str(consumer.get("ConsumerARN") or ""))
            ]
            if external and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Kinesis stream has externally managed enhanced-fan-out consumers; force_destroy is required",
                    ["external_consumers_present"],
                    retryable=False,
                )
            for consumer in consumers:
                if force_destroy or self._is_managed_resource(str(consumer.get("ConsumerARN") or "")):
                    self._kinesis.deregister_stream_consumer(
                        ConsumerARN=str(consumer["ConsumerARN"]),
                    )
            self._kinesis.delete_stream(
                StreamARN=arn,
                EnforceConsumerDeletion=bool(consumers) or force_destroy,
            )
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Kinesis stream", exc)
        return DeprovisionResult(True, spec.handle, f"Kinesis stream {name} deletion queued")

    @driver_op(cloud="aws", driver="stream_kinesis")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, arn = parse_handle(handle.handle)
        name = _stream_name_from_arn(arn)
        try:
            summary = self._summary(arn)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", f"Kinesis stream {name} does not exist")
            return ServiceStatus(handle.handle, "error", f"describe Kinesis stream: {exc}")
        provider_state = str(summary.get("StreamStatus") or "UNKNOWN")
        state = {
            "ACTIVE": "available",
            "CREATING": "provisioning",
            "UPDATING": "updating",
            "DELETING": "deprovisioning",
        }.get(provider_state, "error")
        mode = str((summary.get("StreamModeDetails") or {}).get("StreamMode") or "PROVISIONED")
        shards = int(summary.get("OpenShardCount", 0) or 0)
        return ServiceStatus(
            handle.handle,
            state,
            f"Kinesis reports {provider_state} ({mode}, {shards} open shard(s))",
        )

    @driver_op(cloud="aws", driver="stream_kinesis")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, arn = parse_handle(handle.handle)
        summary = self._summary(arn)
        name = str(summary.get("StreamName") or _stream_name_from_arn(arn))
        resolved_arn = str(summary.get("StreamARN") or arn)
        cfg = config or {}
        access_mode = str(cfg.get("access_mode") or "produce")
        actions = ["kinesis:DescribeStreamSummary"]
        if access_mode in {"produce", "read_write", "manage"}:
            actions.extend(["kinesis:PutRecord", "kinesis:PutRecords"])
        if access_mode in {"consume", "read_write", "manage"}:
            actions.extend(
                [
                    "kinesis:GetRecords",
                    "kinesis:GetShardIterator",
                    "kinesis:ListShards",
                    "kinesis:ListStreamConsumers",
                ],
            )
        if access_mode == "manage":
            actions.extend(
                [
                    "kinesis:AddTagsToStream",
                    "kinesis:DecreaseStreamRetentionPeriod",
                    "kinesis:DeleteResourcePolicy",
                    "kinesis:DisableEnhancedMonitoring",
                    "kinesis:EnableEnhancedMonitoring",
                    "kinesis:IncreaseStreamRetentionPeriod",
                    "kinesis:PutResourcePolicy",
                    "kinesis:RegisterStreamConsumer",
                    "kinesis:RemoveTagsFromStream",
                    "kinesis:StartStreamEncryption",
                    "kinesis:StopStreamEncryption",
                    "kinesis:UpdateMaxRecordSize",
                    "kinesis:UpdateShardCount",
                    "kinesis:UpdateStreamMode",
                    "kinesis:UpdateStreamWarmThroughput",
                ],
            )
        grants = [Grant(resource=resolved_arn, actions=sorted(set(actions)))]
        if access_mode in {"consume", "read_write", "manage"}:
            consumer_arns = {
                str(consumer.get("ConsumerARN") or "")
                for consumer in self._consumers(resolved_arn)
                if consumer.get("ConsumerARN")
            }
            if consumer_arns:
                grants.extend(
                    Grant(
                        resource=consumer_arn,
                        actions=(
                            [
                                "kinesis:DeregisterStreamConsumer",
                                "kinesis:DescribeStreamConsumer",
                                "kinesis:SubscribeToShard",
                            ]
                            if access_mode == "manage"
                            else ["kinesis:DescribeStreamConsumer", "kinesis:SubscribeToShard"]
                        ),
                    )
                    for consumer_arn in sorted(consumer_arns)
                )
            if access_mode == "manage":
                grants.append(
                    Grant(
                        resource=f"{resolved_arn}/consumer/*",
                        actions=[
                            "kinesis:DeregisterStreamConsumer",
                            "kinesis:DescribeStreamConsumer",
                            "kinesis:SubscribeToShard",
                        ],
                    ),
                )
        key_id = str(summary.get("KeyId") or "")
        if key_id and not key_id.startswith("alias/aws/"):
            kms_actions = []
            if access_mode in {"produce", "read_write", "manage"}:
                kms_actions.append("kms:GenerateDataKey")
            if access_mode in {"consume", "read_write", "manage"}:
                kms_actions.append("kms:Decrypt")
            if kms_actions:
                kms_actions.append("kms:DescribeKey")
                grants.append(Grant(resource=key_id, actions=sorted(set(kms_actions))))
        endpoint = f"https://kinesis.{self._config.region}.amazonaws.com"
        return Binding(
            env_vars={
                "STREAM_NAME": ValueRef(literal=name),
                "STREAM_ARN": ValueRef(literal=resolved_arn),
                "STREAM_ENDPOINT": ValueRef(literal=endpoint),
                "STREAM_REGION": ValueRef(literal=self._config.region),
                "AWS_REGION": ValueRef(literal=self._config.region),
                "KINESIS_STREAM_NAME": ValueRef(literal=name),
                "KINESIS_STREAM_ARN": ValueRef(literal=resolved_arn),
            },
            iam_grants=grants,
            notes=f"Kinesis {access_mode} access",
        )

    @driver_op(cloud="aws", driver="stream_kinesis")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "Kinesis Data Streams has no snapshot API; use a Firehose/S3 archival sink",
        )

    @driver_op(cloud="aws", driver="stream_kinesis")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise ManagedServiceError("Kinesis Data Streams cannot be restored from a snapshot")

    @driver_op(cloud="aws", driver="stream_kinesis", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "access_mode": {
                    "type": "string",
                    "enum": ["produce", "consume", "read_write", "manage"],
                    "default": "produce",
                },
                "stream_mode": {"type": "string", "enum": sorted(_MODES)},
                "shard_count": {"type": "integer", "minimum": 1},
                "retention_hours": {"type": "integer", "minimum": 24, "maximum": 8760},
                "kms_key_id": {"type": "string"},
                "encryption_enabled": {"type": "boolean", "default": True},
                "enhanced_monitoring": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(_METRICS)},
                    "uniqueItems": True,
                },
                "warm_throughput_mibps": {"type": "integer", "minimum": 0},
                "max_record_size_kib": {"type": "integer", "minimum": 1024, "maximum": 10240},
                "resource_policy": {"type": ["object", "string", "null"]},
                "allow_retention_decrease": {"type": "boolean", "default": False},
                "consumers": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                    "uniqueItems": True,
                },
                "prune_consumers": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="aws", driver="stream_kinesis", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "STREAM_NAME": "Portable stream name",
                "STREAM_ARN": "Portable stream resource ID",
                "STREAM_ENDPOINT": "Portable service endpoint",
                "STREAM_REGION": "Portable stream region",
                "AWS_REGION": "AWS region",
                "KINESIS_STREAM_NAME": "AWS Kinesis stream name",
                "KINESIS_STREAM_ARN": "AWS Kinesis stream ARN",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "allow_retention_decrease",
            "consumers",
            "deletion_protection",
            "encryption_enabled",
            "enhanced_monitoring",
            "kms_key_id",
            "max_record_size_kib",
            "prune_consumers",
            "resource_policy",
            "retention_hours",
            "shard_count",
            "stream_mode",
            "warm_throughput_mibps",
        ]

    def _reconcile(
        self,
        arn: str,
        summary: dict[str, Any],
        cfg: dict[str, Any],
        *,
        size: str,
        spec: ProvisionSpec | None = None,
        newly_created: bool = False,
    ) -> None:
        name = str(summary.get("StreamName") or _stream_name_from_arn(arn))
        if spec is not None:
            self._kinesis.add_tags_to_stream(
                StreamARN=arn,
                Tags={tag["Key"]: tag["Value"] for tag in tags_for(spec)},
            )
        desired_mode = str(cfg.get("stream_mode") or self._config.stream_mode_default)
        current_mode = str((summary.get("StreamModeDetails") or {}).get("StreamMode") or "PROVISIONED")
        mode_changed = "stream_mode" in cfg and desired_mode != current_mode
        if mode_changed:
            request: dict[str, Any] = {
                "StreamARN": arn,
                "StreamModeDetails": {"StreamMode": desired_mode},
            }
            if "warm_throughput_mibps" in cfg:
                request["WarmThroughputMiBps"] = int(cfg["warm_throughput_mibps"])
            self._kinesis.update_stream_mode(**request)
            self._await_active(name)
            summary = self._summary(arn)
            current_mode = desired_mode
        desired_shards = _desired_shards(cfg, size=size)
        if current_mode == "PROVISIONED" and desired_shards is not None:
            current_shards = int(summary.get("OpenShardCount", 0) or 0)
            if current_shards and desired_shards != current_shards:
                self._kinesis.update_shard_count(
                    StreamARN=arn,
                    TargetShardCount=desired_shards,
                    ScalingType="UNIFORM_SCALING",
                )
                self._await_active(name)
                summary = self._summary(arn)
        if "retention_hours" in cfg or spec is not None:
            desired_retention = int(cfg.get("retention_hours", self._config.retention_hours_default))
            current_retention = int(summary.get("RetentionPeriodHours", 24) or 24)
            if desired_retention > current_retention:
                self._kinesis.increase_stream_retention_period(
                    StreamARN=arn,
                    RetentionPeriodHours=desired_retention,
                )
            elif desired_retention < current_retention:
                if not cfg.get("allow_retention_decrease"):
                    raise ManagedServiceError(
                        "decreasing Kinesis retention can discard older records; set allow_retention_decrease=true",
                    )
                self._kinesis.decrease_stream_retention_period(
                    StreamARN=arn,
                    RetentionPeriodHours=desired_retention,
                )
            if desired_retention != current_retention:
                self._await_active(name)
                summary = self._summary(arn)
        if "warm_throughput_mibps" in cfg and not newly_created and not mode_changed:
            desired_warm = int(cfg["warm_throughput_mibps"])
            current_warm = (summary.get("WarmThroughput") or {}).get("TargetMiBps")
            if current_warm is None or int(current_warm) != desired_warm:
                self._kinesis.update_stream_warm_throughput(
                    StreamARN=arn,
                    WarmThroughputMiBps=desired_warm,
                )
                self._await_active(name)
                summary = self._summary(arn)
        if "max_record_size_kib" in cfg and not newly_created:
            current_max = int(summary.get("MaxRecordSizeInKiB", 1024) or 1024)
            desired_max = int(cfg["max_record_size_kib"])
            if desired_max != current_max:
                self._kinesis.update_max_record_size(
                    StreamARN=arn,
                    MaxRecordSizeInKiB=desired_max,
                )
                self._await_active(name)
                summary = self._summary(arn)
        if self._reconcile_encryption(arn, summary, cfg, creating=spec is not None):
            self._await_active(name)
            summary = self._summary(arn)
        self._reconcile_monitoring(arn, summary, cfg, name=name)
        self._reconcile_policy(arn, cfg)
        self._reconcile_consumers(arn, cfg, spec=spec)

    def _reconcile_encryption(
        self,
        arn: str,
        summary: dict[str, Any],
        cfg: dict[str, Any],
        *,
        creating: bool,
    ) -> bool:
        if not creating and "encryption_enabled" not in cfg and "kms_key_id" not in cfg:
            return False
        enabled = bool(cfg.get("encryption_enabled", True))
        key_id = str(cfg.get("kms_key_id") or self._config.kms_key_id or "alias/aws/kinesis")
        encrypted = str(summary.get("EncryptionType") or "NONE") == "KMS"
        current_key = str(summary.get("KeyId") or "")
        if enabled and (not encrypted or current_key != key_id):
            self._kinesis.start_stream_encryption(
                StreamARN=arn,
                EncryptionType="KMS",
                KeyId=key_id,
            )
            return True
        elif not enabled and encrypted:
            self._kinesis.stop_stream_encryption(
                StreamARN=arn,
                EncryptionType="KMS",
                KeyId=current_key or key_id,
            )
            return True
        return False

    def _reconcile_monitoring(
        self,
        arn: str,
        summary: dict[str, Any],
        cfg: dict[str, Any],
        *,
        name: str,
    ) -> None:
        if "enhanced_monitoring" not in cfg:
            return
        current: set[str] = set()
        for group in summary.get("EnhancedMonitoring") or []:
            current.update(str(metric) for metric in group.get("ShardLevelMetrics") or [])
        desired = {str(metric) for metric in cfg.get("enhanced_monitoring") or []}
        disable = sorted(current - desired)
        enable = sorted(desired - current)
        if disable:
            self._kinesis.disable_enhanced_monitoring(
                StreamARN=arn,
                ShardLevelMetrics=disable,
            )
            self._await_active(name)
        if enable:
            self._kinesis.enable_enhanced_monitoring(
                StreamARN=arn,
                ShardLevelMetrics=enable,
            )
            self._await_active(name)

    def _reconcile_policy(self, arn: str, cfg: dict[str, Any]) -> None:
        if "resource_policy" not in cfg:
            return
        policy = cfg["resource_policy"]
        if policy in (None, "", {}):
            try:
                self._kinesis.delete_resource_policy(ResourceARN=arn)
            except Exception as exc:
                if not _not_found(exc):
                    raise
            return
        self._kinesis.put_resource_policy(
            ResourceARN=arn,
            Policy=_json_document(policy, field="resource_policy"),
        )

    def _reconcile_consumers(
        self,
        arn: str,
        cfg: dict[str, Any],
        *,
        spec: ProvisionSpec | None,
    ) -> None:
        if "consumers" not in cfg:
            return
        current = {str(row.get("ConsumerName") or ""): row for row in self._consumers(arn)}
        desired = {str(name) for name in cfg.get("consumers") or []}
        tags = tags_for(spec) if spec is not None else []
        for name in sorted(desired - set(current)):
            request: dict[str, Any] = {"StreamARN": arn, "ConsumerName": name}
            if tags:
                request["Tags"] = {tag["Key"]: tag["Value"] for tag in tags}
            self._kinesis.register_stream_consumer(**request)
        if cfg.get("prune_consumers"):
            for name in sorted(set(current) - desired):
                consumer_arn = str(current[name].get("ConsumerARN") or "")
                if consumer_arn and self._is_managed_resource(consumer_arn):
                    self._kinesis.deregister_stream_consumer(ConsumerARN=consumer_arn)

    def _consumers(self, arn: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"StreamARN": arn}
            if token:
                request["NextToken"] = token
            response = self._kinesis.list_stream_consumers(**request)
            rows.extend(response.get("Consumers") or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return rows

    def _is_managed_resource(self, arn: str) -> bool:
        if not arn:
            return False
        tags = self._kinesis.list_tags_for_resource(ResourceARN=arn).get("Tags") or []
        return any(tag.get("Key") == "astrolift.io/managed-by" and tag.get("Value") == "platform" for tag in tags)

    def _is_own_stream(self, arn: str, spec: ProvisionSpec) -> bool:
        """Platform-made is not enough to adopt: it must be this service's (#1961)."""
        if not self._is_managed_resource(arn):
            return False
        tags = self._kinesis.list_tags_for_resource(ResourceARN=arn).get("Tags") or []
        return adoption_refusal(tags, spec, resource="Kinesis stream") is None

    def _summary(self, arn: str) -> dict[str, Any]:
        response = self._kinesis.describe_stream_summary(StreamARN=arn)
        return dict(response["StreamDescriptionSummary"])

    def _await_active(self, name: str) -> None:
        self._kinesis.get_waiter("stream_exists").wait(
            StreamName=name,
            WaiterConfig={"Delay": 5, "MaxAttempts": 60},
        )

    def _create_request(self, name: str, spec: ProvisionSpec) -> dict[str, Any]:
        cfg = spec.config or {}
        mode = str(cfg.get("stream_mode") or self._config.stream_mode_default)
        request: dict[str, Any] = {
            "StreamName": name,
            "StreamModeDetails": {"StreamMode": mode},
            "Tags": {tag["Key"]: tag["Value"] for tag in tags_for(spec)},
        }
        shards = _desired_shards(cfg, size=spec.size)
        if mode == "PROVISIONED":
            request["ShardCount"] = shards or 1
        if "warm_throughput_mibps" in cfg:
            request["WarmThroughputMiBps"] = int(cfg["warm_throughput_mibps"])
        if "max_record_size_kib" in cfg:
            request["MaxRecordSizeInKiB"] = int(cfg["max_record_size_kib"])
        return request

    def _validate_config(self, cfg: dict[str, Any], *, size: str, partial: bool = False) -> str:
        mode = str(cfg.get("stream_mode") or self._config.stream_mode_default)
        if mode not in _MODES:
            return "stream_mode must be ON_DEMAND or PROVISIONED"
        if cfg.get("access_mode", "produce") not in {"produce", "consume", "read_write", "manage"}:
            return "access_mode must be produce, consume, read_write, or manage"
        if "shard_count" in cfg:
            shards = cfg["shard_count"]
            if not isinstance(shards, int) or isinstance(shards, bool) or shards < 1:
                return "shard_count must be a positive integer"
            if mode != "PROVISIONED":
                return "shard_count is only valid for PROVISIONED streams"
        if size and size not in _SIZE_SHARDS and size != "custom":
            return f"unsupported stream size {size}"
        if mode == "PROVISIONED" and size == "custom" and "shard_count" not in cfg and not partial:
            return "custom PROVISIONED streams require shard_count"
        retention = cfg.get("retention_hours", self._config.retention_hours_default)
        if not isinstance(retention, int) or isinstance(retention, bool) or not 24 <= retention <= 8760:
            return "retention_hours must be an integer from 24 through 8760"
        for key, minimum, maximum in (
            ("warm_throughput_mibps", 0, None),
            ("max_record_size_kib", 1024, 10240),
        ):
            if key in cfg:
                value = cfg[key]
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < minimum
                    or (maximum is not None and value > maximum)
                ):
                    suffix = f" through {maximum}" if maximum is not None else " or greater"
                    return f"{key} must be an integer from {minimum}{suffix}"
        if "enhanced_monitoring" in cfg:
            metrics = cfg["enhanced_monitoring"]
            if not isinstance(metrics, list) or any(metric not in _METRICS for metric in metrics):
                return "enhanced_monitoring contains an unsupported shard-level metric"
            if "ALL" in metrics and len(metrics) > 1:
                return "enhanced_monitoring ALL cannot be combined with individual metrics"
        if "consumers" in cfg:
            consumers = cfg["consumers"]
            if not isinstance(consumers, list) or any(
                not isinstance(name, str) or not name or len(name) > 128 for name in consumers
            ):
                return "consumers must contain non-empty names no longer than 128 characters"
            if len(set(consumers)) != len(consumers):
                return "consumers cannot contain duplicate names"
        for key in (
            "allow_retention_decrease",
            "deletion_protection",
            "encryption_enabled",
            "prune_consumers",
        ):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        if cfg.get("encryption_enabled") is False and cfg.get("kms_key_id"):
            return "kms_key_id cannot be set when encryption_enabled is false"
        if "resource_policy" in cfg and cfg["resource_policy"] not in (None, "", {}):
            try:
                _json_document(cfg["resource_policy"], field="resource_policy")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return str(exc)
        return ""

    def _stream_name(self, spec: ProvisionSpec) -> str:
        return _name(
            "-".join(
                part
                for part in (
                    self._config.stream_name_prefix,
                    spec.organization_slug,
                    spec.app_slug,
                    spec.environment_name,
                    spec.service_handle_hint or "stream",
                )
                if part
            ),
        )

    def _stream_arn(self, name: str) -> str:
        if not self._config.account_id:
            raise ManagedServiceError("Kinesis requires the AWS account_id to construct a stable stream ARN")
        return f"arn:aws:kinesis:{self._config.region}:{self._config.account_id}:stream/{name}"


def _desired_shards(cfg: dict[str, Any], *, size: str) -> int | None:
    if "shard_count" in cfg:
        return int(cfg["shard_count"])
    return _SIZE_SHARDS.get(size)


def _stream_name_from_arn(arn: str) -> str:
    return arn.rsplit("/", 1)[-1]


def _name(value: str) -> str:
    clean = "".join(char if char.isalnum() or char in "-_." else "-" for char in value)
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip("-_.")
    if not clean:
        raise ManagedServiceError("Kinesis stream name cannot be empty")
    return clean[:128].rstrip("-_.")


def _json_document(value: Any, *, field: str) -> str:
    if isinstance(value, str):
        parsed = json.loads(value)
    elif isinstance(value, dict):
        parsed = value
    else:
        raise TypeError(f"{field} must be a JSON object or JSON object string")
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def _already_exists(exc: Exception) -> bool:
    code = str(((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code") or "")
    return code == "ResourceInUseException" or "already exists" in str(exc).lower()


def _not_found(exc: Exception) -> bool:
    code = str(((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code") or "")
    return code in {"ResourceNotFoundException", "ResourceNotFound"} or "not found" in str(exc).lower()


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0) or 0)
    retryable = (
        status >= 500
        or code.startswith("LimitExceeded")
        or code
        in {
            "ResourceInUseException",
            "InternalFailure",
        }
    )
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [str(exc)], retryable=retryable)
