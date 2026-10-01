"""Cloud Spanner instance, database, property-graph, backup, and binding lifecycle."""

from __future__ import annotations

import contextlib
import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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

KIND = "graph_db"
_API_ROOT = "https://spanner.googleapis.com/v1"
_INSTANCE_STATE = {
    "CREATING": "provisioning",
    "READY": "available",
}
_DATABASE_STATE = {
    "CREATING": "provisioning",
    "READY": "available",
    "READY_OPTIMIZING": "updating",
}
_OWNERSHIP_TABLE = "AstroliftGraphMetadata"
_OWNERSHIP_DDL = f"CREATE TABLE {_OWNERSHIP_TABLE} (marker STRING(1) NOT NULL) PRIMARY KEY (marker)"
#: The identity marker (#2086). ``AstroliftGraphMetadata`` says only that
#: Astrolift made the database, and with a shared instance two orgs' derived
#: database ids collide, so the managed-service id rides in a table name: a
#: Spanner database has no labels. New databases carry both tables; the old one
#: stays so a rollback still recognises them.
_OWNER_TABLE_PREFIX = "AstroliftGraphOwner_"
_OWNER_TABLE = re.compile(rf"\bCREATE\s+TABLE\s+`?{_OWNER_TABLE_PREFIX}([0-9a-f]{{32}})`?", re.I)
#: Tenant DDL may not name either marker table, or it could write one.
_MARKER_REFERENCE = re.compile(rf"{_OWNERSHIP_TABLE}|{_OWNER_TABLE_PREFIX}", re.I)


class SpannerGraphError(RuntimeError):
    pass


class SpannerOwnershipRefusal(SpannerGraphError):
    """A permanent source/container identity refusal, before provider mutation."""


class SpannerGraphNotFound(SpannerGraphError):
    pass


class SpannerGraphAlreadyExists(SpannerGraphError):
    pass


@dataclass(frozen=True)
class SpannerGraphConfig:
    project_id: str
    region: str
    instance_name_prefix: str = "astrolift"
    shared_instance_id: str = ""
    instance_config: str = ""
    edition: str = "ENTERPRISE"
    processing_units: int = 100
    automatic_backup_schedule: bool = True
    deletion_protection_default: bool = True
    backup_retention_days: int = 30
    api_endpoint: str = _API_ROOT
    operation_timeout_seconds: float = 1800.0
    poll_interval_seconds: float = 2.0


