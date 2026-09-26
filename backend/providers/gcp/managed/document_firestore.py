"""Google Firestore Native database, index, TTL, backup, and export lifecycle."""

from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
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

KIND = "document_db"
_API_ROOT = "https://firestore.googleapis.com/v1"
_DATABASE_MUTABLE_FIELDS = {
    "concurrency_mode": "concurrencyMode",
    "point_in_time_recovery": "pointInTimeRecoveryEnablement",
    "app_engine_integration_mode": "appEngineIntegrationMode",
    "delete_protection": "deleteProtectionState",
    "firestore_data_access_mode": "firestoreDataAccessMode",
    "mongodb_compatible_data_access_mode": "mongodbCompatibleDataAccessMode",
}
_DATABASE_IMMUTABLE_FIELDS = {
    "location": "locationId",
    "database_edition": "databaseEdition",
    "realtime_updates_mode": "realtimeUpdatesMode",
}
_DAYS = {
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
}
_JOB_ERROR_STATE = {"NEEDS_REPAIR", "NOT_AVAILABLE"}


class FirestoreError(RuntimeError):
    pass


class FirestoreNotFound(FirestoreError):
    pass


@dataclass(frozen=True)
class FirestoreConfig:
    project_id: str
    location: str
    database_name_prefix: str = "astrolift"
    deletion_protection_default: bool = True
    api_endpoint: str = _API_ROOT
    snapshot_bucket: str = ""
    operation_timeout_seconds: float = 900.0
    poll_interval_seconds: float = 2.0


