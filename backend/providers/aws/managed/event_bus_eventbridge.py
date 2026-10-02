"""Amazon EventBridge custom event-bus managed-service driver."""

from __future__ import annotations

import json
import re
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
from _sdk.physical_naming import managed_service_identity, physical_name
from aws.managed._base import ManagedServiceError, handle_for, live_ownership_refusal, parse_handle, tags_for
from aws.session import aws_client

KIND = "event_bus"
_TARGET_PARAMETERS = {
    "appsync": "AppSyncParameters",
    "batch": "BatchParameters",
    "ecs": "EcsParameters",
    "http": "HttpParameters",
    "kinesis": "KinesisParameters",
    "redshift_data": "RedshiftDataParameters",
    "run_command": "RunCommandParameters",
    "sagemaker_pipeline": "SageMakerPipelineParameters",
    "sqs": "SqsParameters",
}
_TARGET_FIELDS = {
    "AppSyncParameters",
    "BatchParameters",
    "DeadLetterConfig",
    "EcsParameters",
    "HttpParameters",
    "Input",
    "InputPath",
    "InputTransformer",
    "KinesisParameters",
    "RedshiftDataParameters",
    "RetryPolicy",
    "RoleArn",
    "RunCommandParameters",
    "SageMakerPipelineParameters",
    "SqsParameters",
}


@dataclass(frozen=True)
class EventBridgeConfig(CredentialedConfig):
    region: str
    account_id: str
    event_bus_name_prefix: str = "astrolift"
    kms_key_id: str = ""
    deletion_protection_default: bool = True


