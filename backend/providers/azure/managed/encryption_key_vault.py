"""Azure Key Vault key lifecycle driver (#1454).

``encryption_key`` was executable on AWS (KMS) and GCP (Cloud KMS) and declared
``planned`` on Azure, so a manifest that asked for a customer-managed key was
pinned to two of the three clouds. This closes that hole against Key Vault's
data plane.

Two things about Key Vault shape the contract:

*Key material never leaves the vault.* There is no export and therefore no
snapshot, exactly as on the two sibling drivers.

*Soft delete is mandatory.* Retirement has two honest shapes rather than one. A
plain deprovision disables the key and leaves the material recoverable; only
``delete_data=True`` deletes it, and the soft-deleted record is purged only when
the binding explicitly asks for that.

The client is a small typed adapter over the documented Key Vault data-plane
REST API authenticated through ``azure.identity``, the same shape
``gcp/managed/encryption_cloud_kms.py`` uses for the sibling driver. Keeping the
bodies as plain dicts is also what lets the shared ownership verifier read the
tag envelope straight off a live response.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from _sdk._telemetry import driver_op
from _sdk.azure_ownership import (
    OWNERSHIP_ERROR_CODE,
    AzureOperation,
    AzureOwnershipError,
    arm_tags_of,
    owner_of,
    verify_azure_ownership,
)
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
from azure.managed.tags import arm_tags_for

KIND = "encryption_key"
VARIANT = "key_vault_key"

_VAULT_SCOPE = "https://vault.azure.net/.default"
_KEY_NAME = re.compile(r"^[0-9a-zA-Z-]{1,127}$")
_VAULT_NAME = re.compile(r"^[0-9a-zA-Z](?:[0-9a-zA-Z-]{1,22}[0-9a-zA-Z])$")
_ISO_DURATION = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?$")

_RSA_TYPES = ("RSA", "RSA-HSM")
_EC_TYPES = ("EC", "EC-HSM")
_KEY_TYPES = (*_RSA_TYPES, *_EC_TYPES)
_RSA_SIZES = (2048, 3072, 4096)
_CURVES = ("P-256", "P-256K", "P-384", "P-521")
_KEY_OPERATIONS = ("decrypt", "encrypt", "sign", "unwrapKey", "verify", "wrapKey")
_SIGNING_OPERATIONS = frozenset({"sign", "verify"})
_WRAPPING_OPERATIONS = frozenset({"decrypt", "encrypt", "unwrapKey", "wrapKey"})

#: Azure's minimum rotation interval. A shorter policy is rejected by the
#: service, so it is rejected here instead of after a create call.
_MIN_ROTATION_DAYS = 28

#: Azure has no per-operation crypto roles the way AWS policy actions and GCP
#: Cloud KMS roles do; these four built-ins are the whole vocabulary a key
#: scope can be granted. ``tests/azure/test_managed_encryption_key_vault.py``
#: pins each one against the vetted GUID catalogue in ``azure/role_catalog.py``.
_ACCESS_MODE_ROLES: dict[str, str] = {
    "use": "Key Vault Crypto User",
    "wrap": "Key Vault Crypto Service Encryption User",
    "read": "Key Vault Reader",
    "manage": "Key Vault Crypto Officer",
}

_CONFIG_FIELDS = frozenset(
    {
        "key_type",
        "key_size",
        "curve",
        "key_operations",
        "enabled",
        "expires_on",
        "not_before",
        "rotation_enabled",
        "rotation_period",
        "rotation_notify_before_expiry",
        "access_mode",
        "deletion_protection",
        "purge_on_delete",
    },
)


class AzureKeyVaultKeyError(Exception):
    """Provider error surfaced through the managed-service result protocol."""


class AzureKeyVaultKeyNotFound(AzureKeyVaultKeyError):
    """The vault has no such object."""


@dataclass(frozen=True)
class AzureKeyVaultKeyConfig:
    subscription_id: str
    resource_group: str
    vault_url: str
    key_name_prefix: str = "astrolift"
    deletion_protection_default: bool = True
    purge_on_delete_default: bool = False
    rotation_period_default: str = "P90D"
    rotation_notify_before_expiry_default: str = "P30D"
    api_version: str = "7.4"
    request_timeout_seconds: float = 30.0
    client: Any = None


class _NoRedirect(HTTPRedirectHandler):
    """Never follow a redirect: the request carries a vault bearer token."""

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


class AzureKeyVaultKeyRestClient:
    """Small typed adapter over the Key Vault data-plane JSON API."""

    def __init__(
        self,
        *,
        vault_url: str,
        api_version: str = "7.4",
        credential: Any | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._vault_url = vault_url.rstrip("/")
        self._api_version = api_version
        self._timeout = timeout_seconds
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        self._credential = credential
        self._opener = build_opener(_NoRedirect())

    def get_key(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"/keys/{name}")

    def create_key(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/keys/{name}/create", body=body)

    def update_key(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        # An empty version segment addresses the key's current version, which
        # is the only version a binding ever points at.
        return self._request("PATCH", f"/keys/{name}/", body=body)

    def delete_key(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", f"/keys/{name}")

    def purge_deleted_key(self, name: str) -> None:
        self._request("DELETE", f"/deletedkeys/{name}")

    def get_rotation_policy(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"/keys/{name}/rotationpolicy")

    def set_rotation_policy(self, name: str, policy: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", f"/keys/{name}/rotationpolicy", body=policy)

    def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._vault_url}{path}?api-version={self._api_version}"
        token = self._credential.get_token(_VAULT_SCOPE).token
        request = Request(
            url,
            data=json.dumps(body, separators=(",", ":")).encode() if body is not None else None,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            if exc.code == 404:
                raise AzureKeyVaultKeyNotFound(path) from exc
            detail = exc.read().decode(errors="replace")[:2000]
            raise AzureKeyVaultKeyError(f"Key Vault {method} {path} failed ({exc.code}): {detail}") from exc
        if not payload:
            return {}
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            raise AzureKeyVaultKeyError(f"Key Vault {method} {path} returned a non-object body")
        return parsed


class AzureKeyVaultKeyDriver(ManagedServiceDriver):
    """Own one Key Vault key, its attributes, and its rotation policy."""

    def __init__(self, *, config: AzureKeyVaultKeyConfig, client: Any | None = None) -> None:
        self._config = config
        self._client = client if client is not None else config.client

    @driver_op(
        cloud="azure",
        driver="key_vault_key",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            cfg = self._normalize(spec.config)
            name = self._key_name(spec)
            handle = _handle(self._vault_name(), name)
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_azure_key_vault_key_config"])
        try:
            existing = self._get(name)
            if existing is None:
                key = self._keys.create_key(name, self._create_body(cfg, tags=arm_tags_for(spec)))
            else:
                self._assert_owned(existing, spec, AzureOperation.PROVISION, name)
                self._assert_immutable(existing, cfg)
                key = self._reconcile(name, existing, cfg, tags=arm_tags_for(spec))
            self._reconcile_rotation(name, cfg)
        except AzureOwnershipError as exc:
            return ProvisionResult(False, handle, str(exc), [OWNERSHIP_ERROR_CODE])
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, handle, str(exc), ["invalid_azure_key_vault_key_config"])
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Azure Key Vault key: {exc}", [str(exc)])
        enabled = _is_enabled(key)
        return ProvisionResult(
            True,
            handle,
            f"Azure Key Vault key {name} is {'enabled' if enabled else 'disabled'}",
            ready=enabled,
        )

    @driver_op(cloud="azure", driver="key_vault_key")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            name = self._parse_handle(spec.handle)
            cfg = self._normalize(spec.config)
        except (TypeError, ValueError) as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_azure_key_vault_key_config"], retryable=False)
        try:
            existing = self._get(name)
            if existing is None:
                return UpdateResult(False, spec.handle, "Azure Key Vault key not found", ["not_found"])
            self._assert_owned(existing, spec, AzureOperation.UPDATE, name)
            self._assert_immutable(existing, cfg)
            self._reconcile(name, existing, cfg, tags=None)
            self._reconcile_rotation(name, cfg)
        except AzureOwnershipError as exc:
            return UpdateResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        except (TypeError, ValueError) as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_azure_key_vault_key_config"], retryable=False)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Azure Key Vault key: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Azure Key Vault key {name} reconciled")

    @driver_op(
        cloud="azure",
        driver="key_vault_key",
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
        try:
            name = self._parse_handle(spec.handle)
        except (TypeError, ValueError) as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        try:
            existing = self._get(name)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"describe Azure Key Vault key: {exc}", [str(exc)])
        if existing is None:
            return DeprovisionResult(True, spec.handle, f"Azure Key Vault key {name} is already absent")

        try:
            self._assert_owned(existing, spec, AzureOperation.DELETE, name)
        except AzureOwnershipError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"verify Azure Key Vault key ownership: {exc}", [str(exc)])

        protected = bool(spec.config.get("deletion_protection", self._config.deletion_protection_default))
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Azure Key Vault key {name} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )

        purge = bool(spec.config.get("purge_on_delete", self._config.purge_on_delete_default))
        try:
            if not delete_data:
                if _is_enabled(existing):
                    self._keys.update_key(name, {"attributes": {"enabled": False}})
                return DeprovisionResult(
                    True,
                    spec.handle,
                    (
                        f"Azure Key Vault key {name} disabled; the key material is retained. "
                        "Set delete_data=true to delete it."
                    ),
                )
            self._keys.delete_key(name)
            if purge:
                self._keys.purge_deleted_key(name)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Azure Key Vault key: {exc}", [str(exc)])
        if purge:
            return DeprovisionResult(True, spec.handle, f"Azure Key Vault key {name} deleted and purged")
        return DeprovisionResult(
            True,
            spec.handle,
            (
                f"Azure Key Vault key {name} deleted; the vault keeps the soft-deleted record "
                "for its retention window. Set purge_on_delete=true to purge it."
            ),
        )

    @driver_op(cloud="azure", driver="key_vault_key")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            name = self._parse_handle(handle.handle)
            existing = self._get(name)
            if existing is None:
                return ServiceStatus(handle.handle, "deprovisioned", "Azure Key Vault key does not exist")
            self._assert_owned(existing, handle, AzureOperation.INSPECT, name)
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Azure Key Vault key: {exc}")
        enabled = _is_enabled(existing)
        return ServiceStatus(
            handle.handle,
            "available",
            f"Azure Key Vault key {name} is {'enabled' if enabled else 'disabled'}",
        )

    @driver_op(cloud="azure", driver="key_vault_key")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        name = self._parse_handle(handle.handle)
        existing = self._get(name)
        if existing is None:
            raise AzureKeyVaultKeyError(f"Azure Key Vault key {name} does not exist")
        self._assert_owned(existing, handle, AzureOperation.INSPECT, name)
        material = existing.get("key") or {}
        key_type = str(material.get("kty") or "RSA")
        versioned_id = str(material.get("kid") or "")
        base_id = f"{self._config.vault_url.rstrip('/')}/keys/{name}"
        version = versioned_id[len(base_id) + 1 :] if versioned_id.startswith(f"{base_id}/") else ""
        access_mode = str((config or {}).get("access_mode") or "use")
        if access_mode not in _ACCESS_MODE_ROLES:
            raise AzureKeyVaultKeyError(f"unsupported Key Vault access_mode {access_mode!r}")
        roles = [_ACCESS_MODE_ROLES[access_mode]]
        return Binding(
            env_vars={
                "ENCRYPTION_KEY_ID": ValueRef(literal=base_id),
                "ENCRYPTION_KEY_ARN": ValueRef(literal=base_id),
                "ENCRYPTION_KEY_ALIAS": ValueRef(literal=name),
                "ENCRYPTION_KEY_SPEC": ValueRef(literal=_key_spec(material)),
                "ENCRYPTION_KEY_USAGE": ValueRef(literal=_key_usage(material)),
                "ENCRYPTION_KEY_MULTI_REGION": ValueRef(literal="false"),
                "AZURE_KEY_VAULT_URL": ValueRef(literal=self._config.vault_url.rstrip("/")),
                "AZURE_KEY_VAULT_NAME": ValueRef(literal=self._vault_name()),
                "AZURE_KEY_VAULT_KEY_NAME": ValueRef(literal=name),
                "AZURE_KEY_VAULT_KEY_ID": ValueRef(literal=versioned_id),
                "AZURE_KEY_VAULT_KEY_VERSION": ValueRef(literal=version),
                "AZURE_KEY_VAULT_KEY_TYPE": ValueRef(literal=key_type),
            },
            iam_grants=[Grant(resource=self._key_scope(name), actions=roles)],
            notes=(
                "Azure Key Vault key. ENCRYPTION_KEY_ARN carries the version-less key identifier for "
                "portable consumers; Azure has no ARN and no mutable key alias. The role assignment "
                "authorizes the workload only on a vault in Azure RBAC mode; a vault still on access "
                "policies needs the policy granted separately."
            ),
        )

    @driver_op(cloud="azure", driver="key_vault_key")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise AzureKeyVaultKeyError(
            "Azure Key Vault key material is non-exportable; use rotation or a new key version instead of a snapshot",
        )

    @driver_op(cloud="azure", driver="key_vault_key")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        return ProvisionResult(
            False,
            "",
            "Azure Key Vault key material cannot be restored from a snapshot",
            ["not_implemented"],
        )

    @driver_op(cloud="azure", driver="key_vault_key", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "key_type": {"type": "string", "enum": list(_KEY_TYPES), "default": "RSA"},
                "key_size": {
                    "type": "integer",
                    "enum": list(_RSA_SIZES),
                    "description": "RSA modulus size. Rejected for EC keys, which are sized by their curve.",
                },
                "curve": {
                    "type": "string",
                    "enum": list(_CURVES),
                    "description": "EC curve. Rejected for RSA keys.",
                },
                "key_operations": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": list(_KEY_OPERATIONS)},
                },
                "enabled": {"type": "boolean", "default": True},
                "expires_on": {"type": "string", "format": "date-time"},
                "not_before": {"type": "string", "format": "date-time"},
                "rotation_enabled": {"type": "boolean", "default": True},
                "rotation_period": {
                    "type": "string",
                    "pattern": _ISO_DURATION.pattern,
                    "description": (
                        f"ISO 8601 duration between automatic rotations, at least {_MIN_ROTATION_DAYS} days "
                        "(for example P90D)."
                    ),
                    "default": self._config.rotation_period_default,
                },
                "rotation_notify_before_expiry": {
                    "type": "string",
                    "pattern": _ISO_DURATION.pattern,
                    "default": self._config.rotation_notify_before_expiry_default,
                },
                "access_mode": {
                    "type": "string",
                    "enum": sorted(_ACCESS_MODE_ROLES),
                    "default": "use",
                },
                "deletion_protection": {"type": "boolean", "default": self._config.deletion_protection_default},
                "purge_on_delete": {"type": "boolean", "default": self._config.purge_on_delete_default},
            },
        }

    @driver_op(cloud="azure", driver="key_vault_key", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "ENCRYPTION_KEY_ID": "Version-less Key Vault key identifier",
                "ENCRYPTION_KEY_ARN": "Portable ARN slot carrying the key identifier",
                "ENCRYPTION_KEY_ALIAS": "Key name (Azure has no mutable key alias)",
                "ENCRYPTION_KEY_SPEC": "Key type with its RSA size or EC curve",
                "ENCRYPTION_KEY_USAGE": "ENCRYPT_DECRYPT or SIGN_VERIFY, derived from the key operations",
                "ENCRYPTION_KEY_MULTI_REGION": "Always false; a Key Vault key is single-region",
                "AZURE_KEY_VAULT_URL": "Vault data-plane URL",
                "AZURE_KEY_VAULT_NAME": "Vault name",
                "AZURE_KEY_VAULT_KEY_NAME": "Key name within the vault",
                "AZURE_KEY_VAULT_KEY_ID": "Current versioned key identifier",
                "AZURE_KEY_VAULT_KEY_VERSION": "Current key version",
                "AZURE_KEY_VAULT_KEY_TYPE": "RSA, RSA-HSM, EC, or EC-HSM",
            },
        )

    @driver_op(cloud="azure", driver="key_vault_key", heartbeat=False)
    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "deletion_protection",
            "enabled",
            "expires_on",
            "key_operations",
            "not_before",
            "purge_on_delete",
            "rotation_enabled",
            "rotation_notify_before_expiry",
            "rotation_period",
        ]

    # -- internals ---------------------------------------------------------

    @property
    def _keys(self) -> Any:
        if self._client is None:
            self._client = AzureKeyVaultKeyRestClient(
                vault_url=self._config.vault_url,
                api_version=self._config.api_version,
                timeout_seconds=self._config.request_timeout_seconds,
            )
        return self._client

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        self._validate_install_policy()
        if not isinstance(raw, dict):
            raise ValueError("Azure Key Vault key config must be an object")
        unknown = sorted(set(raw) - _CONFIG_FIELDS)
        if unknown:
            raise ValueError(f"Azure Key Vault key config contains unsupported fields: {unknown}")

        key_type = str(raw.get("key_type") or "RSA")
        if key_type not in _KEY_TYPES:
            raise ValueError(f"key_type must be one of {list(_KEY_TYPES)}")
        key_size = raw.get("key_size")
        curve = raw.get("curve")
        if key_type in _RSA_TYPES:
            if curve is not None:
                raise ValueError("curve is only valid for EC keys")
            if key_size is not None and key_size not in _RSA_SIZES:
                raise ValueError(f"key_size must be one of {list(_RSA_SIZES)}")
        else:
            if key_size is not None:
                raise ValueError("key_size is only valid for RSA keys")
            if curve is not None and curve not in _CURVES:
                raise ValueError(f"curve must be one of {list(_CURVES)}")

        operations = raw.get("key_operations")
        if operations is not None:
            if (
                not isinstance(operations, list)
                or not operations
                or len(set(operations)) != len(operations)
                or any(value not in _KEY_OPERATIONS for value in operations)
            ):
                raise ValueError(f"key_operations must be a unique non-empty subset of {list(_KEY_OPERATIONS)}")
            operations = sorted(operations)

        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        expires_on = _timestamp(raw.get("expires_on"), field="expires_on")
        not_before = _timestamp(raw.get("not_before"), field="not_before")
        if expires_on is not None and not_before is not None and expires_on <= not_before:
            raise ValueError("expires_on must be later than not_before")

        rotation_enabled = raw.get("rotation_enabled", True)
        if not isinstance(rotation_enabled, bool):
            raise ValueError("rotation_enabled must be a boolean")
        rotation_period = str(raw.get("rotation_period") or self._config.rotation_period_default)
        notify = str(raw.get("rotation_notify_before_expiry") or self._config.rotation_notify_before_expiry_default)
        if rotation_enabled:
            if _duration_days(rotation_period, field="rotation_period") < _MIN_ROTATION_DAYS:
                raise ValueError(f"rotation_period must be at least {_MIN_ROTATION_DAYS} days")
            if _duration_days(notify, field="rotation_notify_before_expiry") < 1:
                raise ValueError("rotation_notify_before_expiry must be at least one day")

        access_mode = str(raw.get("access_mode") or "use")
        if access_mode not in _ACCESS_MODE_ROLES:
            raise ValueError(f"access_mode must be one of {sorted(_ACCESS_MODE_ROLES)}")
        for flag in ("deletion_protection", "purge_on_delete"):
            if flag in raw and not isinstance(raw[flag], bool):
                raise ValueError(f"{flag} must be a boolean")

        return {
            "key_type": key_type,
            "key_size": key_size,
            "curve": curve,
            "key_operations": operations,
            "enabled": enabled,
            "expires_on": expires_on,
            "not_before": not_before,
            "rotation_enabled": rotation_enabled,
            "rotation_period": rotation_period,
            "rotation_notify_before_expiry": notify,
            "access_mode": access_mode,
        }

    def _validate_install_policy(self) -> None:
        if not self._config.subscription_id or not self._config.resource_group:
            raise ValueError("Azure Key Vault key requires an install subscription and resource group")
        if not _KEY_NAME.fullmatch(self._config.key_name_prefix):
            raise ValueError("Azure Key Vault key_name_prefix must be letters, numbers, or hyphens")
        self._vault_name()

    def _vault_name(self) -> str:
        parsed = urlparse(self._config.vault_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Azure Key Vault vault_url must be an HTTPS origin without path, query, or credentials")
        name = parsed.hostname.split(".")[0]
        if not _VAULT_NAME.fullmatch(name):
            raise ValueError("Azure Key Vault vault_url must name a 3-24 character vault")
        return name

    def _key_name(self, spec: ProvisionSpec) -> str:
        readable = re.sub(
            r"-+",
            "-",
            re.sub(
                r"[^a-z0-9-]+",
                "-",
                "-".join(
                    (
                        self._config.key_name_prefix,
                        spec.organization_slug,
                        spec.app_slug,
                        spec.environment_name,
                        spec.service_handle_hint or "key",
                    ),
                ).lower(),
            ),
        ).strip("-")
        digest = hashlib.sha256(
            f"{spec.organization_id}:{spec.app_id}:{spec.environment_id}:{spec.managed_service_id}".encode(),
        ).hexdigest()[:10]
        name = f"{readable[:116].rstrip('-')}-{digest}"
        if not _KEY_NAME.fullmatch(name):
            raise ValueError(f"derived Azure Key Vault key name {name!r} is not a legal key name")
        return name

    def _key_scope(self, name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}"
            f"/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.KeyVault/vaults/{self._vault_name()}/keys/{name}"
        )

    def _parse_handle(self, handle: str) -> str:
        prefix = f"{KIND}/"
        expected = self._vault_name()
        if not handle.startswith(prefix):
            raise ValueError(f"handle {handle!r} must begin with {prefix!r}")
        vault, separator, name = handle[len(prefix) :].partition("/keys/")
        if not separator or not _KEY_NAME.fullmatch(name):
            raise ValueError(f"handle {handle!r} must be {prefix}<vault>/keys/<key>")
        if vault.casefold() != expected.casefold():
            raise ValueError(f"handle {handle!r} names vault {vault!r}, but this install manages {expected!r}")
        return name

    def _get(self, name: str) -> dict[str, Any] | None:
        try:
            key: dict[str, Any] = self._keys.get_key(name)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise
        return key

    def _assert_owned(self, key: dict[str, Any], source: object, operation: AzureOperation, name: str) -> None:
        verify_azure_ownership(
            arm_tags_of(key),
            owner_of(source),
            operation=operation,
            resource=f"Azure Key Vault key {name}",
        )

    def _assert_immutable(self, key: dict[str, Any], cfg: dict[str, Any]) -> None:
        material = key.get("key") or {}
        actual_type = str(material.get("kty") or "")
        if actual_type != cfg["key_type"]:
            raise ValueError(
                f"Azure Key Vault key type is immutable ({actual_type!r} != requested {cfg['key_type']!r}); "
                "reprovision under a new binding",
            )
        if cfg["key_size"] is not None and _rsa_size(material) != cfg["key_size"]:
            raise ValueError(
                f"Azure Key Vault key size is immutable ({_rsa_size(material)!r} != requested {cfg['key_size']!r}); "
                "reprovision under a new binding",
            )
        if cfg["curve"] is not None and str(material.get("crv") or "") != cfg["curve"]:
            raise ValueError(
                f"Azure Key Vault key curve is immutable ({material.get('crv')!r} != requested {cfg['curve']!r}); "
                "reprovision under a new binding",
            )

    def _create_body(self, cfg: dict[str, Any], *, tags: dict[str, str]) -> dict[str, Any]:
        body: dict[str, Any] = {"kty": cfg["key_type"], "tags": tags, "attributes": _attributes(cfg)}
        if cfg["key_type"] in _RSA_TYPES:
            body["key_size"] = cfg["key_size"] or 2048
        else:
            body["crv"] = cfg["curve"] or "P-256"
        if cfg["key_operations"] is not None:
            body["key_ops"] = cfg["key_operations"]
        return body

    def _reconcile(
        self,
        name: str,
        key: dict[str, Any],
        cfg: dict[str, Any],
        *,
        tags: dict[str, str] | None,
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        desired = _attributes(cfg)
        current = {field: value for field, value in (key.get("attributes") or {}).items() if field in desired}
        if current != desired:
            patch["attributes"] = desired
        if cfg["key_operations"] is not None:
            actual = sorted(str(value) for value in (key.get("key") or {}).get("key_ops") or [])
            if actual != cfg["key_operations"]:
                patch["key_ops"] = cfg["key_operations"]
        if tags is not None and arm_tags_of(key) != tags:
            patch["tags"] = tags
        if not patch:
            return key
        updated: dict[str, Any] = self._keys.update_key(name, patch)
        return updated

    def _reconcile_rotation(self, name: str, cfg: dict[str, Any]) -> None:
        desired: dict[str, Any] = {"lifetimeActions": []}
        if cfg["rotation_enabled"]:
            desired = {
                "lifetimeActions": [
                    {
                        "trigger": {"timeBeforeExpiry": cfg["rotation_notify_before_expiry"]},
                        "action": {"type": "Rotate"},
                    },
                    {
                        "trigger": {"timeBeforeExpiry": cfg["rotation_notify_before_expiry"]},
                        "action": {"type": "Notify"},
                    },
                ],
                "attributes": {"expiryTime": cfg["rotation_period"]},
            }
        try:
            current = self._keys.get_rotation_policy(name)
        except Exception as exc:
            if not _not_found(exc):
                raise
            current = {}
        if _rotation_shape(current) == _rotation_shape(desired):
            return
        self._keys.set_rotation_policy(name, desired)


def _handle(vault_name: str, key_name: str) -> str:
    return f"{KIND}/{vault_name}/keys/{key_name}"


def _attributes(cfg: dict[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {"enabled": cfg["enabled"]}
    if cfg["expires_on"] is not None:
        attributes["exp"] = cfg["expires_on"]
    if cfg["not_before"] is not None:
        attributes["nbf"] = cfg["not_before"]
    return attributes


def _rotation_shape(policy: dict[str, Any]) -> tuple[str, str]:
    """The rotation decision Astrolift owns: whether a key rotates, and when.

    Compared rather than the whole policy because Key Vault echoes back an id,
    created/updated stamps, and a default Notify action of its own. A raw
    equality check would therefore see drift on every reconcile and rewrite the
    policy forever, which is loudest on the rotation-off path where the service
    keeps re-adding that Notify.
    """
    rotate = next(
        (
            row
            for row in policy.get("lifetimeActions") or []
            if str((row.get("action") or {}).get("type") or "").casefold() == "rotate"
        ),
        None,
    )
    if rotate is None:
        return ("", "")
    trigger = rotate.get("trigger") or {}
    return (
        str((policy.get("attributes") or {}).get("expiryTime") or ""),
        str(trigger.get("timeBeforeExpiry") or trigger.get("timeAfterCreate") or ""),
    )


def _is_enabled(key: dict[str, Any]) -> bool:
    return bool((key.get("attributes") or {}).get("enabled", True))


def _rsa_size(material: dict[str, Any]) -> int | None:
    """RSA modulus size in bits.

    Key Vault returns the modulus rather than the size it was created with, so
    the size the immutability check compares against has to be measured.
    """
    modulus = material.get("n")
    if not isinstance(modulus, str) or not modulus:
        return None
    padded = modulus + "=" * (-len(modulus) % 4)
    try:
        return len(base64.urlsafe_b64decode(padded)) * 8
    except (TypeError, ValueError):
        return None


def _key_spec(material: dict[str, Any]) -> str:
    key_type = str(material.get("kty") or "RSA")
    if key_type in _EC_TYPES:
        return f"{key_type}_{material.get('crv') or 'P-256'}"
    size = _rsa_size(material)
    return f"{key_type}_{size}" if size else key_type


def _key_usage(material: dict[str, Any]) -> str:
    operations = {str(value) for value in material.get("key_ops") or []}
    if operations & _SIGNING_OPERATIONS and not operations & _WRAPPING_OPERATIONS:
        return "SIGN_VERIFY"
    return "ENCRYPT_DECRYPT"


def _timestamp(value: Any, *, field: str) -> int | None:
    """RFC 3339 string to the Unix seconds Key Vault's IntDate attributes use."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an RFC 3339 timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def _duration_days(value: str, *, field: str) -> int:
    match = _ISO_DURATION.fullmatch(value)
    if not match or value == "P":
        raise ValueError(f"{field} must be an ISO 8601 duration in years, months, or days (for example P90D)")
    years, months, days = (int(part) if part else 0 for part in match.groups())
    return years * 365 + months * 30 + days


def _not_found(exc: Exception) -> bool:
    if isinstance(exc, AzureKeyVaultKeyNotFound) or type(exc).__name__ == "ResourceNotFoundError":
        return True
    return getattr(exc, "status_code", None) == 404
