"""AWS API Gateway HTTP API managed-service driver (#987 / #1035 pivot).

Spec ref: spec 23-provider-plugin-aws + _sdk/managed_service.py.

A *public* faas workload (#1035) is reached through an API Gateway HTTP API
that proxies to the function. This replaces the earlier
CloudFront-OAC -> Lambda-Function-URL sigv4 model, which is a confirmed
dead-end on the target account (a full textbook OAC config still 403s, and
the account also blocks public ``AuthType=NONE`` Function URLs). An HTTP
API's ``execute-api`` endpoint is PUBLIC by default, so it needs no signing
and no Function URL at all.

The integration is ``AWS_PROXY`` against the Lambda's *function ARN* (the
faas activity threads it in via ``ProvisionSpec.config['lambda_function_arn']``);
API Gateway invokes the function directly with ``lambda:InvokeFunction`` --
the Function URL is never involved on this path. A single ``$default`` route
forwards every request to that integration, and a ``$default`` stage with
``AutoDeploy=true`` serves it at the root of the ``ApiEndpoint``.

The lambda-side invoke grant is written here (``lambda:AddPermission``,
principal ``apigateway.amazonaws.com``, ``SourceArn`` scoped to this one
API's ``execute-api`` ARN) -- the account id for that ARN is parsed from the
function ARN, so no STS round-trip is needed.

Custom domains (apigatewayv2 domain name + REGIONAL ACM cert + Route53) are a
fast-follow; v1 serves over the ``execute-api`` URL.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)
from aws._errors import map_client_error
from aws._naming import iam_role_name
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
    tags_for,
)

log = logging.getLogger("aws.managed.api_gateway_http")

KIND = "api_gateway"

# The single catch-all route + stage names. "$default" is API Gateway's
# special token: the $default route matches any method/path and the $default
# stage serves at the root of the ApiEndpoint (no /stage-name prefix).
_DEFAULT_ROUTE_KEY = "$default"
_DEFAULT_STAGE_NAME = "$default"

# Deterministic statement id for the API -> Lambda invoke grant so a
# re-provision converges (a second AddPermission under the same id raises
# ResourceConflictException, which is swallowed).
_INVOKE_STATEMENT_ID = "AstroliftApiGatewayInvoke"


@dataclass(frozen=True)
class ApiGatewayHttpConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    region: str = "us-east-1"


class ApiGatewayHttpDriver:
    KIND = KIND

    def __init__(
        self,
        *,
        config: ApiGatewayHttpConfig,
        client: Any | None = None,
        lambda_client: Any | None = None,
    ) -> None:
        self._config = config
        if client is not None:
            self._api = client
        else:
            import boto3

            self._api = boto3.client("apigatewayv2", region_name=config.region)
        if lambda_client is not None:
            self._lambda = lambda_client
        else:
            import boto3

            self._lambda = boto3.client("lambda", region_name=config.region)

    # ---- lifecycle ------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="api_gateway_http",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        lambda_arn = str(cfg.get("lambda_function_arn", "")).strip()
        if not lambda_arn:
            msg = "api_gateway provision requires config.lambda_function_arn"
            return ProvisionResult(ok=False, handle="", message=msg, errors=[msg])

        api_name = self._api_name(spec)
        try:
            api = self._find_api(api_name)
            if api is None:
                api = self._create_api(api_name, spec)
            api_id = api["ApiId"]
            integration_id = self._ensure_integration(api_id, lambda_arn)
            self._ensure_route(api_id, integration_id)
            self._ensure_stage(api_id)
            self._ensure_invoke_permission(lambda_arn=lambda_arn, api_id=api_id)
        except Exception as exc:
            return ProvisionResult(ok=False, handle="", message=str(exc), errors=[str(exc)])

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=api_id),
            message=f"http api {api_id} provisioned",
            # The execute-api endpoint is live the moment the $default stage
            # exists (AutoDeploy) -- no creating -> available transition to
            # poll, so skip the workflow's readiness wait.
            ready=True,
        )

    @driver_op(cloud="aws", driver="api_gateway_http")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, api_id = parse_handle(spec.handle)
        lambda_arn = str((spec.config or {}).get("lambda_function_arn", "")).strip()
        if not lambda_arn:
            msg = "api_gateway update requires config.lambda_function_arn"
            return UpdateResult(ok=False, handle=spec.handle, message=msg, errors=[msg])
        try:
            integration_id = self._ensure_integration(api_id, lambda_arn)
            self._ensure_route(api_id, integration_id)
            self._ensure_stage(api_id)
            self._ensure_invoke_permission(lambda_arn=lambda_arn, api_id=api_id)
        except Exception as exc:
            return UpdateResult(ok=False, handle=spec.handle, message=str(exc), errors=[str(exc)])
        return UpdateResult(ok=True, handle=spec.handle, message=f"http api {api_id} updated")

    @driver_op(
        cloud="aws",
        driver="api_gateway_http",
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
        # An HTTP API holds no persistent state; both safety axes take the same
        # path. Deleting the api cascades its integrations/routes/stages, and
        # the lambda's resource-policy grant dies with the function (torn down
        # by its own managed-service row), so nothing else to reap here.
        del delete_data, force_destroy
        _, api_id = parse_handle(spec.handle)
        try:
            self._api.delete_api(ApiId=api_id)
        except self._api.exceptions.NotFoundException:
            pass
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_api: {exc}",
                errors=[str(exc)],
            )
        return DeprovisionResult(ok=True, handle=spec.handle, message=f"http api {api_id} deleted")

    # ---- read-only ops --------------------------------------------

    @driver_op(cloud="aws", driver="api_gateway_http")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, api_id = parse_handle(handle.handle)
        try:
            self._api.get_api(ApiId=api_id)
        except self._api.exceptions.NotFoundException:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"http api {api_id} does not exist",
            )
        except Exception as exc:
            return ServiceStatus(handle=handle.handle, state="error", message=str(exc))
        return ServiceStatus(handle=handle.handle, state="available", message=f"http api {api_id} available")

    @driver_op(cloud="aws", driver="api_gateway_http")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, api_id = parse_handle(handle.handle)
        endpoint = self._api.get_api(ApiId=api_id).get("ApiEndpoint", "")
        return Binding(
            env_vars={"FAAS_INVOKE_URL": ValueRef(literal=endpoint)},
            notes="Public API Gateway HTTP API endpoint that proxies to the function.",
        )

    @driver_op(cloud="aws", driver="api_gateway_http")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "an HTTP API has no snapshot semantic -- it is reconstructed from the function ARN on every provision",
        )

    @driver_op(cloud="aws", driver="api_gateway_http")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            ok=False,
            handle="",
            message="HTTP API has no snapshot, hence no restore",
            errors=["not_implemented"],
        )

    @driver_op(cloud="aws", driver="api_gateway_http", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["lambda_function_arn"],
            "properties": {
                "lambda_function_arn": {
                    "type": "string",
                    "description": (
                        "ARN of the Lambda function the $default route proxies to "
                        "(AWS_PROXY integration). Threaded in by the faas activity."
                    ),
                },
            },
        }

    @driver_op(cloud="aws", driver="api_gateway_http", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={"FAAS_INVOKE_URL": "Public HTTP API endpoint that invokes the function"},
        )

    def editable_fields(self) -> list[str]:
        return ["*"]

    # ---- internals ------------------------------------------------

    def _api_name(self, spec: ProvisionSpec) -> str:
        # Deterministic + astrolift-* prefixed (matches the control-plane
        # name-prefix grant) and scoped by the per-workload service_handle_hint
        # so two faas workloads in one app/env get distinct APIs -- same shape
        # as faas_lambda._function_name.
        return iam_role_name(
            "astrolift",
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
            max_len=128,
        )

    def _find_api(self, api_name: str) -> dict[str, Any] | None:
        # Idempotent probe: HTTP APIs aren't name-unique, so we match the
        # deterministic name to recover a prior provision's api (paginated).
        try:
            token = ""
            while True:
                kwargs: dict[str, Any] = {"MaxResults": "100"}
                if token:
                    kwargs["NextToken"] = token
                resp = self._api.get_apis(**kwargs)
                for item in resp.get("Items", []) or []:
                    if item.get("Name") == api_name:
                        return item
                token = resp.get("NextToken", "")
                if not token:
                    return None
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _create_api(self, api_name: str, spec: ProvisionSpec) -> dict[str, Any]:
        try:
            return self._api.create_api(
                Name=api_name,
                ProtocolType="HTTP",
                Tags={t["Key"]: t["Value"] for t in tags_for(spec)},
            )
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _ensure_integration(self, api_id: str, lambda_arn: str) -> str:
        # Reuse an existing AWS_PROXY integration to the same function ARN
        # rather than stacking a new one per provision (integrations are not
        # keyed, so each create makes a distinct one).
        try:
            for item in self._api.get_integrations(ApiId=api_id).get("Items", []) or []:
                if item.get("IntegrationUri") == lambda_arn:
                    return item["IntegrationId"]
            resp = self._api.create_integration(
                ApiId=api_id,
                IntegrationType="AWS_PROXY",
                IntegrationUri=lambda_arn,
                PayloadFormatVersion="2.0",
            )
            return resp["IntegrationId"]
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _ensure_route(self, api_id: str, integration_id: str) -> None:
        target = f"integrations/{integration_id}"
        try:
            for item in self._api.get_routes(ApiId=api_id).get("Items", []) or []:
                if item.get("RouteKey") == _DEFAULT_ROUTE_KEY:
                    # Repoint only if the integration drifted (e.g. recreated).
                    if item.get("Target") != target:
                        self._api.update_route(ApiId=api_id, RouteId=item["RouteId"], Target=target)
                    return
            self._api.create_route(ApiId=api_id, RouteKey=_DEFAULT_ROUTE_KEY, Target=target)
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _ensure_stage(self, api_id: str) -> None:
        try:
            self._api.get_stage(ApiId=api_id, StageName=_DEFAULT_STAGE_NAME)
            return
        except self._api.exceptions.NotFoundException:
            pass
        except Exception as exc:
            raise map_client_error(exc) from exc
        try:
            self._api.create_stage(ApiId=api_id, StageName=_DEFAULT_STAGE_NAME, AutoDeploy=True)
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _ensure_invoke_permission(self, *, lambda_arn: str, api_id: str) -> None:
        # API Gateway invokes the function directly (AWS_PROXY) -- grant
        # apigateway.amazonaws.com lambda:InvokeFunction scoped to this one
        # API's execute-api ARN. The account id comes from the function ARN
        # (arn:aws:lambda:<region>:<account>:function:<name>), so no STS call.
        account_id = self._account_from_arn(lambda_arn)
        source_arn = f"arn:aws:execute-api:{self._config.region}:{account_id}:{api_id}/*/*"
        with contextlib.suppress(self._lambda.exceptions.ResourceConflictException):
            self._lambda.add_permission(
                FunctionName=lambda_arn,
                StatementId=_INVOKE_STATEMENT_ID,
                Action="lambda:InvokeFunction",
                Principal="apigateway.amazonaws.com",
                SourceArn=source_arn,
            )

    @staticmethod
    def _account_from_arn(lambda_arn: str) -> str:
        parts = lambda_arn.split(":")
        return parts[4] if len(parts) > 4 else ""
