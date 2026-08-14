"""Tests for the AWS API Gateway HTTP API managed-service driver (#987/#1035).

The astrolift-local container has no moto, so these exercise the driver
against stateful recording fakes for the apigatewayv2 + Lambda clients. The
falsifiable core: a PUBLIC faas invoke surface is an HTTP API whose $default
route AWS_PROXY-proxies to the function ARN over a public execute-api URL,
with a lambda:InvokeFunction grant scoped to THIS api's execute-api ARN --
NOT a CloudFront distribution and NOT a Function URL.
"""

from __future__ import annotations

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from aws.managed.api_gateway_http import (
    KIND,
    ApiGatewayHttpConfig,
    ApiGatewayHttpDriver,
)

_LAMBDA_ARN = "arn:aws:lambda:us-east-1:123456789012:function:astrolift-acme-api-prod-faas"


# ---- recording fakes -------------------------------------------------


class _NotFoundException(Exception):
    pass


class _ApiExceptions:
    NotFoundException = _NotFoundException


class FakeApiGateway:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.exceptions = _ApiExceptions()
        self._apis: dict[str, dict] = {}
        self._integrations: dict[str, list[dict]] = {}
        self._routes: dict[str, list[dict]] = {}
        self._stages: dict[tuple[str, str], dict] = {}
        self._seq = 0

    def _record(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def kwargs_for(self, name: str) -> dict:
        for n, kw in self.calls:
            if n == name:
                return kw
        raise AssertionError(f"{name} was not called")

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq}"

    def seed_api(self, *, name: str) -> str:
        api_id = self._next("api-")
        self._apis[api_id] = {
            "ApiId": api_id,
            "Name": name,
            "ProtocolType": "HTTP",
            "ApiEndpoint": f"https://{api_id}.execute-api.us-east-1.amazonaws.com",
        }
        self._integrations[api_id] = []
        self._routes[api_id] = []
        return api_id

    # ---- apigatewayv2 surface ----
    def get_apis(self, **kwargs):
        self._record("get_apis", kwargs)
        return {"Items": list(self._apis.values())}

    def create_api(self, **kwargs):
        self._record("create_api", kwargs)
        api_id = self.seed_api(name=kwargs["Name"])
        return self._apis[api_id]

    def get_api(self, **kwargs):
        self._record("get_api", kwargs)
        api_id = kwargs["ApiId"]
        if api_id not in self._apis:
            raise _NotFoundException(api_id)
        return self._apis[api_id]

    def delete_api(self, **kwargs):
        self._record("delete_api", kwargs)
        api_id = kwargs["ApiId"]
        if api_id not in self._apis:
            raise _NotFoundException(api_id)
        del self._apis[api_id]

    def get_integrations(self, **kwargs):
        self._record("get_integrations", kwargs)
        return {"Items": list(self._integrations.get(kwargs["ApiId"], []))}

    def create_integration(self, **kwargs):
        self._record("create_integration", kwargs)
        integration_id = self._next("int-")
        item = {
            "IntegrationId": integration_id,
            "IntegrationType": kwargs["IntegrationType"],
            "IntegrationUri": kwargs["IntegrationUri"],
            "PayloadFormatVersion": kwargs.get("PayloadFormatVersion"),
        }
        self._integrations.setdefault(kwargs["ApiId"], []).append(item)
        return item

    def get_routes(self, **kwargs):
        self._record("get_routes", kwargs)
        return {"Items": list(self._routes.get(kwargs["ApiId"], []))}

    def create_route(self, **kwargs):
        self._record("create_route", kwargs)
        route_id = self._next("route-")
        item = {"RouteId": route_id, "RouteKey": kwargs["RouteKey"], "Target": kwargs["Target"]}
        self._routes.setdefault(kwargs["ApiId"], []).append(item)
        return item

    def update_route(self, **kwargs):
        self._record("update_route", kwargs)
        for item in self._routes.get(kwargs["ApiId"], []):
            if item["RouteId"] == kwargs["RouteId"]:
                item["Target"] = kwargs["Target"]
        return {}

    def get_stage(self, **kwargs):
        self._record("get_stage", kwargs)
        key = (kwargs["ApiId"], kwargs["StageName"])
        if key not in self._stages:
            raise _NotFoundException(str(key))
        return self._stages[key]

    def create_stage(self, **kwargs):
        self._record("create_stage", kwargs)
        key = (kwargs["ApiId"], kwargs["StageName"])
        self._stages[key] = {"StageName": kwargs["StageName"], "AutoDeploy": kwargs.get("AutoDeploy")}
        return self._stages[key]


