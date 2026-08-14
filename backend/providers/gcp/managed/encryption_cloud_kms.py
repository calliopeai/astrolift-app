"""Google Cloud KMS key-ring and CryptoKey lifecycle driver.

Cloud KMS deliberately never deletes KeyRing or CryptoKey records.  Astrolift
therefore manages the lifecycle of the key *material* (CryptoKeyVersions): a
safe deprovision disables active versions, while ``delete_data=True`` schedules
their destruction using the key's provider-enforced waiting period.

The production client uses the documented Cloud KMS v1 REST API through
``google-auth``.  That keeps the provider bundle small while still exposing the
complete native ``CryptoKey`` request surface through ``config.crypto_key``.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

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

KIND = "encryption_key"
_API_ROOT = "https://cloudkms.googleapis.com/v1"
_DEFAULT_ROTATION_PERIOD = "7776000s"  # 90 days
_DEFAULT_DESTROY_SCHEDULED_DURATION = "2592000s"  # 30 days
_OWNERSHIP_KEYS = (
    "astrolift_io_managed_by",
    "astrolift_io_organization",
    "astrolift_io_app",
    "astrolift_io_environment",
    "astrolift_io_cluster",
    "astrolift_io_resource_hint",
)


class CloudKMSError(Exception):
    """Provider error surfaced through the managed-service result protocol."""


class CloudKMSNotFound(CloudKMSError):
    """REST resource does not exist."""


@dataclass(frozen=True)
class CloudKMSConfig:
    project_id: str
    location: str
    key_ring_name_prefix: str = "astrolift"
    key_name_prefix: str = "astrolift"
    deletion_protection_default: bool = True
    rotation_period_default: str = _DEFAULT_ROTATION_PERIOD
    destroy_scheduled_duration_default: str = _DEFAULT_DESTROY_SCHEDULED_DURATION
    api_endpoint: str = _API_ROOT


class CloudKMSRestClient:
    """Small typed adapter over the Cloud KMS v1 JSON API."""

    def __init__(self, *, api_endpoint: str = _API_ROOT, session: Any | None = None) -> None:
        self._api_endpoint = api_endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=("https://www.googleapis.com/auth/cloud-platform",),
            )
            session = AuthorizedSession(credentials)
        self._session = session

    def get_key_ring(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def create_key_ring(self, parent: str, key_ring_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/keyRings",
            params={"keyRingId": key_ring_id},
            json={},
        )

    def get_crypto_key(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def create_crypto_key(
        self,
        parent: str,
        crypto_key_id: str,
        crypto_key: dict[str, Any],
        *,
        skip_initial_version_creation: bool,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/cryptoKeys",
            params={
                "cryptoKeyId": crypto_key_id,
                "skipInitialVersionCreation": str(skip_initial_version_creation).lower(),
            },
            json=crypto_key,
        )

    def patch_crypto_key(
        self,
        name: str,
        crypto_key: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        body = dict(crypto_key)
        body["name"] = name
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json=body,
        )

    def list_crypto_key_versions(self, parent: str) -> list[dict[str, Any]]:
        versions: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params = {"pageSize": "1000"}
            if page_token:
                params["pageToken"] = page_token
            response = self._request(
                "GET",
                f"{parent}/cryptoKeyVersions",
                params=params,
            )
            versions.extend(response.get("cryptoKeyVersions") or [])
            page_token = str(response.get("nextPageToken") or "")
            if not page_token:
                return versions

    def patch_crypto_key_version(self, name: str, *, state: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            name,
            params={"updateMask": "state"},
            json={"name": name, "state": state},
        )

    def destroy_crypto_key_version(self, name: str) -> dict[str, Any]:
        return self._request("POST", f"{name}:destroy", json={})

    def restore_crypto_key_version(self, name: str) -> dict[str, Any]:
        return self._request("POST", f"{name}:restore", json={})

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
            f"{self._api_endpoint}/{resource.lstrip('/')}",
            params=params,
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise CloudKMSNotFound(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise CloudKMSError(f"Cloud KMS HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class CloudKMSDriver(ManagedServiceDriver):
    def __init__(self, *, config: CloudKMSConfig, client: Any | None = None) -> None:
        self._config = config
        self._kms = client or CloudKMSRestClient(api_endpoint=config.api_endpoint)

    @driver_op(
        cloud="gcp",
        driver="cloud_kms",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = _validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_cloud_kms_config"])
        try:
            key_ring_id = str(cfg.get("key_ring_id") or self._key_ring_id(spec))
            key_id = str(cfg.get("key_id") or self._key_id(spec))
            _validate_resource_id(key_ring_id, field="key_ring_id")
            _validate_resource_id(key_id, field="key_id")
        except (TypeError, ValueError, CloudKMSError) as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_cloud_kms_name"])

        parent = f"projects/{self._config.project_id}/locations/{self._config.location}"
        key_ring_name = f"{parent}/keyRings/{key_ring_id}"
        key_name = f"{key_ring_name}/cryptoKeys/{key_id}"
        handle = _handle_for(key_name)
        try:
            try:
                self._kms.get_key_ring(key_ring_name)
            except Exception as exc:
                if not _not_found(exc):
                    raise
                self._kms.create_key_ring(parent, key_ring_id)

            try:
                key = self._kms.get_crypto_key(key_name)
            except Exception as exc:
                if not _not_found(exc):
                    raise
                body = self._create_crypto_key(spec, cfg)
                key = self._kms.create_crypto_key(
                    key_ring_name,
                    key_id,
                    body,
                    skip_initial_version_creation=bool(
                        cfg.get("skip_initial_version_creation", body.get("importOnly", False)),
                    ),
                )
            else:
                if not _is_owned(key, spec):
                    return ProvisionResult(
                        False,
                        handle,
                        "refusing to adopt a Cloud KMS key not owned by Astrolift",
                        ["resource_not_owned"],
                    )
                self._validate_immutable(key, cfg)

            key = self._reconcile_key(key_name, key, cfg, labels=_labels_for(spec))
            versions = self._kms.list_crypto_key_versions(key_name)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Cloud KMS key: {exc}", [str(exc)])

        active = [item for item in versions if str(item.get("state")) == "ENABLED"]
        return ProvisionResult(
            True,
            handle,
            f"Cloud KMS key {key.get('name', key_name)} has {len(active)} enabled version(s)",
            ready=bool(active),
        )

    @driver_op(cloud="gcp", driver="cloud_kms")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            key_name = _parse_handle(spec.handle)
            key = self._kms.get_crypto_key(key_name)
            if not _has_platform_ownership(key):
                return UpdateResult(
                    False,
                    spec.handle,
                    "refusing to update a Cloud KMS key not owned by Astrolift",
                    ["resource_not_owned"],
                )
            error = _validate_config(spec.config or {})
            if error:
                return UpdateResult(False, spec.handle, error, ["invalid_cloud_kms_config"])
            self._validate_immutable(key, spec.config or {})
            self._reconcile_key(key_name, key, spec.config or {}, labels=None)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, "Cloud KMS key not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update Cloud KMS key: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, "Cloud KMS key reconciled")

    @driver_op(
        cloud="gcp",
        driver="cloud_kms",
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
            key_name = _parse_handle(spec.handle)
            key = self._kms.get_crypto_key(key_name)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, "Cloud KMS key is already absent")
            return DeprovisionResult(False, spec.handle, f"describe Cloud KMS key: {exc}", [str(exc)])

        if not _has_platform_ownership(key) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "refusing to retire a Cloud KMS key not owned by Astrolift",
                ["resource_not_owned"],
                retryable=False,
            )
        protected = bool(
            spec.config.get("deletion_protection", self._config.deletion_protection_default),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Cloud KMS key has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )

        try:
            versions = self._kms.list_crypto_key_versions(key_name)
            if delete_data:
                pending = [
                    item
                    for item in versions
                    if str(item.get("state")) not in {"ENABLED", "DISABLED", "DESTROY_SCHEDULED", "DESTROYED"}
                ]
                if pending:
                    states = sorted({str(item.get("state") or "UNKNOWN") for item in pending})
                    return DeprovisionResult(
                        False,
                        spec.handle,
                        f"Cloud KMS versions are not destroyable yet: {', '.join(states)}",
                        ["key_versions_not_destroyable"],
                        retryable=True,
                    )
                scheduled = 0
                for version in versions:
                    name = str(version["name"])
                    state = str(version.get("state") or "")
                    if state == "ENABLED":
                        self._kms.patch_crypto_key_version(name, state="DISABLED")
                        state = "DISABLED"
                    if state == "DISABLED":
                        self._kms.destroy_crypto_key_version(name)
                        scheduled += 1
                return DeprovisionResult(
                    True,
                    spec.handle,
                    (
                        f"scheduled destruction for {scheduled} Cloud KMS key version(s); "
                        "the key and key-ring metadata are retained by Google Cloud"
                    ),
                )

            disabled = 0
            for version in versions:
                if str(version.get("state")) == "ENABLED":
                    self._kms.patch_crypto_key_version(str(version["name"]), state="DISABLED")
                    disabled += 1
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"retire Cloud KMS key: {exc}", [str(exc)])
        return DeprovisionResult(
            True,
            spec.handle,
            (
                f"disabled {disabled} Cloud KMS key version(s); key material and immutable "
                "key/key-ring records were retained"
            ),
        )

    @driver_op(cloud="gcp", driver="cloud_kms")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            key_name = _parse_handle(handle.handle)
            self._kms.get_crypto_key(key_name)
            versions = self._kms.list_crypto_key_versions(key_name)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", "Cloud KMS key does not exist")
            return ServiceStatus(handle.handle, "error", f"describe Cloud KMS key: {exc}")

        states = [str(item.get("state") or "UNKNOWN") for item in versions]
        if any(state == "ENABLED" for state in states):
            state = "available"
        elif any(state in {"PENDING_GENERATION", "PENDING_IMPORT"} for state in states) or not states:
            state = "provisioning"
        elif states and all(value == "DESTROYED" for value in states):
            state = "deprovisioned"
        elif any(value == "DESTROY_SCHEDULED" for value in states):
            state = "deprovisioning"
        else:
            state = "available"
        counts = {value: states.count(value) for value in sorted(set(states))}
        detail = ", ".join(f"{name}={count}" for name, count in counts.items()) or "no versions"
        return ServiceStatus(handle.handle, state, f"Cloud KMS key versions: {detail}")

    @driver_op(cloud="gcp", driver="cloud_kms")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        key_name = _parse_handle(handle.handle)
        key = self._kms.get_crypto_key(key_name)
        project_id, location, key_ring_id, key_id = _parts(key_name)
        template = key.get("versionTemplate") or {}
        purpose = str(key.get("purpose") or "ENCRYPT_DECRYPT")
        algorithm = str(template.get("algorithm") or "GOOGLE_SYMMETRIC_ENCRYPTION")
        protection = str(template.get("protectionLevel") or "SOFTWARE")
        access_mode = str((config or {}).get("access_mode") or "use")
        return Binding(
            env_vars={
                "ENCRYPTION_KEY_ID": ValueRef(literal=key_name),
                "ENCRYPTION_KEY_ARN": ValueRef(literal=key_name),
                "ENCRYPTION_KEY_ALIAS": ValueRef(literal=key_id),
                "ENCRYPTION_KEY_SPEC": ValueRef(literal=algorithm),
                "ENCRYPTION_KEY_USAGE": ValueRef(literal=purpose),
                "ENCRYPTION_KEY_MULTI_REGION": ValueRef(literal=str(location == "global").lower()),
                "GCP_KMS_KEY_NAME": ValueRef(literal=key_name),
                "GCP_KMS_KEY_RING": ValueRef(literal=key_ring_id),
                "GCP_KMS_LOCATION": ValueRef(literal=location),
                "GCP_KMS_PROTECTION_LEVEL": ValueRef(literal=protection),
                "GCP_PROJECT_ID": ValueRef(literal=project_id),
            },
            iam_grants=[
                Grant(
                    resource=key_name,
                    actions=_binding_roles(purpose=purpose, access_mode=access_mode),
                ),
            ],
            notes=(
                "Google Cloud KMS CryptoKey. ENCRYPTION_KEY_ARN carries the full GCP resource "
                "name for portable consumers; GCP has no ARN or mutable key alias."
            ),
        )

    @driver_op(cloud="gcp", driver="cloud_kms")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise CloudKMSError(
            "Cloud KMS key material is non-exportable; use rotation or an imported key version instead",
        )

    @driver_op(cloud="gcp", driver="cloud_kms")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "Cloud KMS key material cannot be restored from a snapshot",
            ["not_implemented"],
        )

    @driver_op(cloud="gcp", driver="cloud_kms", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key_ring_id": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9_-]{1,63}$",
                    "description": "Existing or deterministic Cloud KMS KeyRing ID.",
                },
                "key_id": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9_-]{1,63}$",
                    "description": "CryptoKey ID within the key ring.",
                },
                "purpose": {
                    "type": "string",
                    "description": (
                        "Native CryptoKeyPurpose, including ENCRYPT_DECRYPT, ASYMMETRIC_SIGN, "
                        "ASYMMETRIC_DECRYPT, RAW_ENCRYPT_DECRYPT, MAC, and KEY_ENCAPSULATION."
                    ),
                    "default": "ENCRYPT_DECRYPT",
                },
                "algorithm": {
                    "type": "string",
                    "description": "Native CryptoKeyVersionAlgorithm; future provider values are accepted.",
                    "default": "GOOGLE_SYMMETRIC_ENCRYPTION",
                },
                "protection_level": {
                    "type": "string",
                    "description": (
                        "Native protection level: SOFTWARE, HSM, EXTERNAL, EXTERNAL_VPC, or "
                        "HSM_SINGLE_TENANT where available."
                    ),
                    "default": "SOFTWARE",
                },
                "crypto_key": {
                    "type": "object",
                    "description": (
                        "Provider-native CryptoKey request fields. name and labels remain "
                        "Astrolift-owned; first-class fields override matching native values."
                    ),
                },
                "rotation_enabled": {"type": "boolean", "default": True},
                "rotation_period": {
                    "type": "string",
                    "description": "Google Duration (minimum 24h), such as 7776000s for 90 days.",
                },
                "next_rotation_time": {"type": "string", "format": "date-time"},
                "destroy_scheduled_duration": {
                    "type": "string",
                    "description": "Immutable Google Duration for scheduled version destruction.",
                },
                "import_only": {"type": "boolean", "default": False},
                "skip_initial_version_creation": {"type": "boolean"},
                "enabled": {"type": "boolean", "default": True},
                "restore_scheduled_versions": {"type": "boolean", "default": False},
                "access_mode": {
                    "type": "string",
                    "enum": [
                        "use",
                        "encrypt",
                        "decrypt",
                        "sign",
                        "verify",
                        "public",
                        "crypto",
                        "use_via_delegation",
                        "encrypt_via_delegation",
                    ],
                    "default": "use",
                },
                "deletion_protection": {"type": "boolean", "default": True},
            },
        }

    @driver_op(cloud="gcp", driver="cloud_kms", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "ENCRYPTION_KEY_ID": "Full Cloud KMS CryptoKey resource name",
                "ENCRYPTION_KEY_ARN": "Portable ARN slot containing the GCP resource name",
                "ENCRYPTION_KEY_ALIAS": "CryptoKey ID (GCP has no mutable key alias)",
                "ENCRYPTION_KEY_SPEC": "CryptoKeyVersion algorithm",
                "ENCRYPTION_KEY_USAGE": "CryptoKey purpose",
                "ENCRYPTION_KEY_MULTI_REGION": "Whether the key uses the global location",
                "GCP_KMS_KEY_NAME": "Full Cloud KMS CryptoKey resource name",
                "GCP_KMS_KEY_RING": "Cloud KMS KeyRing ID",
                "GCP_KMS_LOCATION": "Cloud KMS location",
                "GCP_KMS_PROTECTION_LEVEL": "SOFTWARE, HSM, external, or single-tenant HSM",
                "GCP_PROJECT_ID": "GCP project containing the key",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "rotation_enabled",
            "rotation_period",
            "next_rotation_time",
            "enabled",
            "restore_scheduled_versions",
            "access_mode",
            "deletion_protection",
        ]

    def _create_crypto_key(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, Any]:
        native = dict(cfg.get("crypto_key") or {})
        native.pop("name", None)
        native.pop("labels", None)
        purpose = str(cfg.get("purpose") or native.get("purpose") or "ENCRYPT_DECRYPT")
        algorithm = str(
            cfg.get("algorithm")
            or (native.get("versionTemplate") or {}).get("algorithm")
            or _default_algorithm(purpose)
        )
        protection = str(
            cfg.get("protection_level") or (native.get("versionTemplate") or {}).get("protectionLevel") or "SOFTWARE"
        )
        native["purpose"] = purpose
        native["versionTemplate"] = {
            **dict(native.get("versionTemplate") or {}),
            "algorithm": algorithm,
            "protectionLevel": protection,
        }
        native["labels"] = _labels_for(spec)
        native["importOnly"] = bool(cfg.get("import_only", native.get("importOnly", False)))
        native["destroyScheduledDuration"] = str(
            cfg.get("destroy_scheduled_duration")
            or native.get("destroyScheduledDuration")
            or self._config.destroy_scheduled_duration_default
        )
        self._apply_rotation(native, cfg)
        return native

    def _reconcile_key(
        self,
        key_name: str,
        key: dict[str, Any],
        cfg: dict[str, Any],
        *,
        labels: dict[str, str] | None,
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        mask: list[str] = []
        if labels is not None and dict(key.get("labels") or {}) != labels:
            patch["labels"] = labels
            mask.append("labels")

        desired = dict(key)
        self._apply_rotation(desired, cfg)
        for field in ("rotationPeriod", "nextRotationTime"):
            if desired.get(field) != key.get(field):
                patch[field] = desired.get(field)
                mask.append(field)
        if mask:
            key = self._kms.patch_crypto_key(key_name, patch, update_mask=mask)

        versions = self._kms.list_crypto_key_versions(key_name)
        if cfg.get("restore_scheduled_versions"):
            for version in versions:
                if str(version.get("state")) == "DESTROY_SCHEDULED":
                    self._kms.restore_crypto_key_version(str(version["name"]))
            versions = self._kms.list_crypto_key_versions(key_name)
        if "enabled" in cfg:
            desired_state = "ENABLED" if bool(cfg["enabled"]) else "DISABLED"
            source_state = "DISABLED" if desired_state == "ENABLED" else "ENABLED"
            for version in versions:
                if str(version.get("state")) == source_state:
                    self._kms.patch_crypto_key_version(str(version["name"]), state=desired_state)
        return key

    def _apply_rotation(self, body: dict[str, Any], cfg: dict[str, Any]) -> None:
        purpose = str(body.get("purpose") or "ENCRYPT_DECRYPT")
        enabled = bool(
            cfg.get(
                "rotation_enabled",
                purpose == "ENCRYPT_DECRYPT" and not bool(body.get("importOnly")),
            ),
        )
        if enabled and purpose != "ENCRYPT_DECRYPT":
            raise CloudKMSError("automatic rotation is supported only for ENCRYPT_DECRYPT keys")
        if not enabled:
            body.pop("rotationPeriod", None)
            body.pop("nextRotationTime", None)
            return
        rotation_period = str(
            cfg.get("rotation_period") or body.get("rotationPeriod") or self._config.rotation_period_default
        )
        _validate_duration(rotation_period, field="rotation_period", minimum_seconds=86400)
        body["rotationPeriod"] = rotation_period
        body["nextRotationTime"] = str(
            cfg.get("next_rotation_time") or body.get("nextRotationTime") or _future_timestamp(rotation_period)
        )

    def _validate_immutable(self, key: dict[str, Any], cfg: dict[str, Any]) -> None:
        native = cfg.get("crypto_key") or {}
        template = key.get("versionTemplate") or {}
        requested_template = native.get("versionTemplate") or {}
        expected = {
            "purpose": cfg.get("purpose", native.get("purpose")),
            "algorithm": cfg.get("algorithm", requested_template.get("algorithm")),
            "protection_level": cfg.get(
                "protection_level",
                requested_template.get("protectionLevel"),
            ),
            "import_only": cfg.get("import_only", native.get("importOnly")),
            "destroy_scheduled_duration": cfg.get(
                "destroy_scheduled_duration",
                native.get("destroyScheduledDuration"),
            ),
        }
        actual = {
            "purpose": key.get("purpose"),
            "algorithm": template.get("algorithm"),
            "protection_level": template.get("protectionLevel"),
            "import_only": key.get("importOnly", False),
            "destroy_scheduled_duration": key.get("destroyScheduledDuration"),
        }
        for field, value in expected.items():
            if value is not None and actual[field] != value:
                raise CloudKMSError(
                    f"Cloud KMS {field} is immutable ({actual[field]!r} != requested {value!r}); "
                    "reprovision with a new key ID",
                )

    def _key_ring_id(self, spec: ProvisionSpec) -> str:
        return _resource_id(
            self._config.key_ring_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        )

    def _key_id(self, spec: ProvisionSpec) -> str:
        return _resource_id(self._config.key_name_prefix, spec.service_handle_hint or "key")


def _validate_config(cfg: dict[str, Any]) -> str:
    if "crypto_key" in cfg and not isinstance(cfg["crypto_key"], dict):
        return "config.crypto_key must be an object"
    reserved = {"name", "labels"}.intersection((cfg.get("crypto_key") or {}).keys())
    if reserved:
        return f"config.crypto_key cannot override Astrolift-owned fields: {', '.join(sorted(reserved))}"
    access_mode = str(cfg.get("access_mode") or "use")
    if access_mode not in {
        "use",
        "encrypt",
        "decrypt",
        "sign",
        "verify",
        "public",
        "crypto",
        "use_via_delegation",
        "encrypt_via_delegation",
    }:
        return f"unsupported Cloud KMS access_mode {access_mode!r}"
    try:
        if cfg.get("rotation_period") is not None:
            _validate_duration(str(cfg["rotation_period"]), field="rotation_period", minimum_seconds=86400)
        if cfg.get("destroy_scheduled_duration") is not None:
            _validate_duration(
                str(cfg["destroy_scheduled_duration"]),
                field="destroy_scheduled_duration",
                minimum_seconds=86400,
            )
        if cfg.get("next_rotation_time") is not None:
            _parse_timestamp(str(cfg["next_rotation_time"]))
    except (TypeError, ValueError, CloudKMSError) as exc:
        return str(exc)
    purpose = str(cfg.get("purpose") or (cfg.get("crypto_key") or {}).get("purpose") or "ENCRYPT_DECRYPT")
    if cfg.get("rotation_enabled") is True and purpose != "ENCRYPT_DECRYPT":
        return "automatic rotation is supported only for ENCRYPT_DECRYPT keys"
    return ""


def _default_algorithm(purpose: str) -> str:
    if purpose == "ENCRYPT_DECRYPT":
        return "GOOGLE_SYMMETRIC_ENCRYPTION"
    raise CloudKMSError(f"config.algorithm is required for Cloud KMS purpose {purpose!r}")


def _binding_roles(*, purpose: str, access_mode: str) -> list[str]:
    by_mode = {
        "encrypt": "roles/cloudkms.cryptoKeyEncrypter",
        "decrypt": "roles/cloudkms.cryptoKeyDecrypter",
        "sign": "roles/cloudkms.signer",
        "verify": "roles/cloudkms.verifier",
        "public": "roles/cloudkms.publicKeyViewer",
        "crypto": "roles/cloudkms.cryptoOperator",
        "use_via_delegation": "roles/cloudkms.cryptoKeyEncrypterDecrypterViaDelegation",
        "encrypt_via_delegation": "roles/cloudkms.cryptoKeyEncrypterViaDelegation",
    }
    if access_mode in by_mode:
        return [by_mode[access_mode]]
    if purpose == "ASYMMETRIC_SIGN":
        return ["roles/cloudkms.signerVerifier"]
    if purpose == "KEY_ENCAPSULATION":
        return ["roles/cloudkms.decapsulator"]
    if purpose == "ASYMMETRIC_DECRYPT":
        return ["roles/cloudkms.cryptoKeyDecrypter", "roles/cloudkms.publicKeyViewer"]
    if purpose == "MAC":
        return ["roles/cloudkms.cryptoOperator"]
    return ["roles/cloudkms.cryptoKeyEncrypterDecrypter"]


def _labels_for(spec: ProvisionSpec) -> dict[str, str]:
    labels = _ownership_labels(spec)
    if spec.binding_id:
        labels["astrolift_io_binding"] = _label_value(spec.binding_id)
    if spec.managed_service_id:
        labels["astrolift_io_managed_service_id"] = _label_value(spec.managed_service_id)
    labels["astrolift_io_isolation"] = _label_value(spec.isolation)
    for key, value in (spec.tags or {}).items():
        label_key = _label_key(f"astrolift_extra_{key}")
        if label_key in labels:
            raise CloudKMSError(f"duplicate normalized Cloud KMS label {label_key!r}")
        labels[label_key] = _label_value(str(value))
    if len(labels) > 64:
        raise CloudKMSError("Cloud KMS supports at most 64 labels")
    return labels


def _ownership_labels(spec: ProvisionSpec) -> dict[str, str]:
    return {
        "astrolift_io_managed_by": "platform",
        "astrolift_io_organization": _label_value(spec.organization_slug),
        "astrolift_io_app": _label_value(spec.app_slug),
        "astrolift_io_environment": _label_value(spec.environment_name),
        "astrolift_io_cluster": _label_value(spec.tenant_cluster_id),
        "astrolift_io_resource_hint": _label_value(spec.service_handle_hint or "key"),
    }


def _is_owned(key: dict[str, Any], spec: ProvisionSpec) -> bool:
    labels = dict(key.get("labels") or {})
    expected = _ownership_labels(spec)
    return all(labels.get(name) == value for name, value in expected.items())


def _has_platform_ownership(key: dict[str, Any]) -> bool:
    labels = dict(key.get("labels") or {})
    return labels.get("astrolift_io_managed_by") == "platform" and all(labels.get(name) for name in _OWNERSHIP_KEYS[1:])


def _resource_id(*parts: str) -> str:
    value = "-".join(part for part in parts if part).lower()
    value = "".join(char if (char.isalnum() or char in "-_") else "-" for char in value)
    while "--" in value:
        value = value.replace("--", "-")
    value = value.strip("-_")[:63].rstrip("-_")
    _validate_resource_id(value, field="generated resource ID")
    return value


def _validate_resource_id(value: str, *, field: str) -> None:
    if not value or len(value) > 63 or any(not (char.isalnum() or char in "-_") for char in value):
        raise CloudKMSError(f"{field} must match [A-Za-z0-9_-]{{1,63}}")


def _label_key(value: str) -> str:
    normalized = "".join(char if (char.isalnum() or char in "_-") else "_" for char in value.lower())
    normalized = normalized[:63]
    if not normalized or not normalized[0].isalpha():
        normalized = f"k_{normalized}"[:63]
    return normalized


def _label_value(value: str) -> str:
    return "".join(char if (char.isalnum() or char in "_-") else "_" for char in value.lower())[:63]


def _handle_for(key_name: str) -> str:
    return f"{KIND}/{key_name}"


def _parse_handle(handle: str) -> str:
    prefix = f"{KIND}/"
    if not handle.startswith(prefix):
        raise CloudKMSError(f"handle {handle!r} must begin with {prefix!r}")
    key_name = handle[len(prefix) :]
    _parts(key_name)
    return key_name


def _parts(key_name: str) -> tuple[str, str, str, str]:
    parts = key_name.split("/")
    if (
        len(parts) != 8
        or parts[0] != "projects"
        or parts[2] != "locations"
        or parts[4] != "keyRings"
        or parts[6] != "cryptoKeys"
    ):
        raise CloudKMSError(
            f"Cloud KMS key name {key_name!r} must be projects/*/locations/*/keyRings/*/cryptoKeys/*",
        )
    return parts[1], parts[3], parts[5], parts[7]


def _validate_duration(value: str, *, field: str, minimum_seconds: int) -> None:
    seconds = _duration_seconds(value)
    if seconds < minimum_seconds:
        raise CloudKMSError(f"{field} must be at least {minimum_seconds}s")


def _duration_seconds(value: str) -> float:
    if not value.endswith("s"):
        raise CloudKMSError("Google Duration values must end in 's'")
    try:
        seconds = float(value[:-1])
    except ValueError as exc:
        raise CloudKMSError(f"invalid Google Duration {value!r}") from exc
    if seconds <= 0:
        raise CloudKMSError("Google Duration must be positive")
    return seconds


def _future_timestamp(duration: str) -> str:
    moment = datetime.now(UTC) + timedelta(seconds=_duration_seconds(duration))
    return moment.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CloudKMSError(f"invalid RFC 3339 timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        raise CloudKMSError("next_rotation_time must include a timezone")
    return parsed


def _not_found(exc: Exception) -> bool:
    if isinstance(exc, CloudKMSNotFound) or type(exc).__name__ == "NotFound":
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code == 404:
        return True
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            return code() == 404 or getattr(code(), "value", None) == 404
        except Exception:
            return False
    return False
