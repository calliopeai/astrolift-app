"""Azure Functions managed-service driver.

The preview owns one ``Microsoft.Web/sites`` Function App. Hosting plans,
deployment/host storage, registries, and user-assigned managed identities are
operator-owned dependencies selected from install-time allowlists. The driver
never accepts storage connection strings, registry passwords, SAS URLs, or
plain secret application settings.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
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
from azure.managed.tags import MANAGED_BY_TAG, MANAGED_SERVICE_ID_TAG, arm_tags_for

KIND = "faas"
VARIANT = "azure_functions"

_FUNCTION_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])$")
_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
_STORAGE_ACCOUNT_RE = re.compile(r"^[a-z0-9]{3,24}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
_IMAGE_RE = re.compile(r"^(?P<host>[a-z0-9.-]+)/(?P<path>[a-z0-9._/-]+)@sha256:(?P<digest>[0-9a-f]{64})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_APP_SETTING_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_DNS_SUFFIX_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_API_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-preview)?$")

_SITE_API_VERSION = "2024-11-01"
_IDENTITY_API_VERSION = "2023-01-31"
_AUTHORIZATION_API_VERSION = "2022-04-01"

_STORAGE_BLOB_DATA_OWNER = "b7e6dc6d-f1e8-4753-8033-0f276bb0955b"
_STORAGE_QUEUE_DATA_CONTRIBUTOR = "974c5e8b-45b9-4653-ba55-5f855dd0fb88"
_STORAGE_ACCOUNT_CONTRIBUTOR = "17d1049b-9a84-46fb-8f53-869881c3d3ab"
_ACR_PULL = "7f951dda-4ed3-4680-a7ca-43fe172d538d"

_PLATFORM_SETTINGS = {
    "FUNCTIONS_EXTENSION_VERSION",
    "FUNCTIONS_WORKER_RUNTIME",
    "WEBSITES_ENABLE_APP_SERVICE_STORAGE",
    "DOCKER_REGISTRY_SERVER_URL",
    "AzureWebJobsStorage",
    "AzureWebJobsStorage__accountName",
    "AzureWebJobsStorage__credential",
    "AzureWebJobsStorage__clientId",
    "AzureWebJobsStorage__managedIdentityResourceId",
}
_SECRET_MARKERS = (
    "accountkey=",
    "sharedaccesssignature=",
    "defaultendpointsprotocol=",
    "password=",
    "sig=",
)


class AzureFunctionsError(RuntimeError):
    pass


class AzureFunctionsNotFound(AzureFunctionsError):
    pass


class AzureFunctionsConflict(AzureFunctionsError):
    pass


@dataclass(frozen=True)
class ArmHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes = b""


ArmTransport = Callable[[str, str, Mapping[str, str], bytes | None, float], ArmHttpResponse]


@dataclass(frozen=True)
class AzureFunctionsConfig:
    subscription_id: str
    resource_group: str
    location: str = "eastus"
    function_name_prefix: str = "astrolift"
    default_plan_resource_id: str = ""
    default_identity_resource_id: str = ""
    allowed_plan_resource_ids: tuple[str, ...] = ()
    allowed_identity_resource_ids: tuple[str, ...] = ()
    allowed_storage_resource_ids: tuple[str, ...] = ()
    allowed_registry_resource_ids: tuple[str, ...] = ()
    allowed_subnet_resource_ids: tuple[str, ...] = ()
    allow_public_network: bool = False
    deletion_protection_default: bool = True
    storage_blob_endpoint_suffix: str = "blob.core.windows.net"
    registry_login_server_suffix: str = "azurecr.io"
    site_api_version: str = _SITE_API_VERSION
    identity_api_version: str = _IDENTITY_API_VERSION
    storage_api_version: str = "2023-05-01"
    registry_api_version: str = "2023-07-01"
    authorization_api_version: str = _AUTHORIZATION_API_VERSION
    operation_timeout_seconds: float = 900
    poll_interval_seconds: float = 3
    max_instances: int = 100
    client: Any | None = None


class AzureFunctionsRestClient:
    """Small authenticated ARM adapter with same-origin LRO polling."""

    def __init__(
        self,
        *,
        endpoint: str = "https://management.azure.com",
        credential: Any | None = None,
        transport: ArmTransport | None = None,
        timeout_seconds: float = 900,
        poll_interval_seconds: float = 3,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        parsed = urllib.parse.urlsplit(self._endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Azure management endpoint must be an HTTPS origin")
        self._origin = (parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or 443)
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        self._credential = credential
        self._transport = transport or _urllib_transport
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._sleep = sleep
        self._monotonic = monotonic

    def get(self, resource_id: str, api_version: str) -> dict[str, Any]:
        return self._request("GET", resource_id, api_version=api_version)

    def put(self, resource_id: str, api_version: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", resource_id, api_version=api_version, body=body)

    def patch(self, resource_id: str, api_version: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", resource_id, api_version=api_version, body=body)

    def delete(self, resource_id: str, api_version: str) -> None:
        self._request("DELETE", resource_id, api_version=api_version)

    def _request(
        self,
        method: str,
        resource_id: str,
        *,
        api_version: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not resource_id.startswith("/") or "?" in resource_id or "#" in resource_id:
            raise AzureFunctionsError("ARM resource ID must be an absolute query-free path")
        url = f"{self._endpoint}{resource_id}?{urllib.parse.urlencode({'api-version': api_version})}"
        response = self._send(method, url, body)
        if response.status_code == 404:
            raise AzureFunctionsNotFound(resource_id)
        if response.status_code == 409:
            raise AzureFunctionsConflict(_error_detail(response))
        if response.status_code not in {200, 201, 202, 204}:
            raise AzureFunctionsError(
                f"ARM HTTP {response.status_code}: {_error_detail(response)}",
            )
        poll_url = _header(response.headers, "Azure-AsyncOperation") or _header(response.headers, "Location")
        if response.status_code == 202 or poll_url:
            if not poll_url:
                raise AzureFunctionsError("ARM accepted an asynchronous request without a polling URL")
            return self._poll(poll_url)
        return _json_object(response)

    def _send(self, method: str, url: str, body: dict[str, Any] | None) -> ArmHttpResponse:
        token = self._credential.get_token("https://management.azure.com/.default").token
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        return self._transport(method, url, headers, payload, 30)

    def _poll(self, url: str) -> dict[str, Any]:
        if not self._same_origin(url):
            raise AzureFunctionsError("ARM returned an untrusted asynchronous polling URL")
        deadline = self._monotonic() + self._timeout_seconds
        while True:
            response = self._send("GET", url, None)
            if response.status_code not in {200, 201, 202, 204}:
                raise AzureFunctionsError(
                    f"ARM operation HTTP {response.status_code}: {_error_detail(response)}",
                )
            payload = _json_object(response)
            status = str(payload.get("status") or payload.get("properties", {}).get("provisioningState") or "")
            if status.lower() in {"succeeded", "completed"}:
                return payload
            if status.lower() in {"failed", "canceled", "cancelled"}:
                raise AzureFunctionsError(f"ARM operation {status}: {_operation_error(payload)}")
            if response.status_code in {200, 201, 204} and not status:
                return payload
            if self._monotonic() >= deadline:
                raise AzureFunctionsError("ARM operation timed out")
            retry = _header(response.headers, "Retry-After")
            delay = float(retry) if retry and retry.isdigit() else self._poll_interval_seconds
            self._sleep(delay)

    def _same_origin(self, url: str) -> bool:
        parsed = urllib.parse.urlsplit(url)
        return (
            parsed.scheme.lower(),
            (parsed.hostname or "").lower(),
            parsed.port or 443,
        ) == self._origin


class AzureFunctionsDriver(ManagedServiceDriver):
    KIND = KIND

    def __init__(self, *, config: AzureFunctionsConfig) -> None:
        self._config = config
        self._client = config.client or AzureFunctionsRestClient(
            timeout_seconds=config.operation_timeout_seconds,
            poll_interval_seconds=config.poll_interval_seconds,
        )

    @driver_op(
        cloud="azure",
        driver="faas_azure_functions",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate(cfg, update=False)
        if error:
            return ProvisionResult(False, "", error, ["invalid_azure_functions_config"])
        name = self._function_name(spec, cfg)
        handle = _handle(self._config.resource_group, name)
        resource_id = self._site_resource_id(self._config.resource_group, name)
        try:
            current = self._get(resource_id, self._config.site_api_version)
            if current is not None:
                self._assert_owned(current, spec.managed_service_id)
            resolved = self._resolve_dependencies(cfg)
            self._assert_plan(resolved["plan"], cfg)
            body = self._site_body(spec, cfg, resolved, current=current)
            if current is None:
                self._client.put(resource_id, self._config.site_api_version, body)
            else:
                self._client.patch(resource_id, self._config.site_api_version, body)
            self._ensure_role_assignments(name, cfg, resolved)
            if cfg["deployment_mode"] == "flex_zip":
                self._deploy_package_if_needed(resource_id, cfg, current)
                self._stamp_package_digest(resource_id, body["tags"], str(cfg["package_sha256"]))
            ready_resource = self._client.get(resource_id, self._config.site_api_version)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Azure Function App: {exc}", [str(exc)])
        ready = _site_state(ready_resource) == "available"
        return ProvisionResult(True, handle, f"Azure Function App {name} reconciled", ready=ready)

    @driver_op(cloud="azure", driver="faas_azure_functions")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            resource_group, name = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        resource_id = self._site_resource_id(resource_group, name)
        try:
            current = self._client.get(resource_id, self._config.site_api_version)
            self._assert_owned(current)
            if spec.config.get("function_name") not in {None, name}:
                return UpdateResult(
                    False,
                    spec.handle,
                    "function_name is immutable; reprovision the Function App",
                    ["immutable_field"],
                )
            package_keys = {key for key in ("package_uri", "package_sha256") if key in spec.config}
            if package_keys and package_keys != {"package_uri", "package_sha256"}:
                return UpdateResult(
                    False,
                    spec.handle,
                    "package_uri and package_sha256 must be updated together",
                    ["invalid_azure_functions_config"],
                )
            cfg = self._merge_current_config(current, dict(spec.config or {}))
            error = self._validate(cfg, update=True)
            if error:
                return UpdateResult(
                    False,
                    spec.handle,
                    error,
                    ["invalid_azure_functions_config"],
                )
            resolved = self._resolve_dependencies(cfg)
            self._assert_plan(resolved["plan"], cfg)
            self._ensure_role_assignments(name, cfg, resolved)
            body = self._update_body(cfg, resolved, current)
            self._client.patch(resource_id, self._config.site_api_version, body)
            if cfg["deployment_mode"] == "flex_zip" and "package_uri" in spec.config:
                self._deploy_package_if_needed(resource_id, cfg, current)
                self._stamp_package_digest(resource_id, body["tags"], str(cfg["package_sha256"]))
        except AzureFunctionsNotFound:
            return UpdateResult(False, spec.handle, "Azure Function App not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Azure Function App: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Azure Function App {name} reconciled")

    @driver_op(
        cloud="azure",
        driver="faas_azure_functions",
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
        try:
            resource_group, name = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        resource_id = self._site_resource_id(resource_group, name)
        current = self._get(resource_id, self._config.site_api_version)
        if current is not None:
            try:
                self._assert_owned(current)
            except Exception as exc:
                return DeprovisionResult(False, spec.handle, str(exc), ["ownership_guard"], retryable=False)
            protected = str((current.get("tags") or {}).get("astrolift-deletion-protection") or "true") == "true"
            if protected and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Azure Function App deletion protection is enabled; use force_destroy",
                    ["deletion_protection_enabled"],
                    retryable=False,
                )
            try:
                self._client.delete(resource_id, self._config.site_api_version)
            except AzureFunctionsNotFound:
                pass
            except Exception as exc:
                return DeprovisionResult(False, spec.handle, f"delete Azure Function App: {exc}", [str(exc)])
        cleanup = self._cleanup_role_assignments(name, dict(spec.config or {}))
        if cleanup:
            return DeprovisionResult(False, spec.handle, cleanup, ["role_assignment_cleanup_failed"])
        return DeprovisionResult(
            True,
            spec.handle,
            f"Azure Function App {name} deleted; external plan, identity, storage, registry, and packages retained",
        )

    @driver_op(cloud="azure", driver="faas_azure_functions")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            resource_group, name = _parse_handle(handle.handle)
            resource = self._client.get(
                self._site_resource_id(resource_group, name),
                self._config.site_api_version,
            )
            self._assert_owned(resource)
        except AzureFunctionsNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Azure Function App does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Azure Function App: {exc}")
        state = _site_state(resource)
        provider_state = str((resource.get("properties") or {}).get("state") or "unknown")
        return ServiceStatus(handle.handle, state, f"Azure Function App {name} is {provider_state}")

    @driver_op(cloud="azure", driver="faas_azure_functions")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        resource_group, name = _parse_handle(handle.handle)
        resource = self._client.get(
            self._site_resource_id(resource_group, name),
            self._config.site_api_version,
        )
        self._assert_owned(resource)
        properties = dict(resource.get("properties") or {})
        hostname = str(properties.get("defaultHostName") or "")
        url = f"https://{hostname}" if hostname else ""
        identity_ids = sorted((resource.get("identity") or {}).get("userAssignedIdentities") or {})
        identity_id = identity_ids[0] if len(identity_ids) == 1 else ""
        return Binding(
            env_vars={
                "FUNCTION_NAME": ValueRef(literal=name),
                # FUNCTION_ARN is the portable resource-locator slot for this
                # kind, not an AWS-only name: Knative fills it with a k8s://
                # URI. Only the keys in the platform's faas envelope are
                # injected into a workload, so the ARM resource ID has to land
                # here rather than in a driver-local FUNCTION_RESOURCE_ID.
                "FUNCTION_ARN": ValueRef(
                    literal=str(
                        resource.get("id") or self._site_resource_id(resource_group, name),
                    ),
                ),
                "FUNCTION_URL": ValueRef(literal=url),
                "FUNCTION_REGION": ValueRef(literal=str(resource.get("location") or self._config.location)),
                "AZURE_FUNCTION_APP_NAME": ValueRef(literal=name),
                "AZURE_FUNCTION_APP_URL": ValueRef(literal=url),
                "AZURE_FUNCTION_IDENTITY_RESOURCE_ID": ValueRef(literal=identity_id),
            },
            notes=(
                "No function key or cloud credential is emitted. Runtime access uses the attached UAMI; "
                "HTTP authorization remains an application/Easy Auth contract."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise AzureFunctionsError(
            "Azure Function Apps have no honest service snapshot; retain the immutable package/image and declaration",
        )

    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        return ProvisionResult(
            False,
            "",
            "Azure Function Apps restore by redeploying an immutable package/image, not from a service snapshot",
            ["not_supported"],
        )

    @driver_op(cloud="azure", driver="faas_azure_functions", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        resource_id = {"type": "string", "pattern": "^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/[^/]+/.+$"}
        environment = {
            "type": "object",
            "additionalProperties": {"type": "string", "maxLength": 4096},
            "description": "Non-secret app settings. Use Key Vault references for secret values.",
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["deployment_mode", "host_storage_resource_id"],
            "properties": {
                "function_name": {"type": "string", "pattern": _FUNCTION_NAME_RE.pattern},
                "deployment_mode": {"type": "string", "enum": ["flex_zip", "container"]},
                "plan_resource_id": resource_id,
                "identity_resource_id": resource_id,
                "host_storage_resource_id": resource_id,
                "deployment_container": {"type": "string", "pattern": _CONTAINER_RE.pattern},
                "package_uri": {"type": "string", "format": "uri"},
                "package_sha256": {"type": "string", "pattern": _SHA256_RE.pattern},
                "runtime_name": {
                    "type": "string",
                    "enum": ["dotnet-isolated", "node", "java", "powershell", "python", "custom"],
                },
                "runtime_version": {"type": "string", "minLength": 1, "maxLength": 32},
                "instance_memory_mb": {"type": "integer", "enum": [512, 2048, 4096]},
                "maximum_instance_count": {"type": "integer", "minimum": 1},
                "http_per_instance_concurrency": {"type": "integer", "minimum": 1, "maximum": 1000},
                "registry_resource_id": resource_id,
                "image_uri": {"type": "string", "pattern": _IMAGE_RE.pattern},
                "environment": environment,
                "public_network_access": {"type": "boolean", "default": False},
                "virtual_network_subnet_id": resource_id,
                "deletion_protection": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="azure", driver="faas_azure_functions", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "FUNCTION_NAME": "Portable function application name",
                "FUNCTION_ARN": "Portable resource locator; the Function App ARM resource ID",
                "FUNCTION_URL": "Portable HTTPS endpoint; no function key is embedded",
                "FUNCTION_REGION": "Azure region",
                "AZURE_FUNCTION_APP_NAME": "Azure Function App name",
                "AZURE_FUNCTION_APP_URL": "Azure Function App HTTPS endpoint",
                "AZURE_FUNCTION_IDENTITY_RESOURCE_ID": "Attached user-assigned managed identity",
            },
        )

    def _validate(self, cfg: dict[str, Any], *, update: bool) -> str:
        if not _UUID_RE.fullmatch(self._config.subscription_id):
            return "Azure Functions requires a UUID subscription_id"
        if not self._config.resource_group:
            return "Azure Functions requires a resource group"
        if not re.fullmatch(r"[A-Za-z0-9_.()\-]{1,90}", self._config.resource_group):
            return "Azure Functions resource_group is not a valid ARM resource-group name"
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?", self._config.function_name_prefix):
            return "Azure Functions function_name_prefix must be 1-50 lowercase letters, numbers, or hyphens"
        if not self._config.location or len(self._config.location) > 64:
            return "Azure Functions requires a valid location"
        for suffix, label in (
            (self._config.storage_blob_endpoint_suffix, "storage_blob_endpoint_suffix"),
            (self._config.registry_login_server_suffix, "registry_login_server_suffix"),
        ):
            if not _DNS_SUFFIX_RE.fullmatch(suffix.lower()):
                return f"Azure Functions {label} must be a DNS suffix without a scheme or path"
        for api_version, label in (
            (self._config.site_api_version, "site_api_version"),
            (self._config.identity_api_version, "identity_api_version"),
            (self._config.storage_api_version, "storage_api_version"),
            (self._config.registry_api_version, "registry_api_version"),
            (self._config.authorization_api_version, "authorization_api_version"),
        ):
            if not _API_VERSION_RE.fullmatch(api_version):
                return f"Azure Functions {label} must be a dated Azure API version"
        if self._config.operation_timeout_seconds <= 0 or self._config.poll_interval_seconds <= 0:
            return "Azure Functions operation and polling timeouts must be positive"
        if self._config.max_instances < 1:
            return "Azure Functions max_instances must be positive"
        mode = str(cfg.get("deployment_mode") or "")
        if mode not in {"flex_zip", "container"}:
            return "deployment_mode must be flex_zip or container"
        if cfg.get("function_name") and not _FUNCTION_NAME_RE.fullmatch(str(cfg["function_name"])):
            return "function_name must be 2-60 lowercase letters, numbers, or hyphens"
        plan_id = str(cfg.get("plan_resource_id") or self._config.default_plan_resource_id)
        identity_id = str(cfg.get("identity_resource_id") or self._config.default_identity_resource_id)
        for value, allowed, label, resource_type in (
            (plan_id, self._config.allowed_plan_resource_ids, "plan_resource_id", "Microsoft.Web/serverfarms"),
            (
                identity_id,
                self._config.allowed_identity_resource_ids,
                "identity_resource_id",
                "Microsoft.ManagedIdentity/userAssignedIdentities",
            ),
            (
                str(cfg.get("host_storage_resource_id") or ""),
                self._config.allowed_storage_resource_ids,
                "host_storage_resource_id",
                "Microsoft.Storage/storageAccounts",
            ),
        ):
            error = self._validate_dependency(value, allowed, label, resource_type)
            if error:
                return error
        subnet_id = str(cfg.get("virtual_network_subnet_id") or "")
        if subnet_id:
            error = self._validate_dependency(
                subnet_id,
                self._config.allowed_subnet_resource_ids,
                "virtual_network_subnet_id",
                "Microsoft.Network/virtualNetworks/subnets",
            )
            if error:
                return error
        if bool(cfg.get("public_network_access", False)) and not self._config.allow_public_network:
            return "public_network_access requires install policy faas_allow_public_network=true"
        environment = dict(cfg.get("environment") or {})
        for key, value in environment.items():
            if not _APP_SETTING_RE.fullmatch(str(key)):
                return f"environment key {key!r} is invalid"
            if str(key) in _PLATFORM_SETTINGS:
                return f"environment cannot override platform setting {key}"
            if any(marker in str(value).lower() for marker in _SECRET_MARKERS):
                return f"environment value for {key} looks credential-bearing; use a Key Vault reference"
        if mode == "flex_zip":
            required = [
                "deployment_container",
                "package_sha256",
                "runtime_name",
                "runtime_version",
            ]
            if not update:
                required.append("package_uri")
            for key in required:
                if not str(cfg.get(key) or ""):
                    return f"flex_zip requires {key}"
            if not _CONTAINER_RE.fullmatch(str(cfg["deployment_container"])):
                return "deployment_container must be a 3-63 character lowercase Azure Blob container name"
            if not _SHA256_RE.fullmatch(str(cfg["package_sha256"])):
                return "package_sha256 must be a lowercase SHA-256 digest"
            if cfg.get("package_uri"):
                package_error = self._validate_package_uri(cfg)
                if package_error:
                    return package_error
            memory = int(cfg.get("instance_memory_mb") or 2048)
            if memory not in {512, 2048, 4096}:
                return "instance_memory_mb must be 512, 2048, or 4096 for Flex Consumption"
            maximum = int(cfg.get("maximum_instance_count") or 100)
            if maximum < 1 or maximum > self._config.max_instances:
                return f"maximum_instance_count must be between 1 and {self._config.max_instances}"
            if cfg.get("registry_resource_id") or cfg.get("image_uri"):
                return "flex_zip cannot declare registry_resource_id or image_uri"
        else:
            registry_id = str(cfg.get("registry_resource_id") or "")
            error = self._validate_dependency(
                registry_id,
                self._config.allowed_registry_resource_ids,
                "registry_resource_id",
                "Microsoft.ContainerRegistry/registries",
            )
            if error:
                return error
            image = _IMAGE_RE.fullmatch(str(cfg.get("image_uri") or ""))
            if not image:
                return "container requires an ACR image_uri pinned by @sha256 digest"
            registry_name = _resource_name(registry_id)
            expected_host = f"{registry_name}.{self._config.registry_login_server_suffix}".lower()
            if image.group("host").lower() != expected_host:
                return f"image_uri must use the allowlisted registry host {expected_host}"
            if not str(cfg.get("runtime_name") or ""):
                return "container requires runtime_name for FUNCTIONS_WORKER_RUNTIME"
            if any(cfg.get(key) for key in ("deployment_container", "package_uri", "package_sha256")):
                return "container cannot declare zip deployment fields"
        return ""

    def _validate_dependency(
        self,
        value: str,
        allowed: tuple[str, ...],
        label: str,
        resource_type: str,
    ) -> str:
        if not value:
            return f"Azure Functions requires {label}"
        parsed = _parse_resource_id(value)
        if parsed is None or parsed[0].lower() != self._config.subscription_id.lower():
            return f"{label} must be a valid resource in the configured subscription"
        if parsed[1].lower() != resource_type.lower():
            return f"{label} must reference {resource_type}"
        if value.lower() not in {item.rstrip("/").lower() for item in allowed}:
            return f"{label} is not allowed by the cluster install policy"
        return ""

    def _validate_package_uri(self, cfg: dict[str, Any]) -> str:
        parsed = urllib.parse.urlsplit(str(cfg["package_uri"]))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            return "package_uri must be a credential-free HTTPS Azure Blob URL"
        storage_name = _resource_name(str(cfg["host_storage_resource_id"]))
        expected_host = f"{storage_name}.{self._config.storage_blob_endpoint_suffix}".lower()
        if parsed.hostname.lower() != expected_host:
            return f"package_uri must use the allowlisted storage host {expected_host}"
        path = urllib.parse.unquote(parsed.path)
        prefix = f"/{cfg['deployment_container']}/"
        if not path.startswith(prefix) or not path.endswith(".zip") or ".." in path.split("/"):
            return "package_uri must be a zip beneath the configured deployment container"
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if len(query) != 1 or query[0][0].lower() != "versionid" or not query[0][1]:
            return "package_uri must contain exactly one non-secret versionid for an immutable Blob version"
        return ""

    def _resolve_dependencies(self, cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
        ids = {
            "plan": str(cfg.get("plan_resource_id") or self._config.default_plan_resource_id),
            "identity": str(cfg.get("identity_resource_id") or self._config.default_identity_resource_id),
            "storage": str(cfg["host_storage_resource_id"]),
        }
        if cfg["deployment_mode"] == "container":
            ids["registry"] = str(cfg["registry_resource_id"])
        api_versions = {
            "plan": self._config.site_api_version,
            "identity": self._config.identity_api_version,
            "storage": self._config.storage_api_version,
            "registry": self._config.registry_api_version,
        }
        return {key: self._client.get(resource_id, api_versions[key]) for key, resource_id in ids.items()}

    def _assert_plan(self, plan: dict[str, Any], cfg: dict[str, Any]) -> None:
        if str(plan.get("location") or "").replace(" ", "").lower() != self._config.location.replace(" ", "").lower():
            raise AzureFunctionsError("Function App location must match its hosting plan")
        sku = dict(plan.get("sku") or {})
        flex = str(sku.get("name") or "").upper() == "FC1"
        if not bool((plan.get("properties") or {}).get("reserved")):
            raise AzureFunctionsError("Azure Functions requires a Linux hosting plan")
        if cfg["deployment_mode"] == "flex_zip" and not flex:
            raise AzureFunctionsError("flex_zip requires an FC1 Flex Consumption hosting plan")
        if cfg["deployment_mode"] == "container":
            allowed_tiers = {"basic", "standard", "premium", "premiumv2", "premiumv3", "elasticpremium"}
            if flex or str(sku.get("tier") or "").lower() not in allowed_tiers:
                raise AzureFunctionsError("container mode requires a Premium or Dedicated Linux plan")

    def _ensure_role_assignments(
        self,
        function_name: str,
        cfg: dict[str, Any],
        resources: dict[str, dict[str, Any]],
    ) -> None:
        identity = resources["identity"]
        principal_id = str((identity.get("properties") or {}).get("principalId") or "")
        client_id = str((identity.get("properties") or {}).get("clientId") or "")
        if not _UUID_RE.fullmatch(principal_id) or not _UUID_RE.fullmatch(client_id):
            raise AzureFunctionsError("allowlisted UAMI is missing valid principalId/clientId properties")
        assignments = [
            (str(cfg["host_storage_resource_id"]), _STORAGE_BLOB_DATA_OWNER),
            (str(cfg["host_storage_resource_id"]), _STORAGE_QUEUE_DATA_CONTRIBUTOR),
            (str(cfg["host_storage_resource_id"]), _STORAGE_ACCOUNT_CONTRIBUTOR),
        ]
        if cfg["deployment_mode"] == "container":
            assignments.append((str(cfg["registry_resource_id"]), _ACR_PULL))
        for scope, role_id in assignments:
            assignment_id = self._role_assignment_resource_id(scope, function_name, principal_id, role_id)
            body = {
                "properties": {
                    "principalId": principal_id,
                    "principalType": "ServicePrincipal",
                    "roleDefinitionId": (
                        f"/subscriptions/{self._config.subscription_id}/providers/"
                        f"Microsoft.Authorization/roleDefinitions/{role_id}"
                    ),
                },
            }
            existing = self._get(assignment_id, self._config.authorization_api_version)
            if existing is not None:
                properties = dict(existing.get("properties") or {})
                if str(properties.get("principalId") or "").lower() != principal_id.lower() or not str(
                    properties.get("roleDefinitionId") or ""
                ).lower().endswith(f"/{role_id}"):
                    raise AzureFunctionsError("deterministic role-assignment name is occupied by a foreign grant")
                continue
            try:
                self._client.put(assignment_id, self._config.authorization_api_version, body)
            except AzureFunctionsConflict:
                current = self._client.get(assignment_id, self._config.authorization_api_version)
                properties = dict(current.get("properties") or {})
                if str(properties.get("principalId") or "").lower() != principal_id.lower() or not str(
                    properties.get("roleDefinitionId") or ""
                ).lower().endswith(f"/{role_id}"):
                    raise AzureFunctionsError("concurrent role-assignment collision") from None

    def _site_body(
        self,
        spec: ProvisionSpec,
        cfg: dict[str, Any],
        resources: dict[str, dict[str, Any]],
        *,
        current: dict[str, Any] | None,
    ) -> dict[str, Any]:
        tags = arm_tags_for(
            spec,
            platform_tags={
                "deletion-protection": str(
                    bool(cfg.get("deletion_protection", self._config.deletion_protection_default)),
                ).lower(),
                "faas-variant": VARIANT,
                "deployment-mode": cfg["deployment_mode"],
            },
        )
        old_digest = str((current or {}).get("tags", {}).get("astrolift-package-sha256") or "")
        if old_digest:
            tags["astrolift-package-sha256"] = old_digest
        return self._body(cfg, resources, tags)

    def _update_body(
        self,
        cfg: dict[str, Any],
        resources: dict[str, dict[str, Any]],
        current: dict[str, Any],
    ) -> dict[str, Any]:
        tags = dict(current.get("tags") or {})
        tags["astrolift-deletion-protection"] = str(
            bool(cfg.get("deletion_protection", self._config.deletion_protection_default)),
        ).lower()
        tags["astrolift-deployment-mode"] = str(cfg["deployment_mode"])
        tags["astrolift-faas-variant"] = VARIANT
        return self._body(cfg, resources, tags)

    def _body(
        self,
        cfg: dict[str, Any],
        resources: dict[str, dict[str, Any]],
        tags: dict[str, str],
    ) -> dict[str, Any]:
        identity_id = str(resources["identity"]["id"])
        identity_properties = dict(resources["identity"].get("properties") or {})
        client_id = str(identity_properties["clientId"])
        storage_name = _resource_name(str(resources["storage"]["id"]))
        public = bool(cfg.get("public_network_access", False))
        app_settings = {
            "FUNCTIONS_EXTENSION_VERSION": "~4",
            "FUNCTIONS_WORKER_RUNTIME": str(cfg["runtime_name"]),
            "AzureWebJobsStorage__accountName": storage_name,
            "AzureWebJobsStorage__credential": "managedidentity",
            "AzureWebJobsStorage__clientId": client_id,
            **{str(key): str(value) for key, value in dict(cfg.get("environment") or {}).items()},
        }
        site_config: dict[str, Any] = {
            "appSettings": [{"name": key, "value": value} for key, value in sorted(app_settings.items())],
            "ftpsState": "Disabled",
            "http20Enabled": True,
            "minTlsVersion": "1.2",
        }
        properties: dict[str, Any] = {
            "serverFarmId": str(resources["plan"]["id"]),
            "httpsOnly": True,
            "publicNetworkAccess": "Enabled" if public else "Disabled",
            "hostNamesDisabled": not public,
            "siteConfig": site_config,
        }
        if cfg.get("virtual_network_subnet_id"):
            properties["virtualNetworkSubnetId"] = str(cfg["virtual_network_subnet_id"])
            site_config["vnetRouteAllEnabled"] = True
        if cfg["deployment_mode"] == "flex_zip":
            storage_uri = (
                f"https://{storage_name}.{self._config.storage_blob_endpoint_suffix}/{cfg['deployment_container']}"
            )
            scale: dict[str, Any] = {
                "instanceMemoryMB": int(cfg.get("instance_memory_mb") or 2048),
                "maximumInstanceCount": int(cfg.get("maximum_instance_count") or 100),
            }
            if cfg.get("http_per_instance_concurrency"):
                scale["triggers"] = {
                    "http": {"perInstanceConcurrency": int(cfg["http_per_instance_concurrency"])},
                }
            properties["functionAppConfig"] = {
                "deployment": {
                    "storage": {
                        "type": "blobContainer",
                        "value": storage_uri,
                        "authentication": {
                            "type": "UserAssignedIdentity",
                            "userAssignedIdentityResourceId": identity_id,
                        },
                    },
                },
                "runtime": {
                    "name": str(cfg["runtime_name"]),
                    "version": str(cfg["runtime_version"]),
                },
                "scaleAndConcurrency": scale,
            }
        else:
            image = str(cfg["image_uri"])
            registry_name = _resource_name(str(resources["registry"]["id"]))
            site_config.update(
                {
                    "acrUseManagedIdentityCreds": True,
                    "acrUserManagedIdentityID": client_id,
                    "alwaysOn": True,
                    "linuxFxVersion": f"DOCKER|{image}",
                },
            )
            app_settings["DOCKER_REGISTRY_SERVER_URL"] = (
                f"https://{registry_name}.{self._config.registry_login_server_suffix}"
            )
            app_settings["WEBSITES_ENABLE_APP_SERVICE_STORAGE"] = "false"
            site_config["appSettings"] = [{"name": key, "value": value} for key, value in sorted(app_settings.items())]
            tags["astrolift-image-sha256"] = image.rsplit("@sha256:", 1)[1]
        return {
            "kind": "functionapp,linux",
            "location": self._config.location,
            "identity": {
                "type": "UserAssigned",
                "userAssignedIdentities": {identity_id: {}},
            },
            "tags": tags,
            "properties": properties,
        }

    def _deploy_package_if_needed(
        self,
        resource_id: str,
        cfg: dict[str, Any],
        current: dict[str, Any] | None,
    ) -> None:
        digest = str(cfg["package_sha256"])
        if str((current or {}).get("tags", {}).get("astrolift-package-sha256") or "") == digest:
            return
        self._client.put(
            f"{resource_id}/extensions/onedeploy",
            self._config.site_api_version,
            {
                "location": self._config.location,
                "properties": {
                    "packageUri": str(cfg["package_uri"]),
                    "remoteBuild": False,
                },
            },
        )

    def _stamp_package_digest(self, resource_id: str, tags: dict[str, str], digest: str) -> None:
        final_tags = {**tags, "astrolift-package-sha256": digest}
        self._client.patch(
            resource_id,
            self._config.site_api_version,
            {"tags": final_tags},
        )

    def _merge_current_config(self, current: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
        properties = dict(current.get("properties") or {})
        site_config = dict(properties.get("siteConfig") or {})
        function_config = dict(properties.get("functionAppConfig") or {})
        identity_ids = sorted((current.get("identity") or {}).get("userAssignedIdentities") or {})
        settings = {
            str(item.get("name")): str(item.get("value"))
            for item in site_config.get("appSettings") or []
            if item.get("name")
        }
        storage_name = str(settings.get("AzureWebJobsStorage__accountName") or "")
        mode = "flex_zip" if function_config else "container"
        merged: dict[str, Any] = {
            "deployment_mode": mode,
            "plan_resource_id": str(properties.get("serverFarmId") or ""),
            "identity_resource_id": identity_ids[0] if len(identity_ids) == 1 else "",
            "host_storage_resource_id": self._allowlisted_id_for(
                self._config.allowed_storage_resource_ids,
                storage_name,
                _storage_resource_id(
                    self._config.subscription_id,
                    self._config.resource_group,
                    storage_name,
                ),
            ),
            "runtime_name": str(settings.get("FUNCTIONS_WORKER_RUNTIME") or ""),
            "public_network_access": str(properties.get("publicNetworkAccess") or "Disabled") == "Enabled",
            "deletion_protection": (
                str((current.get("tags") or {}).get("astrolift-deletion-protection") or "true") == "true"
            ),
            "environment": {key: value for key, value in settings.items() if key not in _PLATFORM_SETTINGS},
        }
        if properties.get("virtualNetworkSubnetId"):
            merged["virtual_network_subnet_id"] = properties["virtualNetworkSubnetId"]
        if mode == "flex_zip":
            deployment = dict(function_config.get("deployment") or {})
            storage = dict(deployment.get("storage") or {})
            runtime = dict(function_config.get("runtime") or {})
            scale = dict(function_config.get("scaleAndConcurrency") or {})
            uri = urllib.parse.urlsplit(str(storage.get("value") or ""))
            merged.update(
                {
                    "deployment_container": uri.path.strip("/").split("/", 1)[0],
                    "package_sha256": str((current.get("tags") or {}).get("astrolift-package-sha256") or ""),
                    "runtime_name": str(runtime.get("name") or merged["runtime_name"]),
                    "runtime_version": str(runtime.get("version") or ""),
                    "instance_memory_mb": int(scale.get("instanceMemoryMB") or 2048),
                    "maximum_instance_count": int(scale.get("maximumInstanceCount") or 100),
                },
            )
        else:
            image = str(site_config.get("linuxFxVersion") or "").removeprefix("DOCKER|")
            registry_host = image.split("/", 1)[0]
            registry_name = registry_host.split(".", 1)[0]
            merged.update(
                {
                    "registry_resource_id": self._allowlisted_id_for(
                        self._config.allowed_registry_resource_ids,
                        registry_name,
                        _registry_resource_id(
                            self._config.subscription_id,
                            self._config.resource_group,
                            registry_name,
                        ),
                    ),
                    "image_uri": image,
                },
            )
        merged.update(delta)
        return merged

    def _allowlisted_id_for(self, allowed: tuple[str, ...], name: str, fallback: str) -> str:
        """Recover a dependency's full resource ID from the install allowlist.

        Only the resource *name* survives in live Function App state (an app
        setting for the host storage account, the image host for the
        registry). Operator-owned dependencies are allowlisted as complete ARM
        IDs and may live in any resource group in the subscription, so
        rebuilding the ID from the cluster's own resource group would refuse
        every update against a shared dependency. Prefer the allowlisted ID
        whose name matches; fall back to the cluster group so an unmatched
        name still fails the allowlist check with an honest identifier.
        """
        if not name:
            return fallback
        for candidate in allowed:
            if _resource_name(candidate).lower() == name.lower():
                return candidate.rstrip("/")
        return fallback

    def _cleanup_role_assignments(self, function_name: str, cfg: dict[str, Any]) -> str:
        error = self._validate_cleanup_config(cfg)
        if error:
            return error
        try:
            identity = self._client.get(
                str(cfg.get("identity_resource_id") or self._config.default_identity_resource_id),
                self._config.identity_api_version,
            )
            principal_id = str((identity.get("properties") or {}).get("principalId") or "")
            assignments = [
                (str(cfg["host_storage_resource_id"]), _STORAGE_BLOB_DATA_OWNER),
                (str(cfg["host_storage_resource_id"]), _STORAGE_QUEUE_DATA_CONTRIBUTOR),
                (str(cfg["host_storage_resource_id"]), _STORAGE_ACCOUNT_CONTRIBUTOR),
            ]
            if cfg.get("deployment_mode") == "container" and cfg.get("registry_resource_id"):
                assignments.append((str(cfg["registry_resource_id"]), _ACR_PULL))
            for scope, role_id in assignments:
                assignment_id = self._role_assignment_resource_id(
                    scope,
                    function_name,
                    principal_id,
                    role_id,
                )
                with suppress(AzureFunctionsNotFound):
                    self._client.delete(assignment_id, self._config.authorization_api_version)
        except Exception as exc:
            return f"cleanup Azure Functions role assignments: {exc}"
        return ""

    def _validate_cleanup_config(self, cfg: dict[str, Any]) -> str:
        if not cfg:
            return (
                "Azure Function App is gone but stored config is unavailable; "
                "role-assignment cleanup requires operator action"
            )
        for key in ("deployment_mode", "host_storage_resource_id"):
            if not str(cfg.get(key) or ""):
                return f"role-assignment cleanup requires stored {key}"
        identity_id = str(cfg.get("identity_resource_id") or self._config.default_identity_resource_id)
        for value, allowed, label, resource_type in (
            (
                identity_id,
                self._config.allowed_identity_resource_ids,
                "identity_resource_id",
                "Microsoft.ManagedIdentity/userAssignedIdentities",
            ),
            (
                str(cfg["host_storage_resource_id"]),
                self._config.allowed_storage_resource_ids,
                "host_storage_resource_id",
                "Microsoft.Storage/storageAccounts",
            ),
        ):
            error = self._validate_dependency(value, allowed, label, resource_type)
            if error:
                return f"role-assignment cleanup refused: {error}"
        if cfg.get("deployment_mode") == "container":
            error = self._validate_dependency(
                str(cfg.get("registry_resource_id") or ""),
                self._config.allowed_registry_resource_ids,
                "registry_resource_id",
                "Microsoft.ContainerRegistry/registries",
            )
            if error:
                return f"role-assignment cleanup refused: {error}"
        return ""

    def _role_assignment_resource_id(
        self,
        scope: str,
        function_name: str,
        principal_id: str,
        role_id: str,
    ) -> str:
        assignment = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"astrolift:{self._config.subscription_id}:{function_name}:{principal_id}:{scope.lower()}:{role_id}",
        )
        return f"{scope.rstrip('/')}/providers/Microsoft.Authorization/roleAssignments/{assignment}"

    def _function_name(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        explicit = str(cfg.get("function_name") or "")
        if explicit:
            return explicit
        seed = "-".join(
            (
                self._config.function_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint,
            ),
        ).lower()
        normalized = re.sub(r"[^a-z0-9-]+", "-", seed).strip("-")
        normalized = re.sub(r"-+", "-", normalized)
        digest = hashlib.sha256(seed.encode()).hexdigest()[:8]
        prefix = (normalized or "function")[:51].rstrip("-")
        return f"{prefix}-{digest}"

    def _site_resource_id(self, resource_group: str, name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Web/sites/{name}"
        )

    def _get(self, resource_id: str, api_version: str) -> dict[str, Any] | None:
        try:
            return self._client.get(resource_id, api_version)
        except AzureFunctionsNotFound:
            return None

    def _assert_owned(self, resource: dict[str, Any], expected_service_id: str = "") -> None:
        tags = dict(resource.get("tags") or {})
        if tags.get(MANAGED_BY_TAG) != "platform" or tags.get("astrolift-faas-variant") != VARIANT:
            raise AzureFunctionsError("existing Function App is not Astrolift-owned")
        actual_service_id = str(tags.get(MANAGED_SERVICE_ID_TAG) or "")
        if expected_service_id and actual_service_id != expected_service_id:
            raise AzureFunctionsError("existing Function App belongs to another managed service")


def _handle(resource_group: str, name: str) -> str:
    return f"{KIND}/{resource_group}/{name}"


def _parse_handle(handle: str) -> tuple[str, str]:
    parts = handle.split("/")
    if len(parts) != 3 or parts[0] != KIND or not parts[1] or not _FUNCTION_NAME_RE.fullmatch(parts[2]):
        raise ValueError("invalid Azure Functions handle; expected faas/<resource-group>/<function-name>")
    return parts[1], parts[2]


def _parse_resource_id(value: str) -> tuple[str, str] | None:
    parts = [part for part in value.strip("/").split("/") if part]
    if len(parts) < 8 or parts[0].lower() != "subscriptions" or parts[2].lower() != "resourcegroups":
        return None
    try:
        provider_index = next(index for index, part in enumerate(parts) if part.lower() == "providers")
    except StopIteration:
        return None
    if provider_index + 2 >= len(parts):
        return None
    resource_type_parts = [parts[provider_index + 1]]
    resource_names_and_types = parts[provider_index + 2 :]
    resource_type_parts.extend(resource_names_and_types[::2])
    return parts[1], "/".join(resource_type_parts)


def _resource_name(resource_id: str) -> str:
    return resource_id.rstrip("/").rsplit("/", 1)[-1]


def _storage_resource_id(subscription_id: str, resource_group: str, name: str) -> str:
    return (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Storage/storageAccounts/{name}"
    )


def _registry_resource_id(subscription_id: str, resource_group: str, name: str) -> str:
    return (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.ContainerRegistry/registries/{name}"
    )


def _site_state(resource: dict[str, Any]) -> str:
    properties = dict(resource.get("properties") or {})
    provisioning = str(properties.get("provisioningState") or "Succeeded").lower()
    state = str(properties.get("state") or "").lower()
    if provisioning in {"creating", "updating", "inprogress", "accepted"}:
        return "provisioning"
    if provisioning in {"deleting"}:
        return "deprovisioning"
    if provisioning not in {"succeeded", ""}:
        return "error"
    if state == "running" and bool(properties.get("enabled", True)):
        return "available"
    if state in {"stopped", "stopping"} or not bool(properties.get("enabled", True)):
        return "error"
    return "provisioning"


def _header(headers: Mapping[str, str], name: str) -> str:
    return next((str(value) for key, value in headers.items() if key.lower() == name.lower()), "")


def _json_object(response: ArmHttpResponse) -> dict[str, Any]:
    if not response.body:
        return {}
    try:
        payload = json.loads(response.body)
    except (TypeError, ValueError) as exc:
        raise AzureFunctionsError("ARM returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise AzureFunctionsError("ARM returned a non-object response")
    return dict(payload)


def _error_detail(response: ArmHttpResponse) -> str:
    try:
        payload = _json_object(response)
        error = payload.get("error") or payload
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "request failed")
    except AzureFunctionsError:
        pass
    return response.body.decode(errors="replace")[:1000] or "request failed"


def _operation_error(payload: dict[str, Any]) -> str:
    error = payload.get("error") or payload.get("properties", {}).get("error") or {}
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "operation failed")
    return str(error or "operation failed")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> ArmHttpResponse:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return ArmHttpResponse(
                status_code=int(response.status),
                headers={str(key): str(value) for key, value in response.headers.items()},
                body=response.read(),
            )
    except urllib.error.HTTPError as exc:
        return ArmHttpResponse(
            status_code=int(exc.code),
            headers={str(key): str(value) for key, value in exc.headers.items()},
            body=exc.read(),
        )