class _ResourceConflictException(Exception):
    pass


class _ResourceNotFoundException(Exception):
    pass


class _LambdaExceptions:
    ResourceConflictException = _ResourceConflictException
    ResourceNotFoundException = _ResourceNotFoundException


class FakeLambda:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.exceptions = _LambdaExceptions()
        self._permissions: set[tuple[str, str]] = set()

    def _record(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def kwargs_for(self, name: str) -> dict:
        for n, kw in self.calls:
            if n == name:
                return kw
        raise AssertionError(f"{name} was not called")

    def add_permission(self, **kwargs):
        self._record("add_permission", kwargs)
        key = (kwargs["FunctionName"], kwargs["StatementId"])
        if key in self._permissions:
            raise _ResourceConflictException(str(key))
        self._permissions.add(key)


# ---- helpers ---------------------------------------------------------


def _driver(*, api: FakeApiGateway | None = None, lam: FakeLambda | None = None):
    api = api or FakeApiGateway()
    lam = lam or FakeLambda()
    drv = ApiGatewayHttpDriver(config=ApiGatewayHttpConfig(region="us-east-1"), client=api, lambda_client=lam)
    return drv, api, lam


def _spec(config: dict | None = None) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="api",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="api-prod-api",
        size="small",
        config=config if config is not None else {"lambda_function_arn": _LAMBDA_ARN},
    )


# ---- provision -------------------------------------------------------


def test_provision_creates_http_api_proxy_route_stage_and_grant():
    drv, api, lam = _driver()
    result = drv.provision(_spec())
    assert result.ok is True
    assert result.handle.startswith(f"{KIND}/")
    assert result.ready is True  # execute-api is live the moment the stage exists

    # HTTP API (not REST), AWS_PROXY integration to the function ARN, v2 payload.
    assert api.kwargs_for("create_api")["ProtocolType"] == "HTTP"
    assert api.kwargs_for("create_api")["Name"].startswith("astrolift-")
    integ = api.kwargs_for("create_integration")
    assert integ["IntegrationType"] == "AWS_PROXY"
    assert integ["IntegrationUri"] == _LAMBDA_ARN
    assert integ["PayloadFormatVersion"] == "2.0"

    # $default route -> the integration, and a $default AutoDeploy stage.
    route = api.kwargs_for("create_route")
    assert route["RouteKey"] == "$default"
    assert route["Target"].startswith("integrations/")
    stage = api.kwargs_for("create_stage")
    assert stage["StageName"] == "$default"
    assert stage["AutoDeploy"] is True

    # The ONLY invoke grant: apigateway principal, lambda:InvokeFunction,
    # SourceArn scoped to THIS api's execute-api ARN (account parsed from the
    # function ARN). Falsifiable: a CloudFront principal / Function-URL action
    # / wildcard SourceArn fails these.
    perm = lam.kwargs_for("add_permission")
    assert perm["Principal"] == "apigateway.amazonaws.com"
    assert perm["Action"] == "lambda:InvokeFunction"
    assert perm["FunctionName"] == _LAMBDA_ARN
    api_id = result.handle.split("/", 1)[1]
    assert perm["SourceArn"] == f"arn:aws:execute-api:us-east-1:123456789012:{api_id}/*/*"


def test_provision_requires_lambda_function_arn():
    drv, api, lam = _driver()
    result = drv.provision(_spec({}))
    assert result.ok is False
    assert "lambda_function_arn" in result.message
    # No cloud mutation attempted on a validation failure.
    assert "create_api" not in api.names()
    assert "add_permission" not in lam.names()


def test_provision_is_idempotent_reuses_api_integration_route_stage():
    # A prior provision left the full chain; a re-run must find + reuse each
    # piece, never stacking a duplicate api/integration/route/stage.
    api = FakeApiGateway()
    drv, api, _ = _driver(api=api)
    first = drv.provision(_spec())
    api_id = first.handle.split("/", 1)[1]
    # Seed the stage as the real AutoDeploy stage would persist.
    api._stages[(api_id, "$default")] = {"StageName": "$default", "AutoDeploy": True}

    creates_before = [n for n in api.names() if n.startswith("create_")]
    second = drv.provision(_spec())
    assert second.ok is True
    assert second.handle == first.handle  # same api reused (matched by name)
    creates_after = [n for n in api.names() if n.startswith("create_")]
    # No NEW create_* calls on the second provision.
    assert creates_after == creates_before
    assert len(api._apis) == 1
    assert len(api._integrations[api_id]) == 1
    assert len(api._routes[api_id]) == 1


