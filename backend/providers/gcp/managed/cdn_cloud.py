"""Google Cloud CDN backend, cache policy, and global edge endpoint lifecycle."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import ipaddress
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

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
from gcp._raw_fields import raw_field_conflicts

KIND = "cdn"
VARIANT = "cloud_cdn"
_API_ROOT = "https://compute.googleapis.com/compute/v1"
_ID_PATTERN = re.compile(r"[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?")
_CACHE_MODES = {"CACHE_ALL_STATIC", "FORCE_CACHE_ALL", "USE_ORIGIN_HEADERS"}
_COLLECTION_PATHS = {
    "backendBuckets": "global/backendBuckets",
    "backendServices": "global/backendServices",
    "urlMaps": "global/urlMaps",
    "targetHttpsProxies": "global/targetHttpsProxies",
    "targetHttpProxies": "global/targetHttpProxies",
    "addresses": "global/addresses",
    "forwardingRules": "global/forwardingRules",
    "sslCertificates": "global/sslCertificates",
}
_OUTPUT_FIELDS = {
    "id",
    "kind",
    "selfLink",
    "creationTimestamp",
    "fingerprint",
    "labelFingerprint",
    "status",
    "usedBy",
    "region",
}
_BACKEND_BUCKET_OWNED_FIELDS = {
    "name",
    "description",
    "bucketName",
    "enableCdn",
    "cdnPolicy",
    "compressionMode",
    "customResponseHeaders",
    "edgeSecurityPolicy",
    "signedUrlKeyNames",
}
_BACKEND_SERVICE_OWNED_FIELDS = {
    "name",
    "description",
    "enableCDN",
    "cdnPolicy",
    "compressionMode",
    "customResponseHeaders",
    "securityPolicy",
    "edgeSecurityPolicy",
    "backends",
    "healthChecks",
    "loadBalancingScheme",
    "protocol",
    "portName",
    "timeoutSec",
    "signedUrlKeyNames",
}
_URL_MAP_OWNED_FIELDS = {
    "name",
    "description",
    "defaultService",
    "defaultUrlRedirect",
    "defaultCustomErrorResponsePolicy",
}
_PROXY_OWNED_FIELDS = {
    "name",
    "description",
    "urlMap",
    "sslCertificates",
    "certificateMap",
    "sslPolicy",
    "quicOverride",
}
_ADDRESS_OWNED_FIELDS = {
    "name",
    "description",
    "addressType",
    "ipVersion",
    "networkTier",
    "purpose",
}
_FORWARDING_OWNED_FIELDS = {
    "name",
    "description",
    "IPAddress",
    "IPProtocol",
    "portRange",
    "ports",
    "allPorts",
    "target",
    "backendService",
    "loadBalancingScheme",
    "networkTier",
    "ipVersion",
}
_CERTIFICATE_OWNED_FIELDS = {
    "name",
    "description",
    "type",
    "managed",
    "certificate",
    "privateKey",
    "selfManaged",
}


class CloudCdnError(RuntimeError):
    pass


class CloudCdnNotFound(CloudCdnError):
    pass


class CloudCdnAlreadyExists(CloudCdnError):
    pass


@dataclass(frozen=True)
class CloudCdnConfig:
    project_id: str
    name_prefix: str = "astrolift"
    deletion_protection_default: bool = True
    cache_mode_default: str = "CACHE_ALL_STATIC"
    default_ttl_seconds: int = 3600
    max_ttl_seconds: int = 86400
    client_ttl_seconds: int = 3600
    serve_while_stale_seconds: int = 86400
    invalidation_role: str = "roles/compute.loadBalancerAdmin"
    api_endpoint: str = _API_ROOT
    operation_timeout_seconds: float = 900.0
    poll_interval_seconds: float = 2.0


class ComputeCdnRestClient:
    """Authenticated request-shaped adapter for global Compute v1 resources."""

    def __init__(
        self,
        *,
        project_id: str,
        endpoint: str = _API_ROOT,
        session: Any | None = None,
    ) -> None:
        self._project_id = project_id
        self._endpoint = endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=("https://www.googleapis.com/auth/cloud-platform",),
            )
            session = AuthorizedSession(credentials)
        self._session = session

    def get_resource(self, collection: str, name: str) -> dict[str, Any]:
        return self._request("GET", f"{self._path(collection)}/{quote(name, safe='')}")

    def list_resources(self, collection: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            params = {"maxResults": "500"}
            if token:
                params["pageToken"] = token
            payload = self._request("GET", self._path(collection), params=params)
            rows.extend(dict(row) for row in payload.get("items") or [])
            token = str(payload.get("nextPageToken") or "")
            if not token:
                return rows

    def insert_resource(self, collection: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", self._path(collection), json=body)

    def patch_resource(
        self,
        collection: str,
        name: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"{self._path(collection)}/{quote(name, safe='')}",
            json=body,
        )

    def delete_resource(self, collection: str, name: str) -> dict[str, Any]:
        return self._request("DELETE", f"{self._path(collection)}/{quote(name, safe='')}")

    def invalidate_cache(self, url_map: str, *, path: str, host: str = "") -> dict[str, Any]:
        body = {"path": path}
        if host:
            body["host"] = host
        return self._request(
            "POST",
            f"{self._path('urlMaps')}/{quote(url_map, safe='')}/invalidateCache",
            json=body,
        )

    def add_signed_url_key(
        self,
        collection: str,
        name: str,
        *,
        key_name: str,
        key_value: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self._path(collection)}/{quote(name, safe='')}/addSignedUrlKey",
            json={"keyName": key_name, "keyValue": key_value},
        )

    def delete_signed_url_key(
        self,
        collection: str,
        name: str,
        *,
        key_name: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self._path(collection)}/{quote(name, safe='')}/deleteSignedUrlKey",
            params={"keyName": key_name},
        )

    def set_backend_policy(
        self,
        collection: str,
        name: str,
        *,
        action: str,
        policy: str,
    ) -> dict[str, Any]:
        field = "securityPolicy"
        return self._request(
            "POST",
            f"{self._path(collection)}/{quote(name, safe='')}/{action}",
            json={field: policy},
        )

    def get_operation(self, name: str) -> dict[str, Any]:
        operation_id = name.rsplit("/", 1)[-1]
        return self._request("GET", f"global/operations/{quote(operation_id, safe='')}")

    def _path(self, collection: str) -> str:
        try:
            suffix = _COLLECTION_PATHS[collection]
        except KeyError as exc:
            raise ValueError(f"unsupported Compute collection {collection!r}") from exc
        return suffix

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
            f"{self._endpoint}/projects/{quote(self._project_id, safe='')}/{resource.lstrip('/')}",
            params=params,
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise CloudCdnNotFound(resource)
        if response.status_code == 409:
            raise CloudCdnAlreadyExists(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with contextlib.suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise CloudCdnError(f"Compute HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class CloudCdnDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: CloudCdnConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._compute = client or ComputeCdnRestClient(
            project_id=config.project_id,
            endpoint=config.api_endpoint,
        )
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="cloud_cdn",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_cloud_cdn_config"])
        stack_id = str(cfg.get("cdn_id") or self._stack_id(spec))
        handle = _handle(stack_id)
        marker = _ownership_marker(stack_id, spec=spec)
        try:
            resources = self._ensure_stack(stack_id, cfg, marker=marker, allow_create=True)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Cloud CDN: {exc}", [str(exc)])
        ready, detail = self._readiness(stack_id, cfg, resources)
        return ProvisionResult(
            True,
            handle,
            f"Cloud CDN {stack_id}: {detail}",
            ready=ready,
        )

    @driver_op(cloud="gcp", driver="cloud_cdn")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            stack_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_cloud_cdn_config"])
        try:
            marker = self._existing_marker(stack_id, cfg)
            self._ensure_stack(stack_id, cfg, marker=marker, allow_create=True)
        except CloudCdnNotFound:
            return UpdateResult(False, spec.handle, "Cloud CDN stack not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Cloud CDN: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Cloud CDN {stack_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="cloud_cdn",
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
            stack_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        protected = bool(cfg.get("deletion_protection", self._config.deletion_protection_default))
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Cloud CDN has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            plan = self._deletion_plan(stack_id, cfg)
            self._preflight_delete(stack_id, cfg, plan, force_destroy=force_destroy)
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"preflight Cloud CDN delete: {exc}",
                [str(exc)],
                retryable=False,
            )
        if not any(plan.values()):
            return DeprovisionResult(True, spec.handle, f"Cloud CDN {stack_id} already gone")
        try:
            for collection in (
                "forwardingRules",
                "targetHttpsProxies",
                "targetHttpProxies",
                "urlMaps",
                "sslCertificates",
                "addresses",
                "backendBuckets",
                "backendServices",
            ):
                for resource in plan.get(collection, []):
                    self._wait_operation(
                        self._compute.delete_resource(collection, str(resource["name"])),
                    )
        except CloudCdnNotFound:
            pass
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Cloud CDN: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"Cloud CDN {stack_id} deleted")

    @driver_op(cloud="gcp", driver="cloud_cdn")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            stack_id = _parse_handle(handle.handle)
            cfg: dict[str, Any] = {}
            resources = self._discover_stack(stack_id, cfg)
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Cloud CDN: {exc}")
        if not resources.get("backend") and not resources.get("forwarding"):
            return ServiceStatus(handle.handle, "deprovisioned", "Cloud CDN stack does not exist")
        ready, detail = self._readiness(stack_id, cfg, resources)
        return ServiceStatus(handle.handle, "available" if ready else "provisioning", detail)

    @driver_op(cloud="gcp", driver="cloud_cdn")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        stack_id = _parse_handle(handle.handle)
        cfg = dict(config or {})
        resources = self._discover_stack(stack_id, cfg)
        backend = resources.get("backend")
        forwarding = resources.get("forwarding")
        address = resources.get("address")
        if not backend or not forwarding or not address:
            raise CloudCdnError("Cloud CDN endpoint is incomplete")
        self._assert_owned_or_external_backend(backend, stack_id, cfg)
        ip_address = str(address.get("address") or forwarding.get("IPAddress") or "")
        domains = _domains(cfg)
        hostname = str(cfg.get("hostname") or (domains[0] if domains else ip_address))
        insecure = bool(cfg.get("allow_insecure_http"))
        scheme = "http" if insecure else "https"
        url = f"{scheme}://{hostname}"
        backend_kind = (
            "backend_service" if resources.get("backend_collection") == "backendServices" else "backend_bucket"
        )
        backend_name = str(backend.get("name") or "")
        console_url = (
            f"https://console.cloud.google.com/net-services/cdn/list?project={quote(self._config.project_id, safe='')}"
        )
        return Binding(
            env_vars={
                "CDN_URL": ValueRef(literal=url),
                "CDN_DISTRIBUTION_ID": ValueRef(literal=stack_id),
                "CDN_DOMAIN": ValueRef(literal=hostname),
                "CDN_DOMAIN_NAME": ValueRef(literal=hostname),
                "CDN_IP_ADDRESS": ValueRef(literal=ip_address),
                "CDN_INVALIDATION_ROLE": ValueRef(literal=self._config.invalidation_role),
                "GCP_CLOUD_CDN_PROJECT": ValueRef(literal=self._config.project_id),
                "GCP_CLOUD_CDN_BACKEND_KIND": ValueRef(literal=backend_kind),
                "GCP_CLOUD_CDN_BACKEND": ValueRef(literal=backend_name),
                "GCP_CLOUD_CDN_URL_MAP": ValueRef(literal=_name(stack_id, "map")),
                "GCP_CLOUD_CDN_CONSOLE_URL": ValueRef(literal=console_url),
            },
            iam_grants=[
                Grant(
                    resource=_self_link(self._config.project_id, "urlMaps", _name(stack_id, "map")),
                    actions=[self._config.invalidation_role],
                ),
            ],
            notes=(
                "Cloud CDN invalidation uses a project IAM role; configure a custom role containing "
                "compute.urlMaps.invalidateCache for least privilege."
            ),
        )

    @driver_op(cloud="gcp", driver="cloud_cdn", audit=True)
    def invalidate(
        self,
        distribution_id: str,
        paths: list[str] | None = None,
        *,
        host: str = "",
    ) -> dict[str, str]:
        stack_id = _parse_distribution_id(distribution_id)
        url_map = self._compute.get_resource("urlMaps", _name(stack_id, "map"))
        self._assert_owned(url_map, stack_id)
        invalidation_ids: list[str] = []
        for path in paths or ["/*"]:
            if not path.startswith("/"):
                raise CloudCdnError("cache invalidation paths must start with '/'")
            operation = self._wait_operation(
                self._compute.invalidate_cache(_name(stack_id, "map"), path=path, host=host),
            )
            invalidation_ids.append(str(operation.get("name") or operation.get("id") or ""))
        return {"invalidation_id": ",".join(filter(None, invalidation_ids))}

    @driver_op(cloud="gcp", driver="cloud_cdn", audit=True, sensitive_kind="secret.rotate")
    def rotate_signed_url_key(
        self,
        handle: ServiceHandle,
        *,
        key_name: str,
        key_value_b64: str,
        config: dict[str, Any] | None = None,
        previous_key_name: str = "",
    ) -> None:
        stack_id = _parse_handle(handle.handle)
        _validate_signed_key(key_name, key_value_b64)
        cfg = dict(config or {})
        url_map = self._compute.get_resource("urlMaps", _name(stack_id, "map"))
        self._assert_owned(url_map, stack_id)
        if cfg.get("origin_backend_service") and not cfg.get("allow_external_key_rotation"):
            raise CloudCdnError(
                "signed URL key changes on an external backend require allow_external_key_rotation=true",
            )
        collection, backend_name = self._backend_target(stack_id, cfg)
        backend = self._compute.get_resource(collection, backend_name)
        self._assert_owned_or_external_backend(backend, stack_id, cfg)
        self._wait_operation(
            self._compute.add_signed_url_key(
                collection,
                backend_name,
                key_name=key_name,
                key_value=key_value_b64,
            ),
        )
        if previous_key_name and previous_key_name != key_name:
            self._wait_operation(
                self._compute.delete_signed_url_key(
                    collection,
                    backend_name,
                    key_name=previous_key_name,
                ),
            )

    @driver_op(cloud="gcp", driver="cloud_cdn")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise CloudCdnError(
            "Cloud CDN has no durable snapshot semantic; snapshot the origin and keep declarative config",
        )

    @driver_op(cloud="gcp", driver="cloud_cdn")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "Cloud CDN has no provider snapshot to restore; restore the origin then provision the CDN config",
            ["not_implemented"],
        )

    @driver_op(cloud="gcp", driver="cloud_cdn", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "cdn_id": {"type": "string", "pattern": "^[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?$"},
                "size": {
                    "type": "string",
                    "enum": ["small", "medium", "large", "xlarge", "custom"],
                    "description": "Portable service size hint; Cloud CDN is usage-scaled.",
                },
                "origin_bucket": {
                    "type": "string",
                    "description": (
                        "Cloud Storage origin. Astrolift does not make it public implicitly; "
                        "grant public read explicitly or rotate a Cloud CDN signed-URL key."
                    ),
                },
                "origin_backend_service": {
                    "type": "string",
                    "description": "Existing global backend service name or self-link; must already have CDN enabled.",
                },
                "backends": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Native global BackendService backend entries (instance groups or NEGs).",
                    "items": {"type": "object"},
                },
                "backend_bucket_id": {"type": "string"},
                "backend_service_id": {"type": "string"},
                "backend_bucket": {"type": "object"},
                "backend_service": {"type": "object"},
                "health_checks": {"type": "array", "items": {"type": "string"}},
                "protocol": {"type": "string", "default": "HTTPS"},
                "port_name": {"type": "string", "default": "https"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
                "cache_policy": {
                    "type": "object",
                    "description": "Native BackendBucketCdnPolicy or BackendServiceCdnPolicy fields.",
                },
                "compression_mode": {
                    "type": "string",
                    "enum": ["AUTOMATIC", "DISABLED"],
                    "default": "AUTOMATIC",
                },
                "custom_response_headers": {"type": "array", "items": {"type": "string"}},
                "security_policy": {"type": "string"},
                "edge_security_policy": {"type": "string"},
                "allow_force_cache_all": {"type": "boolean", "default": False},
                "url_map": {"type": "object"},
                "spa": {"type": "boolean", "default": False},
                "index": {"type": "string", "default": "index.html"},
                "domains": {"type": "array", "items": {"type": "string"}},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "hostname": {"type": "string"},
                "ssl_certificates": {"type": "array", "items": {"type": "string"}},
                "certificate_map": {"type": "string"},
                "ssl_policy": {"type": "string"},
                "quic_override": {
                    "type": "string",
                    "enum": ["ENABLE", "DISABLE", "NONE"],
                    "default": "NONE",
                },
                "managed_certificate": {"type": "object"},
                "target_https_proxy": {"type": "object"},
                "target_http_proxy": {"type": "object"},
                "address": {"type": "object"},
                "forwarding_rule": {"type": "object"},
                "redirect_http_to_https": {"type": "boolean", "default": True},
                "allow_insecure_http": {"type": "boolean", "default": False},
                "delete_adopted_resources": {"type": "boolean", "default": False},
                "allow_external_key_rotation": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
            },
            "oneOf": [
                {"required": ["origin_bucket"]},
                {"required": ["origin_backend_service"]},
                {"required": ["backends"]},
            ],
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="cloud_cdn", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "CDN_URL": "Cloud CDN public URL",
                "CDN_DISTRIBUTION_ID": "Astrolift Cloud CDN stack ID",
                "CDN_DOMAIN": "Configured hostname or load-balancer IP",
                "CDN_DOMAIN_NAME": "Compatibility alias for CDN_DOMAIN",
                "CDN_IP_ADDRESS": "Reserved global load-balancer IP",
                "CDN_INVALIDATION_ROLE": "GCP role used for cache invalidation",
                "GCP_CLOUD_CDN_PROJECT": "Google Cloud project ID",
                "GCP_CLOUD_CDN_BACKEND_KIND": "backend_bucket or backend_service",
                "GCP_CLOUD_CDN_BACKEND": "Compute backend resource name",
                "GCP_CLOUD_CDN_URL_MAP": "Compute URL map name",
                "GCP_CLOUD_CDN_CONSOLE_URL": "Google Cloud CDN console URL",
            },
        )

    def editable_fields(self) -> list[str]:
        return ["*"]

    def _ensure_stack(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
        allow_create: bool,
    ) -> dict[str, Any]:
        backend_collection, backend = self._ensure_backend(
            stack_id,
            cfg,
            marker=marker,
            allow_create=allow_create,
        )
        backend_link = str(
            backend.get("selfLink") or _self_link(self._config.project_id, backend_collection, str(backend["name"])),
        )
        url_map = self._ensure_resource(
            "urlMaps",
            _name(stack_id, "map"),
            self._url_map_body(stack_id, cfg, marker=marker, backend_link=backend_link),
            marker=marker,
            allow_create=allow_create,
        )
        address = self._ensure_resource(
            "addresses",
            _name(stack_id, "ip"),
            self._address_body(stack_id, cfg, marker=marker),
            marker=marker,
            allow_create=allow_create,
        )
        address_link = str(
            address.get("selfLink") or _self_link(self._config.project_id, "addresses", str(address["name"])),
        )
        url_map_link = str(
            url_map.get("selfLink") or _self_link(self._config.project_id, "urlMaps", str(url_map["name"])),
        )
        domains = _domains(cfg)
        insecure = bool(cfg.get("allow_insecure_http"))
        self._prune_conflicting_http_endpoint(stack_id, cfg, insecure=insecure)
        certificate: dict[str, Any] | None = None
        if insecure:
            proxy = self._ensure_resource(
                "targetHttpProxies",
                _name(stack_id, "http-proxy"),
                self._http_proxy_body(stack_id, cfg, marker=marker, url_map=url_map_link),
                marker=marker,
                allow_create=allow_create,
            )
            forwarding = self._ensure_resource(
                "forwardingRules",
                _name(stack_id, "http-fr"),
                self._forwarding_body(
                    stack_id,
                    cfg,
                    marker=marker,
                    address=address_link,
                    target=str(
                        proxy.get("selfLink")
                        or _self_link(self._config.project_id, "targetHttpProxies", str(proxy["name"])),
                    ),
                    port="80",
                ),
                marker=marker,
                allow_create=allow_create,
            )
        else:
            certificates = [
                (
                    str(item)
                    if str(item).startswith(("https://", "http://"))
                    else _self_link(
                        self._config.project_id,
                        "sslCertificates",
                        _resource_name(str(item)),
                    )
                )
                for item in cfg.get("ssl_certificates") or []
            ]
            certificate_map = str(cfg.get("certificate_map") or "")
            if not certificates and not certificate_map:
                certificate = self._ensure_managed_certificate(
                    stack_id,
                    cfg,
                    marker=marker,
                    domains=domains,
                    allow_create=allow_create,
                )
                certificates = [
                    str(
                        certificate.get("selfLink")
                        or _self_link(
                            self._config.project_id,
                            "sslCertificates",
                            str(certificate["name"]),
                        ),
                    ),
                ]
            proxy = self._ensure_resource(
                "targetHttpsProxies",
                _name(stack_id, "https-proxy"),
                self._https_proxy_body(
                    stack_id,
                    cfg,
                    marker=marker,
                    url_map=url_map_link,
                    certificates=certificates,
                    certificate_map=certificate_map,
                ),
                marker=marker,
                allow_create=allow_create,
            )
            forwarding = self._ensure_resource(
                "forwardingRules",
                _name(stack_id, "https-fr"),
                self._forwarding_body(
                    stack_id,
                    cfg,
                    marker=marker,
                    address=address_link,
                    target=str(
                        proxy.get("selfLink")
                        or _self_link(self._config.project_id, "targetHttpsProxies", str(proxy["name"])),
                    ),
                    port="443",
                ),
                marker=marker,
                allow_create=allow_create,
            )
            if bool(cfg.get("redirect_http_to_https", True)):
                self._ensure_https_redirect(stack_id, cfg, marker=marker, address=address_link)
        self._prune_obsolete_endpoint_resources(
            stack_id,
            cfg,
            active_certificate=str((certificate or {}).get("name") or ""),
        )
        return {
            "backend_collection": backend_collection,
            "backend": backend,
            "url_map": url_map,
            "address": address,
            "proxy": proxy,
            "forwarding": forwarding,
            "certificate": certificate,
        }

    def _ensure_backend(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
        allow_create: bool,
    ) -> tuple[str, dict[str, Any]]:
        if cfg.get("origin_backend_service"):
            backend_name = _resource_name(str(cfg["origin_backend_service"]))
            backend = self._compute.get_resource("backendServices", backend_name)
            if not bool(backend.get("enableCDN")):
                raise CloudCdnError(
                    "external origin_backend_service must already have enableCDN=true; "
                    "declare backends to let Astrolift manage it",
                )
            return "backendServices", backend
        if cfg.get("origin_bucket"):
            collection = "backendBuckets"
            backend_name = str(cfg.get("backend_bucket_id") or _name(stack_id, "bucket"))
            body = self._backend_bucket_body(stack_id, cfg, marker=marker)
        else:
            collection = "backendServices"
            backend_name = str(cfg.get("backend_service_id") or _name(stack_id, "service"))
            body = self._backend_service_body(stack_id, cfg, marker=marker)
        backend = self._ensure_resource(
            collection,
            backend_name,
            body,
            marker=marker,
            allow_create=allow_create,
        )
        self._reconcile_security_policies(collection, backend_name, cfg)
        return collection, backend

    def _ensure_resource(
        self,
        collection: str,
        name: str,
        desired: dict[str, Any],
        *,
        marker: str,
        allow_create: bool,
    ) -> dict[str, Any]:
        try:
            current = self._compute.get_resource(collection, name)
        except CloudCdnNotFound:
            if not allow_create:
                raise
            self._wait_operation(self._compute.insert_resource(collection, desired))
            return self._compute.get_resource(collection, name)
        if not _owned(current, marker):
            # The marker names this stack, boundary and managed service, so a
            # mismatch is either a resource Astrolift never made or another
            # service's. Adoption of an existing resource is a separate,
            # operator-authorized operation (#1365) that no tenant config flag
            # may grant (#2021).
            raise CloudCdnError(
                f"{collection}/{name} exists but is not owned by this Cloud CDN stack; adoption is a "
                "separate, operator-authorized operation and cannot be granted by tenant config",
            )
        patch = {
            key: value for key, value in desired.items() if key != "name" and not _contains(current.get(key), value)
        }
        if patch and collection in {"addresses", "sslCertificates"}:
            raise CloudCdnError(f"immutable {collection}/{name} cannot be updated in place")
        if patch:
            self._wait_operation(self._compute.patch_resource(collection, name, patch))
            current = self._compute.get_resource(collection, name)
        return current

    def _ensure_managed_certificate(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
        domains: list[str],
        allow_create: bool,
    ) -> dict[str, Any]:
        digest = hashlib.sha256("\n".join(domains).encode()).hexdigest()[:10]
        cert_name = _name(stack_id, f"cert-{digest}")
        raw = _safe_raw(cfg, "managed_certificate", _CERTIFICATE_OWNED_FIELDS)
        body = {
            **raw,
            "name": cert_name,
            "description": marker,
            "type": "MANAGED",
            "managed": {"domains": domains},
        }
        certificate = self._ensure_resource(
            "sslCertificates",
            cert_name,
            body,
            marker=marker,
            allow_create=allow_create,
        )
        return certificate

    def _ensure_https_redirect(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
        address: str,
    ) -> None:
        redirect_map = self._ensure_resource(
            "urlMaps",
            _name(stack_id, "redirect-map"),
            {
                "name": _name(stack_id, "redirect-map"),
                "description": marker,
                "defaultUrlRedirect": {
                    "httpsRedirect": True,
                    "redirectResponseCode": "MOVED_PERMANENTLY_DEFAULT",
                    "stripQuery": False,
                },
            },
            marker=marker,
            allow_create=True,
        )
        redirect_map_link = str(
            redirect_map.get("selfLink") or _self_link(self._config.project_id, "urlMaps", str(redirect_map["name"])),
        )
        proxy = self._ensure_resource(
            "targetHttpProxies",
            _name(stack_id, "redirect-proxy"),
            {
                **_safe_raw(cfg, "target_http_proxy", _PROXY_OWNED_FIELDS),
                "name": _name(stack_id, "redirect-proxy"),
                "description": marker,
                "urlMap": redirect_map_link,
            },
            marker=marker,
            allow_create=True,
        )
        proxy_link = str(
            proxy.get("selfLink") or _self_link(self._config.project_id, "targetHttpProxies", str(proxy["name"])),
        )
        self._ensure_resource(
            "forwardingRules",
            _name(stack_id, "redirect-fr"),
            self._forwarding_body(
                stack_id,
                cfg,
                marker=marker,
                address=address,
                target=proxy_link,
                port="80",
                suffix="redirect-fr",
            ),
            marker=marker,
            allow_create=True,
        )

    def _backend_bucket_body(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
    ) -> dict[str, Any]:
        return {
            **_safe_raw(cfg, "backend_bucket", _BACKEND_BUCKET_OWNED_FIELDS),
            "name": str(cfg.get("backend_bucket_id") or _name(stack_id, "bucket")),
            "description": marker,
            "bucketName": str(cfg["origin_bucket"]),
            "enableCdn": True,
            "cdnPolicy": self._cache_policy(cfg),
            "compressionMode": str(cfg.get("compression_mode") or "AUTOMATIC"),
            "customResponseHeaders": list(cfg.get("custom_response_headers") or []),
        }

    def _backend_service_body(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
    ) -> dict[str, Any]:
        return {
            **_safe_raw(cfg, "backend_service", _BACKEND_SERVICE_OWNED_FIELDS),
            "name": str(cfg.get("backend_service_id") or _name(stack_id, "service")),
            "description": marker,
            "enableCDN": True,
            "cdnPolicy": self._cache_policy(cfg),
            "loadBalancingScheme": "EXTERNAL_MANAGED",
            "protocol": str(cfg.get("protocol") or "HTTPS"),
            "portName": str(cfg.get("port_name") or "https"),
            "timeoutSec": int(cfg.get("timeout_seconds") or 30),
            "backends": [dict(item) for item in cfg.get("backends") or []],
            "healthChecks": [str(item) for item in cfg.get("health_checks") or []],
            "customResponseHeaders": list(cfg.get("custom_response_headers") or []),
        }

    def _url_map_body(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
        backend_link: str,
    ) -> dict[str, Any]:
        body = {
            **_safe_raw(cfg, "url_map", _URL_MAP_OWNED_FIELDS),
            "name": _name(stack_id, "map"),
            "description": marker,
            "defaultService": backend_link,
        }
        if cfg.get("spa"):
            index = str(cfg.get("index") or "index.html").lstrip("/")
            body["defaultCustomErrorResponsePolicy"] = {
                "errorService": backend_link,
                "errorResponseRules": [
                    {
                        "matchResponseCodes": ["404"],
                        "path": f"/{index}",
                        "overrideResponseCode": 200,
                    },
                ],
            }
        else:
            body["defaultCustomErrorResponsePolicy"] = None
        return body

    def _address_body(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
    ) -> dict[str, Any]:
        return {
            **_safe_raw(cfg, "address", _ADDRESS_OWNED_FIELDS),
            "name": _name(stack_id, "ip"),
            "description": marker,
            "addressType": "EXTERNAL",
            "ipVersion": "IPV4",
            "networkTier": "PREMIUM",
        }

    def _https_proxy_body(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
        url_map: str,
        certificates: list[str],
        certificate_map: str,
    ) -> dict[str, Any]:
        body = {
            **_safe_raw(cfg, "target_https_proxy", _PROXY_OWNED_FIELDS),
            "name": _name(stack_id, "https-proxy"),
            "description": marker,
            "urlMap": url_map,
            "quicOverride": str(cfg.get("quic_override") or "NONE"),
        }
        if certificate_map:
            body["certificateMap"] = certificate_map
        else:
            body["sslCertificates"] = certificates
        if cfg.get("ssl_policy"):
            body["sslPolicy"] = str(cfg["ssl_policy"])
        return body

    def _http_proxy_body(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
        url_map: str,
    ) -> dict[str, Any]:
        return {
            **_safe_raw(cfg, "target_http_proxy", _PROXY_OWNED_FIELDS),
            "name": _name(stack_id, "http-proxy"),
            "description": marker,
            "urlMap": url_map,
        }

    def _forwarding_body(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        marker: str,
        address: str,
        target: str,
        port: str,
        suffix: str | None = None,
    ) -> dict[str, Any]:
        return {
            **_safe_raw(cfg, "forwarding_rule", _FORWARDING_OWNED_FIELDS),
            "name": _name(stack_id, suffix or ("https-fr" if port == "443" else "http-fr")),
            "description": marker,
            "IPAddress": address,
            "IPProtocol": "TCP",
            "portRange": port,
            "target": target,
            "loadBalancingScheme": "EXTERNAL_MANAGED",
            "networkTier": "PREMIUM",
        }

    def _cache_policy(self, cfg: dict[str, Any]) -> dict[str, Any]:
        policy = {
            "cacheMode": self._config.cache_mode_default,
            "defaultTtl": self._config.default_ttl_seconds,
            "maxTtl": self._config.max_ttl_seconds,
            "clientTtl": self._config.client_ttl_seconds,
            "negativeCaching": True,
            "requestCoalescing": True,
            "serveWhileStale": self._config.serve_while_stale_seconds,
            "bypassCacheOnRequestHeaders": [{"headerName": "Authorization"}],
        }
        policy.update(dict(cfg.get("cache_policy") or {}))
        return policy

    def _reconcile_security_policies(
        self,
        collection: str,
        backend_name: str,
        cfg: dict[str, Any],
    ) -> None:
        current = self._compute.get_resource(collection, backend_name)
        if "edge_security_policy" in cfg:
            edge = str(cfg.get("edge_security_policy") or "")
            if str(current.get("edgeSecurityPolicy") or "") == edge:
                edge = ""
        else:
            edge = ""
        if "edge_security_policy" in cfg and str(current.get("edgeSecurityPolicy") or "") != str(
            cfg.get("edge_security_policy") or "",
        ):
            self._wait_operation(
                self._compute.set_backend_policy(
                    collection,
                    backend_name,
                    action="setEdgeSecurityPolicy",
                    policy=edge,
                ),
            )
        security = str(cfg.get("security_policy") or "")
        if "security_policy" in cfg and str(current.get("securityPolicy") or "") != security:
            if collection != "backendServices":
                raise CloudCdnError("security_policy is supported only by backend services")
            self._wait_operation(
                self._compute.set_backend_policy(
                    collection,
                    backend_name,
                    action="setSecurityPolicy",
                    policy=security,
                ),
            )

    def _prune_obsolete_endpoint_resources(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        active_certificate: str,
    ) -> None:
        insecure = bool(cfg.get("allow_insecure_http"))
        redirect = not insecure and bool(cfg.get("redirect_http_to_https", True))
        keep: dict[str, set[str]] = {
            "forwardingRules": {
                _name(stack_id, "http-fr" if insecure else "https-fr"),
                *({_name(stack_id, "redirect-fr")} if redirect else set()),
            },
            "targetHttpsProxies": (set() if insecure else {_name(stack_id, "https-proxy")}),
            "targetHttpProxies": (
                {_name(stack_id, "http-proxy")}
                if insecure
                else ({_name(stack_id, "redirect-proxy")} if redirect else set())
            ),
            "urlMaps": {
                _name(stack_id, "map"),
                *({_name(stack_id, "redirect-map")} if redirect else set()),
            },
            "sslCertificates": ({active_certificate} if active_certificate else set()),
        }
        backend_collection, backend_name = self._backend_target(stack_id, cfg)
        keep["backendBuckets"] = {backend_name} if backend_collection == "backendBuckets" else set()
        keep["backendServices"] = (
            {backend_name}
            if backend_collection == "backendServices" and not cfg.get("origin_backend_service")
            else set()
        )
        for collection in (
            "forwardingRules",
            "targetHttpsProxies",
            "targetHttpProxies",
            "urlMaps",
            "sslCertificates",
            "backendBuckets",
            "backendServices",
        ):
            for resource in self._compute.list_resources(collection):
                name = str(resource.get("name") or "")
                if not _owned_stack(resource, stack_id) or name in keep.get(collection, set()):
                    continue
                if _adopted(resource) and not cfg.get("delete_adopted_resources"):
                    raise CloudCdnError(
                        f"obsolete adopted {collection}/{name} requires delete_adopted_resources=true",
                    )
                self._wait_operation(self._compute.delete_resource(collection, name))

    def _prune_conflicting_http_endpoint(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        *,
        insecure: bool,
    ) -> None:
        """Remove the prior port-80 graph before changing its semantics.

        A forwarding rule is unique by IP/port.  Creating the insecure HTTP
        rule while the HTTPS redirect rule still exists (or vice versa) is a
        provider conflict, so this narrow prune must precede creation.  The
        HTTPS endpoint on port 443 remains live throughout the transition.
        """

        obsolete = (
            (
                ("forwardingRules", _name(stack_id, "redirect-fr")),
                ("targetHttpProxies", _name(stack_id, "redirect-proxy")),
                ("urlMaps", _name(stack_id, "redirect-map")),
            )
            if insecure
            else (
                ("forwardingRules", _name(stack_id, "http-fr")),
                ("targetHttpProxies", _name(stack_id, "http-proxy")),
            )
        )
        for collection, name in obsolete:
            resource = self._get_optional(collection, name)
            if resource is None:
                continue
            if not _owned_stack(resource, stack_id):
                raise CloudCdnError(
                    f"refusing to replace unowned {collection}/{name} during HTTP mode change",
                )
            if _adopted(resource) and not cfg.get("delete_adopted_resources"):
                raise CloudCdnError(
                    f"HTTP mode change would replace adopted {collection}/{name}; "
                    "delete_adopted_resources=true is required",
                )
            self._wait_operation(self._compute.delete_resource(collection, name))

    def _readiness(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        resources: dict[str, Any],
    ) -> tuple[bool, str]:
        if not resources.get("backend") or not resources.get("forwarding"):
            return False, "backend or forwarding rule is missing"
        certificate = resources.get("certificate")
        if certificate:
            managed = certificate.get("managed") or {}
            status = str(managed.get("status") or "PROVISIONING")
            if status not in {"ACTIVE", "PROVISIONING"}:
                return False, f"managed certificate is {status}"
            if status != "ACTIVE":
                return False, "endpoint exists; managed certificate is provisioning"
        address = resources.get("address") or {}
        ip_address = str(address.get("address") or "")
        return True, f"endpoint is available at {ip_address or _name(stack_id, 'ip')}"

    def _discover_stack(self, stack_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
        url_map = self._get_optional("urlMaps", _name(stack_id, "map"))
        if url_map is not None and not _owned_stack(url_map, stack_id):
            raise CloudCdnError("Cloud CDN URL map is not owned by Astrolift")
        backend_collection, backend_name = self._backend_target(stack_id, cfg)
        backend = self._get_optional(backend_collection, backend_name)
        if backend is None and not cfg and url_map:
            default_service = str(url_map.get("defaultService") or "")
            if "/backendBuckets/" in default_service:
                backend_collection = "backendBuckets"
                backend_name = _resource_name(default_service)
            elif "/backendServices/" in default_service:
                backend_collection = "backendServices"
                backend_name = _resource_name(default_service)
            backend = self._get_optional(backend_collection, backend_name)
        insecure = bool(cfg.get("allow_insecure_http"))
        forwarding_name = _name(stack_id, "http-fr" if insecure else "https-fr")
        forwarding = self._get_optional("forwardingRules", forwarding_name)
        if forwarding is None and not cfg:
            insecure = True
            forwarding_name = _name(stack_id, "http-fr")
            forwarding = self._get_optional("forwardingRules", forwarding_name)
        certs = [item for item in self._compute.list_resources("sslCertificates") if _owned_stack(item, stack_id)]
        return {
            "backend_collection": backend_collection,
            "backend": backend,
            "url_map": url_map,
            "address": self._get_optional("addresses", _name(stack_id, "ip")),
            "proxy": self._get_optional(
                "targetHttpProxies" if insecure else "targetHttpsProxies",
                _name(stack_id, "http-proxy" if insecure else "https-proxy"),
            ),
            "forwarding": forwarding,
            "certificate": certs[0] if certs else None,
        }

    def _deletion_plan(self, stack_id: str, cfg: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        plan: dict[str, list[dict[str, Any]]] = {}
        for collection in _COLLECTION_PATHS:
            resources = [item for item in self._compute.list_resources(collection) if _owned_stack(item, stack_id)]
            if resources:
                plan[collection] = resources
        return plan

    def _preflight_delete(
        self,
        stack_id: str,
        cfg: dict[str, Any],
        plan: dict[str, list[dict[str, Any]]],
        *,
        force_destroy: bool,
    ) -> None:
        seen: set[tuple[str, str]] = set()
        for collection, resources in plan.items():
            for resource in resources:
                identity = (collection, str(resource.get("name") or ""))
                if identity in seen:
                    continue
                seen.add(identity)
                if not _owned_stack(resource, stack_id):
                    raise CloudCdnError(f"refusing to delete unowned {collection}/{identity[1]}")
                if _adopted(resource) and not cfg.get("delete_adopted_resources"):
                    raise CloudCdnError(
                        f"adopted {collection}/{identity[1]} requires delete_adopted_resources=true",
                    )
                if collection in {"backendBuckets", "backendServices"}:
                    external_users = [
                        item
                        for item in resource.get("usedBy") or []
                        if stack_id not in str(item.get("reference") or item)
                    ]
                    if external_users and not force_destroy:
                        raise CloudCdnError(
                            f"{collection}/{identity[1]} has external dependents; force_destroy is required",
                        )

    def _existing_marker(self, stack_id: str, cfg: dict[str, Any]) -> str:
        url_map = self._compute.get_resource("urlMaps", _name(stack_id, "map"))
        marker = str(url_map.get("description") or "")
        if not _owned_stack(url_map, stack_id):
            raise CloudCdnError("Cloud CDN URL map is not owned by Astrolift")
        return marker.split("; adopted=true", 1)[0]

    def _backend_target(self, stack_id: str, cfg: dict[str, Any]) -> tuple[str, str]:
        if cfg.get("origin_backend_service"):
            return "backendServices", _resource_name(str(cfg["origin_backend_service"]))
        if cfg.get("origin_bucket"):
            return "backendBuckets", str(cfg.get("backend_bucket_id") or _name(stack_id, "bucket"))
        return "backendServices", str(cfg.get("backend_service_id") or _name(stack_id, "service"))

    def _assert_owned(self, resource: dict[str, Any], stack_id: str) -> None:
        if not _owned_stack(resource, stack_id):
            raise CloudCdnError("resource is not owned by this Cloud CDN stack")

    def _assert_owned_or_external_backend(
        self,
        resource: dict[str, Any],
        stack_id: str,
        cfg: dict[str, Any],
    ) -> None:
        if cfg.get("origin_backend_service"):
            return
        self._assert_owned(resource, stack_id)

    def _get_optional(self, collection: str, name: str) -> dict[str, Any] | None:
        try:
            return self._compute.get_resource(collection, name)
        except CloudCdnNotFound:
            return None

    def _wait_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation:
            return {}
        current = operation
        name = str(operation.get("name") or "")
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        while str(current.get("status") or "") != "DONE":
            if not name:
                raise CloudCdnError("Compute operation response has no name")
            if self._monotonic() >= deadline:
                raise CloudCdnError(f"Compute operation {name} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._compute.get_operation(name)
        error = current.get("error") or {}
        if error:
            details = error.get("errors") or error
            raise CloudCdnError(f"Compute operation {name} failed: {details}")
        return current

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        unknown = sorted(set(cfg) - set(self.config_schema()["properties"]))
        if unknown:
            return f"unknown Cloud CDN config keys: {', '.join(unknown)}"
        origins = sum(bool(cfg.get(key)) for key in ("origin_bucket", "origin_backend_service", "backends"))
        if origins != 1:
            return "exactly one of origin_bucket, origin_backend_service, or backends is required"
        for key in ("origin_bucket", "origin_backend_service"):
            value = cfg.get(key)
            if value is not None and not isinstance(value, str):
                return f"{key} must be a string"
        if cfg.get("origin_backend_service"):
            external_mutations = sorted(
                set(cfg).intersection(
                    {
                        "backend_service",
                        "cache_policy",
                        "compression_mode",
                        "custom_response_headers",
                        "security_policy",
                        "edge_security_policy",
                        "health_checks",
                        "protocol",
                        "port_name",
                        "timeout_seconds",
                    },
                ),
            )
            if external_mutations:
                return (
                    "external origin_backend_service must be preconfigured; unsupported mutation fields: "
                    + ", ".join(external_mutations)
                )
        for key in ("cdn_id", "backend_bucket_id", "backend_service_id"):
            value = cfg.get(key)
            if value is not None and (not isinstance(value, str) or not _ID_PATTERN.fullmatch(value)):
                return f"{key} must be a valid RFC1035 Compute resource ID"
        for key in (
            "backend_bucket",
            "backend_service",
            "cache_policy",
            "url_map",
            "managed_certificate",
            "target_https_proxy",
            "target_http_proxy",
            "address",
            "forwarding_rule",
        ):
            if key in cfg and not isinstance(cfg[key], dict):
                return f"{key} must be an object"
        if "size" in cfg and cfg["size"] not in {"small", "medium", "large", "xlarge", "custom"}:
            return "size must be small, medium, large, xlarge, or custom"
        if cfg.get("compression_mode", "AUTOMATIC") not in {"AUTOMATIC", "DISABLED"}:
            return "compression_mode must be AUTOMATIC or DISABLED"
        if cfg.get("quic_override", "NONE") not in {"ENABLE", "DISABLE", "NONE"}:
            return "quic_override must be ENABLE, DISABLE, or NONE"
        if cfg.get("protocol", "HTTPS") not in {"HTTP", "HTTPS", "HTTP2"}:
            return "protocol must be HTTP, HTTPS, or HTTP2"
        if "port_name" in cfg and (not isinstance(cfg["port_name"], str) or not cfg["port_name"]):
            return "port_name must be a non-empty string"
        timeout = cfg.get("timeout_seconds", 30)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 86400:
            return "timeout_seconds must be an integer between 1 and 86400"
        for key in (
            "allow_force_cache_all",
            "spa",
            "redirect_http_to_https",
            "allow_insecure_http",
            "delete_adopted_resources",
            "allow_external_key_rotation",
            "deletion_protection",
        ):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        raw_fields = {
            "backend_bucket": _BACKEND_BUCKET_OWNED_FIELDS,
            "backend_service": _BACKEND_SERVICE_OWNED_FIELDS,
            "url_map": _URL_MAP_OWNED_FIELDS,
            "managed_certificate": _CERTIFICATE_OWNED_FIELDS,
            "target_https_proxy": _PROXY_OWNED_FIELDS,
            "target_http_proxy": _PROXY_OWNED_FIELDS,
            "address": _ADDRESS_OWNED_FIELDS,
            "forwarding_rule": _FORWARDING_OWNED_FIELDS,
        }
        for key, owned_fields in raw_fields.items():
            # Google's JSON parser accepts a field's proto name as well as its
            # lowerCamelCase JSON name, so a raw field spelled in proto form
            # bypassed the exact-match check below (#1981).
            proto_names, reserved = raw_field_conflicts(cfg.get(key) or {}, owned_fields | _OUTPUT_FIELDS)
            if proto_names:
                return f"{key} must use the API's lowerCamelCase JSON field names, not {', '.join(proto_names)}"
            if reserved:
                return f"{key} cannot override Astrolift-owned fields: {', '.join(reserved)}"
        if cfg.get("security_policy") and not cfg.get("backends"):
            return "security_policy requires an Astrolift-managed backend service"
        policy = self._cache_policy(cfg)
        cache_mode = str(policy.get("cacheMode") or "")
        if cache_mode not in _CACHE_MODES:
            return f"invalid Cloud CDN cacheMode {cache_mode!r}"
        if cache_mode == "FORCE_CACHE_ALL" and not cfg.get("allow_force_cache_all"):
            return "FORCE_CACHE_ALL requires allow_force_cache_all=true because it can cache private responses"
        for key, maximum in (
            ("defaultTtl", 31622400),
            ("maxTtl", 31622400),
            ("clientTtl", 31622400),
            ("serveWhileStale", 604800),
        ):
            value = policy.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                return f"cache_policy.{key} must be an integer between 0 and {maximum}"
        if int(policy["defaultTtl"]) > int(policy["maxTtl"]):
            return "cache_policy.defaultTtl cannot exceed maxTtl"
        cache_key = policy.get("cacheKeyPolicy") or {}
        if not isinstance(cache_key, dict):
            return "cache_policy.cacheKeyPolicy must be an object"
        if cache_key.get("queryStringWhitelist") and cache_key.get("queryStringBlacklist"):
            return "cache key query string whitelist and blacklist are mutually exclusive"
        bypass = policy.get("bypassCacheOnRequestHeaders") or []
        if not isinstance(bypass, list) or len(bypass) > 5:
            return "cache policy allows at most five bypass request headers"
        for key in ("domains", "aliases"):
            value = cfg.get(key)
            if value is not None and (
                not isinstance(value, list) or not all(isinstance(item, str) and item for item in value)
            ):
                return f"{key} must be a list of non-empty hostnames"
        domains = _domains(cfg)
        for domain in domains:
            if not _valid_domain(domain):
                return f"invalid Cloud CDN domain {domain!r}"
        hostname = cfg.get("hostname")
        if hostname is not None and (
            not isinstance(hostname, str)
            or not hostname
            or not (_valid_domain(hostname.lower().rstrip(".")) or _valid_ip(hostname))
        ):
            return "hostname must be a valid DNS hostname or IP address"
        if cfg.get("domains") and cfg.get("aliases"):
            return "domains and aliases are aliases of the same field; specify only one"
        if cfg.get("certificate_map") and cfg.get("ssl_certificates"):
            return "certificate_map and ssl_certificates are mutually exclusive"
        insecure = bool(cfg.get("allow_insecure_http"))
        if insecure and (domains or cfg.get("certificate_map") or cfg.get("ssl_certificates")):
            return "allow_insecure_http cannot be combined with TLS domains or certificates"
        if not insecure and not (domains or cfg.get("certificate_map") or cfg.get("ssl_certificates")):
            return "HTTPS Cloud CDN requires domains, ssl_certificates, or certificate_map"
        if cfg.get("spa") and not cfg.get("origin_bucket"):
            return "spa error fallback requires an origin_bucket"
        index = str(cfg.get("index") or "index.html")
        if not index or index.endswith("/") or len(index) > 1023:
            return "index must name a file path of at most 1023 characters"
        if cfg.get("backends") and (
            not isinstance(cfg["backends"], list)
            or not cfg["backends"]
            or not all(isinstance(item, dict) and item.get("group") for item in cfg["backends"])
        ):
            return "backends must be a non-empty list of native entries containing group"
        for key in ("health_checks", "custom_response_headers", "ssl_certificates"):
            value = cfg.get(key)
            if value is not None and (
                not isinstance(value, list) or not all(isinstance(item, str) and item for item in value)
            ):
                return f"{key} must be a list of non-empty strings"
        return ""

    def _stack_id(self, spec: ProvisionSpec) -> str:
        raw = "-".join(
            filter(
                None,
                (
                    self._config.name_prefix,
                    spec.organization_slug,
                    spec.app_slug,
                    spec.environment_name,
                    spec.service_handle_hint or "cdn",
                ),
            ),
        )
        return _resource_id(raw, maximum=48)


def _safe_raw(cfg: dict[str, Any], key: str, owned_fields: set[str]) -> dict[str, Any]:
    raw = dict(cfg.get(key) or {})
    # Backstop for _validate_config: body assembly must never merge a raw
    # field _validate_config would have refused (#1981).
    proto_names, reserved = raw_field_conflicts(raw, owned_fields | _OUTPUT_FIELDS)
    if proto_names:
        raise CloudCdnError(f"{key} must use the API's lowerCamelCase JSON field names, not {', '.join(proto_names)}")
    if reserved:
        raise CloudCdnError(f"{key} cannot override Astrolift-owned fields: {', '.join(reserved)}")
    return raw


def _ownership_marker(stack_id: str, *, spec: ProvisionSpec) -> str:
    boundary = "/".join(
        (
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.tenant_cluster_id,
        ),
    )
    identity = spec.managed_service_id or spec.service_handle_hint or stack_id
    return f"Astrolift managed CDN; stack={stack_id}; boundary={boundary}; resource={identity}"


def _owned(resource: dict[str, Any], marker: str) -> bool:
    description = str(resource.get("description") or "")
    return description == marker or description == f"{marker}; adopted=true"


def _owned_stack(resource: dict[str, Any], stack_id: str) -> bool:
    return str(resource.get("description") or "").startswith(
        f"Astrolift managed CDN; stack={stack_id};",
    )


def _adopted(resource: dict[str, Any]) -> bool:
    return str(resource.get("description") or "").endswith("; adopted=true")


def _handle(stack_id: str) -> str:
    return f"{KIND}/{stack_id}"


def _parse_handle(handle: str) -> str:
    prefix = f"{KIND}/"
    if not handle.startswith(prefix):
        raise ValueError(f"handle {handle!r} must start with {prefix!r}")
    stack_id = handle[len(prefix) :]
    if not _ID_PATTERN.fullmatch(stack_id):
        raise ValueError(f"handle {handle!r} contains an invalid Cloud CDN stack ID")
    return stack_id


def _parse_distribution_id(value: str) -> str:
    if value.startswith(f"{KIND}/"):
        return _parse_handle(value)
    if not _ID_PATTERN.fullmatch(value):
        raise CloudCdnError(f"invalid Cloud CDN distribution ID {value!r}")
    return value


def _resource_id(value: str, *, maximum: int) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not normalized or not normalized[0].isalpha():
        normalized = f"a-{normalized}"
    if len(normalized) > maximum:
        digest = hashlib.sha256(normalized.encode()).hexdigest()[:10]
        normalized = f"{normalized[: maximum - len(digest) - 1].rstrip('-')}-{digest}"
    normalized = normalized.rstrip("-")
    return normalized or "cdn"


def _name(stack_id: str, suffix: str) -> str:
    return _resource_id(f"{stack_id}-{suffix}", maximum=63)


def _resource_name(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1]


def _self_link(project_id: str, collection: str, name: str) -> str:
    return f"https://www.googleapis.com/compute/v1/projects/{project_id}/{_COLLECTION_PATHS[collection]}/{name}"


def _domains(cfg: dict[str, Any]) -> list[str]:
    return [str(item).lower().rstrip(".") for item in (cfg.get("domains") or cfg.get("aliases") or [])]


def _valid_domain(value: str) -> bool:
    if len(value) > 253 or "." not in value:
        return False
    labels = value.split(".")
    return all(1 <= len(label) <= 63 and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in labels)


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _validate_signed_key(key_name: str, key_value_b64: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", key_name):
        raise CloudCdnError("signed URL key name must be 1-63 letters, numbers, underscores, or hyphens")
    if not isinstance(key_value_b64, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+={0,2}",
        key_value_b64,
    ):
        raise CloudCdnError("signed URL key must be URL-safe base64")
    try:
        padding = "=" * (-len(key_value_b64) % 4)
        raw = base64.b64decode(
            f"{key_value_b64}{padding}",
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise CloudCdnError("signed URL key must be URL-safe base64") from exc
    if len(raw) != 16:
        raise CloudCdnError("signed URL key must decode to exactly 16 bytes")


def _contains(actual: Any, desired: Any) -> bool:
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains(actual[key], value) for key, value in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(desired)
            and all(_contains(left, right) for left, right in zip(actual, desired, strict=True))
        )
    return actual == desired
