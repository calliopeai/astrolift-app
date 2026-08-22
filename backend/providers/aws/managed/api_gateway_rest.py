"""Amazon API Gateway REST API managed-service driver."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

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
from aws._naming import iam_role_name
from aws.managed._base import ManagedServiceError, handle_for, parse_handle, tags_for
from aws.session import aws_client

if TYPE_CHECKING:
    from aws.managed.api_gateway_http import ApiGatewayHttpConfig

KIND = "api_gateway"
_DEFAULT_STAGE = "prod"
_STAGE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ApiGatewayRestDriver(ManagedServiceDriver):
    """Own a REST API whose routes are declared by an OpenAPI document.

    OpenAPI is the portable configuration boundary for REST API resources,
    methods, integrations, authorizers, and responses.  The ``rest_api`` and
    ``stage`` fragments expose AWS-native create options without forcing the
    platform to maintain a lossy mirror of API Gateway's surface.
    """

    def __init__(
        self,
        *,
        config: ApiGatewayHttpConfig,
        client: Any | None = None,
        lambda_client: Any | None = None,
    ) -> None:
        self._config = config
        if client is None:
            client = aws_client("apigateway", region=config.region, credential=config.credential)
        if lambda_client is None:
            lambda_client = aws_client("lambda", region=config.region, credential=config.credential)
        self._api = client
        self._lambda = lambda_client

    @driver_op(
        cloud="aws",
        driver="api_gateway_rest",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_rest_api_config"])
        name = self._api_name(spec)
        api_id = ""
        try:
            api = self._find_owned_api(name, spec)
            if api is None:
                request = dict(cfg.get("rest_api") or {})
                request.update(
                    name=name,
                    tags={item["Key"]: item["Value"] for item in tags_for(spec)},
                )
                api = self._api.create_rest_api(**request)
            api_id = str(api["id"])
            self._reconcile(api_id, cfg)
        except Exception as exc:
            handle = handle_for(kind=KIND, resource_id=api_id) if api_id else ""
            return ProvisionResult(False, handle, f"provision REST API: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=api_id),
            f"REST API {api_id} deployed",
            ready=True,
        )

    @driver_op(cloud="aws", driver="api_gateway_rest")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, api_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_rest_api_config"])
        try:
            api = self._api.get_rest_api(restApiId=api_id)
            if not self._is_owned(api):
                return UpdateResult(
                    False,
                    spec.handle,
                    "refusing to update a REST API not owned by Astrolift",
                    ["resource_not_owned"],
                )
            self._reconcile(api_id, cfg)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, f"REST API {api_id} not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update REST API: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"REST API {api_id} reconciled")

    @driver_op(
        cloud="aws",
        driver="api_gateway_rest",
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
        _, api_id = parse_handle(spec.handle)
        try:
            api = self._api.get_rest_api(restApiId=api_id)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"REST API {api_id} already gone")
            return DeprovisionResult(False, spec.handle, f"describe REST API: {exc}", [str(exc)])
        if not self._is_owned(api) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "refusing to delete a REST API not owned by Astrolift",
                ["resource_not_owned"],
                retryable=False,
            )
        protected = bool(
            spec.config.get(
                "deletion_protection",
                getattr(self._config, "deletion_protection_default", False),
            ),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"REST API {api_id} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            self._api.delete_rest_api(restApiId=api_id)
        except Exception as exc:
            if not _not_found(exc):
                return DeprovisionResult(False, spec.handle, f"delete REST API: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"REST API {api_id} deleted")

    @driver_op(cloud="aws", driver="api_gateway_rest")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, api_id = parse_handle(handle.handle)
        try:
            api = self._api.get_rest_api(restApiId=api_id)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", f"REST API {api_id} does not exist")
            return ServiceStatus(handle.handle, "error", f"describe REST API: {exc}")
        if not self._is_owned(api):
            return ServiceStatus(handle.handle, "error", "REST API exists but is not owned by Astrolift")
        return ServiceStatus(handle.handle, "available", f"REST API {api_id} available")

    @driver_op(cloud="aws", driver="api_gateway_rest")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, api_id = parse_handle(handle.handle)
        cfg = config or {}
        stage = str(cfg.get("stage_name") or _DEFAULT_STAGE)
        url = f"https://{api_id}.execute-api.{self._config.region}.amazonaws.com/{stage}"
        grants: list[Grant] = []
        if cfg.get("iam_authorized") or cfg.get("access_mode") == "iam":
            account_id = str(getattr(self._config, "account_id", "") or "*")
            grants.append(
                Grant(
                    resource=(f"arn:aws:execute-api:{self._config.region}:{account_id}:{api_id}/{stage}/*/*"),
                    actions=["execute-api:Invoke"],
                ),
            )
        return Binding(
            env_vars={
                "API_GATEWAY_URL": ValueRef(literal=url),
                "API_GATEWAY_ID": ValueRef(literal=api_id),
                "API_GATEWAY_STAGE": ValueRef(literal=stage),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=grants,
            notes="Amazon API Gateway REST API invoke endpoint.",
        )

    @driver_op(cloud="aws", driver="api_gateway_rest")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "a REST API is reconstructed from its repository-owned OpenAPI document and has no portable snapshot",
        )

    @driver_op(cloud="aws", driver="api_gateway_rest")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "REST API restore is not supported; provision from the OpenAPI source",
            ["not_implemented"],
        )

    @driver_op(cloud="aws", driver="api_gateway_rest", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "oneOf": [
                {"required": ["openapi"]},
                {"required": ["lambda_function_arn"]},
            ],
            "properties": {
                "openapi": {
                    "description": "OpenAPI 2.0 or 3.0 document, as an object or serialized JSON/YAML string.",
                },
                "openapi_mode": {"type": "string", "enum": ["merge", "overwrite"], "default": "overwrite"},
                "openapi_parameters": {"type": "object", "additionalProperties": {"type": "string"}},
                "fail_on_warnings": {"type": "boolean", "default": True},
                "lambda_function_arn": {
                    "type": "string",
                    "description": "Shortcut that generates root and greedy AWS_PROXY Lambda routes.",
                },
                "lambda_function_arns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lambda integrations embedded in OpenAPI that need invoke grants.",
                },
                "rest_api": {
                    "type": "object",
                    "description": "Native boto3 create_rest_api fields except name and tags.",
                },
                "rest_api_patch_operations": {"type": "array", "items": {"type": "object"}},
                "stage_name": {"type": "string", "default": _DEFAULT_STAGE},
                "stage": {
                    "type": "object",
                    "description": "Native boto3 create_stage fields except resource identifiers.",
                },
                "stage_patch_operations": {"type": "array", "items": {"type": "object"}},
                "deployment": {
                    "type": "object",
                    "description": "Native boto3 create_deployment fields except restApiId and stageName.",
                },
                "iam_authorized": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": False},
            },
        }

    @driver_op(cloud="aws", driver="api_gateway_rest", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "API_GATEWAY_URL": "REST API stage invoke URL",
                "API_GATEWAY_ID": "REST API identifier",
                "API_GATEWAY_STAGE": "Deployed stage name",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return ["*"]

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        openapi = cfg.get("openapi")
        lambda_arn = str(cfg.get("lambda_function_arn") or "").strip()
        if openapi is None and not lambda_arn:
            return "REST API requires config.openapi or config.lambda_function_arn"
        if openapi is not None and not isinstance(openapi, (dict, str, bytes)):
            return "config.openapi must be an object, string, or bytes"
        mode = str(cfg.get("openapi_mode") or "overwrite")
        if mode not in {"merge", "overwrite"}:
            return "config.openapi_mode must be merge or overwrite"
        stage = str(cfg.get("stage_name") or _DEFAULT_STAGE)
        if not _STAGE_RE.fullmatch(stage):
            return "config.stage_name must contain only letters, numbers, underscore, or hyphen"
        for key in ("rest_api", "stage", "deployment", "openapi_parameters"):
            if key in cfg and not isinstance(cfg[key], dict):
                return f"config.{key} must be an object"
        for key in ("rest_api_patch_operations", "stage_patch_operations", "lambda_function_arns"):
            if key in cfg and not isinstance(cfg[key], list):
                return f"config.{key} must be an array"
        reserved = {"name", "tags"}.intersection((cfg.get("rest_api") or {}).keys())
        if reserved:
            return f"config.rest_api cannot override Astrolift-owned fields: {', '.join(sorted(reserved))}"
        return ""

    def _api_name(self, spec: ProvisionSpec) -> str:
        return iam_role_name(
            "astrolift",
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
            max_len=128,
        )

    def _find_owned_api(self, name: str, spec: ProvisionSpec) -> dict[str, Any] | None:
        position = ""
        while True:
            request: dict[str, Any] = {"limit": 500}
            if position:
                request["position"] = position
            response = self._api.get_rest_apis(**request)
            for api in response.get("items", []) or []:
                tags = api.get("tags") or {}
                if (
                    api.get("name") == name
                    and tags.get("astrolift.io/managed-by") == "platform"
                    and tags.get("astrolift.io/organization") == spec.organization_slug
                    and tags.get("astrolift.io/app") == spec.app_slug
                    and tags.get("astrolift.io/environment") == spec.environment_name
                ):
                    return api
            position = str(response.get("position") or "")
            if not position:
                return None

    @staticmethod
    def _is_owned(api: dict[str, Any]) -> bool:
        return (api.get("tags") or {}).get("astrolift.io/managed-by") == "platform"

    def _reconcile(self, api_id: str, cfg: dict[str, Any]) -> None:
        patch_operations = list(cfg.get("rest_api_patch_operations") or [])
        if patch_operations:
            self._api.update_rest_api(restApiId=api_id, patchOperations=patch_operations)
        self._api.put_rest_api(
            restApiId=api_id,
            mode=str(cfg.get("openapi_mode") or "overwrite"),
            failOnWarnings=bool(cfg.get("fail_on_warnings", True)),
            parameters={str(key): str(value) for key, value in (cfg.get("openapi_parameters") or {}).items()},
            body=self._openapi_body(cfg),
        )
        deployment_request = dict(cfg.get("deployment") or {})
        deployment_request.pop("restApiId", None)
        deployment_request.pop("stageName", None)
        deployment = self._api.create_deployment(restApiId=api_id, **deployment_request)
        deployment_id = str(deployment["id"])
        self._ensure_stage(api_id, deployment_id, cfg)
        for lambda_arn in self._lambda_arns(cfg):
            self._ensure_invoke_permission(lambda_arn, api_id)

    def _ensure_stage(self, api_id: str, deployment_id: str, cfg: dict[str, Any]) -> None:
        stage_name = str(cfg.get("stage_name") or _DEFAULT_STAGE)
        try:
            self._api.get_stage(restApiId=api_id, stageName=stage_name)
        except Exception as exc:
            if not _not_found(exc):
                raise
            request = dict(cfg.get("stage") or {})
            for key in ("restApiId", "stageName", "deploymentId"):
                request.pop(key, None)
            self._api.create_stage(
                restApiId=api_id,
                stageName=stage_name,
                deploymentId=deployment_id,
                **request,
            )
            return
        operations = [{"op": "replace", "path": "/deploymentId", "value": deployment_id}]
        operations.extend(cfg.get("stage_patch_operations") or [])
        self._api.update_stage(restApiId=api_id, stageName=stage_name, patchOperations=operations)

    def _openapi_body(self, cfg: dict[str, Any]) -> bytes:
        document = cfg.get("openapi")
        if document is None:
            document = self._lambda_openapi(str(cfg["lambda_function_arn"]))
        if isinstance(document, bytes):
            return document
        if isinstance(document, str):
            return document.encode()
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()

    def _lambda_openapi(self, lambda_arn: str) -> dict[str, Any]:
        uri = f"arn:aws:apigateway:{self._config.region}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"
        integration = {
            "type": "aws_proxy",
            "httpMethod": "POST",
            "uri": uri,
        }
        operation = {
            "responses": {"200": {"description": "Lambda proxy response"}},
            "x-amazon-apigateway-integration": integration,
        }
        return {
            "openapi": "3.0.1",
            "info": {"title": "Astrolift managed REST API", "version": "1.0.0"},
            "paths": {
                "/": {"x-amazon-apigateway-any-method": operation},
                "/{proxy+}": {
                    "parameters": [
                        {"name": "proxy", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "x-amazon-apigateway-any-method": operation,
                },
            },
        }

    @staticmethod
    def _lambda_arns(cfg: dict[str, Any]) -> list[str]:
        arns = [str(item).strip() for item in cfg.get("lambda_function_arns") or []]
        shortcut = str(cfg.get("lambda_function_arn") or "").strip()
        if shortcut:
            arns.append(shortcut)
        return list(dict.fromkeys(arn for arn in arns if arn))

    def _ensure_invoke_permission(self, lambda_arn: str, api_id: str) -> None:
        account_id = _account_from_arn(lambda_arn)
        digest = hashlib.sha256(f"{api_id}:{lambda_arn}".encode()).hexdigest()[:16]
        statement_id = f"AstroliftApiGateway{digest}"
        try:
            self._lambda.add_permission(
                FunctionName=lambda_arn,
                StatementId=statement_id,
                Action="lambda:InvokeFunction",
                Principal="apigateway.amazonaws.com",
                SourceArn=(f"arn:aws:execute-api:{self._config.region}:{account_id}:{api_id}/*/*/*"),
            )
        except Exception as exc:
            conflict = getattr(getattr(self._lambda, "exceptions", None), "ResourceConflictException", ())
            if not conflict or not isinstance(exc, conflict):
                raise


def _account_from_arn(arn: str) -> str:
    parts = arn.split(":")
    return parts[4] if len(parts) > 4 else ""


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    return code in {"NotFoundException", "ResourceNotFoundException"} or "not found" in str(exc).lower()