class FirestoreRestClient:
    """Authenticated request-shaped adapter for the Firestore Admin v1 API."""

    def __init__(self, *, endpoint: str = _API_ROOT, session: Any | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(scopes=("https://www.googleapis.com/auth/cloud-platform",))
            session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._session = session

    def get_database(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def create_database(self, project_id: str, database_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"projects/{project_id}/databases",
            params={"databaseId": database_id},
            json=body,
        )

    def patch_database(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        payload = {"name": name, **body}
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json=payload,
        )

    def delete_database(self, name: str, *, etag: str = "") -> dict[str, Any]:
        params = {"etag": etag} if etag else None
        return self._request("DELETE", name, params=params)

    def clone_database(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"projects/{project_id}/databases:clone", json=body)

    def restore_database(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"projects/{project_id}/databases:restore", json=body)

    def export_documents(self, database_name: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{database_name}:exportDocuments", json=body)

    def import_documents(self, database_name: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{database_name}:importDocuments", json=body)

    def get_operation(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def list_backup_schedules(self, database_name: str) -> list[dict[str, Any]]:
        payload = self._request("GET", f"{database_name}/backupSchedules")
        return list(payload.get("backupSchedules") or [])

    def create_backup_schedule(self, database_name: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{database_name}/backupSchedules", json=body)

    def patch_backup_schedule(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json={"name": name, **body},
        )

    def delete_backup_schedule(self, name: str) -> None:
        self._request("DELETE", name)

    def list_backups(self, project_id: str, location: str) -> list[dict[str, Any]]:
        return self._paged(
            f"projects/{project_id}/locations/{location}/backups",
            key="backups",
        )

    def list_indexes(self, parent: str) -> list[dict[str, Any]]:
        return self._paged(f"{parent}/indexes", key="indexes")

    def create_index(self, parent: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{parent}/indexes", json=body)

    def delete_index(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", name)

    def get_field(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def list_fields(self, parent: str, *, filter_value: str) -> list[dict[str, Any]]:
        return self._paged(
            f"{parent}/fields",
            key="fields",
            base_params={"filter": filter_value},
        )

    def patch_field(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json={"name": name, **body},
        )

    def _paged(
        self,
        resource: str,
        *,
        key: str,
        base_params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params = {**(base_params or {}), "pageSize": "1000"}
            if page_token:
                params["pageToken"] = page_token
            payload = self._request("GET", resource, params=params)
            rows.extend(payload.get(key) or [])
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                return rows

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
            raise FirestoreNotFound(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise FirestoreError(f"Firestore HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class FirestoreNativeDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: FirestoreConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._firestore = client or FirestoreRestClient(endpoint=config.api_endpoint)
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="document_firestore_native",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        return self._provision(spec)

    def _provision(self, spec: ProvisionSpec, *, adopt_restored: bool = False) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_firestore_config"])
        database_id = self._database_id(spec)
        name = self._database_name(database_id)
        try:
            current = self._get_database(name)
            if current is None:
                # Nothing to adopt: a tenant-chosen database_id is free to
                # name a database that does not exist yet.
                operation = self._create_or_clone(database_id, cfg)
                self._wait_operation(operation)
                current = self._firestore.get_database(name)
            elif not adopt_restored and self._requires_adoption(database_id, cfg):
                # It already existed before this call and Firestore databases
                # carry no label an ownership check could read, so there is no
                # way to tell "we created this on an earlier call" from "a
                # tenant pointed database_id at something else's database".
                # Adoption of an existing resource is a separate,
                # operator-authorized operation (#1365); no tenant config flag
                # may grant it (#2021).
                raise FirestoreError(
                    f"database id {database_id!r} already exists and is outside the Astrolift namespace; "
                    "adoption is a separate, operator-authorized operation and cannot be granted by tenant config",
                )
            self._reconcile_database(name, current, cfg)
            self._reconcile_backup_schedules(name, cfg)
            self._reconcile_indexes(name, cfg)
            self._reconcile_fields(name, cfg)
        except Exception as exc:
            return ProvisionResult(False, _handle(database_id), f"provision Firestore: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            _handle(database_id),
            f"Firestore Native database {database_id} reconciled",
            ready=True,
        )

    @driver_op(cloud="gcp", driver="document_firestore_native")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            database_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_firestore_config"])
        name = self._database_name(database_id)
        try:
            current = self._firestore.get_database(name)
            self._reconcile_database(name, current, cfg)
            self._reconcile_backup_schedules(name, cfg)
            self._reconcile_indexes(name, cfg)
            self._reconcile_fields(name, cfg)
        except FirestoreNotFound:
            return UpdateResult(False, spec.handle, f"Firestore database {database_id} not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Firestore: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Firestore Native database {database_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="document_firestore_native",
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
            database_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        name = self._database_name(database_id)
        try:
            current = self._get_database(name)
        except Exception as exc:
            return _deprovision_error(spec.handle, "describe Firestore database", exc)
        if current is None:
            return DeprovisionResult(True, spec.handle, f"Firestore database {database_id} already gone")
        if self._requires_adoption(database_id, cfg) and not cfg.get("delete_adopted"):
            return DeprovisionResult(
                False,
                spec.handle,
                "adopted Firestore databases require delete_adopted=true before deletion",
                ["adopted_resource_guard"],
                retryable=False,
            )
        protected = current.get("deleteProtectionState") == "DELETE_PROTECTION_ENABLED"
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Firestore database {database_id} has delete protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        retained = ""
        if not delete_data:
            try:
                retained = self._retain_data(ServiceHandle(spec.handle))
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"retain Firestore data before delete: {exc}",
                    [str(exc)],
                    retryable=False,
                )
        try:
            if protected:
                operation = self._firestore.patch_database(
                    name,
                    {"deleteProtectionState": "DELETE_PROTECTION_DISABLED"},
                    update_mask=["deleteProtectionState"],
                )
                self._wait_operation(operation)
                current = self._firestore.get_database(name)
            operation = self._firestore.delete_database(name, etag=str(current.get("etag") or ""))
            self._wait_operation(operation)
        except FirestoreNotFound:
            pass
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Firestore database", exc)
        message = f"Firestore database {database_id} deleted"
        if retained:
            message += f"; retained snapshot {retained}"
        return DeprovisionResult(True, spec.handle, message)

    @driver_op(cloud="gcp", driver="document_firestore_native")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            database_id = _parse_handle(handle.handle)
            name = self._database_name(database_id)
            self._firestore.get_database(name)
        except FirestoreNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Firestore database does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Firestore database: {exc}")
        try:
            parent = f"{name}/collectionGroups/-"
            indexes = self._firestore.list_indexes(parent)
            fields = self._firestore.list_fields(parent, filter_value="ttlConfig:*")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Firestore indexes and TTL: {exc}")
        for row in indexes:
            state = str(row.get("state") or "STATE_UNSPECIFIED")
            if state in _JOB_ERROR_STATE:
                return ServiceStatus(handle.handle, "error", f"Firestore index is {state}")
            if state != "READY":
                return ServiceStatus(handle.handle, "provisioning", f"Firestore index is {state}")
        for row in fields:
            state = str((row.get("ttlConfig") or {}).get("state") or "ACTIVE")
            if state in _JOB_ERROR_STATE:
                return ServiceStatus(handle.handle, "error", f"Firestore TTL configuration is {state}")
            if state != "ACTIVE":
                return ServiceStatus(handle.handle, "updating", f"Firestore TTL configuration is {state}")
        return ServiceStatus(
            handle.handle,
            "available",
            f"Firestore Native database available ({len(indexes)} indexes, {len(fields)} TTL fields)",
        )

    @driver_op(cloud="gcp", driver="document_firestore_native")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        database_id = _parse_handle(handle.handle)
        name = self._database_name(database_id)
        current = self._firestore.get_database(name)
        cfg = dict(config or {})
        access_mode = str(cfg.get("access_mode") or "write")
        role = {
            "read": "roles/datastore.viewer",
            "write": "roles/datastore.user",
            "admin": "roles/datastore.owner",
        }.get(access_mode)
        if role is None:
            raise FirestoreError(f"invalid Firestore access_mode {access_mode!r}")
        client_api = str(cfg.get("client_api") or "firestore")
        auth_mode = "workload_identity"
        uri = f"firestore://{self._config.project_id}/{database_id}"
        if client_api == "mongodb":
            if current.get("databaseEdition") != "ENTERPRISE":
                raise FirestoreError("MongoDB client bindings require Firestore ENTERPRISE edition")
            if current.get("mongodbCompatibleDataAccessMode") != "DATA_ACCESS_MODE_ENABLED":
                raise FirestoreError("MongoDB-compatible data access is not enabled on this Firestore database")
            database_uid = str(current.get("uid") or "")
            location = str(current.get("locationId") or "")
            if not database_uid or not location:
                raise FirestoreError("Firestore did not return the UID and location required for a MongoDB endpoint")
            auth_mode = "mongodb_oidc_gcp"
            uri = (
                f"mongodb://{database_uid}.{location}.firestore.goog:443/{database_id}"
                "?loadBalanced=true&tls=true&retryWrites=false&authMechanism=MONGODB-OIDC"
                "&authMechanismProperties=ENVIRONMENT:gcp,TOKEN_RESOURCE:FIRESTORE"
            )
        elif client_api != "firestore":
            raise FirestoreError(f"invalid Firestore client_api {client_api!r}")
        return Binding(
            env_vars={
                "DOCDB_URI": ValueRef(literal=uri),
                "DOCDB_DB": ValueRef(literal=database_id),
                "DOCDB_AUTH_MODE": ValueRef(literal=auth_mode),
                "DOCDB_TLS": ValueRef(literal="1"),
                "DOCDB_RESOURCE_ARN": ValueRef(literal=name),
                "GCP_FIRESTORE_PROJECT": ValueRef(literal=self._config.project_id),
                "GCP_FIRESTORE_DATABASE": ValueRef(literal=database_id),
                "GCP_FIRESTORE_UID": ValueRef(literal=str(current.get("uid") or "")),
                "GCP_FIRESTORE_LOCATION": ValueRef(literal=str(current.get("locationId") or self._config.location)),
                "GCP_FIRESTORE_EDITION": ValueRef(literal=str(current.get("databaseEdition") or "STANDARD")),
                "GCP_FIRESTORE_CLIENT_API": ValueRef(literal=client_api),
            },
            iam_grants=[Grant(f"projects/{self._config.project_id}", [role])],
            notes=(
                f"Firestore {access_mode} access through Google workload identity/OIDC; "
                "no database password is emitted."
            ),
        )

    @driver_op(cloud="gcp", driver="document_firestore_native")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        database_id = _parse_handle(handle.handle)
        database_name = self._database_name(database_id)
        self._firestore.get_database(database_name)
        bucket = _snapshot_bucket_uri(self._config.snapshot_bucket)
        if not bucket:
            raise FirestoreError(
                "Firestore on-demand snapshots use managed exports; configure firestore_snapshot_bucket",
            )
        timestamp = datetime.now(UTC)
        output = f"{bucket}/astrolift/firestore/{database_id}/{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}"
        completed = self._wait_operation(
            self._firestore.export_documents(database_name, {"outputUriPrefix": output}),
        )
        response = completed.get("response") or {}
        snapshot_id = str(response.get("outputUriPrefix") or output)
        return SnapshotHandle(handle.handle, snapshot_id, timestamp.isoformat())

    @driver_op(cloud="gcp", driver="document_firestore_native")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        cfg = dict(target.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_firestore_config"])
        database_id = self._database_id(target)
        database_name = self._database_name(database_id)
        try:
            if self._get_database(database_name) is not None:
                raise FirestoreError(f"restore target database {database_id} already exists")
            if snapshot.snapshot_id.startswith("projects/") and "/backups/" in snapshot.snapshot_id:
                body: dict[str, Any] = {
                    "databaseId": database_id,
                    "backup": snapshot.snapshot_id,
                }
                if cfg.get("restore_encryption_config"):
                    body["encryptionConfig"] = _camelize(cfg["restore_encryption_config"])
                if cfg.get("tags"):
                    body["tags"] = dict(cfg["tags"])
                self._wait_operation(self._firestore.restore_database(self._config.project_id, body))
            elif snapshot.snapshot_id.startswith("gs://"):
                created = self.provision(target)
                if not created.ok:
                    return created
                self._wait_operation(
                    self._firestore.import_documents(
                        database_name,
                        {"inputUriPrefix": snapshot.snapshot_id},
                    ),
                )
                return ProvisionResult(
                    True,
                    _handle(database_id),
                    f"Firestore database {database_id} restored from export",
                    ready=True,
                )
            else:
                raise FirestoreError("Firestore snapshot must be a backup resource name or gs:// export URI")
            # The restoreDatabase call above already created the target
            # inside this operation, so the namespace guard -- which exists
            # to keep a tenant pointing database_id at a database it did not
            # create -- does not apply to the one this restore just made.
            reconciled = self._provision(target, adopt_restored=True)
            if not reconciled.ok:
                return reconciled
            return replace(
                reconciled,
                message=f"Firestore database {database_id} restored from backup",
            )
        except Exception as exc:
            return ProvisionResult(False, _handle(database_id), f"restore Firestore: {exc}", [str(exc)])

    @driver_op(cloud="gcp", driver="document_firestore_native", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        string_map = {"type": "object", "additionalProperties": {"type": "string"}}
        source_encryption = {
            "type": "object",
            "properties": {
                "use_source_encryption": {"type": "object", "additionalProperties": False},
                "google_default_encryption": {"type": "object", "additionalProperties": False},
                "customer_managed_encryption": {
                    "type": "object",
                    "required": ["kms_key_name"],
                    "properties": {"kms_key_name": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "oneOf": [
                {"required": ["use_source_encryption"]},
                {"required": ["google_default_encryption"]},
                {"required": ["customer_managed_encryption"]},
            ],
            "additionalProperties": False,
        }
        scheduling = {
            "type": "object",
            "required": ["recurrence", "retention"],
            "properties": {
                "recurrence": {"type": "string", "enum": ["daily", "weekly"]},
                "day_of_week": {"type": "string", "enum": sorted(_DAYS)},
                "retention": {"type": "string", "pattern": "^[0-9]+s$"},
            },
            "additionalProperties": False,
        }
        index_field = {
            "type": "object",
            "required": ["field_path"],
            "properties": {
                "field_path": {"type": "string"},
                "order": {"type": "string", "enum": ["ASCENDING", "DESCENDING"]},
                "array_config": {"type": "string", "enum": ["CONTAINS"]},
                "vector_config": {
                    "type": "object",
                    "required": ["dimension", "flat"],
                    "properties": {
                        "dimension": {"type": "integer", "minimum": 1, "maximum": 2048},
                        "flat": {"type": "object", "additionalProperties": False},
                    },
                    "additionalProperties": False,
                },
                "search_config": {
                    "type": "object",
                    "properties": {
                        "geo_spec": {
                            "type": "object",
                            "properties": {"geo_json_indexing_disabled": {"type": "boolean"}},
                            "additionalProperties": False,
                        },
                        "text_spec": {
                            "type": "object",
                            "required": ["index_specs"],
                            "properties": {
                                "index_specs": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "required": ["match_type", "index_type"],
                                        "properties": {
                                            "match_type": {"const": "MATCH_GLOBALLY"},
                                            "index_type": {"const": "TOKENIZED"},
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                    "minProperties": 1,
                    "additionalProperties": False,
                },
            },
            "oneOf": [
                {"required": ["order"]},
                {"required": ["array_config"]},
                {"required": ["vector_config"]},
                {"required": ["search_config"]},
            ],
            "additionalProperties": False,
        }
        composite_index = {
            "type": "object",
            "required": ["collection_group", "query_scope", "fields"],
            "properties": {
                "collection_group": {"type": "string"},
                "query_scope": {
                    "type": "string",
                    "enum": ["COLLECTION", "COLLECTION_GROUP"],
                },
                "api_scope": {
                    "type": "string",
                    "enum": ["ANY_API", "MONGODB_COMPATIBLE_API"],
                },
                "density": {"type": "string", "enum": ["SPARSE_ALL", "SPARSE_ANY", "DENSE"]},
                "multikey": {"type": "boolean"},
                "shard_count": {"type": "integer", "minimum": 0},
                "unique": {"type": "boolean"},
                "search_index_options": {
                    "type": "object",
                    "properties": {
                        "text_language": {"type": "string"},
                        "text_language_override_field_path": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "fields": {"type": "array", "minItems": 1, "maxItems": 100, "items": index_field},
            },
            "additionalProperties": False,
        }
        field_override = {
            "type": "object",
            "required": ["collection_group", "field_path"],
            "properties": {
                "collection_group": {"type": "string"},
                "field_path": {"type": "string"},
                "index_config": {"type": "object", "additionalProperties": True},
                "inherit_index_config": {"type": "boolean"},
                "ttl": {
                    "type": "object",
                    "required": ["enabled"],
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "expiration_offset": {"type": "string", "pattern": "^[0-9]+s$"},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "minLength": 4,
                    "maxLength": 63,
                    "description": (
                        "Custom database id. Only usable to create a new database: an id that "
                        "already exists and is outside the Astrolift namespace is refused, since "
                        "Firestore databases carry no label an ownership check could verify."
                    ),
                },
                "delete_adopted": {"type": "boolean", "default": False},
                "access_mode": {"type": "string", "enum": ["read", "write", "admin"], "default": "write"},
                "client_api": {"type": "string", "enum": ["firestore", "mongodb"], "default": "firestore"},
                "location": {"type": "string"},
                "database_edition": {"type": "string", "enum": ["STANDARD", "ENTERPRISE"]},
                "concurrency_mode": {
                    "type": "string",
                    "enum": ["OPTIMISTIC", "PESSIMISTIC"],
                },
                "point_in_time_recovery": {
                    "type": "string",
                    "enum": ["POINT_IN_TIME_RECOVERY_ENABLED", "POINT_IN_TIME_RECOVERY_DISABLED"],
                },
                "app_engine_integration_mode": {"type": "string", "enum": ["ENABLED", "DISABLED"]},
                "delete_protection": {"type": "boolean"},
                "kms_key_name": {"type": "string"},
                "tags": string_map,
                "realtime_updates_mode": {
                    "type": "string",
                    "enum": ["REALTIME_UPDATES_MODE_ENABLED", "REALTIME_UPDATES_MODE_DISABLED"],
                },
                "firestore_data_access_mode": {
                    "type": "string",
                    "enum": ["DATA_ACCESS_MODE_ENABLED", "DATA_ACCESS_MODE_DISABLED"],
                },
                "mongodb_compatible_data_access_mode": {
                    "type": "string",
                    "enum": ["DATA_ACCESS_MODE_ENABLED", "DATA_ACCESS_MODE_DISABLED"],
                },
                "clone_source_database": {"type": "string"},
                "clone_snapshot_time": {"type": "string", "format": "date-time"},
                "clone_encryption_config": source_encryption,
                "restore_encryption_config": source_encryption,
                "backup_schedules": {"type": "array", "maxItems": 2, "items": scheduling},
                "prune_backup_schedules": {"type": "boolean", "default": False},
                "replace_backup_schedules": {"type": "boolean", "default": False},
                "composite_indexes": {"type": "array", "items": composite_index},
                "prune_composite_indexes": {"type": "boolean", "default": False},
                "delete_composite_indexes": {"type": "array", "items": {"type": "string"}},
                "field_overrides": {"type": "array", "items": field_override},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="document_firestore_native", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "DOCDB_URI": "Portable Firestore Native or MongoDB-compatible connection URI",
                "DOCDB_DB": "Firestore database id",
                "DOCDB_AUTH_MODE": "workload_identity or mongodb_oidc_gcp",
                "DOCDB_TLS": "1",
                "DOCDB_RESOURCE_ARN": "Full Firestore database resource name",
                "GCP_FIRESTORE_PROJECT": "Google Cloud project id",
                "GCP_FIRESTORE_DATABASE": "Firestore database id",
                "GCP_FIRESTORE_UID": "Immutable Firestore database UID",
                "GCP_FIRESTORE_LOCATION": "Firestore location",
                "GCP_FIRESTORE_EDITION": "STANDARD or ENTERPRISE",
                "GCP_FIRESTORE_CLIENT_API": "firestore or mongodb",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "client_api",
            "app_engine_integration_mode",
            "backup_schedules",
            "composite_indexes",
            "concurrency_mode",
            "delete_composite_indexes",
            "delete_adopted",
            "delete_protection",
            "field_overrides",
            "firestore_data_access_mode",
            "mongodb_compatible_data_access_mode",
            "point_in_time_recovery",
            "prune_backup_schedules",
            "prune_composite_indexes",
            "replace_backup_schedules",
        ]

    def _create_or_clone(self, database_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
        if cfg.get("clone_source_database"):
            source = str(cfg["clone_source_database"])
            if not source.startswith("projects/"):
                source = self._database_name(source)
            body: dict[str, Any] = {
                "databaseId": database_id,
                "pitrSnapshot": {
                    "database": source,
                    "snapshotTime": cfg["clone_snapshot_time"],
                },
            }
            if cfg.get("clone_encryption_config"):
                body["encryptionConfig"] = _camelize(cfg["clone_encryption_config"])
            if cfg.get("tags"):
                body["tags"] = dict(cfg["tags"])
            return self._firestore.clone_database(self._config.project_id, body)
        return self._firestore.create_database(
            self._config.project_id,
            database_id,
            self._database_document(database_id, cfg),
        )

    def _database_document(self, database_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
        edition = str(cfg.get("database_edition") or "STANDARD")
        body: dict[str, Any] = {
            "locationId": str(cfg.get("location") or self._config.location),
            "type": "FIRESTORE_NATIVE",
            "databaseEdition": edition,
            "deleteProtectionState": _delete_protection_state(
                cfg.get("delete_protection", self._config.deletion_protection_default),
            ),
        }
        for source, target in {**_DATABASE_MUTABLE_FIELDS, **_DATABASE_IMMUTABLE_FIELDS}.items():
            if source in cfg and source not in {"location", "database_edition", "delete_protection"}:
                body[target] = cfg[source]
        if cfg.get("kms_key_name"):
            body["cmekConfig"] = {"kmsKeyName": str(cfg["kms_key_name"])}
        if cfg.get("tags"):
            body["tags"] = dict(cfg["tags"])
        return body

    def _reconcile_database(self, name: str, current: dict[str, Any], cfg: dict[str, Any]) -> None:
        if str(current.get("type") or "") != "FIRESTORE_NATIVE":
            raise FirestoreError(f"database {name} is not FIRESTORE_NATIVE")
        desired = self._database_document(name.rsplit("/", 1)[-1], cfg)
        for source, target in _DATABASE_IMMUTABLE_FIELDS.items():
            if source not in cfg:
                continue
            if str(current.get(target) or "") != str(desired.get(target) or ""):
                raise FirestoreError(f"Firestore field {source!r} is immutable; create a replacement database")
        if "kms_key_name" in cfg:
            desired_key = str((desired.get("cmekConfig") or {}).get("kmsKeyName") or "")
            current_key = str((current.get("cmekConfig") or {}).get("kmsKeyName") or "")
            if desired_key != current_key:
                raise FirestoreError("Firestore field 'kms_key_name' is immutable; create a replacement database")
        mutable: dict[str, Any] = {}
        for source, target in _DATABASE_MUTABLE_FIELDS.items():
            if source == "delete_protection":
                value = desired[target]
            elif source not in cfg:
                continue
            else:
                value = desired[target]
            if current.get(target) != value:
                mutable[target] = value
        if mutable:
            self._wait_operation(
                self._firestore.patch_database(name, mutable, update_mask=sorted(mutable)),
            )

    def _reconcile_backup_schedules(self, database_name: str, cfg: dict[str, Any]) -> None:
        if "backup_schedules" not in cfg:
            return
        existing_rows = self._firestore.list_backup_schedules(database_name)
        existing = {_schedule_kind(row): row for row in existing_rows}
        desired = {str(row["recurrence"]): row for row in cfg.get("backup_schedules") or []}
        for recurrence, declaration in desired.items():
            body = _backup_schedule_document(declaration)
            current = existing.get(recurrence)
            if current is None:
                self._firestore.create_backup_schedule(database_name, body)
                continue
            if recurrence == "weekly":
                actual_day = str((current.get("weeklyRecurrence") or {}).get("day") or "")
                desired_day = str((body.get("weeklyRecurrence") or {}).get("day") or "")
                if actual_day != desired_day:
                    if not cfg.get("replace_backup_schedules"):
                        raise FirestoreError(
                            "weekly backup recurrence is immutable; set replace_backup_schedules=true",
                        )
                    self._firestore.delete_backup_schedule(str(current["name"]))
                    self._firestore.create_backup_schedule(database_name, body)
                    continue
            if current.get("retention") != body["retention"]:
                self._firestore.patch_backup_schedule(
                    str(current["name"]),
                    {"retention": body["retention"]},
                    update_mask=["retention"],
                )
        if cfg.get("prune_backup_schedules"):
            for recurrence, row in existing.items():
                if recurrence not in desired:
                    self._firestore.delete_backup_schedule(str(row["name"]))

    def _reconcile_indexes(self, database_name: str, cfg: dict[str, Any]) -> None:
        if "composite_indexes" not in cfg and "delete_composite_indexes" not in cfg:
            return
        desired_by_parent: dict[str, list[dict[str, Any]]] = {}
        for declaration in cfg.get("composite_indexes") or []:
            parent = f"{database_name}/collectionGroups/{declaration['collection_group']}"
            desired_by_parent.setdefault(parent, []).append(_index_document(declaration))
        existing = self._firestore.list_indexes(f"{database_name}/collectionGroups/-")
        desired_rows_by_group: dict[str, list[dict[str, Any]]] = {}
        for parent, desired_rows in desired_by_parent.items():
            collection_group = parent.rsplit("/", 1)[-1]
            desired_rows_by_group[collection_group] = desired_rows
            for document in desired_rows:
                if not any(
                    _index_collection_group(row) == collection_group and _index_matches(row, document)
                    for row in existing
                ):
                    self._wait_operation(self._firestore.create_index(parent, document))
        if cfg.get("prune_composite_indexes"):
            for row in existing:
                collection_group = _index_collection_group(row)
                if not any(_index_matches(row, desired) for desired in desired_rows_by_group.get(collection_group, [])):
                    self._delete_index_ignoring_not_found(str(row["name"]))
        for item in cfg.get("delete_composite_indexes") or []:
            name = str(item)
            if not name.startswith("projects/"):
                raise FirestoreError("delete_composite_indexes entries must be full Firestore index resource names")
            if not name.startswith(f"{database_name}/collectionGroups/"):
                raise FirestoreError("refusing to delete an index outside this Firestore database")
            self._delete_index_ignoring_not_found(name)

    def _reconcile_fields(self, database_name: str, cfg: dict[str, Any]) -> None:
        if "field_overrides" not in cfg:
            return
        for declaration in cfg.get("field_overrides") or []:
            name = (
                f"{database_name}/collectionGroups/{declaration['collection_group']}/fields/{declaration['field_path']}"
            )
            body, update_mask = _field_document(declaration)
            if not update_mask:
                continue
            try:
                current = self._firestore.get_field(name)
            except FirestoreNotFound:
                current = {}
            if _field_matches(current, body, update_mask):
                continue
            self._wait_operation(
                self._firestore.patch_field(name, body, update_mask=update_mask),
            )

    def _wait_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation or operation.get("done") is True:
            return _raise_operation_error(operation)
        name = str(operation.get("name") or "")
        if not name:
            return operation
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        current = operation
        while current.get("done") is not True:
            if self._monotonic() >= deadline:
                raise FirestoreError(f"Firestore operation {name} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._firestore.get_operation(name)
        return _raise_operation_error(current)

    def _retain_data(self, handle: ServiceHandle) -> str:
        database_id = _parse_handle(handle.handle)
        database_name = self._database_name(database_id)
        try:
            backups = self._firestore.list_backups(self._config.project_id, "-")
        except Exception as list_error:
            if self._config.snapshot_bucket:
                try:
                    return self.snapshot(handle).snapshot_id
                except Exception as export_error:
                    raise FirestoreError(
                        f"list scheduled backups failed ({list_error}); managed export failed ({export_error})",
                    ) from export_error
            raise FirestoreError(f"list scheduled backups failed: {list_error}") from list_error
        ready = [row for row in backups if row.get("database") == database_name and row.get("state") == "READY"]
        if self._config.snapshot_bucket:
            try:
                return self.snapshot(handle).snapshot_id
            except Exception:
                if not ready:
                    raise
                latest = max(ready, key=lambda row: str(row.get("snapshotTime") or ""))
                return str(latest["name"])
        if ready:
            latest = max(ready, key=lambda row: str(row.get("snapshotTime") or ""))
            return str(latest["name"])
        return self.snapshot(handle).snapshot_id

    def _get_database(self, name: str) -> dict[str, Any] | None:
        try:
            return self._firestore.get_database(name)
        except FirestoreNotFound:
            return None

    def _database_id(self, spec: ProvisionSpec) -> str:
        explicit = str((spec.config or {}).get("database_id") or "")
        if explicit:
            _validate_database_id(explicit)
            return explicit
        raw = "-".join(
            part
            for part in (
                self._config.database_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "documents",
            )
            if part
        )
        return _database_id(raw)

    def _database_name(self, database_id: str) -> str:
        return f"projects/{self._config.project_id}/databases/{database_id}"

    def _requires_adoption(self, database_id: str, cfg: dict[str, Any]) -> bool:
        """Whether ``database_id``, if it already exists, would need an operator adoption to use.

        Only ever consulted when a database was already there before this
        call (see ``_provision``); a name that is about to be created fresh
        has nothing to adopt. A tenant-supplied ``database_id`` and the
        well-known ``(default)`` database are always outside the namespace,
        because Firestore databases carry no label a later call could read
        back to prove "we created this one already".
        """
        if cfg.get("database_id"):
            return True
        namespace = _database_id_prefix(self._config.database_name_prefix)
        managed_shape = database_id.startswith(f"{namespace}-") and bool(re.search(r"-[0-9a-f]{8}$", database_id))
        return database_id == "(default)" or not managed_shape

    def _delete_index_ignoring_not_found(self, name: str) -> None:
        with suppress(FirestoreNotFound):
            self._wait_operation(self._firestore.delete_index(name))

    def _validate_config(self, cfg: dict[str, Any]) -> str | None:
        if not str(cfg.get("location") or self._config.location):
            return "Firestore location is required"
        if cfg.get("database_id"):
            try:
                _validate_database_id(str(cfg["database_id"]))
            except ValueError as exc:
                return str(exc)
        if str(cfg.get("access_mode") or "write") not in {"read", "write", "admin"}:
            return "access_mode must be read, write, or admin"
        if str(cfg.get("client_api") or "firestore") not in {"firestore", "mongodb"}:
            return "client_api must be firestore or mongodb"
        edition = str(cfg.get("database_edition") or "STANDARD")
        if edition not in {"STANDARD", "ENTERPRISE"}:
            return "database_edition must be STANDARD or ENTERPRISE"
        if cfg.get("database_id") == "(default)" and edition != "STANDARD":
            return "the (default) Firestore database supports STANDARD edition only"
        enum_fields = {
            "concurrency_mode": {"OPTIMISTIC", "PESSIMISTIC"},
            "point_in_time_recovery": {
                "POINT_IN_TIME_RECOVERY_ENABLED",
                "POINT_IN_TIME_RECOVERY_DISABLED",
            },
            "app_engine_integration_mode": {"ENABLED", "DISABLED"},
            "realtime_updates_mode": {
                "REALTIME_UPDATES_MODE_ENABLED",
                "REALTIME_UPDATES_MODE_DISABLED",
            },
            "firestore_data_access_mode": {"DATA_ACCESS_MODE_ENABLED", "DATA_ACCESS_MODE_DISABLED"},
            "mongodb_compatible_data_access_mode": {
                "DATA_ACCESS_MODE_ENABLED",
                "DATA_ACCESS_MODE_DISABLED",
            },
        }
        for field, choices in enum_fields.items():
            if field in cfg and cfg[field] not in choices:
                return f"{field} must be one of {', '.join(sorted(choices))}"
        if edition == "STANDARD" and cfg.get("mongodb_compatible_data_access_mode") == "DATA_ACCESS_MODE_ENABLED":
            return "MongoDB-compatible data access requires ENTERPRISE edition"
        if cfg.get("client_api") == "mongodb" and edition != "ENTERPRISE":
            return "MongoDB client bindings require ENTERPRISE edition"
        if (
            cfg.get("client_api") == "mongodb"
            and cfg.get("mongodb_compatible_data_access_mode") == "DATA_ACCESS_MODE_DISABLED"
        ):
            return "MongoDB client bindings require MongoDB-compatible data access"
        clone_source = cfg.get("clone_source_database")
        clone_time = cfg.get("clone_snapshot_time")
        if bool(clone_source) != bool(clone_time):
            return "clone_source_database and clone_snapshot_time must be set together"
        for field in ("clone_encryption_config", "restore_encryption_config"):
            encryption = cfg.get(field)
            if encryption is None:
                continue
            if not isinstance(encryption, dict):
                return f"{field} must be an object"
            choices = {
                "use_source_encryption",
                "google_default_encryption",
                "customer_managed_encryption",
            }
            selected = choices.intersection(encryption)
            if len(selected) != 1 or set(encryption) != selected:
                return f"{field} must select exactly one supported encryption mode"
            customer_managed = encryption.get("customer_managed_encryption")
            if customer_managed is not None and (
                not isinstance(customer_managed, dict) or not customer_managed.get("kms_key_name")
            ):
                return f"{field}.customer_managed_encryption.kms_key_name is required"
        schedules = cfg.get("backup_schedules")
        if schedules is not None and not isinstance(schedules, list):
            return "backup_schedules must be an array"
        if len(schedules or []) > 2:
            return "Firestore supports at most one daily and one weekly backup schedule"
        recurrences: set[str] = set()
        for index, row in enumerate(schedules or []):
            if not isinstance(row, dict):
                return f"backup_schedules[{index}] must be an object"
            recurrence = str(row.get("recurrence") or "")
            if recurrence not in {"daily", "weekly"}:
                return f"backup_schedules[{index}].recurrence must be daily or weekly"
            if recurrence in recurrences:
                return f"backup_schedules contains more than one {recurrence} schedule"
            recurrences.add(recurrence)
            day = row.get("day_of_week")
            if recurrence == "weekly" and day not in _DAYS:
                return f"backup_schedules[{index}].day_of_week is required for weekly recurrence"
            if recurrence == "daily" and day:
                return f"backup_schedules[{index}].day_of_week is only valid for weekly recurrence"
            retention = str(row.get("retention") or "")
            seconds = _duration_seconds(retention)
            if seconds is None or seconds < 1 or seconds > 8_467_200:
                return f"backup_schedules[{index}].retention must be 1s through 8467200s"
        indexes = cfg.get("composite_indexes")
        if indexes is not None and not isinstance(indexes, list):
            return "composite_indexes must be an array"
        signatures: set[str] = set()
        for index, row in enumerate(indexes or []):
            if not isinstance(row, dict) or not row.get("collection_group") or not row.get("fields"):
                return f"composite_indexes[{index}] requires collection_group and fields"
            if len(row.get("fields") or []) > 100:
                return f"composite_indexes[{index}] supports at most 100 fields"
            if row.get("query_scope") not in {"COLLECTION", "COLLECTION_GROUP"}:
                return f"composite_indexes[{index}].query_scope is required and must be valid"
            if row.get("api_scope", "ANY_API") not in {
                "ANY_API",
                "MONGODB_COMPATIBLE_API",
            }:
                return f"composite_indexes[{index}].api_scope is invalid"
            if row.get("density") not in {None, "SPARSE_ALL", "SPARSE_ANY", "DENSE"}:
                return f"composite_indexes[{index}].density is invalid"
            if edition == "STANDARD" and row.get("density") not in {None, "SPARSE_ALL"}:
                return f"composite_indexes[{index}] STANDARD edition only supports SPARSE_ALL density"
            if row.get("api_scope") == "MONGODB_COMPATIBLE_API" and edition != "ENTERPRISE":
                return f"composite_indexes[{index}] MongoDB API scope requires ENTERPRISE edition"
            if row.get("api_scope", "ANY_API") != "MONGODB_COMPATIBLE_API" and any(
                key in row for key in ("multikey", "unique", "search_index_options")
            ):
                return (
                    f"composite_indexes[{index}] multikey, unique, and search_index_options "
                    "require MONGODB_COMPATIBLE_API"
                )
            if "shard_count" in row and (
                isinstance(row["shard_count"], bool)
                or not isinstance(row["shard_count"], int)
                or row["shard_count"] < 0
            ):
                return f"composite_indexes[{index}].shard_count must be a non-negative integer"
            for field_name in ("multikey", "unique"):
                if field_name in row and not isinstance(row[field_name], bool):
                    return f"composite_indexes[{index}].{field_name} must be a boolean"
            search_options = row.get("search_index_options")
            if search_options is not None and (
                not isinstance(search_options, dict)
                or set(search_options) - {"text_language", "text_language_override_field_path"}
            ):
                return f"composite_indexes[{index}].search_index_options is invalid"
            for field_index, field in enumerate(row.get("fields") or []):
                if not isinstance(field, dict) or not field.get("field_path"):
                    return f"composite_indexes[{index}].fields[{field_index}].field_path is required"
                modes = [key for key in ("order", "array_config", "vector_config", "search_config") if key in field]
                if len(modes) != 1:
                    return f"composite_indexes[{index}].fields[{field_index}] requires exactly one index mode"
                vector = field.get("vector_config")
                if vector is not None:
                    if (
                        not isinstance(vector, dict)
                        or isinstance(vector.get("dimension"), bool)
                        or not isinstance(vector.get("dimension"), int)
                    ):
                        return f"composite_indexes[{index}].fields[{field_index}].vector_config.dimension is required"
                    if not 1 <= vector["dimension"] <= 2_048:
                        return (
                            f"composite_indexes[{index}].fields[{field_index}].vector_config.dimension must be 1-2048"
                        )
                    if vector.get("flat") != {}:
                        return (
                            f"composite_indexes[{index}].fields[{field_index}]"
                            ".vector_config.flat must be an empty object"
                        )
                search = field.get("search_config")
                if search is not None:
                    if not isinstance(search, dict) or not {
                        "geo_spec",
                        "text_spec",
                    }.intersection(search):
                        return (
                            f"composite_indexes[{index}].fields[{field_index}]"
                            ".search_config requires geo_spec or text_spec"
                        )
                    text_spec = search.get("text_spec")
                    if text_spec is not None and (not isinstance(text_spec, dict) or not text_spec.get("index_specs")):
                        return (
                            f"composite_indexes[{index}].fields[{field_index}]"
                            ".search_config.text_spec.index_specs is required"
                        )
                    for text_index in (text_spec or {}).get("index_specs", []):
                        if not isinstance(text_index, dict) or text_index.get("match_type") != "MATCH_GLOBALLY":
                            return (
                                f"composite_indexes[{index}].fields[{field_index}]"
                                ".search_config text match_type must be MATCH_GLOBALLY"
                            )
                        if text_index.get("index_type") != "TOKENIZED":
                            return (
                                f"composite_indexes[{index}].fields[{field_index}]"
                                ".search_config text index_type must be TOKENIZED"
                            )
            signature = f"{row['collection_group']}:{_index_signature(_index_document(row))}"
            if signature in signatures:
                return f"composite_indexes[{index}] duplicates an earlier index"
            signatures.add(signature)
        fields = cfg.get("field_overrides")
        if fields is not None and not isinstance(fields, list):
            return "field_overrides must be an array"
        names: set[tuple[str, str]] = set()
        for index, row in enumerate(fields or []):
            if not isinstance(row, dict) or not row.get("collection_group") or not row.get("field_path"):
                return f"field_overrides[{index}] requires collection_group and field_path"
            key = (str(row["collection_group"]), str(row["field_path"]))
            if key in names:
                return f"field_overrides[{index}] duplicates an earlier field"
            names.add(key)
            if row.get("inherit_index_config") and "index_config" in row:
                return f"field_overrides[{index}] cannot set index_config and inherit_index_config"
            ttl = row.get("ttl")
            if ttl is not None and not isinstance(ttl, dict):
                return f"field_overrides[{index}].ttl must be an object"
            if ttl and ttl.get("expiration_offset"):
                seconds = _duration_seconds(str(ttl["expiration_offset"]))
                if seconds is None or seconds > 2_147_483_647:
                    return f"field_overrides[{index}].ttl.expiration_offset is invalid"
        return None


def _delete_protection_state(value: Any) -> str:
    return "DELETE_PROTECTION_ENABLED" if bool(value) else "DELETE_PROTECTION_DISABLED"


def _database_id(value: str) -> str:
    normalized = _database_id_prefix(value)[:54].rstrip("-")
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    result = f"{normalized}-{digest}"
    _validate_database_id(result)
    return result


def _database_id_prefix(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not normalized or not normalized[0].isalpha():
        normalized = f"a-{normalized}"
    return normalized[:54].rstrip("-")


def _validate_database_id(value: str) -> None:
    if value == "(default)":
        return
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,61}[a-z0-9]", value):
        raise ValueError(
            "Firestore database_id must be 4-63 lowercase letters, digits, or hyphens and start with a letter",
        )
    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
        raise ValueError("Firestore database_id must not be UUID-shaped")


def _handle(database_id: str) -> str:
    return f"{KIND}/{database_id}"


def _parse_handle(handle: str) -> str:
    kind, separator, database_id = handle.partition("/")
    if separator != "/" or kind != KIND or not database_id or "/" in database_id:
        raise ValueError(f"invalid Firestore handle {handle!r}; expected 'document_db/<database-id>'")
    _validate_database_id(database_id)
    return database_id


def _schedule_kind(row: dict[str, Any]) -> str:
    if "dailyRecurrence" in row:
        return "daily"
    if "weeklyRecurrence" in row:
        return "weekly"
    return "unknown"


def _backup_schedule_document(row: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"retention": str(row["retention"])}
    if row["recurrence"] == "daily":
        body["dailyRecurrence"] = {}
    else:
        body["weeklyRecurrence"] = {"day": str(row["day_of_week"])}
    return body


def _index_document(row: dict[str, Any]) -> dict[str, Any]:
    fields = []
    for field in row.get("fields") or []:
        field_document = {"fieldPath": str(field["field_path"])}
        for source, target in (
            ("order", "order"),
            ("array_config", "arrayConfig"),
            ("vector_config", "vectorConfig"),
            ("search_config", "searchConfig"),
        ):
            if source in field:
                field_document[target] = _camelize(field[source])
        fields.append(field_document)
    index_document: dict[str, Any] = {"fields": fields}
    for source, target in (
        ("query_scope", "queryScope"),
        ("api_scope", "apiScope"),
        ("density", "density"),
        ("multikey", "multikey"),
        ("shard_count", "shardCount"),
        ("unique", "unique"),
        ("search_index_options", "searchIndexOptions"),
    ):
        if source in row:
            index_document[target] = _camelize(row[source])
    return index_document


def _index_signature(row: dict[str, Any]) -> str:
    fields = list(row.get("fields") or [])
    normalized = {
        key: _strip_index_outputs(fields if key == "fields" else row[key])
        for key in (
            "fields",
            "queryScope",
            "apiScope",
            "density",
            "multikey",
            "shardCount",
            "unique",
            "searchIndexOptions",
        )
        if key in row
    }
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _index_matches(current: dict[str, Any], desired: dict[str, Any]) -> bool:
    """Compare a declaration with provider output without hiding choices.

    Firestore materializes defaults such as ``apiScope`` and ``density`` in
    responses even when the create request omitted them.  Provider-only
    defaults must not trigger duplicate indexes, while every option the user
    explicitly declared remains part of the comparison.
    """

    current_fields = list(current.get("fields") or [])
    desired_fields = list(desired.get("fields") or [])
    desired_has_name = bool(desired_fields and desired_fields[-1].get("fieldPath") == "__name__")
    if not desired_has_name and current_fields and current_fields[-1].get("fieldPath") == "__name__":
        current_fields = current_fields[:-1]
    if not _declaration_matches(
        _strip_index_outputs(current_fields),
        _strip_index_outputs(desired_fields),
    ):
        return False
    for field, expected in desired.items():
        if field == "fields":
            continue
        if not _declaration_matches(
            _strip_index_outputs(current.get(field)),
            _strip_index_outputs(expected),
        ):
            return False
    return True


def _index_collection_group(row: dict[str, Any]) -> str:
    name = str(row.get("name") or "")
    marker = "/collectionGroups/"
    if marker not in name:
        raise FirestoreError(f"Firestore index response is missing its collection group: {name!r}")
    return name.split(marker, 1)[1].split("/", 1)[0]


def _strip_index_outputs(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_index_outputs(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _strip_index_outputs(item)
        for key, item in value.items()
        if key not in {"name", "state", "ancestorField", "usesAncestorConfig", "reverting"}
    }


def _declaration_matches(actual: Any, desired: Any) -> bool:
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(
            key in actual and _declaration_matches(actual[key], value) for key, value in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(desired)
            and all(
                _declaration_matches(actual_item, desired_item)
                for actual_item, desired_item in zip(actual, desired, strict=True)
            )
        )
    return actual == desired


def _field_document(row: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    body: dict[str, Any] = {}
    update_mask: list[str] = []
    if row.get("inherit_index_config"):
        update_mask.append("indexConfig")
    elif "index_config" in row:
        body["indexConfig"] = _camelize(row["index_config"])
        update_mask.append("indexConfig")
    if "ttl" in row:
        ttl = row["ttl"] or {}
        if ttl.get("enabled"):
            body["ttlConfig"] = {}
            if ttl.get("expiration_offset"):
                body["ttlConfig"]["expirationOffset"] = str(ttl["expiration_offset"])
        update_mask.append("ttlConfig")
    return body, sorted(update_mask)


def _field_matches(current: dict[str, Any], desired: dict[str, Any], update_mask: list[str]) -> bool:
    for field in update_mask:
        if field == "indexConfig":
            if field not in desired:
                actual_config = current.get(field) or {}
                if actual_config.get("usesAncestorConfig") is True and not actual_config.get("reverting"):
                    continue
            actual = _strip_index_outputs(current.get(field)) if field in current else None
            expected = _strip_index_outputs(desired.get(field)) if field in desired else None
        elif field == "ttlConfig":
            actual_ttl = current.get(field) if field in current else None
            if isinstance(actual_ttl, dict):
                actual = (
                    {"expirationOffset": actual_ttl.get("expirationOffset")}
                    if actual_ttl.get("expirationOffset")
                    else {}
                )
            else:
                actual = None
            expected = desired.get(field) if field in desired else None
        else:
            actual = current.get(field)
            expected = desired.get(field)
        if not _declaration_matches(actual, expected):
            return False
    return True


def _duration_seconds(value: str) -> int | None:
    match = re.fullmatch(r"([0-9]+)s", value)
    return int(match.group(1)) if match else None


def _snapshot_bucket_uri(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    if not cleaned:
        return ""
    return cleaned if cleaned.startswith("gs://") else f"gs://{cleaned}"


def _camelize(value: Any) -> Any:
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {_camel_key(str(key)): _camelize(item) for key, item in value.items()}


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _raise_operation_error(operation: dict[str, Any]) -> dict[str, Any]:
    error = operation.get("error") or {}
    if error:
        raise FirestoreError(
            f"Firestore operation failed ({error.get('code', 'unknown')}): {error.get('message') or error}",
        )
    return operation


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [str(exc)])


__all__ = [
    "FirestoreConfig",
    "FirestoreError",
    "FirestoreNativeDriver",
    "FirestoreNotFound",
    "FirestoreRestClient",
]
