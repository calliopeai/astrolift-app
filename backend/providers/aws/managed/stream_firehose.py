"""Amazon Data Firehose managed delivery-stream driver.

Firehose destination payloads are intentionally exposed as the exact AWS
request structures. This keeps every provider option available (including
new destinations) without flattening or silently discarding nested fields.
The driver validates those structures against the installed botocore model.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _sdk.cloud_credentials import CredentialedConfig
from aws.session import aws_client

if TYPE_CHECKING:
    from collections.abc import Callable

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
from _sdk.physical_naming import managed_service_identity, physical_name
from aws.managed._base import ManagedServiceError, handle_for, live_ownership_refusal, parse_handle, tags_for

KIND = "stream"
_SOURCE_TYPES = {
    "database": ("DatabaseAsSource", "DatabaseSourceConfiguration"),
    "direct_put": ("DirectPut", "DirectPutSourceConfiguration"),
    "kinesis": ("KinesisStreamAsSource", "KinesisStreamSourceConfiguration"),
    "msk": ("MSKAsSource", "MSKSourceConfiguration"),
}
_DESTINATIONS = {
    "elasticsearch": ("ElasticsearchDestinationConfiguration", "ElasticsearchDestinationUpdate"),
    "extended_s3": ("ExtendedS3DestinationConfiguration", "ExtendedS3DestinationUpdate"),
    "http_endpoint": ("HttpEndpointDestinationConfiguration", "HttpEndpointDestinationUpdate"),
    "iceberg": ("IcebergDestinationConfiguration", "IcebergDestinationUpdate"),
    "opensearch": (
        "AmazonopensearchserviceDestinationConfiguration",
        "AmazonopensearchserviceDestinationUpdate",
    ),
    "opensearch_serverless": (
        "AmazonOpenSearchServerlessDestinationConfiguration",
        "AmazonOpenSearchServerlessDestinationUpdate",
    ),
    "redshift": ("RedshiftDestinationConfiguration", "RedshiftDestinationUpdate"),
    "s3": ("S3DestinationConfiguration", "S3DestinationUpdate"),
    "snowflake": ("SnowflakeDestinationConfiguration", "SnowflakeDestinationUpdate"),
    "splunk": ("SplunkDestinationConfiguration", "SplunkDestinationUpdate"),
}
_DESTINATION_DESCRIPTIONS = {
    destination_type: create_field.replace("Configuration", "Description")
    for destination_type, (create_field, _update_field) in _DESTINATIONS.items()
}


@dataclass(frozen=True)
class FirehoseConfig(CredentialedConfig):
    region: str
    account_id: str
    delivery_stream_name_prefix: str = "astrolift"
    kms_key_id: str = ""
    deletion_protection_default: bool = True
    poll_delay_seconds: float = 5.0
    max_poll_attempts: int = 60


class FirehoseDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: FirehoseConfig,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._sleep = sleep
        if client is None:
            client = aws_client("firehose", region=config.region, credential=config.credential)
        self._firehose = client

    @driver_op(
        cloud="aws",
        driver="stream_firehose",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_firehose_config"])
        if not re.fullmatch(r"[0-9]{12}", self._config.account_id) or not self._config.region:
            return ProvisionResult(
                False,
                "",
                "Firehose requires a region and 12-digit AWS account_id for exact delivery-stream identity",
                ["missing_account_id"],
            )
        name = self._stream_name(spec)
        arn = self._stream_arn(name)
        created = False
        if not spec.recorded_handle:
            try:
                response = self._firehose.create_delivery_stream(**self._create_request(name, spec))
                if response.get("DeliveryStreamARN") != arn:
                    raise ManagedServiceError("Firehose creation response does not match the exact target")
                created = True
            except Exception as exc:
                if not _already_exists(exc):
                    return ProvisionResult(False, "", f"create Firehose stream: {exc}", [str(exc)])
        handle = handle_for(kind=KIND, resource_id=arn)
        try:
            description = self._await_delivery_state(name, {"ACTIVE"})
            self._assert_owner(arn, spec.managed_service_id)
            if not created:
                self._verify_create_only_identity(description, cfg)
            self._reconcile(name, arn, description, cfg, spec=spec, newly_created=created)
        except Exception as exc:
            action = "configure new" if created else "reconcile existing"
            return ProvisionResult(False, handle, f"{action} Firehose stream: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=arn),
            f"Firehose delivery stream {name} available",
            ready=True,
        )

    @driver_op(cloud="aws", driver="stream_firehose")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        arn, name = self._target(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg, partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_firehose_config"])
        try:
            description = self._description(name)
            self._assert_owner(arn, spec.managed_service_id)
            self._reconcile(name, arn, description, cfg)
        except ManagedServiceError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["ownership_refused"], retryable=False)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, f"Firehose stream {name} not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update Firehose stream: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Firehose delivery stream {name} reconciled")

    @driver_op(
        cloud="aws",
        driver="stream_firehose",
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
        arn, name = self._target(spec.handle)
        try:
            self._description(name)
            self._assert_owner(arn, spec.managed_service_id)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"Firehose stream {name} already gone")
            return _deprovision_error(spec.handle, "describe Firehose stream", exc)
        protected = bool(
            spec.config.get("deletion_protection", self._config.deletion_protection_default),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Firehose stream {name} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "deleting Firehose can discard buffered or in-flight records; set delete_data=true",
                ["buffered_records_require_delete_data"],
                retryable=False,
            )
        try:
            self._firehose.delete_delivery_stream(
                DeliveryStreamName=name,
                AllowForceDelete=force_destroy,
            )
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Firehose stream", exc)
        return DeprovisionResult(True, spec.handle, f"Firehose delivery stream {name} deletion queued")

    @driver_op(cloud="aws", driver="stream_firehose")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, arn = parse_handle(handle.handle)
        name = _stream_name_from_arn(arn)
        try:
            description = self._description(name)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", f"Firehose stream {name} does not exist")
            return ServiceStatus(handle.handle, "error", f"describe Firehose stream: {exc}")
        provider_state = str(description.get("DeliveryStreamStatus") or "UNKNOWN")
        state = {
            "ACTIVE": "available",
            "CREATING": "provisioning",
            "DELETING": "deprovisioning",
        }.get(provider_state, "error")
        source = str(description.get("DeliveryStreamType") or "UNKNOWN")
        encryption = str((description.get("DeliveryStreamEncryptionConfiguration") or {}).get("Status") or "DISABLED")
        return ServiceStatus(
            handle.handle,
            state,
            f"Firehose reports {provider_state} ({source}, encryption {encryption})",
        )

    @driver_op(cloud="aws", driver="stream_firehose")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, arn = parse_handle(handle.handle)
        name = _stream_name_from_arn(arn)
        description = self._description(name)
        resolved_arn = str(description.get("DeliveryStreamARN") or arn)
        cfg = config or {}
        stream_type = str(description.get("DeliveryStreamType") or "DirectPut")
        access_mode = str(cfg.get("access_mode") or ("put" if stream_type == "DirectPut" else "observe"))
        actions = ["firehose:DescribeDeliveryStream"]
        if access_mode in {"put", "manage"} and stream_type == "DirectPut":
            actions.extend(["firehose:PutRecord", "firehose:PutRecordBatch"])
        if access_mode == "manage":
            actions.extend(
                [
                    "firehose:ListTagsForDeliveryStream",
                    "firehose:StartDeliveryStreamEncryption",
                    "firehose:StopDeliveryStreamEncryption",
                    "firehose:TagDeliveryStream",
                    "firehose:UntagDeliveryStream",
                    "firehose:UpdateDestination",
                ],
            )
        grants = [Grant(resource=resolved_arn, actions=sorted(set(actions)))]
        if access_mode == "manage":
            grants.extend(Grant(resource=role_arn, actions=["iam:PassRole"]) for role_arn in sorted(_role_arns(cfg)))
            key_arn = str(
                ((cfg.get("encryption") or {}).get("key_arn")) or self._config.kms_key_id or "",
            )
            if key_arn:
                grants.append(
                    Grant(
                        resource=key_arn,
                        actions=["kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKey"],
                    ),
                )
        endpoint = f"https://firehose.{self._config.region}.amazonaws.com"
        return Binding(
            env_vars={
                "STREAM_NAME": ValueRef(literal=name),
                "STREAM_ARN": ValueRef(literal=resolved_arn),
                "STREAM_ENDPOINT": ValueRef(literal=endpoint),
                "STREAM_REGION": ValueRef(literal=self._config.region),
                "AWS_REGION": ValueRef(literal=self._config.region),
                "FIREHOSE_DELIVERY_STREAM_NAME": ValueRef(literal=name),
                "FIREHOSE_DELIVERY_STREAM_ARN": ValueRef(literal=resolved_arn),
            },
            iam_grants=grants,
            notes=f"Firehose {access_mode} access for {stream_type}",
        )

    @driver_op(cloud="aws", driver="stream_firehose")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "Firehose has no snapshot API; snapshot the configured destination instead",
        )

    @driver_op(cloud="aws", driver="stream_firehose")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise ManagedServiceError("Firehose delivery streams cannot be restored from a snapshot")

    @driver_op(cloud="aws", driver="stream_firehose", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        native = {
            "type": "object",
            "description": "Exact AWS API structure; field names retain AWS casing.",
        }
        return {
            "type": "object",
            "properties": {
                "access_mode": {
                    "type": "string",
                    "enum": ["put", "observe", "manage"],
                },
                "source": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": sorted(_SOURCE_TYPES)},
                        "configuration": native,
                    },
                    "required": ["type"],
                    "additionalProperties": False,
                },
                "destination": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": sorted(_DESTINATIONS)},
                        "configuration": native,
                    },
                    "required": ["type", "configuration"],
                    "additionalProperties": False,
                },
                "destination_update": {
                    "type": "object",
                    "description": "Exact mutable AWS UpdateDestination structure.",
                    "properties": {
                        "type": {"type": "string", "enum": sorted(_DESTINATIONS)},
                        "configuration": native,
                    },
                    "required": ["type", "configuration"],
                    "additionalProperties": False,
                },
                "encryption": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean", "default": True},
                        "key_type": {
                            "type": "string",
                            "enum": ["AWS_OWNED_CMK", "CUSTOMER_MANAGED_CMK"],
                        },
                        "key_arn": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "deletion_protection": {"type": "boolean", "default": True},
            },
            "required": ["destination"],
            "additionalProperties": False,
        }

    @driver_op(cloud="aws", driver="stream_firehose", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "STREAM_NAME": "Portable stream name",
                "STREAM_ARN": "Portable stream resource ID",
                "STREAM_ENDPOINT": "Portable service endpoint",
                "STREAM_REGION": "Portable stream region",
                "AWS_REGION": "AWS region",
                "FIREHOSE_DELIVERY_STREAM_NAME": "AWS Firehose delivery-stream name",
                "FIREHOSE_DELIVERY_STREAM_ARN": "AWS Firehose delivery-stream ARN",
            },
        )

    def editable_fields(self) -> list[str]:
        return ["access_mode", "deletion_protection", "destination_update", "encryption"]

    def _reconcile(
        self,
        name: str,
        arn: str,
        description: dict[str, Any],
        cfg: dict[str, Any],
        *,
        spec: ProvisionSpec | None = None,
        newly_created: bool = False,
    ) -> None:
        if spec is not None:
            self._firehose.tag_delivery_stream(
                DeliveryStreamName=name,
                Tags=tags_for(spec),
            )
        if "destination_update" in cfg:
            self._update_destination(name, description, cfg["destination_update"])
            description = self._await_delivery_state(name, {"ACTIVE"})
        if not newly_created or "encryption" in cfg:
            self._reconcile_encryption(name, description, cfg)

    def _verify_create_only_identity(
        self,
        description: dict[str, Any],
        cfg: dict[str, Any],
    ) -> None:
        source = dict(cfg.get("source") or {"type": "direct_put"})
        expected_stream_type = _SOURCE_TYPES[str(source.get("type") or "direct_put")][0]
        live_stream_type = str(description.get("DeliveryStreamType") or "")
        if live_stream_type != expected_stream_type:
            raise ManagedServiceError(
                f"live Firehose source type {live_stream_type} does not match requested {expected_stream_type}; "
                "reprovision is required",
            )
        destinations = list(description.get("Destinations") or [])
        if len(destinations) != 1:
            raise ManagedServiceError(
                f"Firehose expected exactly one destination, found {len(destinations)}",
            )
        destination_type = str(cfg["destination"]["type"])
        if _DESTINATION_DESCRIPTIONS[destination_type] not in destinations[0]:
            raise ManagedServiceError(
                f"live Firehose destination does not match requested {destination_type}; reprovision is required",
            )

    def _update_destination(
        self,
        name: str,
        description: dict[str, Any],
        update: dict[str, Any],
    ) -> None:
        destinations = list(description.get("Destinations") or [])
        if len(destinations) != 1:
            raise ManagedServiceError(
                f"Firehose expected exactly one destination, found {len(destinations)}",
            )
        destination_id = str(destinations[0].get("DestinationId") or "")
        if not destination_id:
            raise ManagedServiceError("Firehose destination is missing DestinationId")
        destination_type = str(update["type"])
        description_field = _DESTINATION_DESCRIPTIONS[destination_type]
        if description_field not in destinations[0]:
            raise ManagedServiceError(
                f"destination_update type {destination_type} does not match the live Firehose destination",
            )
        update_field = _DESTINATIONS[destination_type][1]
        self._firehose.update_destination(
            DeliveryStreamName=name,
            CurrentDeliveryStreamVersionId=str(description["VersionId"]),
            DestinationId=destination_id,
            **{update_field: dict(update["configuration"])},
        )

    def _reconcile_encryption(
        self,
        name: str,
        description: dict[str, Any],
        cfg: dict[str, Any],
    ) -> None:
        encryption = dict(cfg.get("encryption") or {})
        enabled = bool(encryption.get("enabled", True))
        current = description.get("DeliveryStreamEncryptionConfiguration") or {}
        status = str(current.get("Status") or "DISABLED")
        if status == "ENABLING":
            description = self._await_encryption(name, enabled=True)
            current = description.get("DeliveryStreamEncryptionConfiguration") or {}
            status = str(current.get("Status") or "ENABLED")
        elif status == "DISABLING":
            description = self._await_encryption(name, enabled=False)
            current = description.get("DeliveryStreamEncryptionConfiguration") or {}
            status = str(current.get("Status") or "DISABLED")
        desired = self._encryption_request(encryption)
        current_key = str(current.get("KeyARN") or "")
        desired_key = str(desired.get("KeyARN") or "")
        if enabled and (status != "ENABLED" or (desired_key and current_key != desired_key)):
            if status == "ENABLED" and desired_key and current_key != desired_key:
                self._firehose.stop_delivery_stream_encryption(DeliveryStreamName=name)
                self._await_encryption(name, enabled=False)
            self._firehose.start_delivery_stream_encryption(
                DeliveryStreamName=name,
                DeliveryStreamEncryptionConfigurationInput=desired,
            )
            self._await_encryption(name, enabled=True)
        elif not enabled and status != "DISABLED":
            self._firehose.stop_delivery_stream_encryption(DeliveryStreamName=name)
            self._await_encryption(name, enabled=False)

    def _create_request(self, name: str, spec: ProvisionSpec) -> dict[str, Any]:
        cfg = spec.config or {}
        source = dict(cfg.get("source") or {"type": "direct_put"})
        source_type = str(source.get("type") or "direct_put")
        stream_type, source_field = _SOURCE_TYPES[source_type]
        destination = dict(cfg["destination"])
        destination_field = _DESTINATIONS[str(destination["type"])][0]
        request: dict[str, Any] = {
            "DeliveryStreamName": name,
            "DeliveryStreamType": stream_type,
            destination_field: dict(destination["configuration"]),
            "Tags": tags_for(spec),
        }
        source_configuration = dict(source.get("configuration") or {})
        if source_configuration:
            request[source_field] = source_configuration
        encryption = dict(cfg.get("encryption") or {})
        if encryption.get("enabled", True):
            request["DeliveryStreamEncryptionConfigurationInput"] = self._encryption_request(encryption)
        return request

    def _encryption_request(self, encryption: dict[str, Any]) -> dict[str, str]:
        key_arn = str(encryption.get("key_arn") or self._config.kms_key_id or "")
        key_type = str(
            encryption.get("key_type") or ("CUSTOMER_MANAGED_CMK" if key_arn else "AWS_OWNED_CMK"),
        )
        request = {"KeyType": key_type}
        if key_type == "CUSTOMER_MANAGED_CMK":
            request["KeyARN"] = key_arn
        return request

    def _description(self, name: str) -> dict[str, Any]:
        response = self._firehose.describe_delivery_stream(DeliveryStreamName=name)
        value = dict(response["DeliveryStreamDescription"])
        if value.get("DeliveryStreamARN") != self._stream_arn(name) or value.get("DeliveryStreamName") != name:
            raise ManagedServiceError("live Firehose delivery stream does not match the exact target")
        return value

    def _await_delivery_state(self, name: str, desired: set[str]) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for attempt in range(self._config.max_poll_attempts):
            last = self._description(name)
            state = str(last.get("DeliveryStreamStatus") or "UNKNOWN")
            if state in desired:
                return last
            if state.endswith("_FAILED"):
                raise ManagedServiceError(f"Firehose entered terminal state {state}")
            if attempt + 1 < self._config.max_poll_attempts:
                self._sleep(self._config.poll_delay_seconds)
        raise ManagedServiceError(
            f"Firehose did not reach {sorted(desired)}; last state was {last.get('DeliveryStreamStatus', 'UNKNOWN')}",
        )

    def _await_encryption(self, name: str, *, enabled: bool) -> dict[str, Any]:
        desired = "ENABLED" if enabled else "DISABLED"
        last: dict[str, Any] = {}
        for attempt in range(self._config.max_poll_attempts):
            last = self._description(name)
            current = last.get("DeliveryStreamEncryptionConfiguration") or {}
            status = str(current.get("Status") or "DISABLED")
            if status == desired:
                return last
            if status in {"ENABLING_FAILED", "DISABLING_FAILED"}:
                raise ManagedServiceError(f"Firehose encryption entered terminal state {status}")
            if attempt + 1 < self._config.max_poll_attempts:
                self._sleep(self._config.poll_delay_seconds)
        raise ManagedServiceError(
            f"Firehose encryption did not reach {desired}",
        )

    def _assert_owner(self, arn: str, identity: str) -> None:
        tags: list[dict[str, Any]] = []
        token = ""
        seen: set[str] = set()
        while True:
            request: dict[str, Any] = {"DeliveryStreamName": _stream_name_from_arn(arn)}
            if token:
                request["ExclusiveStartTagKey"] = token
            response = self._firehose.list_tags_for_delivery_stream(**request)
            page = response.get("Tags") or []
            tags.extend(page)
            if not response.get("HasMoreTags"):
                break
            next_token = page[-1].get("Key") if page and isinstance(page[-1], dict) else None
            if not isinstance(next_token, str) or not next_token or next_token in seen:
                raise ManagedServiceError("Firehose ownership pagination cannot be verified")
            seen.add(next_token)
            token = next_token
        refusal = live_ownership_refusal(tags, managed_service_id=identity, resource="Firehose delivery stream")
        if refusal:
            raise ManagedServiceError(refusal)

    def _validate_config(self, cfg: dict[str, Any], *, partial: bool = False) -> str:
        if self._config.poll_delay_seconds < 0 or self._config.max_poll_attempts < 1:
            return "Firehose polling defaults require a non-negative delay and at least one attempt"
        if cfg.get("access_mode", "put") not in {"put", "observe", "manage"}:
            return "access_mode must be put, observe, or manage"
        if "deletion_protection" in cfg and not isinstance(cfg["deletion_protection"], bool):
            return "deletion_protection must be a boolean"
        if not partial and "destination" not in cfg:
            return "destination is required"
        if "source" in cfg:
            source = cfg["source"]
            if not isinstance(source, dict) or source.get("type") not in _SOURCE_TYPES:
                return f"source.type must be one of {sorted(_SOURCE_TYPES)}"
            if "configuration" in source and not isinstance(source["configuration"], dict):
                return "source.configuration must be an object"
            source_type = str(source["type"])
            if source_type != "direct_put" and not source.get("configuration"):
                return f"source.configuration is required for {source_type}"
            if cfg.get("access_mode") == "put" and source_type != "direct_put":
                return "access_mode put is only valid for a direct_put Firehose source"
        for key in ("destination", "destination_update"):
            if key not in cfg:
                continue
            value = cfg[key]
            if not isinstance(value, dict) or value.get("type") not in _DESTINATIONS:
                return f"{key}.type must be one of {sorted(_DESTINATIONS)}"
            if not isinstance(value.get("configuration"), dict) or not value["configuration"]:
                return f"{key}.configuration must be a non-empty object"
            sensitive_path = _plaintext_credential_path(value["configuration"])
            if sensitive_path:
                return (
                    f"{key}.configuration.{sensitive_path} is forbidden because plaintext credentials cannot be "
                    "stored in config; use SecretsManagerConfiguration"
                )
        if "encryption" in cfg:
            encryption = cfg["encryption"]
            if not isinstance(encryption, dict):
                return "encryption must be an object"
            if "enabled" in encryption and not isinstance(encryption["enabled"], bool):
                return "encryption.enabled must be a boolean"
            key_type = encryption.get("key_type")
            if key_type not in (None, "AWS_OWNED_CMK", "CUSTOMER_MANAGED_CMK"):
                return "encryption.key_type must be AWS_OWNED_CMK or CUSTOMER_MANAGED_CMK"
            key_arn = str(encryption.get("key_arn") or self._config.kms_key_id or "")
            if encryption.get("enabled", True) and key_type == "CUSTOMER_MANAGED_CMK" and not key_arn:
                return "CUSTOMER_MANAGED_CMK encryption requires key_arn"
            if key_type == "AWS_OWNED_CMK" and encryption.get("key_arn"):
                return "AWS_OWNED_CMK encryption cannot set key_arn"
        try:
            if not partial:
                validate = self._create_request(
                    "astrolift-validation",
                    ProvisionSpec(
                        organization_id="validation",
                        organization_slug="validation",
                        app_id="validation",
                        app_slug="validation",
                        environment_id="validation",
                        environment_name="validation",
                        tenant_cluster_id="validation",
                        service_handle_hint="validation",
                        size="custom",
                        config=cfg,
                    ),
                )
                _validate_request("CreateDeliveryStream", validate)
            if "destination_update" in cfg:
                update = cfg["destination_update"]
                field = _DESTINATIONS[str(update["type"])][1]
                _validate_request(
                    "UpdateDestination",
                    {
                        "DeliveryStreamName": "astrolift-validation",
                        "CurrentDeliveryStreamVersionId": "1",
                        "DestinationId": "destinationId-000000000001",
                        field: dict(update["configuration"]),
                    },
                )
        except Exception as exc:
            return f"invalid AWS Firehose request structure: {exc}"
        return ""

    def _stream_name(self, spec: ProvisionSpec) -> str:
        managed_service_identity(spec.managed_service_id)
        if spec.recorded_handle:
            return self._target(spec.recorded_handle)[1]
        return physical_name(spec.managed_service_id, prefix=self._config.delivery_stream_name_prefix, max_length=64)

    def _target(self, handle: str) -> tuple[str, str]:
        kind, arn = parse_handle(handle)
        name = _stream_name_from_arn(arn)
        if (
            kind != KIND
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name) is None
            or not re.fullmatch(r"[0-9]{12}", self._config.account_id)
            or arn != self._stream_arn(name)
        ):
            raise ManagedServiceError("recorded Firehose handle does not match the configured driver target")
        return arn, name

    def _stream_arn(self, name: str) -> str:
        from botocore.session import get_session

        partition = get_session().get_partition_for_region(self._config.region)
        return f"arn:{partition}:firehose:{self._config.region}:{self._config.account_id}:deliverystream/{name}"


def _validate_request(operation: str, request: dict[str, Any]) -> None:
    from botocore.session import Session
    from botocore.validate import validate_parameters

    service = Session().get_service_model("firehose")
    validate_parameters(request, service.operation_model(operation).input_shape)


def _role_arns(value: Any) -> set[str]:
    roles: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"RoleARN", "role_arn"} and nested:
                roles.add(str(nested))
            else:
                roles.update(_role_arns(nested))
    elif isinstance(value, list):
        for nested in value:
            roles.update(_role_arns(nested))
    return roles


def _stream_name_from_arn(arn: str) -> str:
    return arn.rsplit("/", 1)[-1]


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
            "ConcurrentModificationException",
            "InternalFailure",
            "ResourceInUseException",
        }
    )
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [str(exc)], retryable=retryable)


# Destination fields that carry a credential in plaintext: Redshift and
# Snowflake passwords, HTTP endpoint access keys, the Splunk HEC token and
# Snowflake private keys (#1953). Each destination has a
# SecretsManagerConfiguration alternative.
_PLAINTEXT_CREDENTIAL_KEYS = frozenset({"password", "accesskey", "hectoken", "privatekey", "keypassphrase"})


def _plaintext_credential_path(value: Any, prefix: str = "") -> str:
    """Dotted path of the first plaintext credential in a destination configuration, or ``""``."""
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if str(key).replace("_", "").lower() in _PLAINTEXT_CREDENTIAL_KEYS and child not in (None, ""):
                return path
            found = _plaintext_credential_path(child, path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _plaintext_credential_path(child, f"{prefix}[{index}]")
            if found:
                return found
    return ""
