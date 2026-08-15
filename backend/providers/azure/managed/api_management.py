"""Azure API Management managed-service driver.

The preview owns one dedicated APIM service per managed-service row and
reconciles a deliberately narrow declarative surface: APIs, operations,
backends, subscriptions, generated policies, identities, and Key Vault-backed
gateway hostnames.  Arbitrary policy XML and literal credentials are not part
of the contract because either would turn repository configuration into a
credential-exfiltration primitive.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree.ElementTree import Element, SubElement, tostring

from _sdk import UnsupportedOperationError
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
from azure.managed.tags import arm_tags_for

KIND = "api_gateway"
VARIANT = "api_management"
_API_VERSION = "2024-05-01"
_MANAGEMENT_HOST = "management.azure.com"
_ARM_SCOPE = "https://management.azure.com/.default"
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_SERVICE_RE = re.compile(r"^[A-Za-z](?:[A-Za-z0-9-]{0,48}[A-Za-z0-9])?$")
_ENTITY_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,78}[A-Za-z0-9])?$")
_RESOURCE_GROUP_RE = re.compile(r"^[A-Za-z0-9_.()\-]{1,89}[A-Za-z0-9_()\-]$")
_HOST_RE = re.compile(r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$")
_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
_SKUS = frozenset(
    {
        "Basic",
        "BasicV2",
        "Consumption",
        "Developer",
        "Premium",
        "PremiumV2",
        "Standard",
        "StandardV2",
    },
)
_INTERNAL_SKUS = frozenset({"Developer", "Premium"})
_POLICY_KINDS = frozenset({"backend", "cors", "managed_identity"})
_OWNERSHIP_TAG = "astrolift-managed-service-id"
_ADOPTED_TAG = "astrolift-adopted"
_CHILD_PREFIX = "astrolift-"


class AzureAPIMError(Exception):
    """An APIM request or safety contract failed."""


class AzureAPIMNotFound(AzureAPIMError):
    """An ARM resource is absent."""


class AzureAPIMClient(Protocol):
    def get(self, path: str) -> dict[str, Any]: ...

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...

    def delete(self, path: str) -> None: ...

    def list(self, path: str) -> list[dict[str, Any]]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        del req, fp, code, msg, headers, newurl
        return None


class AzureAPIMRestClient:
    """Small ARM client with bounded LRO polling and strict next-link scope."""

    def __init__(
        self,
        *,
        subscription_id: str,
        credential: Any | None = None,
        api_endpoint: str = "https://management.azure.com",
        timeout_seconds: float = 30,
        operation_timeout_seconds: float = 3600,
        poll_interval_seconds: float = 5,
        sleep: Any = time.sleep,
    ) -> None:
        endpoint = urlparse(api_endpoint)
        if endpoint.scheme != "https" or endpoint.hostname != _MANAGEMENT_HOST or endpoint.path not in {"", "/"}:
            raise AzureAPIMError("APIM api_endpoint must be https://management.azure.com")
        if not _UUID_RE.fullmatch(subscription_id):
            raise AzureAPIMError("APIM subscription_id must be a UUID")
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        self._credential = credential
        self._subscription_id = subscription_id.lower()
        self._endpoint = api_endpoint.rstrip("/")
        self._timeout = timeout_seconds
        self._operation_timeout = operation_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._sleep = sleep
        self._opener = build_opener(_NoRedirect())

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        headers: dict[str, str] = {}
        try:
            current = self.get(path)
        except AzureAPIMNotFound:
            current = None
        if current is not None:
            headers["If-Match"] = str(current.get("etag") or "*")
        return self._request("PUT", path, body=body, wait=True, extra_headers=headers)

    def delete(self, path: str) -> None:
        current = self.get(path)
        self._request(
            "DELETE",
            path,
            wait=True,
            extra_headers={"If-Match": str(current.get("etag") or "*")},
        )

    def list(self, path: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        next_path = path
        while next_path:
            page = self._request("GET", next_path)
            raw = page.get("value") or []
            if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
                raise AzureAPIMError("APIM list response value must be an array of objects")
            values.extend(raw)
            next_link = str(page.get("nextLink") or "")
            if next_link:
                self._validate_url(next_link)
            next_path = next_link
        return values

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        wait: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = self._url(path)
        payload = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        token = self._credential.get_token(_ARM_SCOPE).token
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }
        request = Request(
            url,
            data=payload,
            method=method,
            headers=headers,
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                response_body = response.read()
                result = json.loads(response_body) if response_body else {}
                async_url = response.headers.get("Azure-AsyncOperation") or response.headers.get("Location")
                if wait and async_url and response.status in {201, 202}:
                    return self._wait(async_url)
                if not isinstance(result, dict):
                    raise AzureAPIMError("APIM ARM response must be an object")
                return result
        except HTTPError as exc:
            if exc.code == 404:
                raise AzureAPIMNotFound(path) from exc
            detail = exc.read().decode(errors="replace")[:2000]
            raise AzureAPIMError(f"APIM ARM {method} failed ({exc.code}): {detail}") from exc

    def _wait(self, async_url: str) -> dict[str, Any]:
        self._validate_url(async_url)
        deadline = time.monotonic() + self._operation_timeout
        while True:
            result = self._request("GET", async_url)
            state = str(result.get("status") or result.get("properties", {}).get("provisioningState") or "")
            if state.lower() in {"succeeded", "created"}:
                return result
            if state.lower() in {"canceled", "cancelled", "failed", "terminationfailed"}:
                raise AzureAPIMError(f"APIM ARM operation ended in {state}: {result}")
            if time.monotonic() >= deadline:
                raise AzureAPIMError("APIM ARM operation timed out")
            self._sleep(self._poll_interval)

    def _url(self, path: str) -> str:
        if path.startswith("https://"):
            self._validate_url(path)
            return path
        if not path.startswith("/") or "?" in path or "#" in path:
            raise AzureAPIMError("APIM ARM path must be absolute and must not contain a query or fragment")
        return f"{self._endpoint}{path}?{urlencode({'api-version': _API_VERSION})}"

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        subscription_marker = f"/subscriptions/{self._subscription_id}/"
        if (
            parsed.scheme != "https"
            or parsed.hostname != _MANAGEMENT_HOST
            or subscription_marker not in parsed.path.lower()
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise AzureAPIMError("APIM ARM continuation URL escaped the configured subscription")


@dataclass(frozen=True)
class AzureAPIMConfig:
    subscription_id: str
    resource_group: str
    location: str = "eastus"
    publisher_email: str = ""
    publisher_name: str = "Astrolift"
    service_name_prefix: str = "astrolift"
    allowed_skus: tuple[str, ...] = ("Developer", "Basic", "Standard", "Premium")
    max_capacity: int = 4
    allowed_policy_kinds: tuple[str, ...] = ("backend", "cors")
    allowed_backend_host_suffixes: tuple[str, ...] = (
        ".azurecontainerapps.io",
        ".azurewebsites.net",
    )
    allowed_backend_identity_resources: tuple[str, ...] = ()
    allowed_user_assigned_identity_ids: tuple[str, ...] = ()
    allowed_subnet_ids: tuple[str, ...] = ()
    allowed_custom_domain_suffixes: tuple[str, ...] = ()
    allowed_key_vault_secret_prefixes: tuple[str, ...] = ()
    allow_internal_network: bool = False
    allow_custom_domains: bool = False
    allow_subscriptions: bool = False
    allow_child_pruning: bool = False
    allow_adoption: bool = False
    deletion_protection_default: bool = True
    max_apis: int = 50
    max_routes_per_api: int = 100
    max_backends: int = 50
    max_subscriptions: int = 25
    api_endpoint: str = "https://management.azure.com"
    request_timeout_seconds: float = 30
    operation_timeout_seconds: float = 3600
    poll_interval_seconds: float = 5
    client: AzureAPIMClient | None = None


class AzureAPIMDriver(ManagedServiceDriver):
    KIND = KIND

    def __init__(self, *, config: AzureAPIMConfig) -> None:
        self._config = config
        self._validate_install()
        self._api = config.client or AzureAPIMRestClient(
            subscription_id=config.subscription_id,
            api_endpoint=config.api_endpoint,
            timeout_seconds=config.request_timeout_seconds,
            operation_timeout_seconds=config.operation_timeout_seconds,
            poll_interval_seconds=config.poll_interval_seconds,
        )

    @driver_op(
        cloud="azure",
        driver="api_management",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate(cfg, update=False)
        if error:
            return ProvisionResult(False, "", error, ["invalid_api_management_config"])
        service_name = self._service_name(spec, cfg)
        path = self._service_path(service_name)
        existing = self._get(path)
        try:
            if existing is not None:
                self._assert_owned_or_adoptable(existing, spec, cfg)
            body = self._service_body(spec, cfg, existing)
            service = self._api.put(path, body)
            self._reconcile_children(service_name, cfg)
        except Exception as exc:
            return ProvisionResult(False, "", f"provision API Management: {exc}", [str(exc)])
        service = service or self._api.get(path)
        state = _provisioning_state(service)
        return ProvisionResult(
            True,
            self._handle(service_name),
            f"API Management {service_name} reconciled ({state or 'accepted'})",
            ready=state.lower() == "succeeded",
        )

    @driver_op(cloud="azure", driver="api_management")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            service_name = self._parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate(cfg, update=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_api_management_config"])
        path = self._service_path(service_name)
        existing = self._get(path)
        if existing is None:
            return UpdateResult(False, spec.handle, "API Management service not found", ["not_found"])
        existing_properties = dict(existing.get("properties") or {})
        effective_network = str(
            cfg.get("network_mode")
            or ("internal" if existing_properties.get("virtualNetworkType") == "Internal" else "public")
        )
        effective_sku = str(cfg.get("sku") or (existing.get("sku") or {}).get("name") or "")
        if effective_network == "internal" and effective_sku not in _INTERNAL_SKUS:
            return UpdateResult(
                False,
                spec.handle,
                "APIM internal network mode requires Developer or Premium",
                ["invalid_api_management_config"],
            )
        try:
            self._assert_owned(existing)
            body = self._update_body(existing, cfg)
            self._api.put(path, body)
            self._reconcile_children(service_name, cfg, only_declared=True)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update API Management: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"API Management {service_name} reconciled")

    @driver_op(
        cloud="azure",
        driver="api_management",
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
            service_name = self._parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        path = self._service_path(service_name)
        existing = self._get(path)
        if existing is None:
            return DeprovisionResult(True, spec.handle, f"API Management {service_name} already gone")
        cfg = dict(spec.config or {})
        if bool(cfg.get("deletion_protection", self._config.deletion_protection_default)):
            return DeprovisionResult(
                False,
                spec.handle,
                "API Management deletion protection is enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "API Management teardown requires force_destroy=true",
                ["force_destroy_required"],
                retryable=False,
            )
        try:
            self._assert_owned(existing)
            tags = _tags(existing)
            if tags.get(_ADOPTED_TAG) == "true" and not cfg.get("delete_adopted"):
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "adopted API Management service requires delete_adopted=true",
                    ["delete_adopted_required"],
                    retryable=False,
                )
            self._api.delete(path)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete API Management: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"API Management {service_name} deleted")

    @driver_op(cloud="azure", driver="api_management")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            service_name = self._parse_handle(handle.handle)
            service = self._api.get(self._service_path(service_name))
            self._assert_owned(service)
        except AzureAPIMNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "API Management service does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe API Management: {exc}")
        raw = _provisioning_state(service)
        state = {
            "activating": "provisioning",
            "created": "provisioning",
            "deleted": "deprovisioned",
            "failed": "error",
            "stopped": "error",
            "succeeded": "available",
            "terminating": "deprovisioning",
            "terminationfailed": "error",
            "updating": "updating",
        }.get(raw.lower(), "updating")
        return ServiceStatus(handle.handle, state, f"Azure reports {raw or 'unknown'}")

    @driver_op(cloud="azure", driver="api_management")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        service_name = self._parse_handle(handle.handle)
        service = self._api.get(self._service_path(service_name))
        self._assert_owned(service)
        properties = dict(service.get("properties") or {})
        endpoint = str(properties.get("gatewayUrl") or f"https://{service_name}.azure-api.net")
        return Binding(
            env_vars={
                "API_GATEWAY_URL": ValueRef(literal=endpoint),
                "API_GATEWAY_ID": ValueRef(literal=service_name),
                "API_GATEWAY_PROVIDER": ValueRef(literal="azure_api_management"),
                "AZURE_APIM_GATEWAY_URL": ValueRef(literal=endpoint),
                "AZURE_APIM_SERVICE_NAME": ValueRef(literal=service_name),
                "AZURE_APIM_RESOURCE_ID": ValueRef(literal=self._service_path(service_name)),
                "AZURE_LOCATION": ValueRef(literal=str(service.get("location") or self._config.location)),
            },
            notes=(
                "No APIM subscription or administrator key is emitted. Configure caller authentication "
                "independently; backend authentication is limited to generated managed-identity policy."
            ),
        )

    @driver_op(cloud="azure", driver="api_management")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise UnsupportedOperationError(
            "Azure API Management configuration has no service-side snapshot semantic; "
            "export and reconcile the declaration",
        )

    @driver_op(cloud="azure", driver="api_management")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise UnsupportedOperationError(
            "Azure API Management restore is declarative; provision the exported configuration as a new service",
        )

    @driver_op(cloud="azure", driver="api_management", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        name = {"type": "string", "pattern": _ENTITY_RE.pattern, "maxLength": 80}
        route = {
            "type": "object",
            "required": ["id", "method", "url_template"],
            "properties": {
                "id": name,
                "display_name": {"type": "string", "maxLength": 300},
                "description": {"type": "string", "maxLength": 1000},
                "method": {"type": "string", "enum": sorted(_METHODS)},
                "url_template": {"type": "string", "maxLength": 1000},
            },
            "additionalProperties": False,
        }
        cors = {
            "type": "object",
            "required": ["origins"],
            "properties": {
                "origins": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string"}},
                "methods": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "enum": sorted(_METHODS)},
                },
                "headers": {"type": "array", "items": {"type": "string"}},
                "expose_headers": {"type": "array", "items": {"type": "string"}},
                "allow_credentials": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["sku", "capacity"],
            "properties": {
                "service_name": {"type": "string", "pattern": _SERVICE_RE.pattern, "maxLength": 50},
                "sku": {"type": "string", "enum": sorted(_SKUS)},
                "capacity": {"type": "integer", "minimum": 0, "maximum": 12},
                "network_mode": {"type": "string", "enum": ["internal", "public"], "default": "public"},
                "subnet_id": {"type": "string"},
                "identity": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["system_assigned", "user_assigned"]},
                        "user_assigned_identity_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "backends": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "url"],
                        "properties": {
                            "id": name,
                            "url": {"type": "string", "format": "uri"},
                            "description": {"type": "string", "maxLength": 2000},
                            "identity_resource": {"type": "string"},
                            "identity_client_id": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "apis": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "display_name", "path"],
                        "properties": {
                            "id": name,
                            "display_name": {"type": "string", "maxLength": 300},
                            "description": {"type": "string", "maxLength": 1000},
                            "path": {"type": "string", "maxLength": 400},
                            "backend_id": name,
                            "subscription_required": {"type": "boolean", "default": False},
                            "routes": {"type": "array", "items": route},
                            "cors": cors,
                        },
                        "additionalProperties": False,
                    },
                },
                "subscriptions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "display_name", "api_id"],
                        "properties": {
                            "id": name,
                            "display_name": {"type": "string", "maxLength": 100},
                            "api_id": name,
                            "state": {"type": "string", "enum": ["active", "suspended"]},
                        },
                        "additionalProperties": False,
                    },
                },
                "custom_domains": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["hostname", "key_vault_secret_id"],
                        "properties": {
                            "hostname": {"type": "string"},
                            "key_vault_secret_id": {"type": "string"},
                            "default_ssl_binding": {"type": "boolean", "default": False},
                        },
                        "additionalProperties": False,
                    },
                },
                "prune_children": {"type": "boolean", "default": False},
                "adopt_existing": {"type": "boolean", "default": False},
                "delete_adopted": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="azure", driver="api_management", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "API_GATEWAY_URL": "Portable APIM gateway base URL",
                "API_GATEWAY_ID": "Portable gateway identifier",
                "API_GATEWAY_PROVIDER": "Provider literal azure_api_management",
                "AZURE_APIM_GATEWAY_URL": "Azure APIM gateway base URL",
                "AZURE_APIM_SERVICE_NAME": "APIM service name",
                "AZURE_APIM_RESOURCE_ID": "APIM service ARM resource ID",
                "AZURE_LOCATION": "Azure region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "apis",
            "backends",
            "capacity",
            "custom_domains",
            "identity",
            "network_mode",
            "prune_children",
            "sku",
            "subnet_id",
            "subscriptions",
        ]

    def _validate_install(self) -> None:
        cfg = self._config
        if not _UUID_RE.fullmatch(cfg.subscription_id):
            raise AzureAPIMError("APIM subscription_id must be a UUID")
        if not _RESOURCE_GROUP_RE.fullmatch(cfg.resource_group):
            raise AzureAPIMError("APIM resource_group is invalid")
        if not cfg.location or len(cfg.location) > 90:
            raise AzureAPIMError("APIM location is required")
        if not _valid_email(cfg.publisher_email):
            raise AzureAPIMError("APIM publisher_email must be an email address")
        if not cfg.publisher_name.strip():
            raise AzureAPIMError("APIM publisher_name is required")
        if not _SERVICE_RE.fullmatch(f"{cfg.service_name_prefix}-x"):
            raise AzureAPIMError("APIM service_name_prefix is invalid")
        invalid_skus = set(cfg.allowed_skus) - _SKUS
        if invalid_skus or not cfg.allowed_skus:
            raise AzureAPIMError(f"APIM allowed_skus contains unsupported values: {sorted(invalid_skus)}")
        invalid_policies = set(cfg.allowed_policy_kinds) - _POLICY_KINDS
        if invalid_policies:
            raise AzureAPIMError(f"APIM allowed_policy_kinds contains unsupported values: {sorted(invalid_policies)}")
        for value, label in (
            (cfg.max_capacity, "max_capacity"),
            (cfg.max_apis, "max_apis"),
            (cfg.max_routes_per_api, "max_routes_per_api"),
            (cfg.max_backends, "max_backends"),
            (cfg.max_subscriptions, "max_subscriptions"),
        ):
            if value < 1:
                raise AzureAPIMError(f"APIM {label} must be positive")
        for suffix in (*cfg.allowed_backend_host_suffixes, *cfg.allowed_custom_domain_suffixes):
            if not suffix.startswith(".") or not _HOST_RE.fullmatch(f"x{suffix}"):
                raise AzureAPIMError(f"APIM host suffix {suffix!r} is invalid")

    def _validate(self, cfg: dict[str, Any], *, update: bool) -> str:
        forbidden = sorted(set(cfg) & {"policy", "policy_xml", "raw_policy", "subscription_key", "credentials"})
        if forbidden:
            return f"APIM config cannot contain raw policies or credentials: {', '.join(forbidden)}"
        allowed = {
            "adopt_existing",
            "apis",
            "backends",
            "capacity",
            "custom_domains",
            "delete_adopted",
            "deletion_protection",
            "identity",
            "network_mode",
            "prune_children",
            "service_name",
            "sku",
            "subnet_id",
            "subscriptions",
        }
        unknown = sorted(set(cfg) - allowed)
        if unknown:
            return f"APIM config contains unsupported fields: {', '.join(unknown)}"
        if update and "service_name" in cfg:
            return "APIM service_name is immutable; reprovision the service"
        if "service_name" in cfg and not _SERVICE_RE.fullmatch(str(cfg["service_name"])):
            return "APIM service_name must be a valid 1-50 character Azure service name"
        if not update and ("sku" not in cfg or "capacity" not in cfg):
            return "APIM provision requires explicit sku and capacity"
        sku = str(cfg.get("sku") or "")
        if sku:
            if sku not in self._config.allowed_skus:
                return f"APIM sku {sku!r} is not in the install allowed_skus"
            try:
                capacity = int(str(cfg.get("capacity")))
            except (TypeError, ValueError):
                return "APIM capacity must be an integer"
            if sku == "Consumption" and capacity != 0:
                return "APIM Consumption requires capacity=0"
            if sku != "Consumption" and not 1 <= capacity <= self._config.max_capacity:
                return f"APIM capacity must be between 1 and {self._config.max_capacity} for {sku}"
        elif "capacity" in cfg:
            return "APIM capacity update requires sku"
        network = str(cfg.get("network_mode") or "public")
        if network == "internal":
            if not self._config.allow_internal_network:
                return "APIM internal network mode is disabled by install policy"
            if sku and sku not in _INTERNAL_SKUS:
                return "APIM internal network mode requires Developer or Premium"
            subnet = str(cfg.get("subnet_id") or "")
            if subnet not in self._config.allowed_subnet_ids:
                return "APIM internal subnet_id is not in the install allowlist"
        elif network != "public":
            return "APIM network_mode must be public or internal"
        elif cfg.get("subnet_id"):
            return "APIM subnet_id is only valid with network_mode=internal"
        if cfg.get("identity") is not None and not isinstance(cfg["identity"], dict):
            return "APIM identity must be an object"
        identity = dict(cfg.get("identity") or {})
        unknown_identity = sorted(set(identity) - {"type", "user_assigned_identity_id"})
        if unknown_identity:
            return f"APIM identity contains unsupported fields: {', '.join(unknown_identity)}"
        identity_type = str(identity.get("type") or "system_assigned")
        if identity_type == "user_assigned":
            identity_id = str(identity.get("user_assigned_identity_id") or "")
            if identity_id not in self._config.allowed_user_assigned_identity_ids:
                return "APIM user-assigned identity is not in the install allowlist"
        elif identity_type != "system_assigned" or identity.get("user_assigned_identity_id"):
            return "APIM identity must be system_assigned or an allowlisted user_assigned identity"
        backends = cfg.get("backends") or []
        apis = cfg.get("apis") or []
        subscriptions = cfg.get("subscriptions") or []
        if not all(isinstance(items, list) for items in (backends, apis, subscriptions)):
            return "APIM backends, apis, and subscriptions must be arrays"
        if len(backends) > self._config.max_backends:
            return f"APIM backends exceed install limit {self._config.max_backends}"
        if len(apis) > self._config.max_apis:
            return f"APIM apis exceed install limit {self._config.max_apis}"
        if len(subscriptions) > self._config.max_subscriptions:
            return f"APIM subscriptions exceed install limit {self._config.max_subscriptions}"
        backend_ids: set[str] = set()
        backend_map: dict[str, dict[str, Any]] = {}
        for backend in backends:
            if not isinstance(backend, dict):
                return "APIM backend declarations must be objects"
            unknown_backend = sorted(
                set(backend) - {"description", "id", "identity_client_id", "identity_resource", "url"}
            )
            if unknown_backend:
                return f"APIM backend contains unsupported fields: {', '.join(unknown_backend)}"
            backend_id = str(backend.get("id") or "")
            error = self._validate_child_id(backend_id, "backend")
            if error:
                return error
            if backend_id in backend_ids:
                return f"duplicate APIM backend id {backend_id!r}"
            backend_ids.add(backend_id)
            backend_map[backend_id] = backend
            error = self._validate_backend(backend)
            if error:
                return error
        api_ids: set[str] = set()
        for api in apis:
            if not isinstance(api, dict):
                return "APIM API declarations must be objects"
            unknown_api = sorted(
                set(api)
                - {
                    "backend_id",
                    "cors",
                    "description",
                    "display_name",
                    "id",
                    "path",
                    "routes",
                    "subscription_required",
                }
            )
            if unknown_api:
                return f"APIM API contains unsupported fields: {', '.join(unknown_api)}"
            api_id = str(api.get("id") or "")
            error = self._validate_child_id(api_id, "API")
            if error:
                return error
            if api_id in api_ids:
                return f"duplicate APIM API id {api_id!r}"
            api_ids.add(api_id)
            display_name = str(api.get("display_name") or "")
            if not display_name.strip() or len(display_name) > 300:
                return f"APIM API {api_id!r} display_name is required and limited to 300 characters"
            path = str(api.get("path") or "")
            if not path or path.startswith("/") or path.endswith("/") or ".." in path.split("/"):
                return f"APIM API {api_id!r} path must be a non-traversing relative path without edge slashes"
            backend_id = str(api.get("backend_id") or "")
            if backend_id and backend_id not in backend_ids:
                return f"APIM API {api_id!r} references undeclared backend {backend_id!r}"
            if backend_id and "backend" not in self._config.allowed_policy_kinds:
                return "APIM backend routing policy is disabled by install policy"
            routes = api.get("routes") or []
            if not isinstance(routes, list) or len(routes) > self._config.max_routes_per_api:
                return f"APIM API {api_id!r} routes exceed install limit {self._config.max_routes_per_api}"
            route_ids: set[str] = set()
            for route in routes:
                if not isinstance(route, dict):
                    return f"APIM API {api_id!r} routes must be objects"
                unknown_route = sorted(set(route) - {"description", "display_name", "id", "method", "url_template"})
                if unknown_route:
                    return f"APIM operation contains unsupported fields: {', '.join(unknown_route)}"
                route_id = str(route.get("id") or "")
                error = self._validate_child_id(route_id, "operation")
                if error:
                    return error
                if route_id in route_ids:
                    return f"duplicate APIM operation id {route_id!r} in API {api_id!r}"
                route_ids.add(route_id)
                method = str(route.get("method") or "").upper()
                template = str(route.get("url_template") or "")
                if method not in _METHODS:
                    return f"APIM operation {route_id!r} has unsupported method"
                if not template.startswith("/") or ".." in template.split("/") or len(template) > 1000:
                    return f"APIM operation {route_id!r} url_template is invalid"
            cors = api.get("cors")
            if cors:
                if "cors" not in self._config.allowed_policy_kinds:
                    return "APIM CORS policy is disabled by install policy"
                error = self._validate_cors(cors)
                if error:
                    return error
            if backend_id:
                backend = backend_map[backend_id]
                if backend.get("identity_resource") and "managed_identity" not in self._config.allowed_policy_kinds:
                    return "APIM managed-identity policy is disabled by install policy"
        if subscriptions and not self._config.allow_subscriptions:
            return "APIM subscription creation is disabled by install policy"
        subscription_ids: set[str] = set()
        for subscription in subscriptions:
            if not isinstance(subscription, dict):
                return "APIM subscription declarations must be objects"
            unknown_subscription = sorted(set(subscription) - {"api_id", "display_name", "id", "state"})
            if unknown_subscription:
                return f"APIM subscription contains unsupported fields: {', '.join(unknown_subscription)}"
            sub_id = str(subscription.get("id") or "")
            error = self._validate_child_id(sub_id, "subscription")
            if error:
                return error
            if sub_id in subscription_ids:
                return f"duplicate APIM subscription id {sub_id!r}"
            subscription_ids.add(sub_id)
            if not str(subscription.get("display_name") or "").strip():
                return f"APIM subscription {sub_id!r} display_name is required"
            if str(subscription.get("api_id") or "") not in api_ids:
                return f"APIM subscription {sub_id!r} references an undeclared API"
            if str(subscription.get("state") or "active") not in {"active", "suspended"}:
                return f"APIM subscription {sub_id!r} state must be active or suspended"
        domains = cfg.get("custom_domains") or []
        if not isinstance(domains, list) or len(domains) > 20:
            return "APIM custom_domains must be an array with at most 20 entries"
        if domains:
            if not self._config.allow_custom_domains:
                return "APIM custom domains are disabled by install policy"
            default_count = 0
            for domain in domains:
                if not isinstance(domain, dict):
                    return "APIM custom domain declarations must be objects"
                unknown_domain = sorted(set(domain) - {"default_ssl_binding", "hostname", "key_vault_secret_id"})
                if unknown_domain:
                    return f"APIM custom domain contains unsupported fields: {', '.join(unknown_domain)}"
                hostname = str(domain.get("hostname") or "").lower().rstrip(".")
                if not _HOST_RE.fullmatch(hostname) or not _host_allowed(
                    hostname,
                    self._config.allowed_custom_domain_suffixes,
                ):
                    return f"APIM custom hostname {hostname!r} is not in the install allowlist"
                secret_id = str(domain.get("key_vault_secret_id") or "")
                parsed_secret = urlparse(secret_id)
                if (
                    parsed_secret.scheme != "https"
                    or not (parsed_secret.hostname or "").endswith(".vault.azure.net")
                    or parsed_secret.username
                    or parsed_secret.password
                    or parsed_secret.query
                    or parsed_secret.fragment
                    or not parsed_secret.path.startswith("/secrets/")
                    or not any(
                        secret_id.startswith(prefix) for prefix in self._config.allowed_key_vault_secret_prefixes
                    )
                ):
                    return f"APIM custom hostname {hostname!r} Key Vault secret is not allowlisted"
                default_count += int(bool(domain.get("default_ssl_binding")))
            if default_count > 1:
                return "APIM custom_domains may select at most one default_ssl_binding"
        if cfg.get("prune_children") and not self._config.allow_child_pruning:
            return "APIM child pruning is disabled by install policy"
        if cfg.get("adopt_existing") and not self._config.allow_adoption:
            return "APIM adoption is disabled by install policy"
        return ""

    def _validate_child_id(self, child_id: str, label: str) -> str:
        if not _ENTITY_RE.fullmatch(child_id) or not child_id.startswith(_CHILD_PREFIX):
            return f"APIM {label} id must be an {_CHILD_PREFIX!r}-prefixed 2-80 character identifier"
        return ""

    def _validate_backend(self, backend: dict[str, Any]) -> str:
        backend_id = str(backend.get("id") or "")
        raw_url = str(backend.get("url") or "")
        parsed = urlparse(raw_url)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.endswith("/")
        ):
            return (
                f"APIM backend {backend_id!r} URL must be credential-free HTTPS "
                "without query, fragment, or trailing slash"
            )
        try:
            address = ipaddress.ip_address(hostname.strip("[]"))
        except ValueError:
            address = None
        if (
            address is not None
            or hostname == "localhost"
            or not _host_allowed(
                hostname,
                self._config.allowed_backend_host_suffixes,
            )
        ):
            return f"APIM backend {backend_id!r} hostname is not in the install allowlist"
        identity_resource = str(backend.get("identity_resource") or "")
        if identity_resource and identity_resource not in self._config.allowed_backend_identity_resources:
            return f"APIM backend {backend_id!r} managed-identity resource is not allowlisted"
        identity_client_id = str(backend.get("identity_client_id") or "")
        if identity_client_id and not identity_resource:
            return f"APIM backend {backend_id!r} identity_client_id requires identity_resource"
        if identity_client_id and not _UUID_RE.fullmatch(identity_client_id):
            return f"APIM backend {backend_id!r} identity_client_id must be a UUID"
        return ""

    def _validate_cors(self, cors: Any) -> str:
        if not isinstance(cors, dict):
            return "APIM cors must be an object"
        origins = cors.get("origins") or []
        if not isinstance(origins, list) or not origins or len(origins) > 20:
            return "APIM cors origins must contain 1-20 HTTPS origins"
        for origin in origins:
            parsed = urlparse(str(origin))
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                return f"APIM CORS origin {origin!r} must be an HTTPS origin without a path"
        methods = cors.get("methods") or ["GET"]
        if not isinstance(methods, list) or not methods or any(str(item).upper() not in _METHODS for item in methods):
            return "APIM cors methods contains an unsupported method"
        for key in ("headers", "expose_headers"):
            values = cors.get(key) or []
            if not isinstance(values, list) or len(values) > 50 or any(not _valid_header(str(item)) for item in values):
                return f"APIM cors {key} contains an invalid header name"
        return ""

    def _service_body(
        self,
        spec: ProvisionSpec,
        cfg: dict[str, Any],
        existing: dict[str, Any] | None,
    ) -> dict[str, Any]:
        tags = arm_tags_for(spec)
        if existing is not None and cfg.get("adopt_existing") and _ownership(existing) != self._owner(spec):
            tags = {**_tags(existing), **tags, _ADOPTED_TAG: "true"}
        properties: dict[str, Any] = {
            "publisherEmail": self._config.publisher_email,
            "publisherName": self._config.publisher_name,
            **self._network_properties(cfg),
        }
        domains = self._hostname_configurations(cfg)
        if domains:
            properties["hostnameConfigurations"] = domains
        return {
            "location": self._config.location,
            "sku": {"name": str(cfg["sku"]), "capacity": int(cfg["capacity"])},
            "identity": self._identity_body(cfg),
            "properties": properties,
            "tags": tags,
        }

    def _update_body(self, existing: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        current_sku = dict(existing.get("sku") or {})
        sku = str(cfg.get("sku") or current_sku.get("name") or "")
        capacity = int(str(cfg.get("capacity", current_sku.get("capacity", 1))))
        existing_properties = dict(existing.get("properties") or {})
        properties = {
            "publisherEmail": str(existing_properties.get("publisherEmail") or self._config.publisher_email),
            "publisherName": str(existing_properties.get("publisherName") or self._config.publisher_name),
            **self._network_properties(cfg, existing=existing_properties),
        }
        if "custom_domains" in cfg:
            properties["hostnameConfigurations"] = self._hostname_configurations(cfg)
        elif existing_properties.get("hostnameConfigurations"):
            properties["hostnameConfigurations"] = existing_properties["hostnameConfigurations"]
        return {
            "location": str(existing.get("location") or self._config.location),
            "sku": {"name": sku, "capacity": capacity},
            "identity": self._identity_body(cfg, existing=existing),
            "properties": properties,
            "tags": dict(existing.get("tags") or {}),
        }

    def _network_properties(
        self,
        cfg: dict[str, Any],
        *,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current_mode = str((existing or {}).get("virtualNetworkType") or "None")
        mode = str(cfg.get("network_mode") or ("internal" if current_mode == "Internal" else "public"))
        if mode == "internal":
            subnet_id = str(
                cfg.get("subnet_id")
                or ((existing or {}).get("virtualNetworkConfiguration") or {}).get("subnetResourceId")
                or ""
            )
            return {
                "publicNetworkAccess": "Disabled",
                "virtualNetworkType": "Internal",
                "virtualNetworkConfiguration": {"subnetResourceId": subnet_id},
            }
        return {"publicNetworkAccess": "Enabled", "virtualNetworkType": "None"}

    def _identity_body(
        self,
        cfg: dict[str, Any],
        *,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = dict(cfg.get("identity") or {})
        if not identity and existing:
            current = dict(existing.get("identity") or {})
            if current:
                return {key: value for key, value in current.items() if key in {"type", "userAssignedIdentities"}}
        if str(identity.get("type") or "system_assigned") == "user_assigned":
            identity_id = str(identity["user_assigned_identity_id"])
            return {"type": "UserAssigned", "userAssignedIdentities": {identity_id: {}}}
        return {"type": "SystemAssigned"}

    def _hostname_configurations(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "type": "Proxy",
                "hostName": str(item["hostname"]).lower().rstrip("."),
                "keyVaultId": str(item["key_vault_secret_id"]),
                "negotiateClientCertificate": False,
                "defaultSslBinding": bool(item.get("default_ssl_binding", False)),
            }
            for item in cfg.get("custom_domains") or []
        ]

    def _reconcile_children(
        self,
        service_name: str,
        cfg: dict[str, Any],
        *,
        only_declared: bool = False,
    ) -> None:
        base = self._service_path(service_name)
        backends = list(cfg.get("backends") or [])
        apis = list(cfg.get("apis") or [])
        subscriptions = list(cfg.get("subscriptions") or [])
        if backends or (not only_declared and "backends" in cfg):
            for backend in backends:
                self._api.put(
                    f"{base}/backends/{_segment(str(backend['id']))}",
                    {
                        "properties": {
                            "protocol": "http",
                            "url": str(backend["url"]),
                            "description": str(backend.get("description") or ""),
                        },
                    },
                )
        backend_map = {str(item["id"]): item for item in backends}
        if apis or (not only_declared and "apis" in cfg):
            for api in apis:
                api_id = str(api["id"])
                api_base = f"{base}/apis/{_segment(api_id)}"
                self._api.put(
                    api_base,
                    {
                        "properties": {
                            "apiType": "http",
                            "displayName": str(api["display_name"]),
                            "description": str(api.get("description") or ""),
                            "path": str(api["path"]),
                            "protocols": ["https"],
                            "subscriptionRequired": bool(api.get("subscription_required", False)),
                        },
                    },
                )
                for route in api.get("routes") or []:
                    self._api.put(
                        f"{api_base}/operations/{_segment(str(route['id']))}",
                        {
                            "properties": {
                                "displayName": str(route.get("display_name") or route["id"]),
                                "description": str(route.get("description") or ""),
                                "method": str(route["method"]).upper(),
                                "urlTemplate": str(route["url_template"]),
                                "templateParameters": [],
                                "responses": [],
                            },
                        },
                    )
                policy = self._policy(api, backend_map)
                if policy:
                    self._api.put(
                        f"{api_base}/policies/policy",
                        {"properties": {"format": "xml", "value": policy}},
                    )
        if subscriptions or (not only_declared and "subscriptions" in cfg):
            for subscription in subscriptions:
                api_id = str(subscription["api_id"])
                self._api.put(
                    f"{base}/subscriptions/{_segment(str(subscription['id']))}",
                    {
                        "properties": {
                            "allowTracing": False,
                            "displayName": str(subscription["display_name"]),
                            "scope": f"{base}/apis/{_segment(api_id)}",
                            "state": str(subscription.get("state") or "active"),
                        },
                    },
                )
        if cfg.get("prune_children"):
            self._prune(base, "backends", {str(item["id"]) for item in backends})
            self._prune(base, "apis", {str(item["id"]) for item in apis})
            self._prune(base, "subscriptions", {str(item["id"]) for item in subscriptions})
            for api in apis:
                api_base = f"{base}/apis/{_segment(str(api['id']))}"
                self._prune(
                    api_base,
                    "operations",
                    {str(item["id"]) for item in api.get("routes") or []},
                )

    def _policy(self, api: dict[str, Any], backends: dict[str, dict[str, Any]]) -> str:
        backend_id = str(api.get("backend_id") or "")
        cors = api.get("cors") or {}
        if not backend_id and not cors:
            return ""
        policies = Element("policies")
        inbound = SubElement(policies, "inbound")
        SubElement(inbound, "base")
        if backend_id:
            SubElement(inbound, "set-backend-service", {"backend-id": backend_id})
            backend = backends[backend_id]
            resource = str(backend.get("identity_resource") or "")
            if resource:
                attrs = {"resource": resource}
                if backend.get("identity_client_id"):
                    attrs["client-id"] = str(backend["identity_client_id"])
                SubElement(inbound, "authentication-managed-identity", attrs)
        if cors:
            cors_element = SubElement(
                inbound,
                "cors",
                {"allow-credentials": str(bool(cors.get("allow_credentials", False))).lower()},
            )
            origins = SubElement(cors_element, "allowed-origins")
            for origin in cors["origins"]:
                SubElement(origins, "origin").text = str(origin)
            methods = SubElement(cors_element, "allowed-methods")
            for method in cors.get("methods") or ["GET"]:
                SubElement(methods, "method").text = str(method).upper()
            headers = SubElement(cors_element, "allowed-headers")
            for header in cors.get("headers") or []:
                SubElement(headers, "header").text = str(header)
            exposed = SubElement(cors_element, "expose-headers")
            for header in cors.get("expose_headers") or []:
                SubElement(exposed, "header").text = str(header)
        backend_stage = SubElement(policies, "backend")
        SubElement(backend_stage, "base")
        outbound = SubElement(policies, "outbound")
        SubElement(outbound, "base")
        on_error = SubElement(policies, "on-error")
        SubElement(on_error, "base")
        return tostring(policies, encoding="unicode", short_empty_elements=True)

    def _prune(self, parent: str, collection: str, desired_ids: set[str]) -> None:
        for item in self._api.list(f"{parent}/{collection}"):
            item_id = str(item.get("name") or "")
            if item_id.startswith(_CHILD_PREFIX) and item_id not in desired_ids:
                self._api.delete(f"{parent}/{collection}/{_segment(item_id)}")

    def _assert_owned_or_adoptable(
        self,
        resource: dict[str, Any],
        spec: ProvisionSpec,
        cfg: dict[str, Any],
    ) -> None:
        owner = _ownership(resource)
        expected = self._owner(spec)
        if owner == expected:
            return
        if owner and owner != expected:
            raise AzureAPIMError("API Management service belongs to another managed-service row")
        if not cfg.get("adopt_existing"):
            raise AzureAPIMError("API Management name collision requires adopt_existing=true")

    def _assert_owned(self, resource: dict[str, Any]) -> None:
        if not _ownership(resource):
            raise AzureAPIMError("API Management service lacks Astrolift ownership tags")

    def _service_name(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        explicit = str(cfg.get("service_name") or "")
        if explicit:
            return explicit
        raw = "-".join(
            filter(
                None,
                (
                    self._config.service_name_prefix,
                    spec.organization_slug,
                    spec.app_slug,
                    spec.environment_name,
                    spec.service_handle_hint,
                ),
            ),
        ).lower()
        safe = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")
        if not safe or not safe[0].isalpha():
            safe = f"a-{safe}"
        if len(safe) > 50:
            suffix = hashlib.sha256(raw.encode()).hexdigest()[:10]
            safe = f"{safe[:39].rstrip('-')}-{suffix}"
        if not _SERVICE_RE.fullmatch(safe):
            raise AzureAPIMError("derived API Management service name is invalid")
        return safe

    def _owner(self, spec: ProvisionSpec) -> str:
        return (
            spec.managed_service_id
            or hashlib.sha256(
                f"{spec.organization_id}:{spec.app_id}:{spec.environment_id}:{spec.service_handle_hint}".encode(),
            ).hexdigest()[:32]
        )

    def _service_path(self, service_name: str) -> str:
        return (
            f"/subscriptions/{_segment(self._config.subscription_id)}/resourceGroups/"
            f"{_segment(self._config.resource_group)}/providers/Microsoft.ApiManagement/service/"
            f"{_segment(service_name)}"
        )

    @staticmethod
    def _handle(service_name: str) -> str:
        return f"{KIND}/{service_name}"

    @staticmethod
    def _parse_handle(handle: str) -> str:
        prefix = f"{KIND}/"
        if not handle.startswith(prefix):
            raise ValueError("invalid Azure API Management handle")
        service_name = handle.removeprefix(prefix)
        if not _SERVICE_RE.fullmatch(service_name):
            raise ValueError("invalid Azure API Management service name in handle")
        return service_name

    def _get(self, path: str) -> dict[str, Any] | None:
        try:
            return self._api.get(path)
        except AzureAPIMNotFound:
            return None


def _segment(value: str) -> str:
    return quote(value, safe="")


def _tags(resource: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in dict(resource.get("tags") or {}).items()}


def _ownership(resource: dict[str, Any]) -> str:
    return _tags(resource).get(_OWNERSHIP_TAG, "")


def _provisioning_state(resource: dict[str, Any]) -> str:
    return str((resource.get("properties") or {}).get("provisioningState") or "")


def _host_allowed(hostname: str, suffixes: tuple[str, ...]) -> bool:
    return any(hostname.endswith(suffix) and hostname != suffix.removeprefix(".") for suffix in suffixes)


def _valid_email(value: str) -> bool:
    if len(value) > 254 or value.count("@") != 1:
        return False
    local, domain = value.rsplit("@", 1)
    return bool(local and len(local) <= 64 and _HOST_RE.fullmatch(f"x.{domain}" if "." not in domain else domain))


def _valid_header(value: str) -> bool:
    return bool(re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", value))


__all__ = [
    "KIND",
    "VARIANT",
    "AzureAPIMClient",
    "AzureAPIMConfig",
    "AzureAPIMDriver",
    "AzureAPIMError",
    "AzureAPIMNotFound",
    "AzureAPIMRestClient",
]
