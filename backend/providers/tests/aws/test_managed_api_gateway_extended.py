"""Lifecycle and native-request tests for REST and WebSocket API Gateway."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import ClassVar

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.api_gateway_http import ApiGatewayHttpConfig
from aws.managed.api_gateway_rest import ApiGatewayRestDriver
from aws.managed.api_gateway_websocket import ApiGatewayWebSocketDriver

_LAMBDA_ARN = "arn:aws:lambda:us-east-1:123456789012:function:handler"
_OTHER_LAMBDA_ARN = "arn:aws:lambda:us-east-1:123456789012:function:authorizer"


class NotFound(Exception):
    response: ClassVar[dict] = {"Error": {"Code": "NotFoundException"}}


class Conflict(Exception):
    pass


class LambdaExceptions:
    ResourceConflictException = Conflict


class FakeLambda:
    exceptions = LambdaExceptions()

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.permissions: set[tuple[str, str]] = set()

    def add_permission(self, **kwargs):
        self.calls.append(("add_permission", kwargs))
        key = (kwargs["FunctionName"], kwargs["StatementId"])
        if key in self.permissions:
            raise Conflict(key)
        self.permissions.add(key)


class FakeRestApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.apis: dict[str, dict] = {}
        self.stages: dict[tuple[str, str], dict] = {}
        self.seq = 0

    def _call(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs_for(self, name: str) -> dict:
        return next(kwargs for call_name, kwargs in self.calls if call_name == name)

    def create_rest_api(self, **kwargs):
        self._call("create_rest_api", kwargs)
        self.seq += 1
        api_id = f"rest{self.seq}"
        api = {"id": api_id, **kwargs}
        self.apis[api_id] = api
        return api

    def get_rest_apis(self, **kwargs):
        self._call("get_rest_apis", kwargs)
        return {"items": list(self.apis.values())}

    def get_rest_api(self, **kwargs):
        self._call("get_rest_api", kwargs)
        try:
            return self.apis[kwargs["restApiId"]]
        except KeyError as exc:
            raise NotFound from exc

    def put_rest_api(self, **kwargs):
        self._call("put_rest_api", kwargs)
        return self.apis[kwargs["restApiId"]]

    def update_rest_api(self, **kwargs):
        self._call("update_rest_api", kwargs)
        return self.apis[kwargs["restApiId"]]

    def create_deployment(self, **kwargs):
        self._call("create_deployment", kwargs)
        self.seq += 1
        return {"id": f"deployment{self.seq}"}

    def get_stage(self, **kwargs):
        self._call("get_stage", kwargs)
        try:
            return self.stages[(kwargs["restApiId"], kwargs["stageName"])]
        except KeyError as exc:
            raise NotFound from exc

    def create_stage(self, **kwargs):
        self._call("create_stage", kwargs)
        self.stages[(kwargs["restApiId"], kwargs["stageName"])] = dict(kwargs)
        return kwargs

    def update_stage(self, **kwargs):
        self._call("update_stage", kwargs)
        return self.stages[(kwargs["restApiId"], kwargs["stageName"])]

    def delete_rest_api(self, **kwargs):
        self._call("delete_rest_api", kwargs)
        try:
            del self.apis[kwargs["restApiId"]]
        except KeyError as exc:
            raise NotFound from exc


class FakeWebSocketApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.apis: dict[str, dict] = {}
        self.integrations: dict[str, list[dict]] = {}
        self.routes: dict[str, list[dict]] = {}
        self.stages: dict[tuple[str, str], dict] = {}
        self.seq = 0

    def _call(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs_for(self, name: str) -> dict:
        return next(kwargs for call_name, kwargs in self.calls if call_name == name)

    def create_api(self, **kwargs):
        self._call("create_api", kwargs)
        self.seq += 1
        api_id = f"ws{self.seq}"
        api = {
            "ApiId": api_id,
            "ApiEndpoint": f"wss://{api_id}.execute-api.us-east-1.amazonaws.com",
            **kwargs,
        }
        self.apis[api_id] = api
        self.integrations[api_id] = []
        self.routes[api_id] = []
        return api

    def get_apis(self, **kwargs):
        self._call("get_apis", kwargs)
        return {"Items": list(self.apis.values())}

    def get_api(self, **kwargs):
        self._call("get_api", kwargs)
        try:
            return self.apis[kwargs["ApiId"]]
        except KeyError as exc:
            raise NotFound from exc

    def update_api(self, **kwargs):
        self._call("update_api", kwargs)
        self.apis[kwargs["ApiId"]].update(kwargs)
        return self.apis[kwargs["ApiId"]]

    def delete_api(self, **kwargs):
        self._call("delete_api", kwargs)
        try:
            del self.apis[kwargs["ApiId"]]
        except KeyError as exc:
            raise NotFound from exc

    def get_integrations(self, **kwargs):
        self._call("get_integrations", kwargs)
        return {"Items": list(self.integrations.get(kwargs["ApiId"], []))}

    def create_integration(self, **kwargs):
        self._call("create_integration", kwargs)
        self.seq += 1
        item = {"IntegrationId": f"integration{self.seq}", **kwargs}
        item.pop("ApiId")
        self.integrations[kwargs["ApiId"]].append(item)
        return item

    def update_integration(self, **kwargs):
        self._call("update_integration", kwargs)
        item = next(
            item for item in self.integrations[kwargs["ApiId"]] if item["IntegrationId"] == kwargs["IntegrationId"]
        )
        item.update(kwargs)
        item.pop("ApiId", None)
        return item

    def delete_integration(self, **kwargs):
        self._call("delete_integration", kwargs)
        self.integrations[kwargs["ApiId"]] = [
            item for item in self.integrations[kwargs["ApiId"]] if item["IntegrationId"] != kwargs["IntegrationId"]
        ]

    def get_routes(self, **kwargs):
        self._call("get_routes", kwargs)
        return {"Items": list(self.routes.get(kwargs["ApiId"], []))}

    def create_route(self, **kwargs):
        self._call("create_route", kwargs)
        self.seq += 1
        item = {"RouteId": f"route{self.seq}", **kwargs}
        item.pop("ApiId")
        self.routes[kwargs["ApiId"]].append(item)
        return item

    def update_route(self, **kwargs):
        self._call("update_route", kwargs)
        item = next(item for item in self.routes[kwargs["ApiId"]] if item["RouteId"] == kwargs["RouteId"])
        item.update(kwargs)
        item.pop("ApiId", None)
        return item

    def delete_route(self, **kwargs):
        self._call("delete_route", kwargs)
        self.routes[kwargs["ApiId"]] = [
            item for item in self.routes[kwargs["ApiId"]] if item["RouteId"] != kwargs["RouteId"]
        ]

    def create_deployment(self, **kwargs):
        self._call("create_deployment", kwargs)
        self.seq += 1
        return {"DeploymentId": f"deployment{self.seq}"}

    def get_stage(self, **kwargs):
        self._call("get_stage", kwargs)
        try:
            return self.stages[(kwargs["ApiId"], kwargs["StageName"])]
        except KeyError as exc:
            raise NotFound from exc

    def create_stage(self, **kwargs):
        self._call("create_stage", kwargs)
        self.stages[(kwargs["ApiId"], kwargs["StageName"])] = dict(kwargs)
        return kwargs

    def update_stage(self, **kwargs):
        self._call("update_stage", kwargs)
        self.stages[(kwargs["ApiId"], kwargs["StageName"])].update(kwargs)
        return self.stages[(kwargs["ApiId"], kwargs["StageName"])]


def _config() -> ApiGatewayHttpConfig:
    return ApiGatewayHttpConfig(region="us-east-1", account_id="123456789012")


def _spec(config: dict) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="chat",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="gateway",
        size="small",
        config=config,
        binding_id="binding-1",
        managed_service_id="service-1",
    )


def _rest(config: dict | None = None):
    api = FakeRestApi()
    lam = FakeLambda()
    driver = ApiGatewayRestDriver(config=_config(), client=api, lambda_client=lam)
    resolved = config if config is not None else {"lambda_function_arn": _LAMBDA_ARN}
    return driver, api, lam, _spec(resolved)


def _websocket(config: dict | None = None):
    api = FakeWebSocketApi()
    lam = FakeLambda()
    driver = ApiGatewayWebSocketDriver(config=_config(), client=api, lambda_client=lam)
    resolved = config if config is not None else {"lambda_function_arn": _LAMBDA_ARN}
    return driver, api, lam, _spec(resolved)


def test_rest_lambda_shortcut_imports_openapi_deploys_and_grants() -> None:
    driver, api, lam, spec = _rest()
    result = driver.provision(spec)

    assert result.ok and result.ready
    create = api.kwargs_for("create_rest_api")
    assert create["name"].startswith("astrolift-")
    assert create["tags"]["astrolift.io/managed-by"] == "platform"
    assert create["tags"]["astrolift.io/binding"] == "binding-1"
    imported = json.loads(api.kwargs_for("put_rest_api")["body"])
    integration = imported["paths"]["/{proxy+}"]["x-amazon-apigateway-any-method"]["x-amazon-apigateway-integration"]
    assert integration["uri"].endswith(f"functions/{_LAMBDA_ARN}/invocations")
    assert "payloadFormatVersion" not in integration
    assert api.kwargs_for("create_stage")["stageName"] == "prod"
    permission = lam.calls[0][1]
    assert permission["SourceArn"].endswith(f":{result.handle.split('/', 1)[1]}/*/*/*")


def test_rest_reprovision_is_idempotent_and_advances_existing_stage() -> None:
    driver, api, _, spec = _rest()
    first = driver.provision(spec)
    second = driver.provision(spec)
    assert first.handle == second.handle
    assert api.names().count("create_rest_api") == 1
    assert "update_stage" in api.names()


def test_rest_child_failure_returns_recoverable_partial_handle() -> None:
    driver, api, _, spec = _rest()

    def fail_import(**kwargs):
        raise RuntimeError("bad OpenAPI")

    api.put_rest_api = fail_import
    result = driver.provision(spec)
    assert not result.ok
    assert result.handle == "api_gateway/rest1"


def test_rest_accepts_native_fragments_and_multiple_lambda_grants() -> None:
    document = {"openapi": "3.0.1", "info": {"title": "x", "version": "1"}, "paths": {}}
    config = {
        "openapi": document,
        "openapi_mode": "merge",
        "openapi_parameters": {"ignore": "documentation"},
        "rest_api": {"description": "native", "endpointConfiguration": {"types": ["REGIONAL"]}},
        "rest_api_patch_operations": [{"op": "replace", "path": "/description", "value": "new"}],
        "deployment": {"description": "release"},
        "stage_name": "beta",
        "stage": {"description": "beta stage", "tracingEnabled": True},
        "lambda_function_arns": [_LAMBDA_ARN, _OTHER_LAMBDA_ARN],
    }
    driver, api, lam, spec = _rest(config)
    result = driver.provision(spec)
    assert result.ok
    assert api.kwargs_for("create_rest_api")["endpointConfiguration"] == {"types": ["REGIONAL"]}
    assert api.kwargs_for("put_rest_api")["mode"] == "merge"
    assert api.kwargs_for("create_deployment")["description"] == "release"
    assert api.kwargs_for("create_stage")["tracingEnabled"] is True
    assert {kwargs["FunctionName"] for _, kwargs in lam.calls} == {_LAMBDA_ARN, _OTHER_LAMBDA_ARN}


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "requires"),
        ({"openapi": []}, "object, string, or bytes"),
        ({"openapi": {}, "openapi_mode": "append"}, "merge or overwrite"),
        ({"openapi": {}, "stage_name": "bad/stage"}, "stage_name"),
        ({"openapi": {}, "rest_api": {"name": "escape"}}, "Astrolift-owned"),
        # cloneFrom copies another API, integration credentials included (#2087).
        ({"openapi": {}, "rest_api": {"cloneFrom": "other-tenant-api"}}, "cannot set cloneFrom"),
    ],
)
def test_rest_rejects_invalid_or_identity_overriding_config(config: dict, message: str) -> None:
    driver, api, _, spec = _rest(config)
    result = driver.provision(spec)
    assert not result.ok and message in result.message
    assert "create_rest_api" not in api.names()


def test_rest_ownership_and_deletion_protection_are_enforced() -> None:
    driver, api, _, spec = _rest({"lambda_function_arn": _LAMBDA_ARN, "deletion_protection": True})
    provisioned = driver.provision(spec)
    handle = provisioned.handle
    assert not driver.deprovision(DeprovisionSpec(handle, spec.config)).ok
    assert driver.deprovision(DeprovisionSpec(handle, spec.config), force_destroy=True).ok

    foreign = api.create_rest_api(name="foreign", tags={})
    foreign_handle = f"api_gateway/{foreign['id']}"
    assert driver.status(ServiceHandle(foreign_handle)).state == "error"
    assert not driver.update(UpdateSpec(foreign_handle, config={"openapi": {}})).ok
    assert not driver.deprovision(DeprovisionSpec(foreign_handle)).ok


def test_rest_binding_exposes_stage_and_optional_iam_invoke_grant() -> None:
    driver, api, _, spec = _rest()
    handle = driver.provision(spec).handle
    binding = driver.binding(ServiceHandle(handle), {"stage_name": "beta", "iam_authorized": True})
    assert binding.env_vars["API_GATEWAY_URL"].literal.endswith("/beta")
    assert binding.env_vars["API_GATEWAY_ID"].literal in api.apis
    assert binding.iam_grants[0].actions == ["execute-api:Invoke"]


def test_websocket_lambda_shortcut_creates_three_routes_and_management_binding() -> None:
    driver, api, lam, spec = _websocket()
    result = driver.provision(spec)
    assert result.ok and result.ready
    create = api.kwargs_for("create_api")
    assert create["ProtocolType"] == "WEBSOCKET"
    assert create["RouteSelectionExpression"] == "$request.body.action"
    integration = api.kwargs_for("create_integration")
    assert integration["IntegrationUri"].endswith(f"functions/{_LAMBDA_ARN}/invocations")
    assert {item["RouteKey"] for item in api.routes[result.handle.split("/", 1)[1]]} == {
        "$connect",
        "$disconnect",
        "$default",
    }
    assert lam.calls[0][1]["SourceArn"].endswith(f":{result.handle.split('/', 1)[1]}/*")

    binding = driver.binding(ServiceHandle(result.handle), spec.config)
    assert binding.env_vars["WEBSOCKET_URL"].literal.endswith("/prod")
    assert binding.env_vars["WEBSOCKET_MANAGEMENT_URL"].literal.startswith("https://")
    assert binding.iam_grants[0].actions == ["execute-api:ManageConnections"]


def test_websocket_reprovision_updates_without_duplicate_resources() -> None:
    driver, api, _, spec = _websocket()
    first = driver.provision(spec)
    second = driver.provision(spec)
    api_id = first.handle.split("/", 1)[1]
    assert second.handle == first.handle
    assert api.names().count("create_api") == 1
    assert len(api.integrations[api_id]) == 1
    assert len(api.routes[api_id]) == 3
    assert api.names().count("update_integration") == 1
    assert api.names().count("update_route") == 3


def test_websocket_child_failure_returns_recoverable_partial_handle() -> None:
    driver, api, _, spec = _websocket()

    def fail_integration(**kwargs):
        raise RuntimeError("invalid integration")

    api.create_integration = fail_integration
    result = driver.provision(spec)
    assert not result.ok
    assert result.handle == "api_gateway/ws1"


def test_websocket_native_declarations_reconcile_and_prune_only_managed_children() -> None:
    first_config = {
        "integrations": [
            {"key": "lambda", "lambda_function_arn": _LAMBDA_ARN, "request": {"TimeoutInMillis": 29000}},
            {
                "key": "http",
                "request": {
                    "IntegrationType": "HTTP_PROXY",
                    "IntegrationMethod": "POST",
                    "IntegrationUri": "https://example.test/events",
                },
            },
        ],
        "routes": [
            {"route_key": "$connect", "integration": "lambda"},
            {"route_key": "events", "integration": "http"},
        ],
        "stage": {"AutoDeploy": False, "Description": "controlled deployment"},
    }
    driver, api, _, spec = _websocket(first_config)
    provisioned = driver.provision(spec)
    assert provisioned.ok
    assert "create_deployment" in api.names()
    assert api.kwargs_for("create_stage")["DeploymentId"].startswith("deployment")

    api_id = provisioned.handle.split("/", 1)[1]
    api.routes[api_id].append(
        {"RouteId": "external", "RouteKey": "external", "OperationName": "customer-owned"},
    )
    next_config = {
        "integrations": [{"key": "lambda", "lambda_function_arn": _LAMBDA_ARN}],
        "routes": [{"route_key": "$connect", "integration": "lambda"}],
    }
    updated = driver.update(UpdateSpec(provisioned.handle, config=next_config))
    assert updated.ok
    assert {item["RouteKey"] for item in api.routes[api_id]} == {"$connect", "external"}
    assert len(api.integrations[api_id]) == 1


def test_websocket_refuses_to_take_over_external_route() -> None:
    driver, api, _, spec = _websocket()
    provisioned = driver.provision(spec)
    api_id = provisioned.handle.split("/", 1)[1]
    route = next(item for item in api.routes[api_id] if item["RouteKey"] == "$default")
    route["OperationName"] = "customer-owned"
    result = driver.update(UpdateSpec(provisioned.handle, config=spec.config))
    assert not result.ok and "not owned" in result.message


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "requires"),
        ({"integrations": [], "routes": [], "stage_name": "bad/stage"}, "stage_name"),
        ({"integrations": [{"key": "x", "request": {}}], "routes": []}, "IntegrationType"),
        (
            {
                "integrations": [{"key": "x", "request": {"IntegrationType": "MOCK"}}],
                "routes": [{"route_key": "go", "integration": "missing"}],
            },
            "unknown key",
        ),
        ({"lambda_function_arn": _LAMBDA_ARN, "api": {"Name": "escape"}}, "Astrolift-owned"),
        (
            {"lambda_function_arn": _LAMBDA_ARN, "integrations": [], "routes": []},
            "cannot be combined",
        ),
        (
            {
                "integrations": [
                    {
                        "key": "x",
                        "request": {"IntegrationType": "MOCK", "Description": "escape"},
                    },
                ],
                "routes": [],
            },
            "Astrolift-owned",
        ),
    ],
)
def test_websocket_rejects_invalid_config(config: dict, message: str) -> None:
    driver, api, _, spec = _websocket(config)
    result = driver.provision(spec)
    assert not result.ok and message in result.message
    assert "create_api" not in api.names()


def test_websocket_ownership_deletion_protection_and_missing_status() -> None:
    config = {"lambda_function_arn": _LAMBDA_ARN, "deletion_protection": True}
    driver, api, _, spec = _websocket(config)
    handle = driver.provision(spec).handle
    assert not driver.deprovision(DeprovisionSpec(handle, config)).ok
    assert driver.deprovision(DeprovisionSpec(handle, config), force_destroy=True).ok
    assert driver.status(ServiceHandle(handle)).state == "deprovisioned"

    foreign = api.create_api(
        Name="foreign",
        ProtocolType="WEBSOCKET",
        RouteSelectionExpression="$request.body.action",
        Tags={},
    )
    foreign_handle = f"api_gateway/{foreign['ApiId']}"
    assert driver.status(ServiceHandle(foreign_handle)).state == "error"
    assert not driver.deprovision(DeprovisionSpec(foreign_handle)).ok


def test_api_gateway_native_requests_match_botocore_models() -> None:
    rest_driver, rest, _, rest_spec = _rest(
        {
            "lambda_function_arn": _LAMBDA_ARN,
            "rest_api": {"endpointConfiguration": {"types": ["REGIONAL"]}},
            "stage": {"tracingEnabled": True},
        },
    )
    assert rest_driver.provision(rest_spec).ok
    assert rest_driver.provision(rest_spec).ok
    ws_driver, websocket, _, ws_spec = _websocket()
    assert ws_driver.provision(ws_spec).ok
    assert ws_driver.provision(ws_spec).ok

    session = Session()
    rest_model = session.get_service_model("apigateway")
    websocket_model = session.get_service_model("apigatewayv2")
    operation_names = {
        "create_rest_api": "CreateRestApi",
        "get_rest_apis": "GetRestApis",
        "put_rest_api": "PutRestApi",
        "create_deployment": "CreateDeployment",
        "get_stage": "GetStage",
        "create_stage": "CreateStage",
        "update_stage": "UpdateStage",
        "create_api": "CreateApi",
        "get_apis": "GetApis",
        "get_integrations": "GetIntegrations",
        "create_integration": "CreateIntegration",
        "update_integration": "UpdateIntegration",
        "get_routes": "GetRoutes",
        "create_route": "CreateRoute",
        "update_route": "UpdateRoute",
        "create_stage_ws": "CreateStage",
        "update_stage_ws": "UpdateStage",
    }
    for name, request in rest.calls:
        if name in operation_names:
            validate_parameters(request, rest_model.operation_model(operation_names[name]).input_shape)
    for name, request in websocket.calls:
        lookup = f"{name}_ws" if name in {"create_stage", "update_stage"} else name
        if lookup in operation_names:
            validate_parameters(request, websocket_model.operation_model(operation_names[lookup]).input_shape)


def test_registration_catalog_cost_and_runtime_config_are_wired() -> None:
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from aws.cost import SERVICE_CODE_BY_VARIANT
    from aws.plugin import PLUGIN

    assert ("api_gateway", "rest_api") in PLUGIN.managed_service_drivers
    assert ("api_gateway", "websocket_api") in PLUGIN.managed_service_drivers
    assert SERVICE_CODE_BY_VARIANT[("api_gateway", "rest_api")] == "AmazonApiGateway"
    assert SERVICE_CODE_BY_VARIANT[("api_gateway", "websocket_api")] == "AmazonApiGateway"
    entries = {
        entry.variant: entry
        for entry in MATRIX.managed_services
        if entry.kind == "api_gateway" and entry.plugin_id == "aws"
    }
    assert entries["rest_api"].status == "preview"
    assert "WEBSOCKET_URL" in entries["websocket_api"].binding_envs

    cluster = SimpleNamespace(
        slug="aws-prod",
        region="us-east-1",
        provider_config={
            "account_id": "123456789012",
            "api_gateway_deletion_protection_default": True,
        },
        auth_config={},
    )
    config = managed_config_for("aws", cluster, kind="api_gateway", variant="rest_api")
    assert config.account_id == "123456789012"
    assert config.deletion_protection_default is True
