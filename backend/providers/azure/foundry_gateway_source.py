"""Bounded exact ARM observations; no inference, IAM or runtime admission proof."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

from azure.foundry_gateway import FoundryGatewaySource, _private_io

ARM_SCOPE = "https://management.azure.com/.default"
API_VERSION = "2025-06-01"
MAX_RESPONSE_BYTES = 256 * 1024
WALL_SECONDS = 30
_GROUP = re.compile(r"[A-Za-z0-9_.()-]{1,90}\Z", re.ASCII)
_REGION = re.compile(r"[a-z0-9]{1,64}\Z", re.ASCII)
_ACCOUNT_PROPERTIES = frozenset(
    [
        "provisioningState",
        "endpoint",
        "internalId",
        "capabilities",
        "isMigrated",
        "migrationToken",
        "skuChangeInfo",
        "customSubDomainName",
        "networkAcls",
        "encryption",
        "userOwnedStorage",
        "amlWorkspace",
        "privateEndpointConnections",
        "publicNetworkAccess",
        "apiProperties",
        "dateCreated",
        "callRateLimit",
        "dynamicThrottlingEnabled",
        "storedCompletionsDisabled",
        "quotaLimit",
        "restrictOutboundNetworkAccess",
        "allowedFqdnList",
        "disableLocalAuth",
        "endpoints",
        "restore",
        "deletionDate",
        "scheduledPurgeDate",
        "locations",
        "commitmentPlanAssociations",
        "abusePenalty",
        "raiMonitorConfig",
        "networkInjections",
        "allowProjectManagement",
        "defaultProject",
        "associatedProjects",
    ]
)
_DEPLOYMENT_PROPERTIES = frozenset(
    [
        "provisioningState",
        "model",
        "scaleSettings",
        "capabilities",
        "raiPolicyName",
        "callRateLimit",
        "rateLimits",
        "versionUpgradeOption",
        "dynamicThrottlingEnabled",
        "currentCapacity",
        "capacitySettings",
        "parentDeploymentName",
        "spilloverDeploymentName",
    ]
)


class SourceReason(StrEnum):
    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    NATIVE_UNAVAILABLE = "native_unavailable"
    INVALID_RESPONSE = "invalid_response"
    SOURCE_CHANGED = "source_changed"
    ENDPOINT_UNVERIFIED = "endpoint_unverified"
    ROUTING_UNVERIFIED = "routing_unverified"
    MUTABLE_ROUTING = "mutable_routing"
    NOT_SUCCEEDED = "not_succeeded"
    CHAT_CAPABILITY_UNVERIFIED = "chat_capability_unverified"
    RUNTIME_DECLARATION_REQUIRED = "runtime_declaration_required"


class SourceError(ValueError):
    def __init__(self, reason: SourceReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class FoundrySourceTarget:
    source: FoundryGatewaySource
    resource_group: str
    region: str
    model_format: str

    def __post_init__(self) -> None:
        if (
            type(self.source) is not FoundryGatewaySource
            or not isinstance(self.resource_group, str)
            or not _GROUP.fullmatch(self.resource_group)
            or self.resource_group.endswith(".")
            or not isinstance(self.region, str)
            or not _REGION.fullmatch(self.region)
            or not isinstance(self.model_format, str)
            or not 1 <= len(self.model_format) <= 64
            or not self.model_format.isascii()
            or any(ord(c) < 33 for c in self.model_format)
        ):
            raise SourceError(SourceReason.SOURCE_CHANGED)

    @property
    def account_id(self) -> str:
        return (
            f"/subscriptions/{self.source.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{self.source.account_name}"
        )

    @property
    def deployment_id(self) -> str:
        return f"{self.account_id}/deployments/{self.source.deployment_name}"


@dataclass(frozen=True, slots=True)
class OptionalText:
    present: bool
    value: str | None


@dataclass(frozen=True, slots=True)
class FoundrySourceMetadata:
    account_id: str
    deployment_id: str
    region: str
    kind: str
    account_etag: str
    deployment_etag: str
    account_created_at: str
    deployment_created_at: str
    custom_subdomain: OptionalText
    endpoint: OptionalText
    endpoints: tuple[str, ...]
    endpoints_present: bool
    endpoints_fingerprint: str | None
    account_capabilities_present: bool
    account_capabilities_fingerprint: str | None
    local_auth_disabled: bool | None
    account_state: OptionalText
    deployment_state: OptionalText
    model_format: str
    model_name: str
    model_version: str
    upgrade_policy: OptionalText
    parent_deployment: OptionalText
    spillover_deployment: OptionalText
    model_source: OptionalText
    model_source_account: OptionalText
    default_project: OptionalText
    regional_routing_present: bool
    regional_routing_fingerprint: str | None
    chat_completion: bool | None

    def fingerprint(self, target: FoundrySourceTarget) -> str:
        # A review digest is an observation commitment, not an ARM incarnation/CAS token.
        declaration = asdict(target)
        del declaration["source"]["source_fingerprint"]
        payload = {"schema": 1, "target": declaration, "metadata": asdict(self)}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FoundrySourceObservation:
    metadata_verified: bool = False
    metadata: FoundrySourceMetadata | None = None
    fingerprint: str | None = None
    reasons: tuple[SourceReason, ...] = ()
    native_gets: int = 0


Checkpoint = Callable[[FoundrySourceTarget], object]
CredentialFactory = Callable[[], Any]


def _text(value: Any, limit: int = 256) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit or any(ord(c) < 32 for c in value):
        raise SourceError(SourceReason.INVALID_RESPONSE)
    return value


def _optional(data: dict[str, Any], key: str, limit: int = 256) -> OptionalText:
    if key not in data:
        return OptionalText(False, None)
    value = data[key]
    if value is None or value == "":
        return OptionalText(True, value)
    return OptionalText(True, _text(value, limit))


def _arm_source(data: dict[str, Any], key: str) -> OptionalText:
    value = _optional(data, key, 512)
    if value.value and not re.fullmatch(
        r"/subscriptions/[a-f0-9-]{36}/resourceGroups/[A-Za-z0-9_.()-]{1,90}"
        r"/providers/[A-Za-z0-9.]+(?:/[A-Za-z0-9_.()-]{1,128}){2,8}",
        value.value,
        re.ASCII,
    ):
        raise SourceError(SourceReason.INVALID_RESPONSE)
    return value


def _mapping(value: Any, limit: int = 64) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) > limit or any(not isinstance(k, str) for k in value):
        raise SourceError(SourceReason.INVALID_RESPONSE)
    return value


def _json(body: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SourceError(SourceReason.INVALID_RESPONSE)
            result[key] = value
        return result

    try:
        result = _mapping(json.loads(body, object_pairs_hook=unique))

        def bounded(value: Any, depth: int = 0) -> None:
            if depth > 16:
                raise SourceError(SourceReason.INVALID_RESPONSE)
            if isinstance(value, (dict, list)):
                if len(value) > 64:
                    raise SourceError(SourceReason.INVALID_RESPONSE)
                for child in value.values() if isinstance(value, dict) else value:
                    bounded(child, depth + 1)

        bounded(result)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise SourceError(SourceReason.INVALID_RESPONSE) from None


class _BoundedResponse:
    @staticmethod
    def build(request: Any, body: bytes) -> Any:
        from azure.core.pipeline.transport import HttpResponse

        class Response(HttpResponse):  # type: ignore[misc]
            def __init__(self) -> None:
                super().__init__(request, None)
                self.status_code = 200
                self.headers = {"Content-Type": "application/json"}
                self.content_type = "application/json"
                self.reason = "OK"

            def body(self) -> bytes:
                return body

            def stream_download(self, pipeline: Any, **kwargs: Any) -> Any:
                return iter([body])

        return Response()


class _FixedTransport:
    @staticmethod
    def build(delegate: Any, owner: FoundrySourceObserver) -> Any:
        from azure.core.pipeline.transport import HttpTransport

        class Transport(HttpTransport):  # type: ignore[misc]
            def open(self) -> None:
                owner._check()
                delegate.open()
                owner._check()

            def close(self) -> None:
                delegate.close()

            def __enter__(self) -> Any:
                self.open()
                return self

            def __exit__(self, *args: Any) -> None:
                self.close()

            def send(self, request: Any, **kwargs: Any) -> Any:
                owner._check()
                url = urlsplit(request.url)
                if (
                    request.method != "GET"
                    or url.scheme != "https"
                    or url.netloc != "management.azure.com"
                    or url.fragment
                    or url.path
                    not in {quote(owner.target.account_id, safe="/"), quote(owner.target.deployment_id, safe="/")}
                    or parse_qsl(url.query, keep_blank_values=True) != [("api-version", API_VERSION)]
                    or getattr(request, "data", None)
                ):
                    raise SourceError(SourceReason.INVALID_RESPONSE)
                request.headers["Accept-Encoding"] = "identity"
                remaining = owner._remaining()
                kwargs.update(stream=True, connection_timeout=min(5, remaining), read_timeout=min(5, remaining))
                owner._gets += 1
                response = None
                try:
                    response = delegate.send(request, **kwargs)
                    owner._check()
                    if response.status_code != 200:
                        raise SourceError(SourceReason.NATIVE_UNAVAILABLE)
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    if (
                        headers.get("content-encoding", "identity").lower() != "identity"
                        or headers.get("content-type", "").split(";")[0].strip().lower() != "application/json"
                    ):
                        raise SourceError(SourceReason.INVALID_RESPONSE)
                    length = headers.get("content-length")
                    if length is not None and (not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
                        raise SourceError(SourceReason.INVALID_RESPONSE)
                    body = bytearray()
                    for chunk in response.stream_download(None):
                        owner._check()
                        if not isinstance(chunk, bytes) or len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise SourceError(SourceReason.INVALID_RESPONSE)
                        body.extend(chunk)
                    owner._check()
                    if length is not None and len(body) != int(length):
                        raise SourceError(SourceReason.INVALID_RESPONSE)
                    owner._last_body = bytes(body)
                    _json(owner._last_body)
                    return _BoundedResponse.build(request, owner._last_body)
                finally:
                    try:
                        if response is not None:
                            close = getattr(response, "close", None)
                            if callable(close):
                                close()
                            else:
                                internal = getattr(response, "internal_response", None)
                                if internal is not None:
                                    internal.close()
                    finally:
                        owner._check()

        return Transport()


@dataclass(slots=True)
class FoundrySourceObserver:
    target: FoundrySourceTarget
    checkpoint: Checkpoint = field(repr=False)
    credential_factory: CredentialFactory = field(repr=False)
    transport_factory: Callable[[], Any] | None = field(default=None, repr=False)
    _deadline: float = field(init=False, default=0, repr=False)
    _gets: int = field(init=False, default=0, repr=False)
    _last_body: bytes = field(init=False, default=b"", repr=False)
    _running: bool = field(init=False, default=False, repr=False)
    _lock: Any = field(init=False, default_factory=threading.Lock, repr=False)

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError(SourceReason.NATIVE_UNAVAILABLE)
        return remaining

    def _check(self) -> None:
        self._remaining()
        try:
            if self.checkpoint(self.target) is not None:
                raise ValueError
        except Exception:
            raise SourceError(SourceReason.AUTHORITY_UNAVAILABLE) from None

    def observe(self) -> FoundrySourceObservation:
        if type(self.target) is not FoundrySourceTarget or not self._lock.acquire(blocking=False):
            return FoundrySourceObservation(reasons=(SourceReason.SOURCE_CHANGED,))
        self._running = True
        self._gets = 0
        self._deadline = time.monotonic() + WALL_SECONDS
        client = credential = None
        try:
            self._check()
            from azure.core.pipeline.transport import RequestsTransport
            from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient

            with _private_io():
                self._check()
                credential = self.credential_factory()
                self._check()
                native_credential = credential
                owner = self

                class Credential:
                    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
                        owner._check()
                        if scopes != (ARM_SCOPE,):
                            raise SourceError(SourceReason.INVALID_RESPONSE)
                        try:
                            with _private_io():
                                kwargs["tracing_options"] = {"enabled": False}
                                return native_credential.get_token(ARM_SCOPE, **kwargs)
                        finally:
                            owner._check()

                self._check()
                delegate = (
                    self.transport_factory() if self.transport_factory else RequestsTransport(use_env_settings=False)
                )
                self._check()
                client = CognitiveServicesManagementClient(
                    Credential(),
                    self.target.source.subscription_id,
                    base_url="https://management.azure.com",
                    api_version=API_VERSION,
                    credential_scopes=[ARM_SCOPE],
                    transport=_FixedTransport.build(delegate, self),
                    retry_total=0,
                    retry_connect=0,
                    retry_read=0,
                    retry_status=0,
                    permit_redirects=False,
                    redirect_max=0,
                    logging_enable=False,
                )
                self._check()
                account = self._read(client.accounts.get, self.target.resource_group, self.target.source.account_name)
                deployment = self._read(
                    client.deployments.get,
                    self.target.resource_group,
                    self.target.source.account_name,
                    self.target.source.deployment_name,
                )
                metadata = self._metadata(account, deployment)
                repeat_account = self._read(
                    client.accounts.get, self.target.resource_group, self.target.source.account_name
                )
                repeat_deployment = self._read(
                    client.deployments.get,
                    self.target.resource_group,
                    self.target.source.account_name,
                    self.target.source.deployment_name,
                )
                if metadata != self._metadata(repeat_account, repeat_deployment):
                    raise SourceError(SourceReason.SOURCE_CHANGED)
                fingerprint = metadata.fingerprint(self.target)
                if fingerprint != self.target.source.source_fingerprint:
                    raise SourceError(SourceReason.SOURCE_CHANGED)
                self._check()
                return FoundrySourceObservation(True, metadata, fingerprint, self._eligibility(metadata), self._gets)
        except SourceError as exc:
            return FoundrySourceObservation(reasons=(exc.reason,), native_gets=self._gets)
        except Exception:
            return FoundrySourceObservation(reasons=(SourceReason.NATIVE_UNAVAILABLE,), native_gets=self._gets)
        finally:
            with _private_io():
                for resource in (client, credential):
                    try:
                        if resource is not None and callable(getattr(resource, "close", None)):
                            resource.close()
                    except Exception:
                        pass
            self._last_body = b""
            self._running = False
            self._lock.release()

    def _read(self, get: Callable[..., Any], *args: str) -> dict[str, Any]:
        self._check()
        get(*args, api_version=API_VERSION, logging_enable=False, tracing_options={"enabled": False})
        self._check()
        return _json(self._last_body)

    def _metadata(self, account: dict[str, Any], deployment: dict[str, Any]) -> FoundrySourceMetadata:
        for data, expected_id, expected_name, expected_type in (
            (account, self.target.account_id, self.target.source.account_name, "Microsoft.CognitiveServices/accounts"),
            (
                deployment,
                self.target.deployment_id,
                self.target.source.deployment_name,
                "Microsoft.CognitiveServices/accounts/deployments",
            ),
        ):
            if data.get("id") != expected_id or data.get("name") != expected_name or data.get("type") != expected_type:
                raise SourceError(SourceReason.SOURCE_CHANGED)
        if account.get("location") != self.target.region or account.get("kind") != "AIServices":
            raise SourceError(SourceReason.SOURCE_CHANGED)
        ap = _mapping(account.get("properties"))
        dp = _mapping(deployment.get("properties"))
        model = _mapping(dp.get("model"))
        if (
            ap.keys() - _ACCOUNT_PROPERTIES
            or dp.keys() - _DEPLOYMENT_PROPERTIES
            or model.keys() - {"publisher", "format", "name", "version", "source", "sourceAccount", "callRateLimit"}
        ):
            raise SourceError(SourceReason.INVALID_RESPONSE)
        if (
            model.get("format") != self.target.model_format
            or model.get("name") != self.target.source.native_model_name
            or model.get("version") != self.target.source.native_model_version
        ):
            raise SourceError(SourceReason.SOURCE_CHANGED)
        endpoints = _mapping(ap["endpoints"], 16) if "endpoints" in ap else {}
        values = tuple(sorted({_text(value, 512) for value in endpoints.values()}))
        endpoint = _optional(ap, "endpoint", 512)
        for value in (*values, endpoint.value):
            if not value:
                continue
            url = urlsplit(value)
            if (
                url.scheme != "https"
                or url.hostname
                not in {
                    f"{self.target.source.account_name}.services.ai.azure.com",
                    f"{self.target.source.account_name}.cognitiveservices.azure.com",
                    f"{self.target.source.account_name}.openai.azure.com",
                }
                or url.username
                or url.password
                or url.port
                or url.query
                or url.fragment
                or url.path not in {"", "/", "/models", "/models/", "/openai", "/openai/", "/openai/v1", "/openai/v1/"}
            ):
                raise SourceError(SourceReason.INVALID_RESPONSE)
        if any(len(key) > 128 for key in endpoints):
            raise SourceError(SourceReason.INVALID_RESPONSE)
        endpoint_digest = (
            hashlib.sha256(json.dumps(endpoints, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if "endpoints" in ap
            else None
        )
        account_capabilities = ap.get("capabilities")
        account_capability_digest = None
        if account_capabilities is not None:
            if not isinstance(account_capabilities, list) or len(account_capabilities) > 64:
                raise SourceError(SourceReason.INVALID_RESPONSE)
            for capability in account_capabilities:
                capability = _mapping(capability, 2)
                if capability.keys() - {"name", "value"}:
                    raise SourceError(SourceReason.INVALID_RESPONSE)
                _text(capability.get("name"), 128)
                _text(capability.get("value"), 256)
            account_capability_digest = hashlib.sha256(
                json.dumps(account_capabilities, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        capabilities = _mapping(dp.get("capabilities", {}), 64)
        if any(len(k) > 128 or not isinstance(v, str) or len(v) > 256 for k, v in capabilities.items()):
            raise SourceError(SourceReason.INVALID_RESPONSE)
        chat = {"true": True, "false": False}.get(capabilities.get("chatCompletion", ""))
        local_auth = ap.get("disableLocalAuth")
        if local_auth is not None and not isinstance(local_auth, bool):
            raise SourceError(SourceReason.INVALID_RESPONSE)
        locations = ap.get("locations")
        routing_digest = None
        if locations is not None:
            locations = _mapping(locations)
            if locations.keys() - {"routingMethod", "regions"}:
                raise SourceError(SourceReason.INVALID_RESPONSE)
            routing_digest = hashlib.sha256(json.dumps(locations, sort_keys=True).encode()).hexdigest()
        return FoundrySourceMetadata(
            account_id=self.target.account_id,
            deployment_id=self.target.deployment_id,
            region=self.target.region,
            kind="AIServices",
            account_etag=_text(account.get("etag")),
            deployment_etag=_text(deployment.get("etag")),
            account_created_at=_text(_mapping(account.get("systemData")).get("createdAt"), 64),
            deployment_created_at=_text(_mapping(deployment.get("systemData")).get("createdAt"), 64),
            custom_subdomain=_optional(ap, "customSubDomainName", 64),
            endpoint=endpoint,
            endpoints=values,
            endpoints_present="endpoints" in ap,
            endpoints_fingerprint=endpoint_digest,
            account_capabilities_present="capabilities" in ap,
            account_capabilities_fingerprint=account_capability_digest,
            local_auth_disabled=local_auth,
            account_state=_optional(ap, "provisioningState", 64),
            deployment_state=_optional(dp, "provisioningState", 64),
            model_format=self.target.model_format,
            model_name=self.target.source.native_model_name,
            model_version=self.target.source.native_model_version,
            upgrade_policy=_optional(dp, "versionUpgradeOption", 64),
            parent_deployment=_optional(dp, "parentDeploymentName", 128),
            spillover_deployment=_optional(dp, "spilloverDeploymentName", 128),
            model_source=_arm_source(model, "source"),
            model_source_account=_arm_source(model, "sourceAccount"),
            default_project=_optional(ap, "defaultProject", 128),
            regional_routing_present="locations" in ap,
            regional_routing_fingerprint=routing_digest,
            chat_completion=chat,
        )

    def _eligibility(self, metadata: FoundrySourceMetadata) -> tuple[SourceReason, ...]:
        reasons = []
        origin = f"https://{self.target.source.account_name}.services.ai.azure.com"
        observed = (metadata.endpoint.value, *metadata.endpoints)
        if metadata.custom_subdomain.value != self.target.source.account_name or not any(
            value in {origin, origin + "/"} for value in observed
        ):
            reasons.append(SourceReason.ENDPOINT_UNVERIFIED)
        routing = (
            metadata.parent_deployment,
            metadata.spillover_deployment,
            metadata.model_source,
            metadata.model_source_account,
            metadata.default_project,
        )
        if (
            any(not value.present for value in routing)
            or not metadata.upgrade_policy.present
            or not metadata.regional_routing_present
        ):
            reasons.append(SourceReason.ROUTING_UNVERIFIED)
        if (
            any(value.value for value in routing)
            or metadata.upgrade_policy.value not in {None, "NoAutoUpgrade"}
            or metadata.regional_routing_fingerprint is not None
        ):
            reasons.append(SourceReason.MUTABLE_ROUTING)
        if metadata.upgrade_policy.present and metadata.upgrade_policy.value is None:
            reasons.append(SourceReason.ROUTING_UNVERIFIED)
        if metadata.account_state.value != "Succeeded" or metadata.deployment_state.value != "Succeeded":
            reasons.append(SourceReason.NOT_SUCCEEDED)
        if metadata.chat_completion is not True:
            reasons.append(SourceReason.CHAT_CAPABILITY_UNVERIFIED)
        reasons.append(SourceReason.RUNTIME_DECLARATION_REQUIRED)
        return tuple(dict.fromkeys(reasons))
