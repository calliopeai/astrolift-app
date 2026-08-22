"""AWS Step Functions Standard and Express managed-service drivers."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
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
from aws._naming import iam_role_name
from aws.managed._base import ManagedServiceError, handle_for, parse_handle, tags_for
from aws.session import aws_client

KIND = "workflow_engine"
_ALIAS_MARKER = "Astrolift managed alias"


@dataclass(frozen=True)
class StepFunctionsConfig(CredentialedConfig):
    region: str
    state_machine_name_prefix: str = "astrolift"
    deletion_protection_default: bool = True


class _StepFunctionsDriver(ManagedServiceDriver):
    WORKFLOW_TYPE = "STANDARD"
    VARIANT = "step_functions_standard"

    def __init__(self, *, config: StepFunctionsConfig, client: Any | None = None) -> None:
        self._config = config
        if client is None:
            client = aws_client("stepfunctions", region=config.region, credential=config.credential)
        self._sfn = client

    @driver_op(
        cloud="aws",
        driver="step_functions",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_step_functions_config"])
        definition = _definition(cfg["definition"])
        definition_error = self._validate_definition(definition)
        if definition_error:
            return ProvisionResult(False, "", definition_error, ["invalid_state_machine_definition"])
        name = self._name(spec)
        state_machine_arn = ""
        try:
            existing = self._find_owned(name, spec)
            request = self._state_machine_request(cfg, definition)
            if existing is None:
                response = self._sfn.create_state_machine(
                    name=name,
                    type=self.WORKFLOW_TYPE,
                    tags=[{"key": item["Key"], "value": item["Value"]} for item in tags_for(spec)],
                    **request,
                )
                state_machine_arn = str(response["stateMachineArn"])
                version_arn = str(response.get("stateMachineVersionArn") or "")
            else:
                state_machine_arn = str(existing["stateMachineArn"])
                response = self._sfn.update_state_machine(
                    stateMachineArn=state_machine_arn,
                    **request,
                )
                version_arn = str(response.get("stateMachineVersionArn") or "")
            self._ensure_alias(state_machine_arn, version_arn, cfg)
        except Exception as exc:
            handle = handle_for(kind=KIND, resource_id=state_machine_arn) if state_machine_arn else ""
            return ProvisionResult(False, handle, f"provision Step Functions state machine: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=state_machine_arn),
            f"{self.WORKFLOW_TYPE.lower()} state machine {name} available",
            ready=True,
        )

    @driver_op(cloud="aws", driver="step_functions")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, state_machine_arn = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_step_functions_config"])
        definition = _definition(cfg["definition"])
        definition_error = self._validate_definition(definition)
        if definition_error:
            return UpdateResult(False, spec.handle, definition_error, ["invalid_state_machine_definition"])
        try:
            machine = self._sfn.describe_state_machine(stateMachineArn=state_machine_arn)
            if str(machine.get("type") or "") != self.WORKFLOW_TYPE:
                return UpdateResult(
                    False,
                    spec.handle,
                    f"state machine is {machine.get('type')}, not immutable type {self.WORKFLOW_TYPE}",
                    ["workflow_type_mismatch"],
                )
            if not self._is_owned(state_machine_arn):
                return UpdateResult(
                    False,
                    spec.handle,
                    "refusing to update a state machine not owned by Astrolift",
                    ["resource_not_owned"],
                )
            response = self._sfn.update_state_machine(
                stateMachineArn=state_machine_arn,
                **self._state_machine_request(cfg, definition),
            )
            self._ensure_alias(
                state_machine_arn,
                str(response.get("stateMachineVersionArn") or ""),
                cfg,
            )
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, "state machine not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update Step Functions state machine: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, "Step Functions state machine reconciled")

    @driver_op(
        cloud="aws",
        driver="step_functions",
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
        del delete_data
        _, state_machine_arn = parse_handle(spec.handle)
        try:
            machine = self._sfn.describe_state_machine(stateMachineArn=state_machine_arn)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, "state machine already gone")
            return DeprovisionResult(False, spec.handle, f"describe state machine: {exc}", [str(exc)])
        if not self._is_owned(state_machine_arn) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "refusing to delete a state machine not owned by Astrolift",
                ["resource_not_owned"],
                retryable=False,
            )
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
                "Step Functions state machine has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if str(machine.get("type") or self.WORKFLOW_TYPE) == "STANDARD":
            try:
                running = self._running_executions(state_machine_arn)
            except Exception as exc:
                return DeprovisionResult(False, spec.handle, f"list running executions: {exc}", [str(exc)])
            if running and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"state machine has {len(running)} running execution(s); force_destroy is required",
                    ["running_executions"],
                    retryable=False,
                )
            if force_destroy:
                for execution_arn in running:
                    try:
                        self._sfn.stop_execution(
                            executionArn=execution_arn,
                            error="AstroliftForceDestroy",
                            cause="Managed state machine deprovisioned with force_destroy=true",
                        )
                    except Exception as exc:
                        if not _not_found(exc):
                            return DeprovisionResult(
                                False,
                                spec.handle,
                                f"stop execution {execution_arn}: {exc}",
                                [str(exc)],
                            )
        try:
            self._sfn.delete_state_machine(stateMachineArn=state_machine_arn)
        except Exception as exc:
            if not _not_found(exc):
                return DeprovisionResult(False, spec.handle, f"delete state machine: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, "Step Functions state machine deletion started")

    @driver_op(cloud="aws", driver="step_functions")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, state_machine_arn = parse_handle(handle.handle)
        try:
            machine = self._sfn.describe_state_machine(stateMachineArn=state_machine_arn)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", "state machine does not exist")
            return ServiceStatus(handle.handle, "error", f"describe state machine: {exc}")
        status = str(machine.get("status") or "ACTIVE")
        state = {"ACTIVE": "available", "DELETING": "deprovisioning"}.get(status, "error")
        return ServiceStatus(
            handle.handle,
            state,
            f"{str(machine.get('type') or self.WORKFLOW_TYPE).lower()} state machine {status.lower()}",
        )

    @driver_op(cloud="aws", driver="step_functions")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, state_machine_arn = parse_handle(handle.handle)
        machine = self._sfn.describe_state_machine(stateMachineArn=state_machine_arn)
        machine_type = str(machine.get("type") or self.WORKFLOW_TYPE)
        name = _name_from_arn(state_machine_arn)
        access_mode = str((config or {}).get("access_mode") or "invoke")
        actions = ["states:StartExecution"]
        if machine_type == "EXPRESS":
            actions.append("states:StartSyncExecution")
        if access_mode in {"observe", "manage"} and machine_type == "STANDARD":
            actions.append("states:ListExecutions")
        grants = [Grant(resource=state_machine_arn, actions=actions)]
        if access_mode in {"observe", "manage"} and machine_type == "STANDARD":
            execution_arn = _execution_resource(state_machine_arn)
            grants.append(
                Grant(
                    resource=execution_arn,
                    actions=["states:DescribeExecution", "states:GetExecutionHistory"],
                ),
            )
        if access_mode == "manage" and machine_type == "STANDARD":
            grants[-1].actions.extend(["states:RedriveExecution", "states:StopExecution"])
        console_url = (
            f"https://{self._config.region}.console.aws.amazon.com/states/home?region="
            f"{self._config.region}#/statemachines/view/{state_machine_arn}"
        )
        return Binding(
            env_vars={
                "WORKFLOW_ENGINE_ID": ValueRef(literal=name),
                "WORKFLOW_ENGINE_ARN": ValueRef(literal=state_machine_arn),
                "WORKFLOW_ENGINE_REGION": ValueRef(literal=self._config.region),
                "WORKFLOW_ENGINE_TYPE": ValueRef(literal=machine_type),
                "STEP_FUNCTIONS_STATE_MACHINE_ARN": ValueRef(literal=state_machine_arn),
                "STEP_FUNCTIONS_CONSOLE_URL": ValueRef(literal=console_url),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=grants,
            notes=f"AWS Step Functions {machine_type.lower()} state machine.",
        )

    @driver_op(cloud="aws", driver="step_functions")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        _, state_machine_arn = parse_handle(handle.handle)
        try:
            response = self._sfn.publish_state_machine_version(
                stateMachineArn=state_machine_arn,
                description="Astrolift managed-service snapshot",
            )
        except Exception as exc:
            raise ManagedServiceError(f"publish Step Functions version: {exc}") from exc
        created_at = response.get("creationDate")
        if isinstance(created_at, datetime):
            timestamp = created_at.astimezone(UTC).isoformat()
        else:
            timestamp = datetime.now(UTC).isoformat()
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=str(response["stateMachineVersionArn"]),
            created_at=timestamp,
        )

    @driver_op(cloud="aws", driver="step_functions")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        try:
            source = self._sfn.describe_state_machine(stateMachineArn=snapshot.snapshot_id)
        except Exception as exc:
            return ProvisionResult(False, "", f"describe Step Functions version: {exc}", [str(exc)])
        source_type = str(source.get("type") or "")
        if source_type != self.WORKFLOW_TYPE:
            message = f"cannot restore {source_type} snapshot with {self.WORKFLOW_TYPE} driver"
            return ProvisionResult(False, "", message, ["workflow_type_mismatch"])
        config = dict(target.config or {})
        config.update(
            definition=source["definition"],
            role_arn=source["roleArn"],
            logging_configuration=source.get("loggingConfiguration") or {"level": "OFF"},
            tracing_configuration=source.get("tracingConfiguration") or {"enabled": False},
            encryption_configuration=source.get("encryptionConfiguration") or {"type": "AWS_OWNED_KEY"},
        )
        config.pop("alias", None)
        config["publish"] = False
        return self.provision(replace(target, config=config))

    @driver_op(cloud="aws", driver="step_functions", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["definition", "role_arn"],
            "properties": {
                "definition": {
                    "description": "Amazon States Language document as an object or serialized JSON string.",
                },
                "role_arn": {
                    "type": "string",
                    "description": "IAM role assumed by Step Functions for service integrations.",
                },
                "state_machine": {
                    "type": "object",
                    "description": "Native create/update_state_machine fields except identity, type, and tags.",
                },
                "logging_configuration": {"type": "object"},
                "tracing_configuration": {"type": "object"},
                "encryption_configuration": {"type": "object"},
                "publish": {"type": "boolean", "default": False},
                "version_description": {"type": "string"},
                "alias": {
                    "type": "object",
                    "required": ["name"],
                    "description": "Managed alias; defaults to routing 100% to the newly published version.",
                },
                "access_mode": {
                    "type": "string",
                    "enum": ["invoke", "observe", "manage"],
                    "default": "invoke",
                },
                "deletion_protection": {"type": "boolean", "default": True},
            },
        }

    @driver_op(cloud="aws", driver="step_functions", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "WORKFLOW_ENGINE_ID": "State machine name",
                "WORKFLOW_ENGINE_ARN": "State machine ARN",
                "WORKFLOW_ENGINE_REGION": "AWS region",
                "WORKFLOW_ENGINE_TYPE": "STANDARD or EXPRESS",
                "STEP_FUNCTIONS_STATE_MACHINE_ARN": "AWS-native state machine ARN alias",
                "STEP_FUNCTIONS_CONSOLE_URL": "AWS console state machine URL",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return ["*"]

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        if "definition" not in cfg:
            return "Step Functions requires config.definition"
        try:
            definition = _definition(cfg["definition"])
            parsed = json.loads(definition)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return f"config.definition must be valid JSON: {exc}"
        if not isinstance(parsed, dict):
            return "config.definition must encode an Amazon States Language object"
        if not str(cfg.get("role_arn") or "").startswith("arn:"):
            return "Step Functions requires config.role_arn as an IAM role ARN"
        for key in (
            "state_machine",
            "logging_configuration",
            "tracing_configuration",
            "encryption_configuration",
            "alias",
        ):
            if key in cfg and not isinstance(cfg[key], dict):
                return f"config.{key} must be an object"
        reserved = {
            "name",
            "definition",
            "roleArn",
            "type",
            "tags",
            "stateMachineArn",
            "loggingConfiguration",
            "tracingConfiguration",
            "encryptionConfiguration",
            "publish",
            "versionDescription",
        }.intersection((cfg.get("state_machine") or {}).keys())
        if reserved:
            return f"config.state_machine cannot override Astrolift-owned fields: {', '.join(sorted(reserved))}"
        alias = cfg.get("alias") or {}
        if alias:
            if not bool(cfg.get("publish", False)):
                return "config.alias requires config.publish=true"
            if not str(alias.get("name") or "").strip():
                return "config.alias.name is required"
            reserved_alias = {"stateMachineAliasArn", "description"}.intersection(alias)
            if reserved_alias:
                return f"config.alias cannot override Astrolift-owned fields: {', '.join(sorted(reserved_alias))}"
        if cfg.get("version_description") and not bool(cfg.get("publish", False)):
            return "config.version_description requires config.publish=true"
        access_mode = str(cfg.get("access_mode") or "invoke")
        if access_mode not in {"invoke", "observe", "manage"}:
            return "config.access_mode must be invoke, observe, or manage"
        return ""

    def _validate_definition(self, definition: str) -> str:
        validate = getattr(self._sfn, "validate_state_machine_definition", None)
        if validate is None:
            return ""
        try:
            response = validate(
                definition=definition,
                type=self.WORKFLOW_TYPE,
                severity="ERROR",
                maxResults=100,
            )
        except Exception as exc:
            return f"validate state machine definition: {exc}"
        if response.get("result") == "OK":
            return ""
        diagnostics = response.get("diagnostics") or []
        details = "; ".join(
            str(item.get("message") or item.get("code") or "invalid definition") for item in diagnostics
        )
        return f"invalid state machine definition: {details or 'AWS validation failed'}"

    def _name(self, spec: ProvisionSpec) -> str:
        return iam_role_name(
            self._config.state_machine_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
            max_len=80,
        )

    def _find_owned(self, name: str, spec: ProvisionSpec) -> dict[str, Any] | None:
        token = ""
        while True:
            request: dict[str, Any] = {"maxResults": 100}
            if token:
                request["nextToken"] = token
            response = self._sfn.list_state_machines(**request)
            for machine in response.get("stateMachines", []) or []:
                if machine.get("name") != name or machine.get("type") != self.WORKFLOW_TYPE:
                    continue
                arn = str(machine["stateMachineArn"])
                tags = self._tags(arn)
                if (
                    tags.get("astrolift.io/managed-by") == "platform"
                    and tags.get("astrolift.io/organization") == spec.organization_slug
                    and tags.get("astrolift.io/app") == spec.app_slug
                    and tags.get("astrolift.io/environment") == spec.environment_name
                ):
                    return dict(machine)
            token = str(response.get("nextToken") or "")
            if not token:
                return None

    def _is_owned(self, state_machine_arn: str) -> bool:
        return self._tags(state_machine_arn).get("astrolift.io/managed-by") == "platform"

    def _tags(self, state_machine_arn: str) -> dict[str, str]:
        response = self._sfn.list_tags_for_resource(resourceArn=state_machine_arn)
        return {str(item.get("key")): str(item.get("value")) for item in response.get("tags", []) or []}

    def _state_machine_request(self, cfg: dict[str, Any], definition: str) -> dict[str, Any]:
        request = dict(cfg.get("state_machine") or {})
        request.update(
            definition=definition,
            roleArn=str(cfg["role_arn"]),
            publish=bool(cfg.get("publish", False)),
        )
        if "logging_configuration" in cfg:
            request["loggingConfiguration"] = dict(cfg["logging_configuration"])
        if "tracing_configuration" in cfg:
            request["tracingConfiguration"] = dict(cfg["tracing_configuration"])
        if "encryption_configuration" in cfg:
            request["encryptionConfiguration"] = dict(cfg["encryption_configuration"])
        if cfg.get("version_description"):
            request["versionDescription"] = str(cfg["version_description"])
        return request

    def _ensure_alias(self, state_machine_arn: str, published_version_arn: str, cfg: dict[str, Any]) -> None:
        alias = cfg.get("alias") or {}
        if not alias:
            return
        if not published_version_arn:
            raise ManagedServiceError("alias reconciliation requires a newly published state machine version")
        name = str(alias["name"])
        routing = alias.get("routing_configuration") or [
            {"stateMachineVersionArn": published_version_arn, "weight": 100},
        ]
        routing = [
            {
                **route,
                "stateMachineVersionArn": (
                    published_version_arn
                    if route.get("stateMachineVersionArn") == "$PUBLISHED"
                    else route.get("stateMachineVersionArn")
                ),
            }
            for route in routing
        ]
        aliases = self._aliases(state_machine_arn)
        current = next(
            (item for item in aliases if _alias_name(str(item.get("stateMachineAliasArn") or "")) == name),
            None,
        )
        request = {
            "description": f"{_ALIAS_MARKER}: {name}",
            "routingConfiguration": routing,
        }
        if current is None:
            self._sfn.create_state_machine_alias(
                name=name,
                **request,
            )
        else:
            details = self._sfn.describe_state_machine_alias(
                stateMachineAliasArn=str(current["stateMachineAliasArn"]),
            )
            if not str(details.get("description") or "").startswith(_ALIAS_MARKER):
                raise ManagedServiceError(f"alias {name!r} exists but is not owned by Astrolift")
            self._sfn.update_state_machine_alias(
                stateMachineAliasArn=str(current["stateMachineAliasArn"]),
                **request,
            )

    def _aliases(self, state_machine_arn: str) -> list[dict[str, Any]]:
        aliases: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"stateMachineArn": state_machine_arn, "maxResults": 100}
            if token:
                request["nextToken"] = token
            response = self._sfn.list_state_machine_aliases(**request)
            aliases.extend(response.get("stateMachineAliases", []) or [])
            token = str(response.get("nextToken") or "")
            if not token:
                return aliases

    def _running_executions(self, state_machine_arn: str) -> list[str]:
        executions: list[str] = []
        token = ""
        while True:
            request: dict[str, Any] = {
                "stateMachineArn": state_machine_arn,
                "statusFilter": "RUNNING",
                "maxResults": 100,
            }
            if token:
                request["nextToken"] = token
            response = self._sfn.list_executions(**request)
            executions.extend(str(item["executionArn"]) for item in response.get("executions", []) or [])
            token = str(response.get("nextToken") or "")
            if not token:
                return executions


class StepFunctionsStandardDriver(_StepFunctionsDriver):
    WORKFLOW_TYPE = "STANDARD"
    VARIANT = "step_functions_standard"


class StepFunctionsExpressDriver(_StepFunctionsDriver):
    WORKFLOW_TYPE = "EXPRESS"
    VARIANT = "step_functions_express"


def _definition(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, str):
        return value
    raise TypeError("definition must be an object or JSON string")


def _name_from_arn(arn: str) -> str:
    marker = ":stateMachine:"
    return arn.split(marker, 1)[1].split(":", 1)[0] if marker in arn else arn.rsplit(":", 1)[-1]


def _alias_name(arn: str) -> str:
    return arn.rsplit(":", 1)[-1]


def _execution_resource(state_machine_arn: str) -> str:
    prefix, _, name = state_machine_arn.partition(":stateMachine:")
    return f"{prefix}:execution:{name}:*"


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    return code in {"ResourceNotFound", "ResourceNotFoundException", "StateMachineDoesNotExist"} or (
        "does not exist" in str(exc).lower()
    )