class SpannerRestClient:
    """Authenticated request-shaped adapter for the Cloud Spanner Admin API."""

    def __init__(self, *, endpoint: str = _API_ROOT, session: Any | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(scopes=("https://www.googleapis.com/auth/cloud-platform",))
            session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._session = session

    def get_instance(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def create_instance(self, project_id: str, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"projects/{project_id}/instances",
            json={"instanceId": instance_id, "instance": body},
        )

    def patch_instance(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        return self._request(
            "PATCH",
            name,
            json={"instance": {"name": name, **body}, "fieldMask": ",".join(update_mask)},
        )

    def delete_instance(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", name)

    def get_database(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def list_databases(self, instance_name: str) -> list[dict[str, Any]]:
        return self._paged(f"{instance_name}/databases", key="databases")

    def create_database(self, instance_name: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{instance_name}/databases", json=body)

    def patch_database(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json={"name": name, **body},
        )

    def drop_database(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", name)

    def get_ddl(self, database_name: str) -> list[str]:
        payload = self._request("GET", f"{database_name}/ddl")
        return [str(row) for row in payload.get("statements") or []]

    def update_ddl(self, database_name: str, statements: list[str], *, operation_id: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"{database_name}/ddl",
            json={"statements": statements, "operationId": operation_id},
        )

    def create_backup(
        self,
        instance_name: str,
        backup_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{instance_name}/backups",
            params={"backupId": backup_id},
            json=body,
        )

    def list_backups(self, instance_name: str) -> list[dict[str, Any]]:
        return self._paged(f"{instance_name}/backups", key="backups")

    def get_backup(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def restore_database(self, instance_name: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{instance_name}/databases:restore", json=body)

    def get_operation(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def _paged(self, resource: str, *, key: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_token = ""
        seen: set[str] = set()
        for _ in range(10):
            params = {"pageSize": "1000"}
            if page_token:
                params["pageToken"] = page_token
            payload = self._request("GET", resource, params=params)
            page = payload.get(key, [])
            if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
                raise SpannerGraphError("invalid Spanner resource inventory; ownership is unknown")
            rows.extend(page)
            if len(rows) > 10000:
                raise SpannerGraphError("Spanner resource inventory limit exceeded; ownership is unknown")
            page_token = payload.get("nextPageToken", "")
            if not isinstance(page_token, str):
                raise SpannerGraphError("invalid Spanner pagination token; ownership is unknown")
            if not page_token:
                return rows
            if page_token in seen:
                raise SpannerGraphError("repeated Spanner pagination token; ownership is unknown")
            seen.add(page_token)
        raise SpannerGraphError("Spanner resource inventory limit exceeded; ownership is unknown")

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
            raise SpannerGraphNotFound(resource)
        if response.status_code == 409:
            raise SpannerGraphAlreadyExists(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with contextlib.suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise SpannerGraphError(f"Spanner HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class SpannerGraphDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: SpannerGraphConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._spanner = client or SpannerRestClient(endpoint=config.api_endpoint)
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="graph_spanner",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg) or _identity_error(spec.managed_service_id)
        if error:
            return ProvisionResult(False, "", error, ["invalid_spanner_graph_config"])
        handle = spec.recorded_handle
        try:
            instance_id = self._instance_id(spec, cfg)
            database_id = (
                _parse_handle(spec.recorded_handle)[1]
                if spec.recorded_handle
                else self._database_id(spec, cfg, instance_id)
            )
            self._assert_config_target(cfg, instance_id, database_id)
            handle = _handle(instance_id, database_id)
            self._assert_container_intent(instance_id, cfg, spec)
            instance = self._ensure_instance(instance_id, database_id, cfg, spec)
            self._assert_instance_compatible(instance, cfg)
            database = self._get_database(instance_id, database_id)
            if database is None:
                body = self._database_create_body(database_id, cfg, spec.managed_service_id)
                self._wait_operation(self._spanner.create_database(self._instance_name(instance_id), body))
                database = self._checked_database(instance_id, database_id)
            # Before anything is mutated: a refused provision must leave both
            # the database and the instance it shares exactly as it found them.
            self._ensure_database_owned(
                instance_id,
                database_id,
                managed_service_id=spec.managed_service_id,
                record_proves=spec.recorded_handle_exclusive and spec.recorded_handle == handle,
                backfill=True,
            )
            self._reconcile_instance_capacity(instance, cfg, spec, database_id)
            self._reconcile_database(database, cfg)
            self._apply_schema_updates(instance_id, database_id, cfg)
            self._assert_graph_exists(instance_id, database_id, self._graph_name(cfg))
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Spanner Graph: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Spanner Graph {self._graph_name(cfg)} reconciled in {instance_id}/{database_id}",
            ready=True,
        )

    @driver_op(cloud="gcp", driver="graph_spanner")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            instance_id, database_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_spanner_graph_config"])
        try:
            instance = self._spanner.get_instance(self._instance_name(instance_id))
            self._assert_config_target(cfg, instance_id, database_id)
            self._assert_container_intent(instance_id, cfg, spec)
            if cfg.get("manage_instance_capacity"):
                self._assert_exclusive_database_set(instance_id, database_id)
            self._assert_owned_instance(instance, spec, database_id, backfill=True)
            self._assert_instance_compatible(instance, cfg)
            database = self._checked_database(instance_id, database_id)
            self._ensure_database_owned(
                instance_id,
                database_id,
                managed_service_id=spec.managed_service_id,
                record_proves=spec.recorded_handle_exclusive,
                backfill=True,
            )
            self._reconcile_instance_capacity(instance, cfg, spec, database_id)
            self._reconcile_database(database, cfg)
            self._apply_schema_updates(instance_id, database_id, cfg)
            self._assert_graph_exists(instance_id, database_id, self._graph_name(cfg))
        except SpannerGraphNotFound:
            return UpdateResult(False, spec.handle, "Spanner Graph database not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Spanner Graph: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Spanner Graph {instance_id}/{database_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="graph_spanner",
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
            instance_id, database_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        database_name = self._database_name(instance_id, database_id)
        try:
            self._assert_config_target(cfg, instance_id, database_id)
            self._assert_container_intent(
                instance_id,
                {},
                spec,
                deleting=bool(delete_data and force_destroy and cfg.get("delete_empty_instance")),
            )
            instance = self._spanner.get_instance(self._instance_name(instance_id))
            self._assert_owned_instance(instance, spec, database_id)
            if delete_data and force_destroy and cfg.get("delete_empty_instance"):
                self._assert_exclusive_database_set(instance_id, database_id, allow_empty=True)
                if self._spanner.list_backups(self._instance_name(instance_id)):
                    raise SpannerGraphError("Spanner instance still contains backups; refusing instance deletion")
            database = self._get_database(instance_id, database_id)
        except Exception as exc:
            return _deprovision_error(spec.handle, "describe Spanner Graph", exc)
        if database is None:
            return DeprovisionResult(True, spec.handle, f"Spanner Graph database {database_id} already gone")
        try:
            statements = self._spanner.get_ddl(database_name)
        except Exception as exc:
            return _deprovision_error(spec.handle, "describe Spanner Graph", exc)
        refusal = _ownership_refusal(
            statements,
            spec.managed_service_id,
            record_proves=spec.recorded_handle_exclusive,
            unmarked_ok=True,
        )
        if refusal:
            return DeprovisionResult(False, spec.handle, refusal, ["resource_not_owned"], retryable=False)
        instance_owned = (instance.get("labels") or {}).get("astrolift-managed-by") == "platform"
        database_owned = bool(_database_owners(statements)) or _ddl_has_ownership_marker(statements)
        if (not instance_owned or not database_owned) and not cfg.get("delete_adopted_database"):
            return DeprovisionResult(
                False,
                spec.handle,
                "refusing to delete an adopted Spanner database; set delete_adopted_database=true",
                ["adopted_database_delete_requires_opt_in"],
                retryable=False,
            )
        protected = bool(database.get("enableDropProtection"))
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Spanner database {database_id} has drop protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        retained = ""
        if not delete_data:
            try:
                retained = self.snapshot(
                    ServiceHandle(
                        spec.handle,
                        managed_service_id=spec.managed_service_id,
                        recorded_handle_exclusive=spec.recorded_handle_exclusive,
                        organization_id=spec.organization_id,
                        recorded_container_exclusive=spec.recorded_container_exclusive,
                    ),
                ).snapshot_id
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"retain Spanner Graph data before delete: {exc}",
                    [str(exc)],
                    retryable=False,
                )
        try:
            if delete_data and force_destroy and cfg.get("delete_empty_instance"):
                self._assert_owned_instance(instance, spec, database_id, backfill=True)
            if protected:
                operation = self._spanner.patch_database(
                    database_name,
                    {"enableDropProtection": False},
                    update_mask=["enableDropProtection"],
                )
                self._wait_operation(operation)
            self._spanner.drop_database(database_name)
            if delete_data and force_destroy and cfg.get("delete_empty_instance"):
                self._delete_instance_if_empty(instance_id, spec)
        except SpannerGraphNotFound:
            pass
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Spanner Graph database", exc)
        message = f"Spanner Graph database {database_id} deleted"
        if retained:
            message += f"; retained backup {retained}"
        return DeprovisionResult(True, spec.handle, message)

    @driver_op(cloud="gcp", driver="graph_spanner")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            instance_id, database_id = _parse_handle(handle.handle)
            instance = self._spanner.get_instance(self._instance_name(instance_id))
            database = self._checked_database(instance_id, database_id)
            self._assert_owned_instance(instance, handle, database_id)
            self._ensure_database_owned(
                instance_id,
                database_id,
                managed_service_id=handle.managed_service_id,
                record_proves=handle.recorded_handle_exclusive,
            )
        except SpannerGraphNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Spanner Graph database does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Spanner Graph: {exc}")
        instance_state = str(instance.get("state") or "STATE_UNSPECIFIED")
        database_state = str(database.get("state") or "STATE_UNSPECIFIED")
        state = _INSTANCE_STATE.get(instance_state, "updating")
        if state == "available":
            state = _DATABASE_STATE.get(database_state, "updating")
        return ServiceStatus(
            handle.handle,
            state,
            f"Spanner instance is {instance_state}; database is {database_state}",
        )

    @driver_op(cloud="gcp", driver="graph_spanner")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        instance_id, database_id = _parse_handle(handle.handle)
        cfg = dict(config or {})
        graph_name = self._graph_name(cfg)
        database_name = self._database_name(instance_id, database_id)
        instance = self._spanner.get_instance(self._instance_name(instance_id))
        self._assert_owned_instance(instance, handle, database_id)
        self._checked_database(instance_id, database_id)
        # The grant below is the payoff of a collision, so the database must be
        # this service's before its URL and databaseUser leave the driver.
        self._ensure_database_owned(
            instance_id,
            database_id,
            managed_service_id=handle.managed_service_id,
            record_proves=handle.recorded_handle_exclusive,
        )
        self._assert_graph_exists(instance_id, database_id, graph_name)
        graph_url = f"spanner://{database_name}/graphs/{quote(graph_name, safe='')}"
        return Binding(
            env_vars={
                "GRAPH_DB_URL": ValueRef(literal=graph_url),
                "GRAPH_DB_ENDPOINT": ValueRef(literal=database_name),
                "GRAPH_DB_PROTOCOL": ValueRef(literal="gql"),
                "GRAPH_DB_TLS": ValueRef(literal="true"),
                "GRAPH_DB_AUTH_MODE": ValueRef(literal="gcp_iam"),
                "GRAPH_DB_REGION": ValueRef(literal=self._config.region),
                "GRAPH_DB_RESOURCE_ARN": ValueRef(literal=database_name),
                "GCP_SPANNER_PROJECT": ValueRef(literal=self._config.project_id),
                "GCP_SPANNER_INSTANCE": ValueRef(literal=instance_id),
                "GCP_SPANNER_DATABASE": ValueRef(literal=database_id),
                "GCP_SPANNER_GRAPH": ValueRef(literal=graph_name),
                "GCP_SPANNER_DIALECT": ValueRef(literal="GOOGLE_STANDARD_SQL"),
            },
            iam_grants=[Grant(database_name, ["roles/spanner.databaseUser"])],
            notes="GoogleSQL Spanner Graph accessed with workload identity and GQL.",
        )

    @driver_op(cloud="gcp", driver="graph_spanner")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        if not 1 <= self._config.backup_retention_days <= 366:
            raise SpannerGraphError("Spanner backup_retention_days must be between 1 and 366")
        instance_id, database_id = _parse_handle(handle.handle)
        database_name = self._database_name(instance_id, database_id)
        instance = self._spanner.get_instance(self._instance_name(instance_id))
        self._assert_owned_instance(instance, handle, database_id)
        self._checked_database(instance_id, database_id)
        # A backup is a copy of the data, retained under this service's record.
        # Held to what deprovision accepts, since teardown takes one first.
        self._ensure_database_owned(
            instance_id,
            database_id,
            managed_service_id=handle.managed_service_id,
            record_proves=handle.recorded_handle_exclusive,
            unmarked_ok=True,
        )
        created = datetime.now(UTC)
        backup_id = _backup_id(database_id, created)
        expire = created + timedelta(days=self._config.backup_retention_days)
        operation = self._wait_operation(
            self._spanner.create_backup(
                self._instance_name(instance_id),
                backup_id,
                {"database": database_name, "expireTime": expire.isoformat().replace("+00:00", "Z")},
            ),
        )
        backup_name = str((operation.get("response") or {}).get("name") or "")
        if not backup_name:
            backup_name = f"{self._instance_name(instance_id)}/backups/{backup_id}"
        backup = self._spanner.get_backup(backup_name)
        if str(backup.get("state") or "") != "READY":
            raise SpannerGraphError(f"Spanner backup {backup_name} is not READY")
        return SnapshotHandle(handle.handle, backup_name, created.isoformat())

    @driver_op(cloud="gcp", driver="graph_spanner")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        if not re.fullmatch(r"projects/[^/]+/instances/[^/]+/backups/[^/]+", snapshot.snapshot_id):
            return ProvisionResult(False, "", "invalid Spanner backup resource name", ["invalid_snapshot"])
        cfg = dict(target.config or {})
        error = self._validate_config(cfg) or _identity_error(target.managed_service_id)
        if error:
            return ProvisionResult(False, "", error, ["invalid_spanner_graph_config"])
        handle = target.recorded_handle
        try:
            instance_id = self._instance_id(target, cfg)
            database_id = (
                _parse_handle(target.recorded_handle)[1]
                if target.recorded_handle
                else self._database_id(target, cfg, instance_id)
            )
            self._assert_config_target(cfg, instance_id, database_id)
            self._assert_container_intent(instance_id, cfg, target)
            handle = _handle(instance_id, database_id)
            if self._get_database(instance_id, database_id) is not None:
                raise SpannerGraphError(f"restore target {instance_id}/{database_id} already exists")
            instance = self._ensure_instance(instance_id, database_id, cfg, target)
            self._assert_instance_compatible(instance, cfg)
            body: dict[str, Any] = {"databaseId": database_id, "backup": snapshot.snapshot_id}
            if cfg.get("restore_kms_key_names"):
                body["encryptionConfig"] = {
                    "encryptionType": "CUSTOMER_MANAGED_ENCRYPTION",
                    "kmsKeyNames": list(cfg["restore_kms_key_names"]),
                }
            self._wait_operation(self._spanner.restore_database(self._instance_name(instance_id), body))
            database = self._checked_database(instance_id, database_id)
            self._stamp_restored_owner(instance_id, database_id, target.managed_service_id)
            self._reconcile_database(database, cfg)
            self._assert_graph_exists(instance_id, database_id, self._graph_name(cfg))
        except Exception as exc:
            return ProvisionResult(False, handle, f"restore Spanner Graph: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"Spanner Graph restored to {instance_id}/{database_id}", ready=True)

    @driver_op(cloud="gcp", driver="graph_spanner", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,62}[a-z0-9]$"},
                "database_id": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,28}[a-z0-9]$"},
                "graph_name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]{0,127}$"},
                "instance_config": {"type": "string"},
                "edition": {"type": "string", "enum": ["ENTERPRISE", "ENTERPRISE_PLUS"]},
                "processing_units": {"type": "integer", "minimum": 100, "multipleOf": 100},
                "autoscaling_min_processing_units": {"type": "integer", "minimum": 1000, "multipleOf": 1000},
                "autoscaling_max_processing_units": {"type": "integer", "minimum": 1000, "multipleOf": 1000},
                "autoscaling_high_priority_cpu_percent": {"type": "integer", "minimum": 10, "maximum": 90},
                "autoscaling_total_cpu_percent": {"type": "integer", "minimum": 10, "maximum": 90},
                "autoscaling_storage_percent": {"type": "integer", "minimum": 10, "maximum": 99},
                "manage_instance_capacity": {"type": "boolean"},
                "automatic_backup_schedule": {"type": "boolean"},
                "deletion_protection": {"type": "boolean"},
                "version_retention_period": {"type": "string", "pattern": "^[1-9][0-9]*[smhd]$"},
                "kms_key_names": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "restore_kms_key_names": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "ddl_statements": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "schema_update_statements": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "delete_adopted_database": {"type": "boolean"},
                "delete_empty_instance": {"type": "boolean"},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="graph_spanner", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "GRAPH_DB_URL": "Cloud Spanner database and property graph URI",
                "GRAPH_DB_ENDPOINT": "Cloud Spanner database resource name",
                "GRAPH_DB_PROTOCOL": "gql",
                "GRAPH_DB_TLS": "true",
                "GRAPH_DB_AUTH_MODE": "gcp_iam",
                "GRAPH_DB_REGION": "Spanner instance region/configuration hint",
                "GRAPH_DB_RESOURCE_ARN": "Portable database resource identity",
                "GCP_SPANNER_PROJECT": "Google Cloud project ID",
                "GCP_SPANNER_INSTANCE": "Cloud Spanner instance ID",
                "GCP_SPANNER_DATABASE": "Cloud Spanner database ID",
                "GCP_SPANNER_GRAPH": "Property graph name",
                "GCP_SPANNER_DIALECT": "GOOGLE_STANDARD_SQL",
            },
        )

    def _ensure_instance(
        self, instance_id: str, database_id: str, cfg: dict[str, Any], spec: ProvisionSpec
    ) -> dict[str, Any]:
        name = self._instance_name(instance_id)
        current = self._get_instance(instance_id)
        if current is not None and cfg.get("manage_instance_capacity"):
            self._assert_exclusive_database_set(instance_id, database_id, allow_empty=True)
        if current is None:
            operation = self._spanner.create_instance(
                self._config.project_id,
                instance_id,
                self._instance_body(instance_id, cfg, spec.organization_id),
            )
            self._wait_operation(operation)
            current = self._spanner.get_instance(name)
        self._assert_owned_instance(current, spec, database_id, backfill=True)
        return current

    def _instance_body(self, instance_id: str, cfg: dict[str, Any], organization_id: str) -> dict[str, Any]:
        if instance_id == self._config.shared_instance_id:
            cfg = {}  # Shared initial compute is also install-owned.
        body: dict[str, Any] = {
            "name": self._instance_name(instance_id),
            "config": self._instance_config(cfg),
            "displayName": (instance_id if len(instance_id) >= 4 else f"{instance_id}-graph")[:30],
            "edition": str(cfg.get("edition") or self._config.edition),
            "labels": _labels()
            if instance_id == self._config.shared_instance_id
            else {**_labels(), "astrolift-organization-id": _organization_id(organization_id)},
            "defaultBackupScheduleType": (
                "AUTOMATIC"
                if bool(cfg.get("automatic_backup_schedule", self._config.automatic_backup_schedule))
                else "NONE"
            ),
        }
        autoscaling = _autoscaling_config(cfg)
        if autoscaling:
            body["autoscalingConfig"] = autoscaling
        else:
            body["processingUnits"] = int(cfg.get("processing_units", self._config.processing_units))
        return body

    def _database_create_body(self, database_id: str, cfg: dict[str, Any], managed_service_id: str) -> dict[str, Any]:
        statements = [
            _OWNERSHIP_DDL,
            _owner_ddl(managed_service_id),
            *(cfg.get("ddl_statements") or _dynamic_graph_ddl(self._graph_name(cfg))),
        ]
        body: dict[str, Any] = {
            "createStatement": f"CREATE DATABASE `{database_id}`",
            "extraStatements": statements,
            "databaseDialect": "GOOGLE_STANDARD_SQL",
        }
        if cfg.get("kms_key_names"):
            body["encryptionConfig"] = {"kmsKeyNames": list(cfg["kms_key_names"])}
        return body

    def _reconcile_instance_capacity(
        self, current: dict[str, Any], cfg: dict[str, Any], spec: Any, database_id: str
    ) -> None:
        if not cfg.get("manage_instance_capacity"):
            return
        instance_id = str(current["name"]).rsplit("/", 1)[-1]
        self._assert_container_intent(instance_id, cfg, spec)
        self._assert_owned_instance(self._spanner.get_instance(self._instance_name(instance_id)), spec, database_id)
        self._assert_exclusive_database_set(instance_id, database_id)
        desired: dict[str, Any] = {}
        mask: list[str] = []
        autoscaling = _autoscaling_config(cfg)
        if autoscaling:
            if current.get("autoscalingConfig") != autoscaling:
                desired["autoscalingConfig"] = autoscaling
                mask.append("autoscalingConfig")
        elif "processing_units" in cfg and int(current.get("processingUnits") or 0) != int(cfg["processing_units"]):
            desired["processingUnits"] = int(cfg["processing_units"])
            mask.append("processingUnits")
        if mask:
            self._wait_operation(self._spanner.patch_instance(str(current["name"]), desired, update_mask=mask))

    def _reconcile_database(self, current: dict[str, Any], cfg: dict[str, Any]) -> None:
        desired_protection = bool(cfg.get("deletion_protection", self._config.deletion_protection_default))
        if bool(current.get("enableDropProtection")) != desired_protection:
            self._wait_operation(
                self._spanner.patch_database(
                    str(current["name"]),
                    {"enableDropProtection": desired_protection},
                    update_mask=["enableDropProtection"],
                ),
            )
        if (
            "version_retention_period" in cfg
            and current.get("versionRetentionPeriod") != cfg["version_retention_period"]
        ):
            database_name = str(current["name"])
            database_id = database_name.rsplit("/", 1)[-1]
            period = str(cfg["version_retention_period"])
            self._apply_ddl(
                database_name,
                [f"ALTER DATABASE `{database_id}` SET OPTIONS (version_retention_period = '{period}')"],
            )

    def _apply_schema_updates(self, instance_id: str, database_id: str, cfg: dict[str, Any]) -> None:
        statements = [str(row).strip() for row in cfg.get("schema_update_statements") or []]
        if not statements:
            return
        self._apply_ddl(self._database_name(instance_id, database_id), statements)

    def _apply_ddl(self, database_name: str, statements: list[str]) -> None:
        operation_id = "astrolift" + hashlib.sha256("\n".join(statements).encode()).hexdigest()[:24]
        try:
            operation = self._spanner.update_ddl(database_name, statements, operation_id=operation_id)
        except SpannerGraphAlreadyExists:
            operation = self._spanner.get_operation(f"{database_name}/operations/{operation_id}")
        self._wait_operation(operation)

    def _ensure_database_owned(
        self,
        instance_id: str,
        database_id: str,
        *,
        managed_service_id: str,
        record_proves: bool,
        backfill: bool = False,
        unmarked_ok: bool = False,
    ) -> None:
        database_name = self._database_name(instance_id, database_id)
        statements = self._spanner.get_ddl(database_name)
        refusal = _ownership_refusal(
            statements,
            managed_service_id,
            record_proves=record_proves,
            unmarked_ok=unmarked_ok,
        )
        if refusal:
            raise SpannerGraphError(refusal)
        if backfill and _ddl_has_ownership_marker(statements) and not _database_owners(statements):
            # Made by Astrolift before #2086, and the platform's own exclusive
            # record says it is this service's: stamp the identity once, so
            # every later check reads the marker instead of the record.
            self._apply_ddl(database_name, [_owner_ddl(managed_service_id)])

    def _stamp_restored_owner(self, instance_id: str, database_id: str, managed_service_id: str) -> None:
        # A restore copies the source's schema, and with it the source's owner
        # table. The database was created by this same call, so it is the
        # target's: replace any other identity rather than inherit it.
        database_name = self._database_name(instance_id, database_id)
        statements = self._spanner.get_ddl(database_name)
        ours = _owner_suffix(managed_service_id)
        owners = _database_owners(statements)
        changes = [f"DROP TABLE {_OWNER_TABLE_PREFIX}{owner}" for owner in sorted(owners - {ours})]
        if not _ddl_has_ownership_marker(statements):
            changes.append(_OWNERSHIP_DDL)
        if ours not in owners:
            changes.append(_owner_ddl(managed_service_id))
        if changes:
            self._apply_ddl(database_name, changes)

    def _assert_graph_exists(self, instance_id: str, database_id: str, graph_name: str) -> None:
        statements = self._spanner.get_ddl(self._database_name(instance_id, database_id))
        pattern = re.compile(rf"\b(?:CREATE|REPLACE).*?PROPERTY\s+GRAPH\s+`?{re.escape(graph_name)}`?\b", re.I | re.S)
        if not any(pattern.search(statement) for statement in statements):
            raise SpannerGraphError(f"property graph {graph_name!r} is not present in the database DDL")

    def _assert_instance_compatible(self, current: dict[str, Any], cfg: dict[str, Any]) -> None:
        expected = {
            "config": self._instance_config(cfg),
            "edition": str(cfg.get("edition") or self._config.edition),
        }
        for field, desired in expected.items():
            actual = str(current.get(field) or "")
            if actual and actual != desired:
                raise SpannerGraphError(f"shared Spanner instance {field} mismatch ({actual!r} != {desired!r})")

    def _assert_config_target(self, cfg: dict[str, Any], instance_id: str, database_id: str) -> None:
        if cfg.get("instance_id") and cfg["instance_id"] != instance_id:
            raise SpannerOwnershipRefusal("config instance_id disagrees with the recorded physical handle")
        if cfg.get("database_id") and cfg["database_id"] != database_id:
            raise SpannerOwnershipRefusal("config database_id disagrees with the recorded physical handle")

    def _assert_container_intent(
        self, instance_id: str, cfg: dict[str, Any], spec: Any, *, deleting: bool = False
    ) -> None:
        _organization_id(spec.organization_id)
        if cfg.get("manage_instance_capacity") or deleting:
            if instance_id == self._config.shared_instance_id:
                raise SpannerOwnershipRefusal(
                    "operator-shared Spanner instance capacity/deletion is not tenant-manageable"
                )
            if not spec.recorded_container_exclusive:
                raise SpannerOwnershipRefusal(
                    "no exclusive platform container proof; refusing instance capacity/deletion"
                )

    def _assert_exclusive_database_set(self, instance_id: str, database_id: str, *, allow_empty: bool = False) -> None:
        instance_name = self._instance_name(instance_id)
        expected = self._database_name(instance_id, database_id)
        rows = self._spanner.list_databases(instance_name)
        if not isinstance(rows, list) or not all(isinstance(row, dict) and row.get("name") for row in rows):
            raise SpannerOwnershipRefusal("Spanner database inventory is unknown; refusing container operation")
        names = [row["name"] for row in rows]
        if names != [expected] and not (allow_empty and not names):
            raise SpannerOwnershipRefusal("Spanner instance has other/unknown databases; refusing container operation")

    def _assert_owned_instance(
        self, current: dict[str, Any], spec: Any, database_id: str, *, backfill: bool = False
    ) -> None:
        instance_name = str(current.get("name") or "")
        instance_id = (
            self._instance_id(spec, dict(spec.config or {}))
            if isinstance(spec, ProvisionSpec)
            else _parse_handle(spec.handle)[0]
        )
        if instance_name != self._instance_name(instance_id):
            raise SpannerOwnershipRefusal("Spanner instance project identity does not match the driver")
        labels = dict(current.get("labels") or {})
        if labels.get("astrolift-managed-by") != "platform":
            raise SpannerOwnershipRefusal(
                "existing Spanner instance carries no Astrolift ownership marker; "
                "operator-authorized adoption is required"
            )
        organization_id = _organization_id(spec.organization_id)
        owner = labels.get("astrolift-organization-id")
        if owner and owner != organization_id:
            raise SpannerOwnershipRefusal("Spanner instance belongs to another organization")
        if instance_id == self._config.shared_instance_id:
            # Only the install-configured container is eligible for multi-org
            # databases. A tenant instance_id cannot confer that privilege.
            if owner:
                raise SpannerOwnershipRefusal("operator-shared Spanner instance unexpectedly carries a tenant owner")
            return
        if owner == organization_id:
            return
        recorded = spec.recorded_handle if isinstance(spec, ProvisionSpec) else spec.handle
        if not (
            spec.recorded_handle_exclusive
            and spec.recorded_container_exclusive
            and recorded == _handle(instance_id, database_id)
        ):
            raise SpannerOwnershipRefusal(
                "legacy Spanner instance has no immutable organization owner or exclusive recorded-container proof"
            )
        self._assert_exclusive_database_set(instance_id, database_id)
        self._ensure_database_owned(
            instance_id, database_id, managed_service_id=spec.managed_service_id, record_proves=True
        )
        if backfill:
            labels["astrolift-organization-id"] = organization_id
            self._wait_operation(
                self._spanner.patch_instance(instance_name, {"labels": labels}, update_mask=["labels"])
            )

    def _delete_instance_if_empty(self, instance_id: str, spec: DeprovisionSpec) -> None:
        instance_name = self._instance_name(instance_id)
        instance = self._spanner.get_instance(instance_name)
        self._assert_owned_instance(instance, spec, _parse_handle(spec.handle)[1])
        self._assert_container_intent(instance_id, {}, spec, deleting=True)
        if (instance.get("labels") or {}).get("astrolift-organization-id") != _organization_id(spec.organization_id):
            raise SpannerOwnershipRefusal(
                "refusing to delete Spanner instance without its immutable organization owner"
            )
        if self._spanner.list_databases(instance_name):
            raise SpannerOwnershipRefusal("Spanner instance still contains databases; refusing instance deletion")
        if self._spanner.list_backups(instance_name):
            raise SpannerOwnershipRefusal("Spanner instance still contains backups; refusing instance deletion")
        self._wait_operation(self._spanner.delete_instance(instance_name))

    def _wait_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation:
            return {}
        name = str(operation.get("name") or "")
        current = operation
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        while not current.get("done"):
            if not name:
                raise SpannerGraphError("Spanner operation response has no name")
            if self._monotonic() >= deadline:
                raise SpannerGraphError(f"Spanner operation {name} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._spanner.get_operation(name)
        if current.get("error"):
            error = current["error"]
            raise SpannerGraphError(f"Spanner operation {name} failed: {error.get('message') or error}")
        return current

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        unknown = sorted(set(cfg) - set(self.config_schema()["properties"]))
        if unknown:
            return f"unknown Spanner Graph config keys: {', '.join(unknown)}"
        patterns = {
            "instance_id": r"[a-z][a-z0-9-]{0,62}[a-z0-9]",
            "database_id": r"[a-z][a-z0-9-]{0,28}[a-z0-9]",
            "graph_name": r"[A-Za-z][A-Za-z0-9_]{0,127}",
        }
        for key, pattern in patterns.items():
            value = cfg.get(key)
            if value is not None and (not isinstance(value, str) or not re.fullmatch(pattern, value)):
                return f"invalid Spanner Graph {key}"
        for key in ("ddl_statements", "schema_update_statements", "kms_key_names", "restore_kms_key_names"):
            value = cfg.get(key)
            if value is not None and (
                not isinstance(value, list) or not value or not all(isinstance(row, str) and row for row in value)
            ):
                return f"{key} must be a non-empty list of strings"
        numeric_bounds = {
            "processing_units": (100, None, 100),
            "autoscaling_min_processing_units": (1000, None, 1000),
            "autoscaling_max_processing_units": (1000, None, 1000),
            "autoscaling_high_priority_cpu_percent": (10, 90, None),
            "autoscaling_total_cpu_percent": (10, 90, None),
            "autoscaling_storage_percent": (10, 99, None),
        }
        for key, (lower, upper, multiple) in numeric_bounds.items():
            value = cfg.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                return f"{key} must be an integer"
            if value < lower or (upper is not None and value > upper):
                return f"{key} is outside the supported range"
            if multiple is not None and value % multiple:
                return f"{key} must be a multiple of {multiple}"
        edition = str(cfg.get("edition") or self._config.edition)
        if edition not in {"ENTERPRISE", "ENTERPRISE_PLUS"}:
            return "Spanner Graph requires ENTERPRISE or ENTERPRISE_PLUS edition"
        if cfg.get("ddl_statements"):
            graph_name = self._graph_name(cfg)
            joined = "\n".join(str(row) for row in cfg["ddl_statements"])
            if not re.search(rf"PROPERTY\s+GRAPH\s+`?{re.escape(graph_name)}`?\b", joined, re.I):
                return f"ddl_statements must create property graph {graph_name!r}"
        for key in ("ddl_statements", "schema_update_statements"):
            for statement in cfg.get(key) or []:
                if not re.match(r"^\s*(?:CREATE|ALTER|RENAME)\b", str(statement), re.I):
                    return f"{key} permits only CREATE, ALTER, or RENAME DDL"
                if _MARKER_REFERENCE.search(str(statement)):
                    return f"{key} cannot name Astrolift's ownership tables"
        minimum = cfg.get("autoscaling_min_processing_units")
        maximum = cfg.get("autoscaling_max_processing_units")
        if (minimum is None) != (maximum is None):
            return "autoscaling requires both autoscaling_min_processing_units and autoscaling_max_processing_units"
        if minimum is not None and maximum is not None and int(maximum) < int(minimum):
            return "autoscaling_max_processing_units must be greater than or equal to the minimum"
        if minimum is not None and not cfg.get("manage_instance_capacity"):
            return "autoscaling changes require manage_instance_capacity=true"
        if minimum is None and any(
            cfg.get(key) is not None
            for key in (
                "autoscaling_high_priority_cpu_percent",
                "autoscaling_total_cpu_percent",
                "autoscaling_storage_percent",
            )
        ):
            return "autoscaling utilization targets require autoscaling capacity limits"
        if cfg.get("processing_units") is not None and minimum is not None:
            return "processing_units and autoscaling settings are mutually exclusive"
        retention = cfg.get("version_retention_period")
        if retention is not None and not re.fullmatch(r"[1-9][0-9]*[smhd]", str(retention)):
            return "version_retention_period must be a positive duration ending in s, m, h, or d"
        return ""

    def _get_instance(self, instance_id: str) -> dict[str, Any] | None:
        try:
            return self._spanner.get_instance(self._instance_name(instance_id))
        except SpannerGraphNotFound:
            return None

    def _checked_database(self, instance_id: str, database_id: str) -> dict[str, Any]:
        expected = self._database_name(instance_id, database_id)
        database = self._spanner.get_database(expected)
        if database.get("name") != expected:
            raise SpannerOwnershipRefusal("Spanner database identity does not match the recorded driver target")
        return database

    def _get_database(self, instance_id: str, database_id: str) -> dict[str, Any] | None:
        try:
            return self._checked_database(instance_id, database_id)
        except SpannerGraphNotFound:
            return None

    def _instance_id(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        return spanner_instance_id(
            managed_service_id=spec.managed_service_id,
            organization_id=spec.organization_id,
            recorded_handle=spec.recorded_handle,
            instance_id=str(cfg.get("instance_id") or ""),
            shared_instance_id=self._config.shared_instance_id,
            instance_name_prefix=self._config.instance_name_prefix,
        )

    def _database_id(self, spec: ProvisionSpec, cfg: dict[str, Any], instance_id: str) -> str:
        explicit = str(cfg.get("database_id") or "")
        if explicit:
            return explicit
        raw = f"{spec.app_slug}-{spec.environment_name}-{spec.service_handle_hint or 'graph'}"
        legacy = _resource_id(raw, maximum=30)
        if spec.recorded_handle == _handle(instance_id, legacy):
            # Named before #2086 and recorded that way: keep it, rather than
            # derive a new name and create an empty database beside it.
            return legacy
        # No org in the slug-joined name, so on a shared instance two orgs with
        # the same app, environment and hint derived one database. The digest
        # of the managed-service id keeps new names apart.
        digest = hashlib.sha256(spec.managed_service_id.encode()).hexdigest()[:8]
        return f"{_resource_id(raw, maximum=30 - len(digest) - 1)}-{digest}"

    def _graph_name(self, cfg: dict[str, Any]) -> str:
        return str(cfg.get("graph_name") or "Graph")

    def _instance_config(self, cfg: dict[str, Any]) -> str:
        value = str(cfg.get("instance_config") or self._config.instance_config or f"regional-{self._config.region}")
        if value.startswith("projects/"):
            return value
        return f"projects/{self._config.project_id}/instanceConfigs/{value}"

    def _instance_name(self, instance_id: str) -> str:
        return f"projects/{self._config.project_id}/instances/{instance_id}"

    def _database_name(self, instance_id: str, database_id: str) -> str:
        return f"{self._instance_name(instance_id)}/databases/{database_id}"


def _dynamic_graph_ddl(graph_name: str) -> list[str]:
    return [
        "CREATE TABLE GraphNode ("
        "id STRING(128) NOT NULL, label STRING(128) NOT NULL, properties JSON"
        ") PRIMARY KEY (id)",
        "CREATE TABLE GraphEdge ("
        "edge_id STRING(128) NOT NULL, source_id STRING(128) NOT NULL, "
        "destination_id STRING(128) NOT NULL, label STRING(128) NOT NULL, properties JSON, "
        "CONSTRAINT FK_GraphEdge_Source FOREIGN KEY (source_id) REFERENCES GraphNode (id), "
        "CONSTRAINT FK_GraphEdge_Destination FOREIGN KEY (destination_id) REFERENCES GraphNode (id)"
        ") PRIMARY KEY (edge_id)",
        "CREATE INDEX GraphEdgeBySource ON GraphEdge(source_id)",
        "CREATE INDEX GraphEdgeByDestination ON GraphEdge(destination_id)",
        f"CREATE PROPERTY GRAPH {graph_name} "
        "NODE TABLES (GraphNode DYNAMIC LABEL (label) DYNAMIC PROPERTIES (properties)) "
        "EDGE TABLES (GraphEdge SOURCE KEY (source_id) REFERENCES GraphNode (id) "
        "DESTINATION KEY (destination_id) REFERENCES GraphNode (id) "
        "DYNAMIC LABEL (label) DYNAMIC PROPERTIES (properties))",
    ]


def _autoscaling_config(cfg: dict[str, Any]) -> dict[str, Any]:
    if cfg.get("autoscaling_min_processing_units") is None:
        return {}
    targets: dict[str, int] = {
        "storageUtilizationPercent": int(cfg.get("autoscaling_storage_percent", 80)),
    }
    if cfg.get("autoscaling_high_priority_cpu_percent") is not None:
        targets["highPriorityCpuUtilizationPercent"] = int(cfg["autoscaling_high_priority_cpu_percent"])
    if cfg.get("autoscaling_total_cpu_percent") is not None:
        targets["totalCpuUtilizationPercent"] = int(cfg["autoscaling_total_cpu_percent"])
    return {
        "autoscalingLimits": {
            "minProcessingUnits": int(cfg["autoscaling_min_processing_units"]),
            "maxProcessingUnits": int(cfg["autoscaling_max_processing_units"]),
        },
        "autoscalingTargets": targets,
    }


def _handle(instance_id: str, database_id: str) -> str:
    return f"{KIND}/{instance_id}/{database_id}"


def _parse_handle(handle: str) -> tuple[str, str]:
    parts = handle.split("/")
    if len(parts) != 3 or parts[0] != KIND or not parts[1] or not parts[2]:
        raise ValueError(f"handle {handle!r} must be 'graph_db/<instance>/<database>'")
    return parts[1], parts[2]


def _resource_id(value: str, *, maximum: int) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")[:maximum].rstrip("-")
    if len(normalized) < 2 or not normalized[0].isalpha() or not normalized[-1].isalnum():
        normalized = f"a-{normalized}"[:maximum].rstrip("-")
    return normalized


def _backup_id(database_id: str, created: datetime) -> str:
    return _resource_id(f"{database_id}-backup-{created.strftime('%Y%m%d%H%M%S%f')}", maximum=60)


def _organization_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise SpannerOwnershipRefusal("Spanner Graph needs its immutable organization UUID") from exc


def spanner_instance_id(
    *,
    managed_service_id: str,
    organization_id: str,
    recorded_handle: str,
    instance_id: str,
    shared_instance_id: str,
    instance_name_prefix: str,
) -> str:
    """Pure physical target derivation shared with the platform ownership proof."""
    if recorded_handle:
        return _parse_handle(recorded_handle)[0]
    explicit = str(instance_id or shared_instance_id)
    if explicit:
        return explicit
    _organization_id(organization_id)
    return _resource_id(f"{instance_name_prefix[:20]}-{uuid.UUID(managed_service_id).hex}", maximum=64)


def _labels() -> dict[str, str]:
    return {
        "astrolift-managed-by": "platform",
        "astrolift-purpose": "spanner-graph",
    }


def _ddl_has_ownership_marker(statements: list[str]) -> bool:
    pattern = re.compile(rf"\bCREATE\s+TABLE\s+`?{re.escape(_OWNERSHIP_TABLE)}`?\b", re.I)
    return any(pattern.search(str(statement)) for statement in statements)


def _owner_suffix(managed_service_id: str) -> str:
    return uuid.UUID(managed_service_id).hex


def _identity_error(managed_service_id: str) -> str:
    try:
        _owner_suffix(managed_service_id)
    except (TypeError, ValueError):
        return "Spanner Graph needs the managed-service id (a UUID) to mark the database it owns"
    return ""


def _owner_ddl(managed_service_id: str) -> str:
    table = f"{_OWNER_TABLE_PREFIX}{_owner_suffix(managed_service_id)}"
    return f"CREATE TABLE {table} (marker STRING(1) NOT NULL) PRIMARY KEY (marker)"


def _database_owners(statements: list[str]) -> set[str]:
    return {match.group(1).lower() for statement in statements for match in _OWNER_TABLE.finditer(str(statement))}


def _ownership_refusal(
    statements: list[str],
    managed_service_id: str,
    *,
    record_proves: bool,
    unmarked_ok: bool = False,
) -> str:
    """Why this service may not act on the database with ``statements``, or ``""``.

    The owner table decides whenever there is one. Without it the database
    predates #2086, and only the platform's exclusive record of the handle can
    say whose it is: ``AstroliftGraphMetadata`` proves Astrolift made it, not
    for whom. ``unmarked_ok`` is teardown's allowance for a recorded database
    carrying neither table, which ``delete_adopted_database`` still gates.
    """
    error = _identity_error(managed_service_id)
    if error:
        return error
    ours = _owner_suffix(managed_service_id)
    owners = _database_owners(statements)
    if owners == {ours}:
        return ""
    if ours in owners:
        return "Spanner database is marked for more than one managed service; an operator must decide which owns it"
    if owners:
        return "Spanner database belongs to another managed service; refusing to act on it"
    if not _ddl_has_ownership_marker(statements) and not unmarked_ok:
        return (
            "existing Spanner database carries no Astrolift ownership marker; adoption is a "
            "separate, operator-authorized operation and cannot be granted by tenant config"
        )
    if record_proves:
        return ""
    return (
        "Spanner database predates the managed-service ownership marker, and no exclusive platform record "
        "says it is this service's (another live service records it, or this one does not); an operator "
        f"who confirms it is must create table {_OWNER_TABLE_PREFIX}{ours} in it"
    )


def _deprovision_error(handle: str, action: str, exc: Exception) -> DeprovisionResult:
    return DeprovisionResult(
        False, handle, f"{action}: {exc}", [str(exc)], retryable=not isinstance(exc, SpannerOwnershipRefusal)
    )