class EventBridgeDriver(ManagedServiceDriver):
    def __init__(self, *, config: EventBridgeConfig, client: Any | None = None) -> None:
        self._config = config
        if client is None:
            client = aws_client("events", region=config.region, credential=config.credential)
        self._events = client

    @driver_op(
        cloud="aws",
        driver="eventbridge",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_eventbridge_config"])
        bus_name = self._bus_name(spec)
        try:
            try:
                existing = self._bus(spec.recorded_handle.partition("/")[2] if spec.recorded_handle else bus_name)
            except Exception as exc:
                if not _not_found(exc):
                    raise
                if spec.recorded_handle:
                    raise ManagedServiceError("recorded EventBridge bus is missing; refusing a replacement") from None
                existing = None
            if existing is None:
                create = self._bus_request(bus_name, cfg, creating=True)
                create["Tags"] = tags_for(spec)
                try:
                    response = self._events.create_event_bus(**create)
                    bus_arn = str(response.get("EventBusArn") or "")
                except Exception as exc:
                    if not _already_exists(exc):
                        raise
                    bus_arn = str(self._bus(bus_name)["Arn"])
            else:
                bus_arn = str(existing["Arn"])
            self._assert_owned_bus(bus_arn, bus_name, spec.managed_service_id)
        except Exception as exc:
            return ProvisionResult(False, "", f"resolve owned EventBridge bus: {exc}", [str(exc)])
        handle = handle_for(kind=KIND, resource_id=bus_arn)
        try:
            self._reconcile_bus(bus_name, bus_arn, cfg, spec=spec, identity=spec.managed_service_id)
        except Exception as exc:
            return ProvisionResult(False, handle, f"reconcile EventBridge bus: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"EventBridge bus {bus_name} available", ready=True)

    @driver_op(cloud="aws", driver="eventbridge")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        bus_arn = self._handle_arn(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg, partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_eventbridge_config"])
        bus_name = _bus_name_from_arn(bus_arn)
        try:
            self._assert_owned_bus(bus_arn, bus_name, spec.managed_service_id)
            self._reconcile_bus(bus_name, bus_arn, cfg, identity=spec.managed_service_id)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, f"EventBridge bus {bus_name} not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update EventBridge bus: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"EventBridge bus {bus_name} reconciled")

    @driver_op(
        cloud="aws",
        driver="eventbridge",
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
        bus_arn = self._handle_arn(spec.handle)
        bus_name = _bus_name_from_arn(bus_arn)
        try:
            self._assert_owned_bus(bus_arn, bus_name, spec.managed_service_id)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"EventBridge bus {bus_name} already gone")
            return _deprovision_error(spec.handle, "describe EventBridge bus", exc)
        protected = bool(
            spec.config.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"EventBridge bus {bus_name} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            archives = self._archives(bus_arn)
            if archives and not delete_data:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"EventBridge bus {bus_name} has {len(archives)} retained archive(s); set delete_data=true",
                    ["retained_archives_require_delete_data"],
                    retryable=False,
                )
            configured_archive = _archive_name(bus_name, spec.config["archive"]) if "archive" in spec.config else ""
            external = [archive for archive in archives if str(archive.get("ArchiveName") or "") != configured_archive]
            if external and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "EventBridge bus has archives outside this resource declaration; force_destroy is required",
                    ["external_archives_present"],
                    retryable=False,
                )
            rules = self._rules(bus_name)
            external_rules = [
                rule for rule in rules if not self._is_managed(str(rule.get("Arn") or ""), spec.managed_service_id)
            ]
            if external_rules and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "EventBridge bus contains rules outside this resource declaration; force_destroy is required",
                    ["external_rules_present"],
                    retryable=False,
                )
            for archive in archives:
                self._assert_archive_parent(str(archive["ArchiveName"]), bus_arn)
            for archive in archives:
                self._events.delete_archive(ArchiveName=str(archive["ArchiveName"]))
            self._delete_rules(bus_name, managed_only=False, identity=spec.managed_service_id)
            self._events.delete_event_bus(Name=bus_name)
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete EventBridge bus", exc)
        return DeprovisionResult(True, spec.handle, f"EventBridge bus {bus_name} deleted")

    @driver_op(cloud="aws", driver="eventbridge")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, bus_arn = parse_handle(handle.handle)
        bus_name = _bus_name_from_arn(bus_arn)
        try:
            bus = self._bus(bus_arn)
            rules = self._rules(bus_name)
            archives = self._archives(str(bus.get("Arn") or bus_arn))
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", f"EventBridge bus {bus_name} does not exist")
            return ServiceStatus(handle.handle, "error", f"describe EventBridge bus: {exc}")
        return ServiceStatus(
            handle.handle,
            "available",
            f"EventBridge bus available with {len(rules)} rule(s) and {len(archives)} archive(s)",
        )

    @driver_op(cloud="aws", driver="eventbridge")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, bus_arn = parse_handle(handle.handle)
        bus = self._bus(bus_arn)
        resolved_arn = str(bus.get("Arn") or bus_arn)
        bus_name = str(bus.get("Name") or _bus_name_from_arn(resolved_arn))
        access_mode = str((config or {}).get("access_mode") or "publish")
        grants = [
            Grant(
                resource=resolved_arn,
                actions=["events:DescribeEventBus", "events:PutEvents"],
            ),
        ]
        if access_mode == "manage":
            parts = resolved_arn.split(":", 5)
            rule_resource = f"arn:{parts[1]}:events:{parts[3]}:{parts[4]}:rule/{bus_name}/*"
            grants.append(
                Grant(
                    resource=rule_resource,
                    actions=[
                        "events:DeleteRule",
                        "events:DescribeRule",
                        "events:PutRule",
                        "events:PutTargets",
                        "events:RemoveTargets",
                    ],
                ),
            )
            grants.append(
                Grant(
                    resource="*",
                    actions=[
                        "events:ListRules",
                        "events:ListTargetsByRule",
                        "events:PutPermission",
                        "events:RemovePermission",
                    ],
                ),
            )
            role_arns = _role_arns(config or {})
            grants.extend(Grant(resource=role_arn, actions=["iam:PassRole"]) for role_arn in sorted(role_arns))
            kms_key = str(bus.get("KmsKeyIdentifier") or "")
            if kms_key:
                grants.append(Grant(resource=kms_key, actions=["kms:Decrypt"]))
        return Binding(
            env_vars={
                "EVENT_BUS_NAME": ValueRef(literal=bus_name),
                "EVENT_BUS_ARN": ValueRef(literal=resolved_arn),
                "EVENT_BUS_REGION": ValueRef(literal=self._config.region),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=grants,
            notes=f"EventBridge {access_mode} access",
        )

    @driver_op(cloud="aws", driver="eventbridge")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "EventBridge does not expose snapshots; configure an in-place archive and replay policy instead",
        )

    @driver_op(cloud="aws", driver="eventbridge")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise ManagedServiceError("EventBridge event buses cannot be restored from a snapshot")

    @driver_op(cloud="aws", driver="eventbridge", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "access_mode": {"type": "string", "enum": ["publish", "manage"], "default": "publish"},
                "description": {"type": "string", "maxLength": 512},
                "kms_key_identifier": {"type": "string"},
                "dead_letter_queue_arn": {"type": "string"},
                "log_config": {
                    "type": "object",
                    "properties": {
                        "include_detail": {"type": "string", "enum": ["NONE", "FULL"]},
                        "level": {"type": "string", "enum": ["OFF", "ERROR", "INFO", "TRACE"]},
                    },
                    "additionalProperties": False,
                },
                "resource_policy": {"type": ["object", "string"]},
                "permissions": {"type": "array", "items": {"type": "object"}},
                "prune_permissions": {"type": "boolean", "default": False},
                "rules": {"type": "array", "items": {"type": "object"}},
                "prune_rules": {"type": "boolean", "default": True},
                "archive": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean", "default": True},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "event_pattern": {"type": ["object", "string"]},
                        "retention_days": {"type": "integer", "minimum": 0},
                        "kms_key_identifier": {"type": "string"},
                        "delete_data": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": False,
                },
                "deletion_protection": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="aws", driver="eventbridge", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EVENT_BUS_NAME": "Portable event-bus name",
                "EVENT_BUS_ARN": "Portable event-bus ARN",
                "EVENT_BUS_REGION": "Portable event-bus region",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "archive",
            "dead_letter_queue_arn",
            "deletion_protection",
            "description",
            "kms_key_identifier",
            "log_config",
            "permissions",
            "prune_permissions",
            "prune_rules",
            "resource_policy",
            "rules",
        ]

    def _reconcile_bus(
        self,
        bus_name: str,
        bus_arn: str,
        cfg: dict[str, Any],
        *,
        spec: ProvisionSpec | None = None,
        identity: str,
    ) -> None:
        update = self._bus_request(bus_name, cfg, creating=False)
        if len(update) > 1:
            self._events.update_event_bus(**update)
        if spec is not None:
            self._events.tag_resource(ResourceARN=bus_arn, Tags=tags_for(spec))
        self._reconcile_permissions(bus_name, cfg)
        if "rules" in cfg:
            self._reconcile_rules(bus_name, cfg, spec=spec, identity=identity)
        if "archive" in cfg:
            self._reconcile_archive(bus_name, bus_arn, cfg["archive"])

    def _reconcile_permissions(self, bus_name: str, cfg: dict[str, Any]) -> None:
        if "resource_policy" in cfg:
            self._events.put_permission(
                EventBusName=bus_name,
                Policy=_json_document(cfg["resource_policy"], field="resource_policy"),
            )
            return
        if "permissions" not in cfg:
            return
        desired: set[str] = set()
        for permission in cfg.get("permissions") or []:
            sid = _permission_sid(str(permission["sid"]))
            desired.add(sid)
            request: dict[str, Any] = {
                "EventBusName": bus_name,
                "Action": str(permission.get("action") or "events:PutEvents"),
                "Principal": str(permission["principal"]),
                "StatementId": sid,
            }
            if permission.get("condition"):
                condition = permission["condition"]
                request["Condition"] = {
                    "Type": str(condition["type"]),
                    "Key": str(condition["key"]),
                    "Value": str(condition["value"]),
                }
            self._events.put_permission(**request)
        if cfg.get("prune_permissions"):
            bus = self._bus(bus_name)
            policy = _json_object(bus.get("Policy") or "{}")
            for statement in policy.get("Statement") or []:
                sid = str(statement.get("Sid") or "")
                if sid.startswith("astrolift-") and sid not in desired:
                    self._events.remove_permission(EventBusName=bus_name, StatementId=sid)

    def _reconcile_rules(
        self,
        bus_name: str,
        cfg: dict[str, Any],
        *,
        spec: ProvisionSpec | None,
        identity: str,
    ) -> None:
        current = {str(rule["Name"]): rule for rule in self._rules(bus_name)}
        desired: set[str] = set()
        for rule in cfg.get("rules") or []:
            name = _name(str(rule["name"]), limit=64)
            existing = current.get(name)
            if existing is not None and not self._is_managed(str(existing.get("Arn") or ""), identity):
                raise ManagedServiceError(
                    f"rule {name} already exists outside this resource declaration",
                )
            desired.add(name)
            request: dict[str, Any] = {"Name": name, "EventBusName": bus_name}
            for key, aws_key in (
                ("schedule_expression", "ScheduleExpression"),
                ("description", "Description"),
                ("state", "State"),
                ("role_arn", "RoleArn"),
            ):
                if key in rule:
                    request[aws_key] = str(rule[key])
            if "event_pattern" in rule:
                request["EventPattern"] = _json_document(rule["event_pattern"], field="event_pattern")
            if name not in current:
                request["Tags"] = (
                    tags_for(spec)
                    if spec
                    else [
                        {"Key": "astrolift.io/managed-by", "Value": "platform"},
                        {"Key": "astrolift.io/managed_service_id", "Value": identity},
                    ]
                )
            response = self._events.put_rule(**request)
            rule_arn = str(response.get("RuleArn") or (existing or {}).get("Arn") or "")
            if rule_arn != self._rule_arn(bus_name, name):
                raise ManagedServiceError("EventBridge rule response does not match the exact bus and rule name")
            if not self._is_managed(rule_arn, identity):
                raise ManagedServiceError("EventBridge rule is not owned by this managed-service identity")
            if spec is not None and rule_arn:
                self._events.tag_resource(ResourceARN=rule_arn, Tags=tags_for(spec))
            if "targets" in rule:
                self._reconcile_targets(bus_name, name, list(rule.get("targets") or []))
        if cfg.get("prune_rules", True):
            for name, rule in current.items():
                if name not in desired and self._is_managed(str(rule.get("Arn") or ""), identity):
                    self._delete_rule(bus_name, name)

    def _reconcile_targets(self, bus_name: str, rule_name: str, desired: list[dict[str, Any]]) -> None:
        current = {str(target["Id"]): target for target in self._targets(bus_name, rule_name)}
        requests = [_target_request(target) for target in desired]
        desired_ids = {str(target["Id"]) for target in requests}
        for chunk in _chunks(requests, 10):
            response = self._events.put_targets(
                Rule=rule_name,
                EventBusName=bus_name,
                Targets=chunk,
            )
            if int(response.get("FailedEntryCount", 0) or 0):
                raise ManagedServiceError(
                    f"put targets for rule {rule_name} partially failed: {response.get('FailedEntries') or []}",
                )
        stale = sorted(set(current) - desired_ids)
        for chunk in _chunks(stale, 10):
            response = self._events.remove_targets(
                Rule=rule_name,
                EventBusName=bus_name,
                Ids=chunk,
            )
            if int(response.get("FailedEntryCount", 0) or 0):
                raise ManagedServiceError(
                    f"remove targets for rule {rule_name} partially failed: {response.get('FailedEntries') or []}",
                )

    def _reconcile_archive(self, bus_name: str, bus_arn: str, config: Any) -> None:
        archive = dict(config or {})
        archive_name = _archive_name(bus_name, archive)
        existing = self._archive(archive_name)
        if existing is not None:
            self._assert_archive_parent(archive_name, bus_arn, current=existing)
        enabled = bool(archive.get("enabled", True))
        if not enabled:
            if existing is None:
                return
            if not archive.get("delete_data"):
                raise ManagedServiceError(
                    f"archive {archive_name} contains retained events; set archive.delete_data=true to remove it",
                )
            self._events.delete_archive(ArchiveName=archive_name)
            return
        request: dict[str, Any] = {"ArchiveName": archive_name}
        for key, aws_key, cast in (
            ("description", "Description", str),
            ("retention_days", "RetentionDays", int),
            ("kms_key_identifier", "KmsKeyIdentifier", str),
        ):
            if key in archive:
                request[aws_key] = cast(archive[key])
        if "event_pattern" in archive:
            request["EventPattern"] = _json_document(archive["event_pattern"], field="archive.event_pattern")
        if existing is None:
            request["EventSourceArn"] = bus_arn
            self._events.create_archive(**request)
        else:
            self._events.update_archive(**request)

    def _bus_request(self, bus_name: str, cfg: dict[str, Any], *, creating: bool) -> dict[str, Any]:
        request: dict[str, Any] = {"Name": bus_name}
        for key, aws_key, cast in (
            ("description", "Description", str),
            ("kms_key_identifier", "KmsKeyIdentifier", str),
        ):
            if key in cfg:
                request[aws_key] = cast(cfg[key])
        if "kms_key_identifier" not in cfg and self._config.kms_key_id:
            request["KmsKeyIdentifier"] = self._config.kms_key_id
        if "dead_letter_queue_arn" in cfg:
            request["DeadLetterConfig"] = {"Arn": str(cfg["dead_letter_queue_arn"])}
        if "log_config" in cfg:
            log = cfg["log_config"] or {}
            request["LogConfig"] = {
                key: str(log[source])
                for source, key in (("include_detail", "IncludeDetail"), ("level", "Level"))
                if source in log
            }
        if not creating and len(request) == 1:
            return request
        return request

    def _bus_name(self, spec: ProvisionSpec) -> str:
        managed_service_identity(spec.managed_service_id)
        if spec.recorded_handle:
            return _bus_name_from_arn(self._handle_arn(spec.recorded_handle))
        # The default account-wide archive appends eight characters and is limited to 48.
        return physical_name(spec.managed_service_id, prefix=self._config.event_bus_name_prefix, max_length=40)

    def _handle_arn(self, handle: str) -> str:
        from botocore.session import get_session

        kind, arn = parse_handle(handle)
        expected = (
            f"arn:{get_session().get_partition_for_region(self._config.region)}:events:"
            f"{self._config.region}:{self._config.account_id}:event-bus/"
        )
        if (
            re.fullmatch(r"[0-9]{12}", self._config.account_id) is None
            or kind != KIND
            or not arn.startswith(expected)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", arn[len(expected) :])
        ):
            raise ManagedServiceError("recorded EventBridge handle does not match the configured driver target")
        return arn

    def _assert_owned_bus(self, arn: str, name: str, identity: str) -> None:
        self._handle_arn(handle_for(kind=KIND, resource_id=arn))
        current = self._bus(arn)
        if current.get("Arn") != arn or current.get("Name") != name:
            raise ManagedServiceError("live EventBridge bus does not match the exact recorded ARN and name")
        if not self._is_managed(arn, identity):
            raise ManagedServiceError("EventBridge bus is not owned by this managed-service identity")

    def _assert_archive_parent(self, name: str, arn: str, *, current: dict[str, Any] | None = None) -> None:
        current = current if current is not None else self._archive(name)
        if current is None or current.get("ArchiveName") != name or current.get("EventSourceArn") != arn:
            raise ManagedServiceError("EventBridge archive does not belong to this exact bus")

    def _bus(self, name: str) -> dict[str, Any]:
        return dict(self._events.describe_event_bus(Name=name))

    def _rule_arn(self, bus: str, name: str) -> str:
        from botocore.session import get_session

        parent = "" if bus == "default" else bus + "/"
        return (
            f"arn:{get_session().get_partition_for_region(self._config.region)}:events:"
            f"{self._config.region}:{self._config.account_id}:rule/{parent}{name}"
        )

    def _rules(self, bus_name: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"EventBusName": bus_name}
            if token:
                request["NextToken"] = token
            response = self._events.list_rules(**request)
            for rule in response.get("Rules") or []:
                name = str(rule.get("Name") or "")
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name) or rule.get("Arn") != self._rule_arn(bus_name, name):
                    raise ManagedServiceError("listed EventBridge rule does not belong to the exact bus")
                rows.append(rule)
            token = str(response.get("NextToken") or "")
            if not token:
                return rows

    def _targets(self, bus_name: str, rule_name: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"EventBusName": bus_name, "Rule": rule_name}
            if token:
                request["NextToken"] = token
            response = self._events.list_targets_by_rule(**request)
            rows.extend(response.get("Targets") or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return rows

    def _archives(self, bus_arn: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"EventSourceArn": bus_arn}
            if token:
                request["NextToken"] = token
            response = self._events.list_archives(**request)
            rows.extend(response.get("Archives") or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return rows

    def _archive(self, archive_name: str) -> dict[str, Any] | None:
        try:
            return dict(self._events.describe_archive(ArchiveName=archive_name))
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _is_managed(self, arn: str, identity: str) -> bool:
        if not arn:
            return False
        tags = self._events.list_tags_for_resource(ResourceARN=arn).get("Tags") or []
        parsed = {}
        for tag in tags:
            if (
                not isinstance(tag, dict)
                or not isinstance(tag.get("Key"), str)
                or not isinstance(tag.get("Value"), str)
                or tag["Key"] in parsed
            ):
                raise ManagedServiceError("live EventBridge ownership tags cannot be verified")
            parsed[tag["Key"]] = tag["Value"]
        return live_ownership_refusal(parsed, managed_service_id=identity, resource="EventBridge resource") is None

    def _delete_rules(self, bus_name: str, *, managed_only: bool, identity: str) -> None:
        rules = self._rules(bus_name)
        if managed_only:
            external = [rule for rule in rules if not self._is_managed(str(rule.get("Arn") or ""), identity)]
            if external:
                raise ManagedServiceError(
                    f"event bus still contains external rule {external[0].get('Name')}; force_destroy is required",
                )
        for rule in rules:
            self._delete_rule(bus_name, str(rule["Name"]))

    def _delete_rule(self, bus_name: str, rule_name: str) -> None:
        ids = [str(target["Id"]) for target in self._targets(bus_name, rule_name)]
        for chunk in _chunks(ids, 10):
            response = self._events.remove_targets(
                Rule=rule_name,
                EventBusName=bus_name,
                Ids=chunk,
                Force=True,
            )
            if int(response.get("FailedEntryCount", 0) or 0):
                raise ManagedServiceError(
                    f"remove targets for rule {rule_name} partially failed: {response.get('FailedEntries') or []}",
                )
        self._events.delete_rule(Name=rule_name, EventBusName=bus_name, Force=True)

    def _validate_config(self, cfg: dict[str, Any], *, partial: bool = False) -> str:
        del partial
        if cfg.get("access_mode", "publish") not in {"publish", "manage"}:
            return "access_mode must be publish or manage"
        if "resource_policy" in cfg and "permissions" in cfg:
            return "set either resource_policy or permissions, not both"
        if "log_config" in cfg:
            log = cfg["log_config"]
            if not isinstance(log, dict):
                return "log_config must be an object"
            if log.get("include_detail") not in (None, "NONE", "FULL"):
                return "log_config.include_detail must be NONE or FULL"
            if log.get("level") not in (None, "OFF", "ERROR", "INFO", "TRACE"):
                return "log_config.level must be OFF, ERROR, INFO, or TRACE"
        permissions = cfg.get("permissions", [])
        if not isinstance(permissions, list):
            return "permissions must be an array"
        permission_sids: set[str] = set()
        for permission in permissions:
            if not isinstance(permission, dict) or not permission.get("sid") or not permission.get("principal"):
                return "each permission requires sid and principal"
            sid = _permission_sid(str(permission["sid"]))
            if sid in permission_sids:
                return f"duplicate permission sid {permission['sid']}"
            permission_sids.add(sid)
            condition = permission.get("condition")
            if condition and not all(key in condition for key in ("type", "key", "value")):
                return "permission condition requires type, key, and value"
        rules = cfg.get("rules", [])
        if not isinstance(rules, list):
            return "rules must be an array"
        rule_names: set[str] = set()
        for rule in rules:
            if not isinstance(rule, dict) or not rule.get("name"):
                return "each rule requires a name"
            name = _name(str(rule["name"]), limit=64)
            if name in rule_names:
                return f"duplicate rule name {name}"
            rule_names.add(name)
            if not rule.get("event_pattern") and not rule.get("schedule_expression"):
                return f"rule {name} requires event_pattern or schedule_expression"
            if rule.get("schedule_expression"):
                return f"rule {name} cannot use schedule_expression on a custom event bus; use EventBridge Scheduler"
            try:
                _json_document(rule["event_pattern"], field=f"rule {name}.event_pattern")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return str(exc)
            if rule.get("state", "ENABLED") not in {
                "DISABLED",
                "ENABLED",
                "ENABLED_WITH_ALL_CLOUDTRAIL_MANAGEMENT_EVENTS",
            }:
                return f"rule {name} has an invalid state"
            targets = rule.get("targets", [])
            if not isinstance(targets, list):
                return f"rule {name} targets must be an array"
            if len(targets) > 5:
                return f"rule {name} cannot define more than five targets"
            target_ids: set[str] = set()
            for target in targets:
                error = _validate_target(target)
                if error:
                    return f"rule {name}: {error}"
                target_id = str(target["id"])
                if target_id in target_ids:
                    return f"rule {name} has duplicate target id {target_id}"
                target_ids.add(target_id)
        if "archive" in cfg:
            archive = cfg["archive"]
            if not isinstance(archive, dict):
                return "archive must be an object"
            if "retention_days" in archive:
                value = archive["retention_days"]
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    return "archive.retention_days must be a non-negative integer"
            if "event_pattern" in archive:
                try:
                    _json_document(archive["event_pattern"], field="archive.event_pattern")
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    return str(exc)
        for key in ("deletion_protection", "prune_permissions", "prune_rules"):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        for key in ("resource_policy",):
            if key in cfg:
                try:
                    _json_document(cfg[key], field=key)
                except (TypeError, ValueError) as exc:
                    return str(exc)
        return ""


def _target_request(config: dict[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] = {"Id": str(config["id"]), "Arn": str(config["arn"])}
    for key, aws_key in (
        ("role_arn", "RoleArn"),
        ("input", "Input"),
        ("input_path", "InputPath"),
    ):
        if key in config:
            value = config[key]
            request[aws_key] = (
                _json_value(value, field=key) if key == "input" and not isinstance(value, str) else str(value)
            )
    if "input_transformer" in config:
        transformer = config["input_transformer"]
        request["InputTransformer"] = {
            "InputPathsMap": {
                str(key): str(value) for key, value in (transformer.get("input_paths_map") or {}).items()
            },
            "InputTemplate": str(transformer["input_template"]),
        }
    if "dead_letter_queue_arn" in config:
        request["DeadLetterConfig"] = {"Arn": str(config["dead_letter_queue_arn"])}
    if "retry_policy" in config:
        retry = config["retry_policy"]
        request["RetryPolicy"] = {
            key: int(retry[source])
            for source, key in (
                ("maximum_event_age_seconds", "MaximumEventAgeInSeconds"),
                ("maximum_retry_attempts", "MaximumRetryAttempts"),
            )
            if source in retry
        }
    for key, aws_key in _TARGET_PARAMETERS.items():
        if key in config:
            request[aws_key] = dict(config[key])
    for key, value in (config.get("aws_parameters") or {}).items():
        if key not in _TARGET_FIELDS:
            raise ManagedServiceError(f"unsupported EventBridge target field {key}")
        request[key] = value
    return request


def _role_arns(config: dict[str, Any]) -> set[str]:
    roles: set[str] = set()
    for rule in config.get("rules") or []:
        if rule.get("role_arn"):
            roles.add(str(rule["role_arn"]))
        for target in rule.get("targets") or []:
            if target.get("role_arn"):
                roles.add(str(target["role_arn"]))
            native_role = (target.get("aws_parameters") or {}).get("RoleArn")
            if native_role:
                roles.add(str(native_role))
    return roles


def _validate_target(target: Any) -> str:
    if not isinstance(target, dict) or not target.get("id") or not target.get("arn"):
        return "each target requires id and arn"
    if len(str(target["id"])) > 64:
        return "target id cannot exceed 64 characters"
    transforms = [key for key in ("input", "input_path", "input_transformer") if key in target]
    if len(transforms) > 1:
        return "input, input_path, and input_transformer are mutually exclusive"
    if "input" in target:
        try:
            _json_value(target["input"], field="input")
        except (TypeError, ValueError) as exc:
            return str(exc)
    if "input_transformer" in target:
        transformer = target["input_transformer"]
        if not isinstance(transformer, dict) or "input_template" not in transformer:
            return "input_transformer requires input_template"
    if "aws_parameters" in target:
        native = target["aws_parameters"]
        if not isinstance(native, dict) or any(key not in _TARGET_FIELDS for key in native):
            return "aws_parameters contains an unsupported EventBridge target field"
    if "retry_policy" in target:
        retry = target["retry_policy"]
        if not isinstance(retry, dict):
            return "retry_policy must be an object"
        for key, minimum, maximum in (
            ("maximum_event_age_seconds", 60, 86400),
            ("maximum_retry_attempts", 0, 185),
        ):
            if key in retry:
                value = retry[key]
                if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                    return f"{key} must be an integer from {minimum} through {maximum}"
    return ""


def _archive_name(bus_name: str, config: Any) -> str:
    archive = config if isinstance(config, dict) else {}
    return _name(str(archive.get("name") or f"{bus_name}-archive"), limit=48)


def _permission_sid(value: str) -> str:
    suffix = _name(value, limit=54)
    return f"astrolift-{suffix}"[:64]


def _bus_name_from_arn(value: str) -> str:
    return value.rsplit("/", 1)[-1]


def _name(value: str, *, limit: int) -> str:
    clean = "".join(char if char.isalnum() or char in "-_." else "-" for char in value)
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip("-_.")
    if not clean:
        raise ManagedServiceError("EventBridge resource name cannot be empty")
    return clean[:limit].rstrip("-_.")


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


def _json_value(value: Any, *, field: str) -> str:
    if isinstance(value, str):
        json.loads(value)
        return value
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except TypeError as exc:
        raise TypeError(f"{field} must be JSON serializable") from exc


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _already_exists(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return "AlreadyExists" in code or "already exists" in str(exc).lower()


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return "NotFound" in code or "not found" in str(exc).lower()


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0) or 0)
    retryable = (
        status >= 500 or code.startswith("Throttl") or code in {"ConcurrentModificationException", "InternalException"}
    )
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [str(exc)], retryable=retryable)
