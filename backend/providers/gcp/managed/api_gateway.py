"""Google Cloud API Gateway lifecycle with immutable config revisions."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
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
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from gcp._raw_fields import raw_field_conflicts

KIND = "api_gateway"
_API_ROOT = "https://apigateway.googleapis.com/v1"
_ID_RE = re.compile(r"^[a-z](?:[a-z0-9-]{2,61}[a-z0-9])$")
_OUTPUT_ONLY = {
    "createTime",
    "defaultHostname",
    "name",
    "serviceConfigId",
    "state",
    "updateTime",
}
_RESERVED_FIELDS = _OUTPUT_ONLY | {
    "apiConfig",
    "displayName",
    "gatewayServiceAccount",
    "grpcServices",
    "labels",
    "managedService",
    "managedServiceConfigs",
    "openapiDocuments",
}


class APIGatewayError(RuntimeError):
    pass


class APIGatewayNotFound(APIGatewayError):
    pass


class APIGatewayConflict(APIGatewayError):
    pass


@dataclass(frozen=True)
class APIGatewayConfig:
    project_id: str
    region: str
    api_id_prefix: str = "astrolift"
    gateway_id_prefix: str = "astrolift"
    config_id_prefix: str = "cfg"
    api_endpoint: str = _API_ROOT
    deletion_protection_default: bool = True
    operation_timeout_seconds: float = 1800
    poll_interval_seconds: float = 5


class APIGatewayRestClient:
    def __init__(self, *, endpoint: str = _API_ROOT, session: Any | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=("https://www.googleapis.com/auth/cloud-platform",),
            )
            session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._session = session

    def get(self, name: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        return self._request("GET", name, params=params)

    def list_resources(
        self,
        parent: str,
        collection: str,
        response_key: str,
        *,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            query = {"pageSize": "1000", **(params or {})}
            if token:
                query["pageToken"] = token
            payload = self._request("GET", f"{parent}/{collection}", params=query)
            rows.extend(payload.get(response_key) or [])
            token = str(payload.get("nextPageToken") or "")
            if not token:
                return rows

    def create(
        self,
        parent: str,
        collection: str,
        resource_id: str,
        id_parameter: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/{collection}",
            params={id_parameter: resource_id},
            json=body,
        )

    def patch(self, name: str, body: dict[str, Any], update_mask: list[str]) -> dict[str, Any]:
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json={"name": name, **body},
        )

    def delete(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", name)

    def get_operation(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def _request(
        self,
        method: str,
        resource: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._session.request(
            method,
            f"{self._endpoint}/{resource.lstrip('/')}",
            params=params,
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise APIGatewayNotFound(resource)
        if response.status_code == 409:
            raise APIGatewayConflict(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise APIGatewayError(f"API Gateway HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            raise APIGatewayError("API Gateway returned a non-object response")
        return dict(payload)


class APIGatewayDriver(ManagedServiceDriver):
    KIND = KIND

    def __init__(
        self,
        *,
        config: APIGatewayConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._api = client or APIGatewayRestClient(endpoint=config.api_endpoint)
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="api_gateway",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate(cfg, update=False)
        if error:
            return ProvisionResult(False, "", error, ["invalid_api_gateway_config"])
        region = str(cfg.get("region") or self._config.region)
        api_id = self._api_id(spec, cfg)
        gateway_id = self._gateway_id(spec, cfg)
        handle = _handle(region, gateway_id)
        labels = self._labels(spec, cfg)
        service_id = _label_value(spec.managed_service_id or gateway_id)
        labels.setdefault("astrolift-io-managed-service-id", service_id)
        labels.setdefault(MANAGED_SERVICE_ID_LABEL, service_id)
        api_name = self._api_name(api_id)
        gateway_name = self._gateway_name(region, gateway_id)
        try:
            api_resource = self._ensure_api(api_name, api_id, cfg, labels, spec.managed_service_id)
            config_name = self._ensure_config(api_name, cfg, labels, spec.managed_service_id)
            gateway = self._ensure_gateway(
                gateway_name,
                gateway_id,
                region,
                config_name,
                cfg,
                labels,
                spec.managed_service_id,
            )
            self._prune_configs(api_name, config_name, cfg, service_id)
            config_resource = self._api.get(config_name)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision API Gateway: {exc}", [str(exc)])
        ready = all(
            str(resource.get("state") or "") == "ACTIVE" for resource in (api_resource, config_resource, gateway)
        )
        return ProvisionResult(
            True,
            handle,
            f"API Gateway {gateway_id} points at {config_name.rsplit('/', 1)[-1]}",
            ready=ready,
        )

    @driver_op(cloud="gcp", driver="api_gateway")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            region, gateway_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate(cfg, update=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_api_gateway_config"])
        if not cfg.get("api_id"):
            return UpdateResult(False, spec.handle, "API Gateway update requires api_id", ["api_id_required"])
        api_name = self._api_name(str(cfg["api_id"]))
        gateway_name = self._gateway_name(region, gateway_id)
        try:
            gateway = self._api.get(gateway_name)
            self._assert_managed(gateway, "gateway")
            current_api_name = str(gateway.get("apiConfig") or "").split("/configs/", 1)[0]
            if current_api_name and current_api_name != api_name and not cfg.get("allow_api_retarget"):
                return UpdateResult(
                    False,
                    spec.handle,
                    "gateway API retargeting requires allow_api_retarget=true",
                    ["api_retarget_guard"],
                )
            api_resource = self._api.get(api_name)
            self._assert_managed(api_resource, "API")
            api_labels = dict(api_resource.get("labels") or {})
            self._patch(api_name, api_resource, self._api_body(cfg, api_labels), immutable={"managedService"})
            service_id = str(api_labels.get("astrolift-io-managed-service-id") or "")
            config_name = self._ensure_config(api_name, cfg, api_labels, service_id)
            gateway_labels = dict(gateway.get("labels") or {})
            desired = self._gateway_body(config_name, cfg, gateway_labels)
            self._patch(gateway_name, gateway, desired)
            service_id = service_id or str(
                gateway_labels.get("astrolift-io-managed-service-id") or _label_value(gateway_id)
            )
            self._prune_configs(api_name, config_name, cfg, service_id)
        except APIGatewayNotFound:
            return UpdateResult(False, spec.handle, "API Gateway not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update API Gateway: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"API Gateway {gateway_id} rollout completed")

    @driver_op(
        cloud="gcp",
        driver="api_gateway",
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
            region, gateway_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        api_id = str(cfg.get("api_id") or "")
        if not _ID_RE.fullmatch(api_id):
            return DeprovisionResult(
                False,
                spec.handle,
                "API Gateway deprovision requires the stored api_id",
                ["api_id_required"],
                retryable=False,
            )
        gateway_name = self._gateway_name(region, gateway_id)
        api_name = self._api_name(api_id)
        gateway = self._get(gateway_name)
        api_resource = self._get(api_name)
        if gateway is None and api_resource is None:
            return DeprovisionResult(True, spec.handle, f"API Gateway {gateway_id} already gone")
        for resource, label in ((gateway, "gateway"), (api_resource, "API")):
            if resource is None:
                continue
            try:
                self._assert_managed(resource, label)
            except Exception as exc:
                return DeprovisionResult(False, spec.handle, str(exc), ["ownership_guard"], retryable=False)
            if (resource.get("labels") or {}).get("astrolift-io-adopted") == "true" and not cfg.get("delete_adopted"):
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"adopted API Gateway {label} requires delete_adopted=true",
                    ["adopted_resource_guard"],
                    retryable=False,
                )
        if bool(cfg.get("deletion_protection", self._config.deletion_protection_default)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "API Gateway deletion protection is enabled; use force_destroy",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            gateways = self._gateways_for_api(api_name)
            external = [item for item in gateways if str(item.get("name")) != gateway_name]
            if external and not (force_destroy and cfg.get("delete_external_gateways")):
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "other gateways reference this API; force_destroy plus delete_external_gateways=true is required",
                    ["external_gateways_present"],
                    retryable=False,
                )
            service_id = str(
                ((api_resource or {}).get("labels") or {}).get("astrolift-io-managed-service-id")
                or _label_value(gateway_id)
            )
            configs = self._list_configs(api_name)
            external_configs = [item for item in configs if not _is_owned(item, service_id)]
            if external_configs and not (force_destroy and cfg.get("delete_external_configs")):
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "external API configs remain; force_destroy plus delete_external_configs=true is required",
                    ["external_configs_present"],
                    retryable=False,
                )
            for item in [*external, *([gateway] if gateway else [])]:
                self._wait(self._api.delete(str(item["name"])))
            for config in configs:
                self._wait(self._api.delete(str(config["name"])))
            if api_resource is not None:
                self._wait(self._api.delete(api_name))
        except APIGatewayNotFound:
            pass
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete API Gateway: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"API Gateway {gateway_id} deleted")

    @driver_op(cloud="gcp", driver="api_gateway")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            region, gateway_id = _parse_handle(handle.handle)
            gateway = self._api.get(self._gateway_name(region, gateway_id))
            config_name = str(gateway.get("apiConfig") or "")
            config = self._api.get(config_name) if config_name else {}
            api_name = config_name.split("/configs/", 1)[0] if "/configs/" in config_name else ""
            api_resource = self._api.get(api_name) if api_name else {}
        except APIGatewayNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "API Gateway does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe API Gateway: {exc}")
        states = [
            str(api_resource.get("state") or ""),
            str(config.get("state") or ""),
            str(gateway.get("state") or "STATE_UNSPECIFIED"),
        ]
        if "FAILED" in states:
            state = "error"
        elif "DELETING" in states:
            state = "deprovisioning"
        elif any(item in {"CREATING", "ACTIVATING"} for item in states):
            state = "provisioning"
        elif "UPDATING" in states:
            state = "updating"
        elif all(item == "ACTIVE" for item in states):
            state = "available"
        else:
            state = "error"
        return ServiceStatus(
            handle.handle,
            state,
            f"API Gateway {gateway_id} is {'/'.join(item for item in states if item)}",
        )

    @driver_op(cloud="gcp", driver="api_gateway")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        del config
        region, gateway_id = _parse_handle(handle.handle)
        gateway = self._api.get(self._gateway_name(region, gateway_id))
        self._assert_managed(gateway, "gateway")
        hostname = str(gateway.get("defaultHostname") or "")
        url = f"https://{hostname}" if hostname else ""
        return Binding(
            env_vars={
                "API_GATEWAY_URL": ValueRef(literal=url),
                "API_GATEWAY_HOST": ValueRef(literal=hostname),
                "API_GATEWAY_ID": ValueRef(literal=gateway_id),
                "GCP_API_GATEWAY_NAME": ValueRef(literal=str(gateway.get("name") or "")),
                "GCP_API_CONFIG_NAME": ValueRef(literal=str(gateway.get("apiConfig") or "")),
                "GOOGLE_CLOUD_PROJECT": ValueRef(literal=self._config.project_id),
                "GOOGLE_CLOUD_REGION": ValueRef(literal=region),
            },
            notes=(
                "Client authentication and authorization are defined by the immutable OpenAPI/gRPC config; "
                "Astrolift does not inject API keys or make an implicit public IAM change."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        region, gateway_id = _parse_handle(handle.handle)
        gateway = self._api.get(self._gateway_name(region, gateway_id))
        self._assert_managed(gateway, "gateway")
        config_name = str(gateway.get("apiConfig") or "")
        if not config_name:
            raise APIGatewayError("gateway has no API config to snapshot")
        config = self._api.get(config_name)
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=config_name,
            created_at=str(config.get("createTime") or datetime.now(tz=UTC).isoformat()),
        )

    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        cfg = dict(target.config or {})
        if "/apis/" not in snapshot.snapshot_id or "/configs/" not in snapshot.snapshot_id:
            return ProvisionResult(False, "", "invalid API config snapshot", ["invalid_snapshot"])
        snapshot_api_id = snapshot.snapshot_id.split("/apis/", 1)[1].split("/configs/", 1)[0]
        if cfg.get("api_id") and cfg["api_id"] != snapshot_api_id:
            return ProvisionResult(False, "", "snapshot belongs to a different api_id", ["snapshot_api_mismatch"])
        cfg["api_id"] = snapshot_api_id
        cfg["api_config_ref"] = snapshot.snapshot_id
        cfg.pop("config_id", None)
        cfg.pop("openapi_documents", None)
        cfg.pop("grpc_services", None)
        cfg.pop("managed_service_configs", None)
        return self.provision(
            ProvisionSpec(
                **{
                    **target.__dict__,
                    "config": cfg,
                },
            ),
        )

    @driver_op(cloud="gcp", driver="api_gateway", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        file_schema = {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "contents": {"type": "string", "description": "UTF-8 file contents."},
                "contents_base64": {"type": "string", "contentEncoding": "base64"},
            },
            "additionalProperties": False,
        }
        document = {
            "type": "object",
            "required": ["document"],
            "properties": {"document": file_schema},
            "additionalProperties": False,
        }
        grpc = {
            "type": "object",
            "required": ["file_descriptor_set"],
            "properties": {
                "file_descriptor_set": file_schema,
                "source": {"type": "array", "items": file_schema},
            },
            "additionalProperties": False,
        }
        raw = {
            "type": "object",
            "description": "Unknown provider-native fields; typed, ownership, and output fields cannot be overridden.",
            "additionalProperties": True,
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "api_id": {"type": "string", "pattern": _ID_RE.pattern},
                "gateway_id": {"type": "string", "pattern": _ID_RE.pattern},
                "region": {"type": "string"},
                "config_id": {"type": "string", "pattern": _ID_RE.pattern},
                "api_config_ref": {"type": "string"},
                "openapi_documents": {"type": "array", "minItems": 1, "items": document},
                "grpc_services": {"type": "array", "minItems": 1, "items": grpc},
                "managed_service_configs": {"type": "array", "minItems": 1, "items": file_schema},
                "gateway_service_account": {"type": "string"},
                "api_display_name": {"type": "string"},
                "config_display_name": {"type": "string"},
                "gateway_display_name": {"type": "string"},
                "managed_service": {"type": "string"},
                "labels": {"type": "object", "additionalProperties": {"type": "string"}},
                "api_raw_fields": raw,
                "config_raw_fields": raw,
                "gateway_raw_fields": raw,
                "prune_config_revisions": {"type": "boolean", "default": False},
                "retain_config_revisions": {"type": "integer", "minimum": 1, "default": 5},
                "allow_config_revision_delete": {"type": "boolean", "default": False},
                "delete_adopted": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
                "delete_external_gateways": {"type": "boolean", "default": False},
                "delete_external_configs": {"type": "boolean", "default": False},
                "allow_api_retarget": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="api_gateway", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "API_GATEWAY_URL": "Portable HTTPS base URL",
                "API_GATEWAY_HOST": "Default gateway.dev hostname",
                "API_GATEWAY_ID": "Portable gateway ID",
                "GCP_API_GATEWAY_NAME": "Fully qualified gateway resource name",
                "GCP_API_CONFIG_NAME": "Active immutable API config resource name",
                "GOOGLE_CLOUD_PROJECT": "Google Cloud project ID",
                "GOOGLE_CLOUD_REGION": "Google Cloud region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "api_config_ref",
            "config_display_name",
            "config_id",
            "config_raw_fields",
            "gateway_display_name",
            "gateway_raw_fields",
            "gateway_service_account",
            "grpc_services",
            "labels",
            "managed_service_configs",
            "openapi_documents",
            "prune_config_revisions",
            "retain_config_revisions",
        ]

    def _validate(self, cfg: dict[str, Any], *, update: bool) -> str:
        if not self._config.project_id:
            return "API Gateway requires a Google Cloud project ID"
        if not str(cfg.get("region") or self._config.region):
            return "API Gateway requires a region"
        if update and any(key in cfg for key in ("region", "gateway_id")):
            return "API Gateway region and gateway_id are immutable; reprovision the gateway"
        for key in ("api_id", "gateway_id", "config_id"):
            if key in cfg and not _ID_RE.fullmatch(str(cfg[key])):
                return f"API Gateway {key} must be a valid 4-63 character ID"
        sources = [
            bool(cfg.get("api_config_ref")),
            bool(cfg.get("openapi_documents")),
            bool(cfg.get("grpc_services")),
        ]
        if sum(sources) != 1:
            return "API Gateway requires exactly one of api_config_ref, openapi_documents, or grpc_services"
        if cfg.get("api_config_ref") and (cfg.get("config_id") or cfg.get("managed_service_configs")):
            return "api_config_ref cannot be combined with config_id or config source documents"
        if cfg.get("grpc_services") and not cfg.get("managed_service_configs"):
            return "gRPC API configs require managed_service_configs"
        if cfg.get("openapi_documents") and cfg.get("managed_service_configs"):
            return "managed_service_configs are only valid with grpc_services"
        api_config_ref = str(cfg.get("api_config_ref") or "")
        if api_config_ref:
            expected = f"projects/{self._config.project_id}/locations/global/apis/"
            if not api_config_ref.startswith(expected) or "/configs/" not in api_config_ref:
                return "api_config_ref must name an API config in the configured project"
            if cfg.get("api_id") and not api_config_ref.startswith(f"{self._api_name(str(cfg['api_id']))}/configs/"):
                return "api_config_ref must belong to api_id"
        files: list[dict[str, Any]] = []
        files.extend(item.get("document") or {} for item in cfg.get("openapi_documents") or [])
        for service in cfg.get("grpc_services") or []:
            files.append(service.get("file_descriptor_set") or {})
            files.extend(service.get("source") or [])
        files.extend(cfg.get("managed_service_configs") or [])
        seen_paths: set[str] = set()
        for file in files:
            path = str(file.get("path") or "")
            if not path or path.startswith("/") or ".." in path.split("/"):
                return "API config file paths must be relative and stay within the config root"
            if path in seen_paths:
                return f"duplicate API config file path {path!r}"
            seen_paths.add(path)
            if bool(file.get("contents")) == bool(file.get("contents_base64")):
                return f"API config file {path!r} requires exactly one of contents or contents_base64"
            if file.get("contents_base64"):
                try:
                    base64.b64decode(str(file["contents_base64"]), validate=True)
                except ValueError:
                    return f"API config file {path!r} contents_base64 is invalid"
        if cfg.get("prune_config_revisions") and not cfg.get("allow_config_revision_delete"):
            return "prune_config_revisions requires allow_config_revision_delete=true"
        if int(cfg.get("retain_config_revisions") or 5) < 1:
            return "retain_config_revisions must be at least 1"
        reserved = sorted(key for key in _normalized_labels(cfg.get("labels") or {}) if key.startswith("astrolift-io-"))
        if reserved:
            return f"labels cannot set Astrolift-reserved keys: {', '.join(reserved)}"
        for key, label in (
            ("api_raw_fields", "api_raw_fields"),
            ("config_raw_fields", "config_raw_fields"),
            ("gateway_raw_fields", "gateway_raw_fields"),
        ):
            # Google's JSON parser accepts a field's proto name as well as its
            # lowerCamelCase JSON name, so a raw field spelled in proto form
            # bypassed the exact-match check below (#1981).
            proto_names, forbidden = raw_field_conflicts(cfg.get(key) or {}, _RESERVED_FIELDS)
            if proto_names:
                return f"{label} must use the API's lowerCamelCase JSON field names, not {', '.join(proto_names)}"
            if forbidden:
                return f"{label} cannot set typed or output fields: {', '.join(forbidden)}"
        return ""

    def _ensure_api(
        self,
        name: str,
        api_id: str,
        cfg: dict[str, Any],
        labels: dict[str, str],
        service_id: str,
    ) -> dict[str, Any]:
        desired = self._api_body(cfg, labels)
        current = self._get(name)
        if current is None:
            try:
                self._wait(self._api.create(self._global_parent(), "apis", api_id, "apiId", desired))
                return self._api.get(name)
            except APIGatewayConflict:
                current = self._api.get(name)
        self._assert_adoptable(current, service_id, "API")
        desired["labels"] = self._merged_labels(current, labels)
        self._patch(name, current, desired, immutable={"managedService"})
        return self._api.get(name)

    def _ensure_config(
        self,
        api_name: str,
        cfg: dict[str, Any],
        labels: dict[str, str],
        service_id: str,
    ) -> str:
        if cfg.get("api_config_ref"):
            name = str(cfg["api_config_ref"])
            current = self._api.get(name, params={"view": "FULL"})
            self._assert_managed(current, "API config")
            expected = _label_value(service_id) if service_id else ""
            if expected and not _is_owned(current, expected):
                raise APIGatewayError("API config belongs to another managed service")
            return name
        body = self._config_body(cfg, labels)
        config_id = str(cfg.get("config_id") or self._derived_config_id(body))
        name = f"{api_name}/configs/{config_id}"
        current = self._get(name, full=True)
        if current is None:
            try:
                self._wait(self._api.create(api_name, "configs", config_id, "apiConfigId", body))
                return name
            except APIGatewayConflict:
                current = self._api.get(name, params={"view": "FULL"})
        self._assert_adoptable(current, service_id, "API config")
        if _immutable_config(current) != _immutable_config(body):
            raise APIGatewayError(
                f"API config {config_id} is immutable and differs from the declaration; use a new config_id",
            )
        desired = {"labels": self._merged_labels(current, labels)}
        if "displayName" in body:
            desired["displayName"] = body["displayName"]
        self._patch(name, current, desired)
        return name

    def _ensure_gateway(
        self,
        name: str,
        gateway_id: str,
        region: str,
        config_name: str,
        cfg: dict[str, Any],
        labels: dict[str, str],
        service_id: str,
    ) -> dict[str, Any]:
        desired = self._gateway_body(config_name, cfg, labels)
        current = self._get(name)
        if current is None:
            try:
                self._wait(
                    self._api.create(
                        self._regional_parent(region),
                        "gateways",
                        gateway_id,
                        "gatewayId",
                        desired,
                    ),
                )
                return self._api.get(name)
            except APIGatewayConflict:
                current = self._api.get(name)
        self._assert_adoptable(current, service_id, "gateway")
        desired["labels"] = self._merged_labels(current, labels)
        self._patch(name, current, desired)
        return self._api.get(name)

    def _prune_configs(
        self,
        api_name: str,
        active_config: str,
        cfg: dict[str, Any],
        service_id: str,
    ) -> None:
        if not cfg.get("prune_config_revisions"):
            return
        retained = int(cfg.get("retain_config_revisions") or 5)
        owned = [
            item
            for item in self._list_configs(api_name)
            if _is_owned(item, service_id) and str(item.get("name")) != active_config
        ]
        owned.sort(key=lambda item: (str(item.get("createTime") or ""), str(item.get("name") or "")), reverse=True)
        for item in owned[max(0, retained - 1) :]:
            self._wait(self._api.delete(str(item["name"])))

    def _api_body(self, cfg: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
        body: dict[str, Any] = {"labels": {**labels, **_normalized_labels(cfg.get("labels") or {})}}
        if cfg.get("api_display_name"):
            body["displayName"] = str(cfg["api_display_name"])
        if cfg.get("managed_service"):
            body["managedService"] = str(cfg["managed_service"])
        body.update(dict(cfg.get("api_raw_fields") or {}))
        body["labels"] = {**labels, **_normalized_labels(cfg.get("labels") or {})}
        return body

    def _config_body(self, cfg: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
        body: dict[str, Any] = {"labels": {**labels, **_normalized_labels(cfg.get("labels") or {})}}
        if cfg.get("config_display_name"):
            body["displayName"] = str(cfg["config_display_name"])
        if cfg.get("gateway_service_account"):
            body["gatewayServiceAccount"] = str(cfg["gateway_service_account"])
        if cfg.get("openapi_documents"):
            body["openapiDocuments"] = [
                {"document": _provider_file(item["document"])} for item in cfg["openapi_documents"]
            ]
        if cfg.get("grpc_services"):
            body["grpcServices"] = [
                {
                    "fileDescriptorSet": _provider_file(item["file_descriptor_set"]),
                    "source": [_provider_file(file) for file in item.get("source") or []],
                }
                for item in cfg["grpc_services"]
            ]
            body["managedServiceConfigs"] = [_provider_file(item) for item in cfg.get("managed_service_configs") or []]
        body.update(dict(cfg.get("config_raw_fields") or {}))
        body["labels"] = {**labels, **_normalized_labels(cfg.get("labels") or {})}
        return body

    def _gateway_body(
        self,
        config_name: str,
        cfg: dict[str, Any],
        labels: dict[str, str],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "apiConfig": config_name,
            "labels": {**labels, **_normalized_labels(cfg.get("labels") or {})},
        }
        if cfg.get("gateway_display_name"):
            body["displayName"] = str(cfg["gateway_display_name"])
        body.update(dict(cfg.get("gateway_raw_fields") or {}))
        body["apiConfig"] = config_name
        body["labels"] = {**labels, **_normalized_labels(cfg.get("labels") or {})}
        return body

    def _derived_config_id(self, body: dict[str, Any]) -> str:
        material = _immutable_config(body)
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()[:16]
        prefix = _dns_id(self._config.config_id_prefix)[:40]
        return f"{prefix}-{digest}"[:63].rstrip("-")

    def _api_id(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        return str(cfg.get("api_id") or _resource_id(self._config.api_id_prefix, spec, "api"))

    def _gateway_id(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        return str(cfg.get("gateway_id") or _resource_id(self._config.gateway_id_prefix, spec, "gateway"))

    def _global_parent(self) -> str:
        return f"projects/{self._config.project_id}/locations/global"

    def _regional_parent(self, region: str) -> str:
        return f"projects/{self._config.project_id}/locations/{region}"

    def _api_name(self, api_id: str) -> str:
        return f"{self._global_parent()}/apis/{api_id}"

    def _gateway_name(self, region: str, gateway_id: str) -> str:
        return f"{self._regional_parent(region)}/gateways/{gateway_id}"

    def _get(self, name: str, *, full: bool = False) -> dict[str, Any] | None:
        try:
            return self._api.get(name, params={"view": "FULL"} if full else None)
        except APIGatewayNotFound:
            return None

    def _list_configs(self, api_name: str) -> list[dict[str, Any]]:
        return self._api.list_resources(api_name, "configs", "apiConfigs")

    def _gateways_for_api(self, api_name: str) -> list[dict[str, Any]]:
        configs = {str(item.get("name")) for item in self._list_configs(api_name)}
        gateways = self._api.list_resources(
            f"projects/{self._config.project_id}/locations/-",
            "gateways",
            "gateways",
        )
        return [item for item in gateways if str(item.get("apiConfig") or "") in configs]

    def _patch(
        self,
        name: str,
        current: dict[str, Any],
        desired: dict[str, Any],
        *,
        immutable: set[str] | None = None,
    ) -> None:
        immutable = immutable or set()
        for field in immutable:
            if desired.get(field) and current.get(field) and desired[field] != current[field]:
                raise APIGatewayError(f"{field} is immutable; create a replacement API")
        changed = {
            key: value
            for key, value in desired.items()
            if key not in _OUTPUT_ONLY | immutable and current.get(key) != value
        }
        if changed:
            self._wait(self._api.patch(name, changed, list(changed)))

    def _wait(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation.get("name"):
            return operation
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        current = operation
        while not current.get("done"):
            if self._monotonic() >= deadline:
                raise APIGatewayError(f"operation {operation['name']} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._api.get_operation(str(operation["name"]))
        if current.get("error"):
            error = current["error"]
            raise APIGatewayError(str(error.get("message") or error))
        return dict(current.get("response") or current)

    def _assert_managed(self, resource: dict[str, Any], label: str) -> None:
        if (resource.get("labels") or {}).get("astrolift-io-managed-by") != "platform":
            raise APIGatewayError(f"{label} is not Astrolift-owned")

    def _assert_adoptable(
        self,
        resource: dict[str, Any],
        service_id: str,
        label: str,
    ) -> None:
        # Neither a resource Astrolift never provisioned nor another managed
        # service's may be taken over from here. Adoption of an existing
        # resource is a separate, operator-authorized operation (#1365) that no
        # tenant config flag may grant (#2021).
        labels = dict(resource.get("labels") or {})
        if labels.get("astrolift-io-managed-by") != "platform":
            raise APIGatewayError(
                f"existing {label} is not Astrolift-owned; adoption is a separate, operator-authorized "
                "operation and cannot be granted by tenant config",
            )
        owner = str(labels.get("astrolift-io-managed-service-id") or "")
        expected = _label_value(service_id) if service_id else ""
        if owner and expected and owner != expected:
            raise APIGatewayError(f"existing {label} belongs to another managed service")

    @staticmethod
    def _merged_labels(resource: dict[str, Any], desired: dict[str, str]) -> dict[str, str]:
        return {**dict(resource.get("labels") or {}), **desired}

    def _labels(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, str]:
        labels = {
            "astrolift-io-managed-by": "platform",
            "astrolift-io-organization": _label_value(spec.organization_slug),
            "astrolift-io-app": _label_value(spec.app_slug),
            "astrolift-io-environment": _label_value(spec.environment_name),
        }
        if spec.binding_id:
            labels["astrolift-io-binding"] = _label_value(spec.binding_id)
        if spec.managed_service_id:
            labels["astrolift-io-managed-service-id"] = _label_value(spec.managed_service_id)
            labels[MANAGED_SERVICE_ID_LABEL] = _label_value(spec.managed_service_id)
        labels.update(_normalized_labels(cfg.get("labels") or {}))
        return labels


def _handle(region: str, gateway_id: str) -> str:
    return f"{KIND}/{region}/{gateway_id}"


def _parse_handle(handle: str) -> tuple[str, str]:
    parts = handle.split("/")
    if len(parts) != 3 or parts[0] != KIND or not parts[1] or not _ID_RE.fullmatch(parts[2]):
        raise ValueError("invalid API Gateway handle; expected api_gateway/<region>/<gateway-id>")
    return parts[1], parts[2]


def _resource_id(prefix: str, spec: ProvisionSpec, suffix: str) -> str:
    return _dns_id(
        "-".join(
            (
                prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint,
                suffix,
            ),
        ),
    )


def _dns_id(value: str) -> str:
    result = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    result = re.sub(r"-+", "-", result)
    if not result or not result[0].isalpha():
        result = f"api-{result}"
    result = result[:63].rstrip("-")
    if len(result) < 4:
        result = f"{result}-api"[:63]
    return result


def _label_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    return (normalized or "label")[:63]


def _label_value(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")[:63]


def _normalized_labels(labels: dict[str, Any]) -> dict[str, str]:
    return {_label_key(str(key)): _label_value(str(value)) for key, value in labels.items()}


def _provider_file(file: dict[str, Any]) -> dict[str, str]:
    contents = file.get("contents_base64")
    if not contents:
        contents = base64.b64encode(str(file["contents"]).encode()).decode()
    return {"path": str(file["path"]), "contents": str(contents)}


def _immutable_config(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in resource.items()
        if key not in _OUTPUT_ONLY | {"displayName", "labels"} and value is not None
    }


def _is_owned(resource: dict[str, Any], service_id: str) -> bool:
    labels = dict(resource.get("labels") or {})
    return labels.get("astrolift-io-managed-by") == "platform" and (
        not service_id or labels.get("astrolift-io-managed-service-id") == service_id
    )
