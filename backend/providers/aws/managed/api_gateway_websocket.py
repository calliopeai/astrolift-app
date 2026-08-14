"""Amazon API Gateway WebSocket API managed-service driver."""

from __future__ import annotations

import hashlib
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

if TYPE_CHECKING:
    from aws.managed.api_gateway_http import ApiGatewayHttpConfig

KIND = "api_gateway"
_DEFAULT_STAGE = "prod"
_STAGE_RE = re.compile(r"^[A-Za-z0-9_$-]{1,128}$")
_INTEGRATION_MARKER = "Astrolift managed integration:"
_ROUTE_MARKER = "Astrolift-"


class ApiGatewayWebSocketDriver(ManagedServiceDriver):
    """Own a WebSocket API, its integrations, routes, deployment, and stage."""

    def __init__(
        self,
        *,
        config: ApiGatewayHttpConfig,
        client: Any | None = None,
        lambda_client: Any | None = None,
    ) -> None:
        self._config = config
        if client is None:
            import boto3

            client = boto3.client("apigatewayv2", region_name=config.region)
        if lambda_client is None:
            import boto3

            lambda_client = boto3.client("lambda", region_name=config.region)
        self._api = client
        self._lambda = lambda_client

    @driver_op(
        cloud="aws",
        driver="api_gateway_websocket",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_websocket_api_config"])
        name = self._api_name(spec)
        api_id = ""
        try:
            api = self._find_owned_api(name, spec)
            if api is None:
                request = dict(cfg.get("api") or {})
                request.update(
                    Name=name,
                    ProtocolType="WEBSOCKET",
                    RouteSelectionExpression=str(cfg.get("route_selection_expression") or "$request.body.action"),
                    Tags={item["Key"]: item["Value"] for item in tags_for(spec)},
                )
                api = self._api.create_api(**request)
            api_id = str(api["ApiId"])
            self._reconcile(api_id, cfg)
        except Exception as exc:
            handle = handle_for(kind=KIND, resource_id=api_id) if api_id else ""
            return ProvisionResult(False, handle, f"provision WebSocket API: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=api_id),
            f"WebSocket API {api_id} deployed",
            ready=True,
        )

    @driver_op(cloud="aws", driver="api_gateway_websocket")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, api_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_websocket_api_config"])
        try:
            api = self._api.get_api(ApiId=api_id)
            if not self._is_owned(api):
                return UpdateResult(
                    False,
                    spec.handle,
                    "refusing to update a WebSocket API not owned by Astrolift",
                    ["resource_not_owned"],
                )
            request = dict(cfg.get("api_update") or {})
            request["RouteSelectionExpression"] = str(cfg.get("route_selection_expression") or "$request.body.action")
            self._api.update_api(ApiId=api_id, **request)
            self._reconcile(api_id, cfg)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, f"WebSocket API {api_id} not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update WebSocket API: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"WebSocket API {api_id} reconciled")

    @driver_op(
        cloud="aws",
        driver="api_gateway_websocket",
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
            api = self._api.get_api(ApiId=api_id)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"WebSocket API {api_id} already gone")
            return DeprovisionResult(False, spec.handle, f"describe WebSocket API: {exc}", [str(exc)])
        if not self._is_owned(api) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "refusing to delete a WebSocket API not owned by Astrolift",
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
                f"WebSocket API {api_id} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            self._api.delete_api(ApiId=api_id)
        except Exception as exc:
            if not _not_found(exc):
                return DeprovisionResult(False, spec.handle, f"delete WebSocket API: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"WebSocket API {api_id} deleted")

    @driver_op(cloud="aws", driver="api_gateway_websocket")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, api_id = parse_handle(handle.handle)
        try:
            api = self._api.get_api(ApiId=api_id)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", f"WebSocket API {api_id} does not exist")
            return ServiceStatus(handle.handle, "error", f"describe WebSocket API: {exc}")
        if not self._is_owned(api):
            return ServiceStatus(handle.handle, "error", "WebSocket API exists but is not owned by Astrolift")
        return ServiceStatus(handle.handle, "available", f"WebSocket API {api_id} available")

    @driver_op(cloud="aws", driver="api_gateway_websocket")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, api_id = parse_handle(handle.handle)
        cfg = config or {}
        stage = str(cfg.get("stage_name") or _DEFAULT_STAGE)
        api = self._api.get_api(ApiId=api_id)
        endpoint = str(api.get("ApiEndpoint") or "")
        if not endpoint:
            endpoint = f"wss://{api_id}.execute-api.{self._config.region}.amazonaws.com"
        endpoint = endpoint.rstrip("/")
        if stage != "$default":
            endpoint = f"{endpoint}/{stage}"
        management = endpoint.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
        account_id = str(getattr(self._config, "account_id", "") or _first_account_id(cfg) or "*")
        return Binding(
            env_vars={
                "API_GATEWAY_URL": ValueRef(literal=endpoint),
                "API_GATEWAY_ID": ValueRef(literal=api_id),
                "API_GATEWAY_STAGE": ValueRef(literal=stage),
                "WEBSOCKET_URL": ValueRef(literal=endpoint),
                "WEBSOCKET_MANAGEMENT_URL": ValueRef(literal=management),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=[
                Grant(
                    resource=(
                        f"arn:aws:execute-api:{self._config.region}:{account_id}:{api_id}/{stage}/POST/@connections/*"
                    ),
                    actions=["execute-api:ManageConnections"],
                ),
            ],
            notes="WebSocket connect URL and HTTPS callback-management endpoint.",
        )

    @driver_op(cloud="aws", driver="api_gateway_websocket")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "a WebSocket API is reconstructed from its repository-owned configuration and has no portable snapshot",
        )

    @driver_op(cloud="aws", driver="api_gateway_websocket")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "WebSocket API restore is not supported; provision from source configuration",
            ["not_implemented"],
        )

    @driver_op(cloud="aws", driver="api_gateway_websocket", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "oneOf": [
                {"required": ["lambda_function_arn"]},
                {"required": ["integrations", "routes"]},
            ],
            "properties": {
                "lambda_function_arn": {
                    "type": "string",
                    "description": "Shortcut that binds $connect, $disconnect, and $default to one Lambda.",
                },
                "route_selection_expression": {
                    "type": "string",
                    "default": "$request.body.action",
                },
                "api": {
                    "type": "object",
                    "description": "Native boto3 create_api fields except Astrolift-owned identity fields.",
                },
                "api_update": {
                    "type": "object",
                    "description": "Native boto3 update_api fields except ApiId and ProtocolType.",
                },
                "integrations": {
                    "type": "array",
                    "description": "Native integration requests; Description is reserved for ownership metadata.",
                    "items": {
                        "type": "object",
                        "required": ["key"],
                        "properties": {
                            "key": {"type": "string"},
                            "lambda_function_arn": {"type": "string"},
                            "request": {"type": "object"},
                        },
                    },
                },
                "routes": {
                    "type": "array",
                    "description": "Native route requests; identity, target, and OperationName are reserved.",
                    "items": {
                        "type": "object",
                        "required": ["route_key", "integration"],
                        "properties": {
                            "route_key": {"type": "string"},
                            "integration": {"type": "string"},
                            "request": {"type": "object"},
                        },
                    },
                },
                "stage_name": {"type": "string", "default": _DEFAULT_STAGE},
                "stage": {
                    "type": "object",
                    "description": "Native boto3 create_stage/update_stage fields except identifiers.",
                },
                "deletion_protection": {"type": "boolean", "default": False},
            },
        }

    @driver_op(cloud="aws", driver="api_gateway_websocket", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "API_GATEWAY_URL": "WebSocket connect URL",
                "API_GATEWAY_ID": "WebSocket API identifier",
                "API_GATEWAY_STAGE": "Deployed stage name",
                "WEBSOCKET_URL": "WebSocket connect URL",
                "WEBSOCKET_MANAGEMENT_URL": "HTTPS callback-management endpoint",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return ["*"]

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        shortcut = str(cfg.get("lambda_function_arn") or "").strip()
        integrations = cfg.get("integrations")
        routes = cfg.get("routes")
        if shortcut and (integrations is not None or routes is not None):
            return "config.lambda_function_arn shortcut cannot be combined with integrations or routes"
        if not shortcut and (not isinstance(integrations, list) or not isinstance(routes, list)):
            return "WebSocket API requires config.lambda_function_arn or integrations and routes arrays"
        for key in ("api", "api_update", "stage"):
            if key in cfg and not isinstance(cfg[key], dict):
                return f"config.{key} must be an object"
        stage_name = str(cfg.get("stage_name") or _DEFAULT_STAGE)
        if not _STAGE_RE.fullmatch(stage_name):
            return "config.stage_name contains unsupported characters"
        reserved_api = {"Name", "ProtocolType", "RouteSelectionExpression", "Tags"}.intersection(
            (cfg.get("api") or {}).keys(),
        )
        if reserved_api:
            return f"config.api cannot override Astrolift-owned fields: {', '.join(sorted(reserved_api))}"
        reserved_update = {"ApiId", "ProtocolType", "RouteSelectionExpression"}.intersection(
            (cfg.get("api_update") or {}).keys(),
        )
        if reserved_update:
            return f"config.api_update cannot override Astrolift-owned fields: {', '.join(sorted(reserved_update))}"
        if shortcut:
            return ""
        integration_keys: set[str] = set()
        for index, integration in enumerate(integrations):
            if not isinstance(integration, dict):
                return f"config.integrations[{index}] must be an object"
            key = str(integration.get("key") or "").strip()
            request = integration.get("request") or {}
            if not key or key in integration_keys:
                return "every WebSocket integration key must be non-empty and unique"
            if not isinstance(request, dict):
                return f"config.integrations[{index}].request must be an object"
            reserved = {"ApiId", "IntegrationId", "Description"}.intersection(request)
            if reserved:
                return (
                    f"config.integrations[{index}].request cannot override Astrolift-owned fields: "
                    f"{', '.join(sorted(reserved))}"
                )
            if not integration.get("lambda_function_arn") and not request.get("IntegrationType"):
                return f"config.integrations[{index}] requires lambda_function_arn or request.IntegrationType"
            integration_keys.add(key)
        route_keys: set[str] = set()
        for index, route in enumerate(routes):
            if not isinstance(route, dict):
                return f"config.routes[{index}] must be an object"
            route_key = str(route.get("route_key") or "").strip()
            integration_key = str(route.get("integration") or "").strip()
            if not route_key or route_key in route_keys:
                return "every WebSocket route_key must be non-empty and unique"
            if integration_key not in integration_keys:
                return f"config.routes[{index}].integration references unknown key {integration_key!r}"
            if not isinstance(route.get("request") or {}, dict):
                return f"config.routes[{index}].request must be an object"
            reserved = {"ApiId", "RouteId", "RouteKey", "Target", "OperationName"}.intersection(
                route.get("request") or {},
            )
            if reserved:
                return (
                    f"config.routes[{index}].request cannot override Astrolift-owned fields: "
                    f"{', '.join(sorted(reserved))}"
                )
            route_keys.add(route_key)
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
        token = ""
        while True:
            request: dict[str, Any] = {"MaxResults": "100"}
            if token:
                request["NextToken"] = token
            response = self._api.get_apis(**request)
            for api in response.get("Items", []) or []:
                tags = api.get("Tags") or {}
                if (
                    api.get("Name") == name
                    and api.get("ProtocolType") == "WEBSOCKET"
                    and tags.get("astrolift.io/managed-by") == "platform"
                    and tags.get("astrolift.io/organization") == spec.organization_slug
                    and tags.get("astrolift.io/app") == spec.app_slug
                    and tags.get("astrolift.io/environment") == spec.environment_name
                ):
                    return api
            token = str(response.get("NextToken") or "")
            if not token:
                return None

    @staticmethod
    def _is_owned(api: dict[str, Any]) -> bool:
        return (api.get("Tags") or {}).get("astrolift.io/managed-by") == "platform"

    def _reconcile(self, api_id: str, cfg: dict[str, Any]) -> None:
        integrations, routes = self._declarations(cfg)
        desired_integrations, stale_integrations = self._reconcile_integrations(api_id, integrations)
        self._reconcile_routes(api_id, routes, desired_integrations)
        for integration_id in stale_integrations:
            self._api.delete_integration(ApiId=api_id, IntegrationId=integration_id)
        self._ensure_stage(api_id, cfg)
        for lambda_arn in _lambda_arns(integrations):
            self._ensure_invoke_permission(lambda_arn, api_id)

    def _declarations(self, cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        lambda_arn = str(cfg.get("lambda_function_arn") or "").strip()
        if not lambda_arn:
            return list(cfg.get("integrations") or []), list(cfg.get("routes") or [])
        integrations = [{"key": "lambda", "lambda_function_arn": lambda_arn, "request": {}}]
        routes = [
            {"route_key": route_key, "integration": "lambda", "request": {}}
            for route_key in ("$connect", "$disconnect", "$default")
        ]
        return integrations, routes

    def _reconcile_integrations(
        self,
        api_id: str,
        declarations: list[dict[str, Any]],
    ) -> tuple[dict[str, str], list[str]]:
        existing = self._integrations(api_id)
        managed = {
            str(item.get("Description") or "").removeprefix(_INTEGRATION_MARKER).strip(): item
            for item in existing
            if str(item.get("Description") or "").startswith(_INTEGRATION_MARKER)
        }
        desired: dict[str, str] = {}
        for declaration in declarations:
            key = str(declaration["key"])
            request = dict(declaration.get("request") or {})
            for reserved in ("ApiId", "IntegrationId"):
                request.pop(reserved, None)
            request["Description"] = f"{_INTEGRATION_MARKER} {key}"
            lambda_arn = str(declaration.get("lambda_function_arn") or "").strip()
            if lambda_arn:
                request.setdefault("IntegrationType", "AWS_PROXY")
                request.setdefault("IntegrationMethod", "POST")
                request.setdefault("IntegrationUri", self._lambda_uri(lambda_arn))
            current = managed.get(key)
            if current is None:
                response = self._api.create_integration(ApiId=api_id, **request)
                desired[key] = str(response["IntegrationId"])
            else:
                integration_id = str(current["IntegrationId"])
                self._api.update_integration(ApiId=api_id, IntegrationId=integration_id, **request)
                desired[key] = integration_id
        stale = [str(item["IntegrationId"]) for key, item in managed.items() if key not in desired]
        return desired, stale

    def _reconcile_routes(
        self,
        api_id: str,
        declarations: list[dict[str, Any]],
        integrations: dict[str, str],
    ) -> None:
        existing = {str(item.get("RouteKey") or ""): item for item in self._routes(api_id)}
        desired_keys: set[str] = set()
        for declaration in declarations:
            route_key = str(declaration["route_key"])
            desired_keys.add(route_key)
            request = dict(declaration.get("request") or {})
            for reserved in ("ApiId", "RouteId", "RouteKey", "Target", "OperationName"):
                request.pop(reserved, None)
            request.update(
                RouteKey=route_key,
                Target=f"integrations/{integrations[str(declaration['integration'])]}",
                OperationName=_route_marker(route_key),
            )
            current = existing.get(route_key)
            if current is None:
                self._api.create_route(ApiId=api_id, **request)
            elif str(current.get("OperationName") or "").startswith(_ROUTE_MARKER):
                self._api.update_route(ApiId=api_id, RouteId=str(current["RouteId"]), **request)
            else:
                raise ManagedServiceError(
                    f"route {route_key!r} already exists but is not owned by Astrolift",
                )
        for route_key, route in existing.items():
            if route_key not in desired_keys and str(route.get("OperationName") or "").startswith(_ROUTE_MARKER):
                self._api.delete_route(ApiId=api_id, RouteId=str(route["RouteId"]))

    def _ensure_stage(self, api_id: str, cfg: dict[str, Any]) -> None:
        stage_name = str(cfg.get("stage_name") or _DEFAULT_STAGE)
        request = dict(cfg.get("stage") or {})
        for reserved in ("ApiId", "StageName", "DeploymentId"):
            request.pop(reserved, None)
        auto_deploy = bool(request.get("AutoDeploy", True))
        request["AutoDeploy"] = auto_deploy
        if not auto_deploy:
            deployment = self._api.create_deployment(ApiId=api_id)
            request["DeploymentId"] = str(deployment["DeploymentId"])
        try:
            self._api.get_stage(ApiId=api_id, StageName=stage_name)
        except Exception as exc:
            if not _not_found(exc):
                raise
            self._api.create_stage(ApiId=api_id, StageName=stage_name, **request)
            return
        self._api.update_stage(ApiId=api_id, StageName=stage_name, **request)

    def _integrations(self, api_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"ApiId": api_id, "MaxResults": "100"}
            if token:
                request["NextToken"] = token
            response = self._api.get_integrations(**request)
            items.extend(response.get("Items", []) or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return items

    def _routes(self, api_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"ApiId": api_id, "MaxResults": "100"}
            if token:
                request["NextToken"] = token
            response = self._api.get_routes(**request)
            items.extend(response.get("Items", []) or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return items

    def _lambda_uri(self, lambda_arn: str) -> str:
        return f"arn:aws:apigateway:{self._config.region}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"

    def _ensure_invoke_permission(self, lambda_arn: str, api_id: str) -> None:
        account_id = _account_from_arn(lambda_arn)
        digest = hashlib.sha256(f"{api_id}:{lambda_arn}".encode()).hexdigest()[:16]
        try:
            self._lambda.add_permission(
                FunctionName=lambda_arn,
                StatementId=f"AstroliftApiGateway{digest}",
                Action="lambda:InvokeFunction",
                Principal="apigateway.amazonaws.com",
                SourceArn=f"arn:aws:execute-api:{self._config.region}:{account_id}:{api_id}/*",
            )
        except Exception as exc:
            conflict = getattr(getattr(self._lambda, "exceptions", None), "ResourceConflictException", ())
            if not conflict or not isinstance(exc, conflict):
                raise


def _route_marker(route_key: str) -> str:
    return f"{_ROUTE_MARKER}{hashlib.sha256(route_key.encode()).hexdigest()[:16]}"


def _lambda_arns(integrations: list[dict[str, Any]]) -> list[str]:
    return list(
        dict.fromkeys(
            str(item.get("lambda_function_arn") or "").strip()
            for item in integrations
            if str(item.get("lambda_function_arn") or "").strip()
        ),
    )


def _first_account_id(cfg: dict[str, Any]) -> str:
    shortcut = str(cfg.get("lambda_function_arn") or "")
    if shortcut:
        return _account_from_arn(shortcut)
    for integration in cfg.get("integrations") or []:
        arn = str(integration.get("lambda_function_arn") or "")
        if arn:
            return _account_from_arn(arn)
    return ""


def _account_from_arn(arn: str) -> str:
    parts = arn.split(":")
    return parts[4] if len(parts) > 4 else ""


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    return code in {"NotFoundException", "ResourceNotFoundException"} or "not found" in str(exc).lower()
