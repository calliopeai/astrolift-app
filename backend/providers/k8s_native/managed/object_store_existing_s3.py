"""Adopt an existing S3-compatible bucket without taking data-plane ownership."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op
from _sdk.k8s_naming import app_namespace, dns_label
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
from k8s_native.managed._handle import ParsedHandle
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle

KIND = "object_store"
VARIANT = "s3_compatible_existing"
API_VERSION = "v1"
RESOURCE_KIND = "ConfigMap"

_RESOURCE = f"{API_VERSION}/{RESOURCE_KIND}"
_OWNER = "app.kubernetes.io/managed-by"
_OWNER_ID = "astrolift.io/managed-service-id"
_COMPONENT = "astrolift.io/component"
_DELETION_PROTECTION = "astrolift.io/deletion-protection"
_CONFIG_KEY = "connection.json"
_CONFIG_VERSION = 1
_BUCKET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,254}$")
_REGION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_SECRET_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")
_SECRET_FIELD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CONFIG_FIELDS = {
    "endpoint",
    "bucket_name",
    "region",
    "prefix",
    "path_style",
    "tls_verify",
    "credential_bundle",
    "access_key_field",
    "secret_key_field",
    "session_token_field",
    "deletion_protection",
}


@dataclass(frozen=True)
class ExistingS3Config:
    cluster_driver: Any = None
    secrets_backend: Any = None
    namespace: str | None = None
    allowed_endpoint_hosts: tuple[str, ...] = ()
    allowed_credential_path_prefixes: tuple[str, ...] = ("managed/object_store/{organization}",)
    allow_insecure_http: bool = False
    allow_skip_tls_verify: bool = False
    allow_endpoint_paths: bool = False


class ExistingS3ObjectStoreDriver(ManagedServiceDriver):
    """Own only an adoption record for an external S3-compatible bucket."""

    def __init__(self, *, config: ExistingS3Config) -> None:
        self._config = config

    @driver_op(
        cloud="k8s_native",
        driver="object_store_existing_s3",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        handle = ""
        try:
            self._require_backends()
            if not spec.managed_service_id:
                raise ValueError("existing S3 adoption requires a managed_service_id")
            namespace = self._config.namespace or app_namespace(
                organization_slug=spec.organization_slug,
                app_slug=spec.app_slug,
            )
            name = dns_label(
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "bucket",
                "s3",
            )
            handle = _pack_handle(
                kind=KIND,
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
            )
            owner = {
                "managed_service_id": spec.managed_service_id,
                "organization": spec.organization_slug,
                "app": spec.app_slug,
                "environment": spec.environment_name,
            }
            config = self._normalize(spec.config, owner=owner)
            self._validate_credentials(config)
            self._assert_adoptable(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
                managed_service_id=spec.managed_service_id,
            )
            record = self._record_manifest(
                namespace=namespace,
                name=name,
                config=config,
                owner=owner,
            )
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, handle, str(exc), ["invalid_existing_s3_config"])

        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            [record],
        )
        if not result.ok:
            return ProvisionResult(
                False,
                handle,
                "existing S3 adoption record was rejected",
                result.summary(),
            )
        return ProvisionResult(
            True,
            handle,
            f"existing S3 bucket {config['bucket_name']} registered; endpoint reachability was not probed",
            ready=True,
        )

    @driver_op(cloud="k8s_native", driver="object_store_existing_s3")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._require_backends()
            parsed = self._parsed(spec.handle)
            current = self._record(parsed)
            if current is None:
                return UpdateResult(
                    False,
                    spec.handle,
                    "existing S3 adoption record does not exist",
                    ["resource_not_found"],
                    retryable=False,
                )
            self._assert_owned(current)
            owner = self._owner_from_labels(current)
            config = self._normalize(spec.config, owner=owner)
            self._validate_credentials(config)
            record = self._record_manifest(
                namespace=parsed.namespace,
                name=parsed.name,
                config=config,
                owner=owner,
            )
        except (TypeError, ValueError) as exc:
            return UpdateResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_existing_s3_update"],
                retryable=False,
            )
        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [record],
        )
        if not result.ok:
            return UpdateResult(False, spec.handle, "existing S3 update was rejected", result.summary())
        return UpdateResult(
            True,
            spec.handle,
            f"existing S3 bucket {config['bucket_name']} registration reconciled",
        )

    @driver_op(
        cloud="k8s_native",
        driver="object_store_existing_s3",
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
        if delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "Astrolift cannot delete data from an adopted S3-compatible bucket",
                ["external_data_deletion_unsupported"],
                retryable=False,
            )
        try:
            self._require_cluster_driver()
            parsed = self._parsed(spec.handle)
            record = self._record(parsed)
            if record is None:
                return DeprovisionResult(
                    True,
                    spec.handle,
                    "existing S3 adoption record already absent; bucket and credentials were retained",
                )
            self._assert_owned(record)
            annotations = dict((record.get("metadata", {}) or {}).get("annotations", {}) or {})
        except ValueError as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_existing_s3_record"],
                retryable=False,
            )
        if annotations.get(_DELETION_PROTECTION) == "true" and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "existing S3 registration deletion protection is enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [self._stub(parsed.namespace, parsed.name)],
        )
        if not result.ok:
            return DeprovisionResult(False, spec.handle, "existing S3 unlink failed", result.summary())
        return DeprovisionResult(
            True,
            spec.handle,
            "existing S3 registration unlinked; external bucket, objects, and credential bundle were retained",
        )

    @driver_op(cloud="k8s_native", driver="object_store_existing_s3")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._require_backends()
            parsed = self._parsed(handle.handle)
            record = self._record(parsed)
            if record is None:
                return ServiceStatus(
                    handle.handle,
                    "deprovisioned",
                    "existing S3 adoption record not found",
                )
            self._assert_owned(record)
            config = self._record_config(record)
            self._validate_credentials(config)
        except ValueError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        return ServiceStatus(
            handle.handle,
            "available",
            (
                f"existing S3 bucket {config['bucket_name']} registration and credential references "
                "are ready; endpoint reachability is not probed"
            ),
        )

    @driver_op(cloud="k8s_native", driver="object_store_existing_s3")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        self._require_backends()
        parsed = self._parsed(handle.handle)
        record = self._record(parsed)
        if record is None:
            raise ValueError("existing S3 adoption record does not exist")
        self._assert_owned(record)
        cfg = self._record_config(record)
        self._validate_credentials(cfg)
        bundle = cfg["credential_bundle"]
        env_vars = {
            "BUCKET_NAME": ValueRef(literal=cfg["bucket_name"]),
            "BUCKET_REGION": ValueRef(literal=cfg["region"]),
            "BUCKET_ENDPOINT": ValueRef(literal=cfg["endpoint"]),
            "BUCKET_PREFIX": ValueRef(literal=cfg["prefix"]),
            "AWS_ACCESS_KEY_ID": ValueRef(
                secret_ref=f"{bundle}#{cfg['access_key_field']}",
            ),
            "AWS_SECRET_ACCESS_KEY": ValueRef(
                secret_ref=f"{bundle}#{cfg['secret_key_field']}",
            ),
            "AWS_REGION": ValueRef(literal=cfg["region"]),
            "AWS_DEFAULT_REGION": ValueRef(literal=cfg["region"]),
            "AWS_ENDPOINT_URL_S3": ValueRef(literal=cfg["endpoint"]),
            "AWS_S3_FORCE_PATH_STYLE": ValueRef(
                literal="true" if cfg["path_style"] else "false",
            ),
            "S3_ENDPOINT_URL": ValueRef(literal=cfg["endpoint"]),
            "S3_BUCKET_ARN": ValueRef(literal=f"arn:aws:s3:::{cfg['bucket_name']}"),
            "S3_TLS_VERIFY": ValueRef(literal="true" if cfg["tls_verify"] else "false"),
        }
        if cfg["session_token_field"]:
            env_vars["AWS_SESSION_TOKEN"] = ValueRef(
                secret_ref=f"{bundle}#{cfg['session_token_field']}",
            )
        return Binding(
            env_vars=env_vars,
            notes=(
                "Astrolift owns only this registration. The external S3-compatible bucket, data, "
                "availability, credential rotation, and least-privilege policy remain operator-owned."
            ),
        )

    @driver_op(cloud="k8s_native", driver="object_store_existing_s3")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise UnsupportedOperationError(
            "managed_service.snapshot(existing S3) is unsupported; use the external store backup/versioning controls",
        )

    @driver_op(cloud="k8s_native", driver="object_store_existing_s3")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise UnsupportedOperationError(
            "managed_service.restore(existing S3) is unsupported; restore through the external store operator",
        )

    @driver_op(cloud="k8s_native", driver="object_store_existing_s3", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["endpoint", "bucket_name", "credential_bundle"],
            "properties": {
                "endpoint": {"type": "string", "format": "uri"},
                "bucket_name": {"type": "string", "minLength": 2, "maxLength": 255},
                "region": {"type": "string", "default": "us-east-1"},
                "prefix": {"type": "string", "default": ""},
                "path_style": {"type": "boolean", "default": True},
                "tls_verify": {"type": "boolean", "default": True},
                "credential_bundle": {"type": "string"},
                "access_key_field": {"type": "string", "default": "accessKey"},
                "secret_key_field": {"type": "string", "default": "secretKey"},
                "session_token_field": {"type": "string", "default": ""},
                "deletion_protection": {"type": "boolean", "default": False},
            },
        }

    @driver_op(cloud="k8s_native", driver="object_store_existing_s3", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "BUCKET_NAME": "Existing S3-compatible bucket name",
                "BUCKET_REGION": "S3 signing region",
                "BUCKET_ENDPOINT": "S3-compatible endpoint URL",
                "BUCKET_PREFIX": "Optional object-key prefix",
                "AWS_ACCESS_KEY_ID": "Access-key field from the external secret bundle",
                "AWS_SECRET_ACCESS_KEY": "Secret-key field from the external secret bundle",
                "AWS_SESSION_TOKEN": "Optional session-token field from the external secret bundle",
                "AWS_REGION": "S3 signing region",
                "AWS_DEFAULT_REGION": "Default S3 signing region",
                "AWS_ENDPOINT_URL_S3": "S3-compatible endpoint URL for AWS SDKs",
                "AWS_S3_FORCE_PATH_STYLE": "Whether clients use path-style addressing",
                "S3_ENDPOINT_URL": "S3-compatible endpoint URL",
                "S3_BUCKET_ARN": "Portable bucket ARN derived from the bucket name",
                "S3_TLS_VERIFY": "Whether clients must verify endpoint TLS",
            },
        )

    @driver_op(cloud="k8s_native", driver="object_store_existing_s3", heartbeat=False)
    def editable_fields(self) -> list[str]:
        return sorted(_CONFIG_FIELDS)

    def _normalize(
        self,
        raw: dict[str, Any],
        *,
        owner: dict[str, str],
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("existing S3 config must be an object")
        config = copy.deepcopy(raw)
        unknown = sorted(set(config) - _CONFIG_FIELDS)
        if unknown:
            raise ValueError(f"existing S3 config contains unsupported fields: {unknown}")
        for field in ("path_style", "tls_verify", "deletion_protection"):
            if field in config and not isinstance(config[field], bool):
                raise ValueError(f"existing S3 {field} must be a boolean")

        endpoint, scheme = self._normalize_endpoint(config.get("endpoint"))
        explicit_tls = config.get("tls_verify")
        if scheme == "http":
            if not self._config.allow_insecure_http:
                raise ValueError("plain HTTP S3-compatible endpoints are disabled by cluster policy")
            if explicit_tls is True:
                raise ValueError("tls_verify cannot be true for a plain HTTP endpoint")
            tls_verify = False
        else:
            tls_verify = True if explicit_tls is None else explicit_tls
            if not tls_verify and not self._config.allow_skip_tls_verify:
                raise ValueError("disabling S3 endpoint TLS verification is blocked by cluster policy")

        bucket = str(config.get("bucket_name") or "")
        if not _BUCKET_NAME.fullmatch(bucket) or ".." in bucket:
            raise ValueError("existing S3 bucket_name is not a portable S3-compatible name")
        region = str(config.get("region") or "us-east-1")
        if not _REGION.fullmatch(region):
            raise ValueError("existing S3 region is invalid")
        prefix = str(config.get("prefix") or "").strip("/")
        if len(prefix) > 1024 or any(ord(character) < 32 for character in prefix):
            raise ValueError("existing S3 prefix is too long or contains control characters")

        bundle = str(config.get("credential_bundle") or "")
        self._validate_secret_path(bundle, owner=owner)
        fields: dict[str, str] = {}
        for key, default in (
            ("access_key_field", "accessKey"),
            ("secret_key_field", "secretKey"),
            ("session_token_field", ""),
        ):
            value = str(config.get(key) or default)
            if value and not _SECRET_FIELD.fullmatch(value):
                raise ValueError(f"existing S3 {key} is not a valid secret-bundle field")
            fields[key] = value
        if fields["access_key_field"] == fields["secret_key_field"]:
            raise ValueError("existing S3 access and secret key fields must differ")

        return {
            "endpoint": endpoint,
            "bucket_name": bucket,
            "region": region,
            "prefix": prefix,
            "path_style": bool(config.get("path_style", True)),
            "tls_verify": tls_verify,
            "credential_bundle": bundle,
            **fields,
            "deletion_protection": bool(config.get("deletion_protection", False)),
        }

    def _normalize_endpoint(self, raw: Any) -> tuple[str, str]:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("existing S3 endpoint must be a non-empty URL")
        try:
            parsed = urlsplit(raw.strip())
            port = parsed.port
        except ValueError as exc:
            raise ValueError("existing S3 endpoint has an invalid port") from exc
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("existing S3 endpoint must use http or https and include a host")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("existing S3 endpoint cannot embed credentials, query strings, or fragments")
        path = parsed.path.rstrip("/")
        if path and not self._config.allow_endpoint_paths:
            raise ValueError("S3 endpoint URL paths are disabled by cluster policy")
        if ".." in path.split("/") or any(ord(character) < 32 or ord(character) == 127 for character in path):
            raise ValueError("existing S3 endpoint path cannot traverse parents or contain control characters")
        host = parsed.hostname.lower().rstrip(".")
        if not self._host_allowed(host):
            raise ValueError(f"existing S3 endpoint host {host!r} is outside the cluster allowlist")
        host_for_url = f"[{host}]" if ":" in host else host
        netloc = f"{host_for_url}:{port}" if port is not None else host_for_url
        return urlunsplit((scheme, netloc, path, "", "")), scheme

    def _validate_secret_path(self, path: str, *, owner: dict[str, str]) -> None:
        if (
            not _SECRET_PATH.fullmatch(path)
            or "#" in path
            or any(segment in {"", ".", ".."} for segment in path.split("/"))
        ):
            raise ValueError("existing S3 credential_bundle must be a canonical secret path without a field selector")
        allowed = False
        replacements = {
            "{organization}": dns_label(owner["organization"]),
            "{app}": dns_label(owner["app"]),
            "{environment}": dns_label(owner["environment"]),
        }
        for raw_prefix in self._config.allowed_credential_path_prefixes:
            prefix = raw_prefix
            for token, value in replacements.items():
                prefix = prefix.replace(token, value)
            prefix = prefix.rstrip("/")
            if not prefix or "{" in prefix or "}" in prefix:
                continue
            if path == prefix or path.startswith(f"{prefix}/"):
                allowed = True
                break
        if not allowed:
            raise ValueError("existing S3 credential_bundle is outside the cluster path allowlist")

    def _validate_credentials(self, config: dict[str, Any]) -> None:
        if self._config.secrets_backend is None:
            raise ValueError("existing S3 adoption requires an external secrets backend")
        try:
            payload = self._config.secrets_backend.get(config["credential_bundle"])
        except Exception as exc:
            raise ValueError("existing S3 credential bundle could not be read") from exc
        if not isinstance(payload, dict):
            raise ValueError("existing S3 credential bundle does not exist or is not a field bundle")
        for field_name in (config["access_key_field"], config["secret_key_field"]):
            value = payload.get(field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"existing S3 credential bundle is missing required field {field_name!r}")
        session_field = config["session_token_field"]
        if session_field:
            value = payload.get(session_field)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"existing S3 credential bundle is missing session-token field {session_field!r}",
                )

    def _record_manifest(
        self,
        *,
        namespace: str,
        name: str,
        config: dict[str, Any],
        owner: dict[str, str],
    ) -> dict[str, Any]:
        labels = {
            _OWNER: "astrolift",
            _OWNER_ID: dns_label(owner["managed_service_id"]),
            _COMPONENT: "s3-compatible-registration",
            "astrolift.io/organization": dns_label(owner["organization"]),
            "astrolift.io/app": dns_label(owner["app"]),
            "astrolift.io/environment": dns_label(owner["environment"]),
        }
        annotations = {
            _DELETION_PROTECTION: "true" if config["deletion_protection"] else "false",
        }
        document = {"version": _CONFIG_VERSION, **config}
        return {
            "apiVersion": API_VERSION,
            "kind": RESOURCE_KIND,
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": annotations,
            },
            "data": {_CONFIG_KEY: json.dumps(document, sort_keys=True, separators=(",", ":"))},
        }

    def _record_config(self, record: dict[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        raw = data.get(_CONFIG_KEY) if isinstance(data, dict) else None
        if not isinstance(raw, str):
            raise ValueError("existing S3 adoption record is missing its connection document")
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("existing S3 adoption record contains malformed connection data") from exc
        if not isinstance(document, dict) or document.pop("version", None) != _CONFIG_VERSION:
            raise ValueError("existing S3 adoption record has an unsupported schema version")
        return self._normalize(document, owner=self._owner_from_labels(record))

    def _assert_adoptable(
        self,
        *,
        cluster_id: str,
        namespace: str,
        name: str,
        managed_service_id: str,
    ) -> None:
        current = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            _RESOURCE,
            name,
        )
        if current is None:
            return
        if not isinstance(current, dict):
            raise ValueError("existing S3 adoption record response is malformed")
        labels = dict((current.get("metadata", {}) or {}).get("labels", {}) or {})
        if (
            labels.get(_OWNER) == "astrolift"
            and labels.get(_OWNER_ID)
            == dns_label(
                managed_service_id,
            )
            and labels.get(_COMPONENT) == "s3-compatible-registration"
        ):
            return
        if labels.get(_OWNER) == "astrolift" and labels.get(_OWNER_ID):
            raise ValueError("existing S3 adoption record belongs to another Astrolift resource")
        raise ValueError("existing S3 adoption record name collides with a foreign ConfigMap")

    @staticmethod
    def _assert_owned(record: dict[str, Any]) -> None:
        labels = dict((record.get("metadata", {}) or {}).get("labels", {}) or {})
        if (
            labels.get(_OWNER) != "astrolift"
            or not labels.get(_OWNER_ID)
            or labels.get(_COMPONENT) != "s3-compatible-registration"
            or not labels.get("astrolift.io/organization")
            or not labels.get("astrolift.io/app")
            or not labels.get("astrolift.io/environment")
        ):
            raise ValueError("existing S3 adoption record is not owned by Astrolift")

    @staticmethod
    def _owner_from_labels(record: dict[str, Any]) -> dict[str, str]:
        labels = dict((record.get("metadata", {}) or {}).get("labels", {}) or {})
        return {
            "managed_service_id": str(labels.get(_OWNER_ID) or ""),
            "organization": str(labels.get("astrolift.io/organization") or ""),
            "app": str(labels.get("astrolift.io/app") or ""),
            "environment": str(labels.get("astrolift.io/environment") or ""),
        }

    def _record(self, parsed: ParsedHandle) -> dict[str, Any] | None:
        record = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            _RESOURCE,
            parsed.name,
        )
        if record is None:
            return None
        if not isinstance(record, dict):
            raise ValueError("existing S3 adoption record response is malformed")
        return record

    @staticmethod
    def _stub(namespace: str, name: str) -> dict[str, Any]:
        return {
            "apiVersion": API_VERSION,
            "kind": RESOURCE_KIND,
            "metadata": {"name": name, "namespace": namespace},
        }

    def _host_allowed(self, host: str) -> bool:
        return any(
            host == allowed.lower().lstrip(".").rstrip(".")
            or host.endswith(f".{allowed.lower().lstrip('.').rstrip('.')}")
            for allowed in self._config.allowed_endpoint_hosts
            if allowed.strip(".")
        )

    def _require_cluster_driver(self) -> None:
        if self._config.cluster_driver is None:
            raise ValueError("existing S3 adoption requires a live cluster driver")

    def _require_backends(self) -> None:
        self._require_cluster_driver()
        if self._config.secrets_backend is None:
            raise ValueError("existing S3 adoption requires an external secrets backend")

    @staticmethod
    def _parsed(handle: str) -> ParsedHandle:
        parsed = _unpack_handle(handle)
        if parsed.kind != KIND:
            raise ValueError(f"existing S3 handle kind must be {KIND!r}, got {parsed.kind!r}")
        if parsed.is_legacy:
            raise ValueError("legacy existing S3 handle has no cluster locator")
        return parsed