def test_provision_repoints_route_when_integration_target_drifts():
    api = FakeApiGateway()
    api_id = api.seed_api(name="astrolift-acme-api-prod-api-prod-api")
    # A stale route pointing at a different integration.
    api._routes[api_id] = [{"RouteId": "route-old", "RouteKey": "$default", "Target": "integrations/stale"}]
    api._integrations[api_id] = []
    api._stages[(api_id, "$default")] = {"StageName": "$default", "AutoDeploy": True}
    drv, api, _ = _driver(api=api)

    result = drv.provision(_spec())
    assert result.ok is True
    # The driver creates the real integration then repoints the existing route.
    updated = api.kwargs_for("update_route")
    new_integration_id = api._integrations[api_id][0]["IntegrationId"]
    assert updated["Target"] == f"integrations/{new_integration_id}"


def test_provision_add_permission_conflict_is_swallowed():
    # A second provision re-asserts the grant; the conflict (same StatementId)
    # must converge, not raise.
    api = FakeApiGateway()
    drv, api, _ = _driver(api=api)
    drv.provision(_spec())
    result = drv.provision(_spec())
    assert result.ok is True


# ---- deprovision -----------------------------------------------------


def test_deprovision_deletes_api():
    api = FakeApiGateway()
    api_id = api.seed_api(name="x")
    drv, api, _ = _driver(api=api)
    result = drv.deprovision(DeprovisionSpec(handle=f"{KIND}/{api_id}"))
    assert result.ok is True
    assert "delete_api" in api.names()
    assert api_id not in api._apis


def test_deprovision_idempotent_when_gone():
    drv, api, _ = _driver()
    result = drv.deprovision(DeprovisionSpec(handle=f"{KIND}/api-missing"))
    assert result.ok is True
    assert "delete_api" in api.names()


# ---- status + binding ------------------------------------------------


def test_status_available_then_deprovisioned():
    api = FakeApiGateway()
    api_id = api.seed_api(name="x")
    drv, api, _ = _driver(api=api)
    assert drv.status(ServiceHandle(handle=f"{KIND}/{api_id}")).state == "available"
    assert drv.status(ServiceHandle(handle=f"{KIND}/api-missing")).state == "deprovisioned"


def test_binding_emits_public_execute_api_url():
    api = FakeApiGateway()
    api_id = api.seed_api(name="x")
    drv, api, _ = _driver(api=api)
    binding = drv.binding(ServiceHandle(handle=f"{KIND}/{api_id}"))
    url = binding.env_vars["FAAS_INVOKE_URL"].literal
    assert url == f"https://{api_id}.execute-api.us-east-1.amazonaws.com"
    assert binding.env_vars["API_GATEWAY_URL"].literal == url
    assert binding.env_vars["API_GATEWAY_ID"].literal == api_id
    assert binding.env_vars["API_GATEWAY_STAGE"].literal == "$default"
    # The invoke surface is public on its own URL -- no IAM grant the consumer
    # must hold to reach it (distinguishes it from the dead Function-URL model).
    assert binding.iam_grants == []


# ---- update ----------------------------------------------------------


def test_update_reconciles_integration_route_stage():
    api = FakeApiGateway()
    api_id = api.seed_api(name="x")
    api._stages[(api_id, "$default")] = {"StageName": "$default", "AutoDeploy": True}
    drv, api, lam = _driver(api=api)
    result = drv.update(UpdateSpec(handle=f"{KIND}/{api_id}", config={"lambda_function_arn": _LAMBDA_ARN}))
    assert result.ok is True
    assert api._integrations[api_id][0]["IntegrationUri"] == _LAMBDA_ARN
    assert "add_permission" in lam.names()


def test_update_requires_lambda_function_arn():
    drv, _, _ = _driver()
    result = drv.update(UpdateSpec(handle=f"{KIND}/api-1", config={}))
    assert result.ok is False
    assert "lambda_function_arn" in result.message


# ---- config_schema + managed_config_for ------------------------------


def test_config_schema_requires_lambda_arn():
    drv, _, _ = _driver()
    schema = drv.config_schema()
    assert "lambda_function_arn" in schema["required"]


def test_managed_config_for_api_gateway_returns_config():
    from types import SimpleNamespace

    from core.cluster_observability import managed_config_for

    cluster = SimpleNamespace(slug="aws-prod", region="us-west-2", provider_config={}, auth_config={})
    cfg = managed_config_for("aws", cluster, kind="api_gateway")
    assert isinstance(cfg, ApiGatewayHttpConfig)
    assert cfg.region == "us-west-2"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
