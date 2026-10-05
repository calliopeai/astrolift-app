"""Exact existing Lambda/HTTP API observation and two individually journaled writes.

Clients are explicit, bounded and share an admitted credential object. The caller
owns their lifetime and the immutable SENT/ACK store. No allocate/update/adoption,
DNS writes or inference from resource names. Native mutations cannot atomically
freeze a separately editable API/Lambda; source is freshly checked before send.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar
from uuid import UUID

from _sdk.domain_route_http_api import (
    HttpApiDomainReceipt,
    HttpApiEffectHooks,
    HttpApiMappingReceipt,
    HttpApiRouteObservation,
    HttpApiRouteTarget,
    HttpApiRouteUnavailable,
    HttpApiSubmission,
    http_api_request_sha256,
)
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_TAG_AWS
from aws.email_delivery_clients import _private_io

if TYPE_CHECKING:
    from collections.abc import Callable

Acknowledgement = TypeVar("Acknowledgement", HttpApiDomainReceipt, HttpApiMappingReceipt)


def _refuse(reason: str = "HTTP_API_SOURCE_UNAVAILABLE") -> NoReturn:
    raise HttpApiRouteUnavailable(reason)


def _text(value: Any, pattern: str, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not re.fullmatch(pattern, value):
        _refuse("HTTP_API_METADATA_INVALID")
    return value


def _uuid(value: str) -> None:
    try:
        valid = str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        _refuse("HTTP_API_IDENTITY_INVALID")


class HttpApiDomainPort:
    def __init__(self, *, api_client: Any, lambda_client: Any, sts_client: Any) -> None:
        self.api, self.function, self.sts = api_client, lambda_client, sts_client

    def _validate(self, target: HttpApiRouteTarget) -> None:
        if type(target) is not HttpApiRouteTarget:
            _refuse("HTTP_API_TARGET_INVALID")
        for value in (target.operation_id, target.api_service_id, target.faas_service_id):
            _uuid(value)
        _text(target.account_id, r"[0-9]{12}", 12)
        _text(target.region, r"[a-z]{2}(?:-[a-z]+)+-\d+", 64)
        _text(target.api_id, r"[a-z0-9]+", 64)
        if target.partition not in {"aws", "aws-us-gov", "aws-cn"}:
            _refuse("HTTP_API_PARTITION_UNSUPPORTED")
        labels = target.hostname.split(".") if isinstance(target.hostname, str) else []
        if (
            len(labels) < 2
            or len(target.hostname) > 253
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
        ):
            _refuse("HTTP_API_HOSTNAME_INVALID")
        _text(
            target.function_arn,
            rf"arn:{target.partition}:lambda:{target.region}:{target.account_id}:function:[A-Za-z0-9_-]+",
            256,
        )
        if target.certificate_arn:
            _uuid(target.certificate_arn.rsplit("/", 1)[-1])
            _text(
                target.certificate_arn,
                rf"arn:{target.partition}:acm:{target.region}:{target.account_id}:certificate/[0-9a-f-]{{36}}",
                256,
            )
        if target.source_fingerprint:
            _text(target.source_fingerprint, r"[0-9a-f]{64}", 64)
        credentials = []
        for client, service in ((self.api, "apigatewayv2"), (self.function, "lambda"), (self.sts, "sts")):
            config = client.meta.config
            if (
                client.meta.service_model.service_name != service
                or client.meta.region_name != target.region
                or config.connect_timeout > 3
                or config.read_timeout > 5
                or config.retries.get("total_max_attempts") != 1
                or client._endpoint.http_session._verify is False
            ):
                _refuse("HTTP_API_CLIENT_UNBOUNDED")
            suffix = "amazonaws.com.cn" if target.partition == "aws-cn" else "amazonaws.com"
            host = "apigateway" if service == "apigatewayv2" else service
            if client.meta.endpoint_url not in {
                f"https://{host}.{target.region}.{suffix}",
                f"https://{host}-fips.{target.region}.{suffix}",
            }:
                _refuse("HTTP_API_CLIENT_ENDPOINT_UNSUPPORTED")
            credentials.append(client._request_signer._credentials)
        if credentials[0] is None or any(value is not credentials[0] for value in credentials):
            _refuse("HTTP_API_CLIENT_IDENTITY_MISMATCH")

    def _read(self, current: Callable[[], None], client: Any, operation: str, **args: Any) -> dict[str, Any]:
        with _private_io():
            current()
            try:
                reply = getattr(client, operation)(**args)
            except Exception:
                current()
                _refuse("HTTP_API_NATIVE_READ_UNAVAILABLE")
            current()
            if not isinstance(reply, dict):
                _refuse("HTTP_API_METADATA_INVALID")
            return reply

    def _account(self, target: HttpApiRouteTarget, current: Callable[[], None]) -> None:
        identity = self._read(current, self.sts, "get_caller_identity")
        if (
            identity.get("Account") != target.account_id
            or not str(identity.get("Arn", "")).startswith(f"arn:{target.partition}:")
            or str(identity.get("Arn", "")).split(":")[4:5] != [target.account_id]
        ):
            _refuse("HTTP_API_ACCOUNT_MISMATCH")

    def _owner(self, tags: Any, service_id: str) -> None:
        if (
            not isinstance(tags, dict)
            or tags.get("astrolift.io/managed-by") != "platform"
            or tags.get(MANAGED_SERVICE_ID_TAG_AWS) != service_id
        ):
            _refuse("HTTP_API_OWNER_MISMATCH")

    def observe_target(self, target: HttpApiRouteTarget, *, current: Callable[[], None]) -> HttpApiRouteObservation:
        current()
        self._validate(target)
        self._account(target, current)
        suffix = "amazonaws.com.cn" if target.partition == "aws-cn" else "amazonaws.com"
        endpoint = f"https://{target.api_id}.execute-api.{target.region}.{suffix}"
        api = self._read(current, self.api, "get_api", ApiId=target.api_id)
        self._owner(api.get("Tags"), target.api_service_id)
        if (
            api.get("ApiId") != target.api_id
            or api.get("ProtocolType") != "HTTP"
            or api.get("ApiEndpoint") != endpoint
            or api.get("DisableExecuteApiEndpoint") is True
        ):
            _refuse("HTTP_API_TARGET_MISMATCH")
        routes = self._read(current, self.api, "get_routes", ApiId=target.api_id, MaxResults="2")
        rows = routes.get("Items")
        if routes.get("NextToken") or not isinstance(rows, list) or len(rows) != 1:
            _refuse("HTTP_API_ROUTES_UNSUPPORTED")
        route = rows[0]
        if (
            route.get("RouteKey") != "$default"
            or route.get("AuthorizationType") != "NONE"
            or route.get("ApiKeyRequired") is True
        ):
            _refuse("HTTP_API_ROUTE_MISMATCH")
        route_id = _text(route.get("RouteId"), r"[a-z0-9]+", 64)
        integration_id = _text(route.get("Target"), r"integrations/[a-z0-9]+", 80).split("/")[1]
        integration = self._read(
            current, self.api, "get_integration", ApiId=target.api_id, IntegrationId=integration_id
        )
        if (
            integration.get("IntegrationId") != integration_id
            or integration.get("IntegrationType") != "AWS_PROXY"
            or integration.get("IntegrationUri") != target.function_arn
            or integration.get("PayloadFormatVersion") != "2.0"
            or integration.get("ConnectionType", "INTERNET") != "INTERNET"
            or integration.get("CredentialsArn")
        ):
            _refuse("HTTP_API_INTEGRATION_MISMATCH")
        stage = self._read(current, self.api, "get_stage", ApiId=target.api_id, StageName="$default")
        if stage.get("StageName") != "$default" or stage.get("AutoDeploy") is not True or stage.get("StageVariables"):
            _refuse("HTTP_API_STAGE_MISMATCH")
        function = self._read(current, self.function, "get_function", FunctionName=target.function_arn)
        self._owner(function.get("Tags"), target.faas_service_id)
        config = function.get("Configuration", {})
        if (
            config.get("FunctionArn") != target.function_arn
            or config.get("State") != "Active"
            or config.get("LastUpdateStatus") != "Successful"
        ):
            _refuse("HTTP_API_LAMBDA_UNAVAILABLE")
        revision = _text(config.get("RevisionId"), r"[A-Za-z0-9_-]+", 128)
        code = _text(config.get("CodeSha256"), r"[A-Za-z0-9+/=]+", 128)
        policy_reply = self._read(current, self.function, "get_policy", FunctionName=target.function_arn)
        policy_raw = _text(policy_reply.get("Policy"), r"[\s\S]+", 16384)
        try:
            policy = json.loads(policy_raw)
            statements = policy["Statement"]
            if (
                not isinstance(statements, list)
                or not 1 <= len(statements) <= 100
                or any(not isinstance(row, dict) for row in statements)
            ):
                _refuse("HTTP_API_POLICY_INVALID")
            selected = [row for row in statements if row.get("Sid") == "AstroliftApiGatewayInvoke"]
        except (TypeError, KeyError, ValueError, AttributeError):
            _refuse("HTTP_API_POLICY_INVALID")
        expected = f"arn:{target.partition}:execute-api:{target.region}:{target.account_id}:{target.api_id}/*/*"
        if (
            len(selected) != 1
            or selected[0].get("Effect") != "Allow"
            or selected[0].get("Action") != "lambda:InvokeFunction"
            or selected[0].get("Resource") != target.function_arn
            or selected[0].get("Principal") != {"Service": "apigateway.amazonaws.com"}
            or selected[0].get("Condition") != {"ArnLike": {"AWS:SourceArn": expected}}
        ):
            _refuse("HTTP_API_POLICY_MISMATCH")
        self._account(target, current)

        def projection(raw: dict[str, Any]) -> dict[str, Any]:
            return {
                key: value
                for key, value in raw.items()
                if key not in {"ResponseMetadata", "CreatedDate", "LastUpdatedDate"}
            }

        fingerprint = http_api_request_sha256(
            {
                "domain": "astrolift.http-api.source.v1",
                "api": projection(api),
                "route": route,
                "integration": projection(integration),
                "stage": projection(stage),
                "lambda": {"arn": target.function_arn, "revision": revision, "code": code, "tags": function["Tags"]},
                "policy": policy,
            }
        )
        if target.source_fingerprint and target.source_fingerprint != fingerprint:
            _refuse("HTTP_API_SOURCE_CHANGED")
        return HttpApiRouteObservation(target, endpoint, route_id, integration_id, fingerprint)

    def _domain(self, target: HttpApiRouteTarget, raw: dict[str, Any]) -> HttpApiDomainReceipt:
        configs = raw.get("DomainNameConfigurations")
        if (
            raw.get("DomainName") != target.hostname
            or not isinstance(configs, list)
            or len(configs) != 1
            or raw.get("MutualTlsAuthentication")
            or raw.get("RoutingMode", "API_MAPPING_ONLY") != "API_MAPPING_ONLY"
        ):
            _refuse("HTTP_API_DOMAIN_MISMATCH")
        config = configs[0]
        if (
            config.get("CertificateArn") != target.certificate_arn
            or config.get("EndpointType") != "REGIONAL"
            or config.get("SecurityPolicy") != "TLS_1_2"
        ):
            _refuse("HTTP_API_DOMAIN_MISMATCH")
        suffix = "amazonaws.com.cn" if target.partition == "aws-cn" else "amazonaws.com"
        arn = _text(
            raw.get("DomainNameArn"),
            rf"arn:{target.partition}:apigateway:{target.region}::/domainnames/{re.escape(target.hostname)}(?:\+[A-Za-z0-9]+)?",
            512,
        )
        regional = _text(
            config.get("ApiGatewayDomainName"), rf"[a-z0-9-]+\.execute-api\.{target.region}\.{re.escape(suffix)}", 253
        )
        zone = _text(config.get("HostedZoneId"), r"Z[A-Z0-9]+", 64)
        state = _text(
            config.get("DomainNameStatus"),
            r"AVAILABLE|UPDATING|PENDING_CERTIFICATE_REIMPORT|PENDING_OWNERSHIP_VERIFICATION",
            64,
        )
        return HttpApiDomainReceipt(target.hostname, arn, regional, zone, target.certificate_arn, state)

    def observe_domain(
        self, target: HttpApiRouteTarget, original: HttpApiDomainReceipt, *, current: Callable[[], None]
    ) -> HttpApiDomainReceipt:
        self.observe_target(target, current=current)
        raw = self._read(current, self.api, "get_domain_name", DomainName=target.hostname)
        observed = self._domain(target, raw)
        self._owner(raw.get("Tags"), target.api_service_id)
        if (
            observed.hostname,
            observed.domain_name_arn,
            observed.regional_target,
            observed.hosted_zone_id,
            observed.certificate_arn,
        ) != (
            original.hostname,
            original.domain_name_arn,
            original.regional_target,
            original.hosted_zone_id,
            original.certificate_arn,
        ):
            _refuse("HTTP_API_DOMAIN_REPLACED")
        self._account(target, current)
        return observed

    def _effect(
        self,
        target: HttpApiRouteTarget,
        operation: str,
        payload: dict[str, Any],
        parse: Callable[[dict[str, Any]], Acknowledgement],
        hooks: HttpApiEffectHooks,
        current: Callable[[], None],
        before_send: Callable[[], None],
    ) -> Acknowledgement:
        current()
        request_sha256 = http_api_request_sha256(payload)
        submission = hooks.commit_sent(json.loads(json.dumps(payload)))
        if type(submission) is not HttpApiSubmission:
            _refuse("HTTP_API_SUBMISSION_INVALID")
        _uuid(submission.nonce)
        if submission.operation_id != target.operation_id or submission.request_sha256 != request_sha256:
            _refuse("HTTP_API_SUBMISSION_MISMATCH")
        before_send()
        with _private_io():
            current()
            try:
                reply = getattr(self.api, operation)(**payload)
            except Exception:
                _refuse("HTTP_API_WRITE_UNCONFIRMED")
            acknowledgement = parse(reply)
            hooks.record_ack(submission, acknowledgement)
            current()
        self._account(target, current)
        return acknowledgement

    def inspect_allocation(self, target: HttpApiRouteTarget, *, current: Callable[[], None]) -> bool:
        """Return confirmed absence, or refuse an existing/unconfirmed native domain.

        This is an exact read for review, never an ownership/adoption receipt.
        Create performs its own fresh inspection before each native send.
        """
        self.observe_target(target, current=current)
        with _private_io():
            current()
            try:
                self.api.get_domain_name(DomainName=target.hostname)
            except self.api.exceptions.NotFoundException:
                current()
            except Exception:
                current()
                _refuse("HTTP_API_NATIVE_READ_UNAVAILABLE")
            else:
                current()
                _refuse("HTTP_API_DOMAIN_ALREADY_EXISTS")
        self._account(target, current)
        return True

    def create_domain(
        self, target: HttpApiRouteTarget, *, hooks: HttpApiEffectHooks, current: Callable[[], None]
    ) -> HttpApiDomainReceipt:
        if not target.certificate_arn or not target.source_fingerprint:
            _refuse("HTTP_API_REVIEW_REQUIRED")

        def before_send() -> None:
            self.inspect_allocation(target, current=current)

        before_send()
        payload = {
            "DomainName": target.hostname,
            "DomainNameConfigurations": [
                {"CertificateArn": target.certificate_arn, "EndpointType": "REGIONAL", "SecurityPolicy": "TLS_1_2"}
            ],
            "RoutingMode": "API_MAPPING_ONLY",
            "Tags": {"astrolift.io/managed-by": "platform", MANAGED_SERVICE_ID_TAG_AWS: target.api_service_id},
        }
        return self._effect(
            target, "create_domain_name", payload, lambda raw: self._domain(target, raw), hooks, current, before_send
        )

    def create_mapping(
        self,
        target: HttpApiRouteTarget,
        domain: HttpApiDomainReceipt,
        *,
        hooks: HttpApiEffectHooks,
        current: Callable[[], None],
    ) -> HttpApiMappingReceipt:
        if not target.source_fingerprint:
            _refuse("HTTP_API_REVIEW_REQUIRED")

        def before_send() -> None:
            observed = self.observe_domain(target, domain, current=current)
            if observed.state != "AVAILABLE":
                _refuse("HTTP_API_DOMAIN_PENDING")
            mappings = self._read(current, self.api, "get_api_mappings", DomainName=target.hostname, MaxResults="1")
            if mappings.get("Items") != [] or mappings.get("NextToken"):
                _refuse("HTTP_API_MAPPING_ALREADY_EXISTS")

        before_send()
        payload = {"DomainName": target.hostname, "ApiId": target.api_id, "Stage": "$default", "ApiMappingKey": ""}

        def parse(raw: dict[str, Any]) -> HttpApiMappingReceipt:
            if (
                raw.get("ApiId") != target.api_id
                or raw.get("Stage") != "$default"
                or raw.get("ApiMappingKey", "") != ""
            ):
                _refuse("HTTP_API_MAPPING_MISMATCH")
            return HttpApiMappingReceipt(
                target.hostname,
                domain.domain_name_arn,
                _text(raw.get("ApiMappingId"), r"[a-z0-9]+", 64),
                target.api_id,
                "$default",
            )

        return self._effect(target, "create_api_mapping", payload, parse, hooks, current, before_send)

    def observe_mapping(
        self,
        target: HttpApiRouteTarget,
        domain: HttpApiDomainReceipt,
        original: HttpApiMappingReceipt,
        *,
        current: Callable[[], None],
    ) -> HttpApiMappingReceipt:
        self.observe_domain(target, domain, current=current)
        rows = self._read(current, self.api, "get_api_mappings", DomainName=target.hostname, MaxResults="2")
        if (
            rows.get("NextToken")
            or rows.get("Items")
            != [
                {
                    "ApiId": original.api_id,
                    "ApiMappingId": original.mapping_id,
                    "ApiMappingKey": original.mapping_key,
                    "Stage": original.stage,
                }
            ]
            or original.hostname != target.hostname
            or original.domain_name_arn != domain.domain_name_arn
            or original.api_id != target.api_id
            or original.stage != "$default"
            or original.mapping_key
        ):
            _refuse("HTTP_API_MAPPING_REPLACED")
        self._account(target, current)
        return original
