"""Google Cloud AlloyDB for PostgreSQL managed-service lifecycle."""

from __future__ import annotations

import copy
import secrets
import string
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from collections.abc import Callable

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
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from gcp.managed._secret_store import ManagedSecretStore, ManagedSecretStoreError

KIND = "postgres"
_API_ROOT = "https://alloydb.googleapis.com/v1"
_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_."
_SIZE_TO_MACHINE_TYPE = {
    "small": "n2-highmem-2",
    "medium": "n2-highmem-4",
    "large": "n2-highmem-8",
    "xlarge": "n2-highmem-16",
}
_READY = "READY"
_PROVISIONING_STATES = {"CREATING", "BOOTSTRAPPING", "STATE_UNSPECIFIED"}
_UPDATING_STATES = {
    "MAINTENANCE",
    "PROMOTING",
    "SWITCHOVER",
    "STARTING",
    "STOPPING",
}


class AlloyDBError(RuntimeError):
    pass


class AlloyDBNotFound(AlloyDBError):
    pass


@dataclass(frozen=True)
class AlloyDBConfig:
    project_id: str
    region: str
    network: str | None = None
    allocated_ip_range: str | None = None
    cluster_name_prefix: str = "astrolift"
    primary_instance_id: str = "primary"
    database_version: str = "POSTGRES_16"
    machine_type_default: str = "n2-highmem-2"
    high_availability_default: bool = True
    deletion_protection_default: bool = True
    backup_retention_days: int = 14
    secret_manager_prefix: str = "astrolift/alloydb"
    secret_id_prefix: str = "astrolift"
    operation_timeout_seconds: float = 1_200.0
    operation_poll_interval_seconds: float = 5.0
    api_endpoint: str = _API_ROOT


class AlloyDBRestClient:
    """Request-shaped adapter over the AlloyDB v1 JSON API."""

    def __init__(self, *, api_endpoint: str = _API_ROOT, session: Any | None = None) -> None:
        self._api_endpoint = api_endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=("https://www.googleapis.com/auth/cloud-platform",),
            )
            session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._session = session

    def get_cluster(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def create_cluster(self, parent: str, cluster_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/clusters",
            params={"clusterId": cluster_id},
            json=body,
        )

    def patch_cluster(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        payload = dict(body)
        payload["name"] = name
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json=payload,
        )

    def delete_cluster(self, name: str, *, force: bool) -> dict[str, Any]:
        return self._request(
            "DELETE",
            name,
            params={"force": str(force).lower()},
        )

    def restore_cluster(
        self,
        parent: str,
        cluster_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(body)
        payload["clusterId"] = cluster_id
        return self._request("POST", f"{parent}/clusters:restore", json=payload)

    def get_instance(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def list_instances(self, parent: str) -> list[dict[str, Any]]:
        instances: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params = {"pageSize": "1000"}
            if page_token:
                params["pageToken"] = page_token
            response = self._request("GET", f"{parent}/instances", params=params)
            instances.extend(response.get("instances") or [])
            page_token = str(response.get("nextPageToken") or "")
            if not page_token:
                return instances

    def create_instance(
        self,
        parent: str,
        instance_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/instances",
            params={"instanceId": instance_id},
            json=body,
        )

    def patch_instance(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        payload = dict(body)
        payload["name"] = name
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json=payload,
        )

    def delete_instance(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", name)

    def get_connection_info(self, parent: str) -> dict[str, Any]:
        return self._request("GET", f"{parent}/connectionInfo")

    def get_backup(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def create_backup(self, parent: str, backup_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/backups",
            params={"backupId": backup_id},
            json=body,
        )

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
            f"{self._api_endpoint}/{resource.lstrip('/')}",
            params=params,
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise AlloyDBNotFound(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise AlloyDBError(f"AlloyDB HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class AlloyDBPostgresDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: AlloyDBConfig,
        client: Any | None = None,
        secrets_client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._alloydb = client or AlloyDBRestClient(api_endpoint=config.api_endpoint)
        if secrets_client is None:
            from google.cloud import secretmanager

            secrets_client = secretmanager.SecretManagerServiceClient()
        self._secret_store = ManagedSecretStore(
            project_id=config.project_id,
            secret_id_prefix=config.secret_id_prefix,
            client=secrets_client,
        )
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="postgres_alloydb",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        try:
            _validate_config(cfg)
            cluster_id = self._cluster_id_for(spec)
            cluster_name = self._cluster_name(cluster_id)
            handle = _handle_for(cluster_id)
            deadline = self._deadline()
            cluster = self._get_cluster(cluster_name)
            password = self._secret_store.get(self._master_secret(cluster_id))
            if cluster is not None:
                if not _is_owned(cluster, spec):
                    return ProvisionResult(
                        False,
                        handle,
                        "refusing to adopt an AlloyDB cluster not owned by Astrolift",
                        ["resource_not_owned"],
                    )
                if password is None:
                    return ProvisionResult(
                        False,
                        handle,
                        "AlloyDB cluster exists but its Astrolift master-password secret is missing",
                        ["missing_master_password_secret"],
                    )
                self._wait_resource_ready(cluster_name, self._alloydb.get_cluster, deadline)
            else:
                password = _generate_master_password()
                self._secret_store.upsert(
                    self._master_secret(cluster_id),
                    password,
                    labels=_labels_for(spec),
                )
                try:
                    operation = self._alloydb.create_cluster(
                        self._location_parent(),
                        cluster_id,
                        self._cluster_body(spec, password),
                    )
                    self._wait_operation(operation, deadline)
                except Exception:
                    self._delete_connection_secrets(cluster_id)
                    raise

            primary_name = self._primary_name(cluster_name, cfg)
            primary = self._get_instance(primary_name)
            if primary is None:
                other_primary = next(
                    (
                        item
                        for item in self._alloydb.list_instances(cluster_name)
                        if str(item.get("instanceType")) == "PRIMARY"
                    ),
                    None,
                )
                if other_primary is not None:
                    return ProvisionResult(
                        False,
                        handle,
                        (
                            "AlloyDB primary_instance_id is immutable after creation; existing primary is "
                            f"{other_primary.get('name')}"
                        ),
                        ["immutable_primary_instance_id"],
                    )
                operation = self._alloydb.create_instance(
                    cluster_name,
                    self._primary_id(cfg),
                    self._primary_body(spec),
                )
                self._wait_operation(operation, deadline)
            else:
                if not _has_platform_ownership(primary):
                    return ProvisionResult(
                        False,
                        handle,
                        "refusing to adopt an AlloyDB primary instance not owned by Astrolift",
                        ["resource_not_owned"],
                    )
                self._wait_resource_ready(primary_name, self._alloydb.get_instance, deadline)

            self._ensure_read_pools(cluster_name, spec, deadline)
            ready = self._aggregate_status(cluster_name).state == "available"
            return ProvisionResult(
                True,
                handle,
                f"AlloyDB cluster {cluster_id} and its primary instance are ready",
                ready=ready,
            )
        except Exception as exc:
            cluster_id = locals().get("cluster_id", "unknown")
            return ProvisionResult(
                False,
                _handle_for(str(cluster_id)) if cluster_id != "unknown" else "",
                f"provision AlloyDB: {exc}",
                [str(exc)],
            )

    @driver_op(cloud="gcp", driver="postgres_alloydb")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            cluster_id = _parse_handle(spec.handle)
            cfg = spec.config or {}
            _validate_config(cfg)
            cluster_name = self._cluster_name(cluster_id)
            cluster = self._alloydb.get_cluster(cluster_name)
            if not _has_platform_ownership(cluster):
                return UpdateResult(
                    False,
                    spec.handle,
                    "refusing to update an AlloyDB cluster not owned by Astrolift",
                    ["resource_not_owned"],
                )

            cluster_patch = _mapping(cfg.get("cluster_patch"), "cluster_patch")
            cluster_patch = _safe_patch(cluster_patch, cluster.get("labels") or {}, instance=False)
            cluster_mask = sorted(cluster_patch)
            deadline = self._deadline()
            if cluster_patch:
                operation = self._alloydb.patch_cluster(
                    cluster_name,
                    cluster_patch,
                    update_mask=cluster_mask,
                )
                self._wait_operation(operation, deadline)

            primary_patch = _mapping(cfg.get("primary_patch"), "primary_patch")
            if spec.size or cfg.get("machine_type") or cfg.get("cpu_count"):
                primary_patch["machineConfig"] = self._machine_config(spec.size, cfg)
            for key, api_key in (
                ("database_flags", "databaseFlags"),
                ("query_insights", "queryInsightsConfig"),
                ("observability", "observabilityConfig"),
                ("connection_pool", "connectionPoolConfig"),
            ):
                if key in cfg:
                    primary_patch[api_key] = _mapping(cfg[key], key)
            if primary_patch:
                primary_name = self._primary_name(cluster_name, cfg)
                primary = self._alloydb.get_instance(primary_name)
                primary_patch = _safe_patch(
                    primary_patch,
                    primary.get("labels") or {},
                    instance=True,
                )
                if primary_patch:
                    operation = self._alloydb.patch_instance(
                        primary_name,
                        primary_patch,
                        update_mask=sorted(primary_patch),
                    )
                    self._wait_operation(operation, deadline)
            self._update_read_pools(cluster_name, cluster, cfg, deadline)
            return UpdateResult(True, spec.handle, "AlloyDB update reconciled")
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, "AlloyDB cluster not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update AlloyDB: {exc}", [str(exc)])

    @driver_op(
        cloud="gcp",
        driver="postgres_alloydb",
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
            cluster_id = _parse_handle(spec.handle)
            cluster_name = self._cluster_name(cluster_id)
            cluster = self._get_cluster(cluster_name)
            if cluster is None:
                if delete_data:
                    self._delete_connection_secrets(cluster_id, strict=True)
                return DeprovisionResult(True, spec.handle, "AlloyDB cluster is already absent")
            if not _has_platform_ownership(cluster) and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "refusing to delete an AlloyDB cluster not owned by Astrolift",
                    ["resource_not_owned"],
                    retryable=False,
                )
            protected = bool(
                spec.config.get(
                    "deletion_protection",
                    self._config.deletion_protection_default,
                ),
            )
            if protected and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "AlloyDB deletion protection is enabled",
                    ["deletion_protection_enabled"],
                    retryable=False,
                )
            backup_id = "skipped"
            if not delete_data:
                backup_id = self.snapshot(ServiceHandle(spec.handle)).snapshot_id
            operation = self._alloydb.delete_cluster(cluster_name, force=True)
            self._wait_operation(operation, self._deadline())
            if delete_data:
                self._delete_connection_secrets(cluster_id, strict=True)
            return DeprovisionResult(
                True,
                spec.handle,
                f"AlloyDB cluster deleted; backup={backup_id}",
            )
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, "AlloyDB cluster is already absent")
            return DeprovisionResult(False, spec.handle, f"deprovision AlloyDB: {exc}", [str(exc)])

    @driver_op(cloud="gcp", driver="postgres_alloydb")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            return self._aggregate_status(self._cluster_name(_parse_handle(handle.handle)))
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", "AlloyDB cluster does not exist")
            return ServiceStatus(handle.handle, "error", f"describe AlloyDB: {exc}")

    @driver_op(cloud="gcp", driver="postgres_alloydb")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        cfg = config or {}
        cluster_id = _parse_handle(handle.handle)
        cluster_name = self._cluster_name(cluster_id)
        primary_name = self._primary_name(cluster_name, cfg)
        instance = self._alloydb.get_instance(primary_name)
        connection = self._alloydb.get_connection_info(primary_name)
        connectivity = str(cfg.get("connectivity") or "auto").lower()
        psc_dns = str((instance.get("pscInstanceConfig") or {}).get("pscDnsName") or "")
        private_ip = str(connection.get("ipAddress") or "")
        public_ip = str(connection.get("publicIpAddress") or "")
        if connectivity == "psc":
            host = psc_dns
        elif connectivity == "public":
            host = public_ip
        elif connectivity == "private":
            host = private_ip
        else:
            host = private_ip or psc_dns or public_ip
        if not host:
            raise AlloyDBError(f"AlloyDB primary {primary_name} has no {connectivity} endpoint")

        password_ref = self._master_secret(cluster_id)
        password = self._secret_store.get(password_ref)
        if password is None:
            raise AlloyDBError(f"AlloyDB master-password secret {password_ref!r} is missing")
        database = str(cfg.get("database") or "postgres")
        user = str(cfg.get("user") or "postgres")
        ssl_mode = str(cfg.get("ssl_mode") or "require")
        url_ref = self._url_secret(cluster_id)
        self._secret_store.upsert(
            url_ref,
            (
                f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@"
                f"{host}:5432/{quote(database, safe='')}?sslmode={quote(ssl_mode, safe='')}"
            ),
        )
        project = f"projects/{self._config.project_id}"
        return Binding(
            env_vars={
                "POSTGRES_HOST": ValueRef(literal=host),
                "POSTGRES_PORT": ValueRef(literal="5432"),
                "POSTGRES_DB": ValueRef(literal=database),
                "POSTGRES_USER": ValueRef(literal=user),
                "POSTGRES_PASSWORD": ValueRef(secret_ref=password_ref),
                "POSTGRES_SSL_MODE": ValueRef(literal=ssl_mode),
                "POSTGRES_MASTER_SECRET_REF": ValueRef(literal=password_ref),
                "DATABASE_URL": ValueRef(secret_ref=url_ref),
                "ALLOYDB_CLUSTER": ValueRef(literal=cluster_name),
                "ALLOYDB_INSTANCE": ValueRef(literal=primary_name),
                "ALLOYDB_HOST": ValueRef(literal=host),
                "ALLOYDB_PORT": ValueRef(literal="5432"),
            },
            iam_grants=[
                Grant(resource=project, actions=["roles/alloydb.client"]),
                Grant(resource=project, actions=["roles/serviceusage.serviceUsageConsumer"]),
            ],
            notes=(
                "The control plane resolves password and DATABASE_URL refs into the workload "
                "binding Secret. AlloyDB client roles authorize connector/private access."
            ),
        )

    @driver_op(cloud="gcp", driver="postgres_alloydb")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        cluster_id = _parse_handle(handle.handle)
        now = datetime.now(UTC)
        backup_id = _snapshot_id(cluster_id, now)
        backup_name = f"{self._location_parent()}/backups/{backup_id}"
        operation = self._alloydb.create_backup(
            self._location_parent(),
            backup_id,
            {
                "clusterName": self._cluster_name(cluster_id),
                "displayName": f"Astrolift final backup for {cluster_id}",
                "description": "Astrolift managed-service snapshot",
                "labels": {"astrolift-managed-by": "platform"},
            },
        )
        self._wait_operation(operation, self._deadline())
        backup = self._alloydb.get_backup(backup_name)
        if str(backup.get("state") or "READY") != "READY":
            raise AlloyDBError(f"AlloyDB backup {backup_name} is not ready")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=backup_name,
            created_at=str(backup.get("createTime") or now.isoformat()),
        )

    @driver_op(cloud="gcp", driver="postgres_alloydb")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        try:
            target_id = self._cluster_id_for(target)
            target_name = self._cluster_name(target_id)
            existing = self._get_cluster(target_name)
            source_id = _parse_handle(snapshot.handle)
            source_password = self._secret_store.get(self._master_secret(source_id))
            if source_password is None:
                return ProvisionResult(
                    False,
                    "",
                    "AlloyDB restore source has no Astrolift master-password secret",
                    ["missing_master_password_secret"],
                )
            deadline = self._deadline()
            if existing is None:
                self._secret_store.upsert(
                    self._master_secret(target_id),
                    source_password,
                    labels=_labels_for(target),
                )
                cluster_body = self._cluster_body(target, source_password)
                cluster_body.pop("initialUser", None)
                operation = self._alloydb.restore_cluster(
                    self._location_parent(),
                    target_id,
                    {
                        "backupSource": {"backupName": snapshot.snapshot_id},
                        "cluster": cluster_body,
                    },
                )
                self._wait_operation(operation, deadline)
            else:
                if not _is_owned(existing, target):
                    return ProvisionResult(
                        False,
                        _handle_for(target_id),
                        "refusing to resume restore into an AlloyDB cluster not owned by Astrolift",
                        ["resource_not_owned"],
                    )
                if self._secret_store.get(self._master_secret(target_id)) is None:
                    return ProvisionResult(
                        False,
                        _handle_for(target_id),
                        "restore target exists but its Astrolift master-password secret is missing",
                        ["missing_master_password_secret"],
                    )
                self._wait_resource_ready(target_name, self._alloydb.get_cluster, deadline)
            primary_name = self._primary_name(target_name, target.config or {})
            if self._get_instance(primary_name) is None:
                operation = self._alloydb.create_instance(
                    target_name,
                    self._primary_id(target.config or {}),
                    self._primary_body(target),
                )
                self._wait_operation(operation, deadline)
            else:
                self._wait_resource_ready(primary_name, self._alloydb.get_instance, deadline)
            self._ensure_read_pools(target_name, target, deadline)
            return ProvisionResult(
                True,
                _handle_for(target_id),
                f"AlloyDB cluster restored from {snapshot.snapshot_id}",
                ready=True,
            )
        except Exception as exc:
            return ProvisionResult(False, "", f"restore AlloyDB: {exc}", [str(exc)])

    @driver_op(cloud="gcp", driver="postgres_alloydb", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        native_object = {"type": "object", "additionalProperties": True}
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "cluster_id": {"type": "string", "minLength": 1, "maxLength": 63},
                "primary_instance_id": {"type": "string", "minLength": 1, "maxLength": 63},
                "database_version": {
                    "type": "string",
                    "enum": [
                        "POSTGRES_14",
                        "POSTGRES_15",
                        "POSTGRES_16",
                        "POSTGRES_17",
                        "POSTGRES_18",
                    ],
                },
                "network": {"type": "string"},
                "allocated_ip_range": {"type": "string"},
                "psc_enabled": {"type": "boolean"},
                "allowed_consumer_projects": {"type": "array", "items": {"type": "string"}},
                "public_ip": {"type": "boolean"},
                "outbound_public_ip": {"type": "boolean"},
                "authorized_external_networks": {"type": "array", "items": native_object},
                "machine_type": {"type": "string"},
                "cpu_count": {"type": "integer", "minimum": 2},
                "high_availability": {"type": "boolean"},
                "deletion_protection": {"type": "boolean"},
                "backup_enabled": {"type": "boolean"},
                "backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 35},
                "continuous_backup_enabled": {"type": "boolean"},
                "continuous_backup_recovery_days": {"type": "integer", "minimum": 1, "maximum": 35},
                "kms_key_name": {"type": "string"},
                "database_flags": native_object,
                "require_connectors": {"type": "boolean"},
                "ssl_config": native_object,
                "connection_pool": native_object,
                "query_insights": native_object,
                "observability": native_object,
                "cluster": native_object,
                "primary_instance": native_object,
                "cluster_patch": native_object,
                "primary_patch": native_object,
                "read_pools": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {
                            "id": {"type": "string"},
                            "node_count": {"type": "integer", "minimum": 1, "maximum": 20},
                            "machine_type": {"type": "string"},
                            "cpu_count": {"type": "integer", "minimum": 2},
                            "delete": {
                                "type": "boolean",
                                "description": "Explicitly remove this read pool during update.",
                            },
                            "instance": native_object,
                        },
                        "additionalProperties": True,
                    },
                },
                "connectivity": {"type": "string", "enum": ["auto", "private", "psc", "public"]},
                "database": {"type": "string"},
                "user": {"type": "string"},
                "ssl_mode": {"type": "string"},
            },
        }

    @driver_op(cloud="gcp", driver="postgres_alloydb", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "POSTGRES_HOST": "AlloyDB private, PSC DNS, or public endpoint",
                "POSTGRES_PORT": "5432",
                "POSTGRES_DB": "PostgreSQL database name",
                "POSTGRES_USER": "PostgreSQL user",
                "POSTGRES_PASSWORD": "Secret Manager ref to the master password",
                "POSTGRES_SSL_MODE": "PostgreSQL SSL mode",
                "POSTGRES_MASTER_SECRET_REF": "Logical Secret Manager master-password ref",
                "DATABASE_URL": "Secret Manager ref to the complete PostgreSQL DSN",
                "ALLOYDB_CLUSTER": "Full AlloyDB cluster resource name",
                "ALLOYDB_INSTANCE": "Full AlloyDB primary instance resource name",
                "ALLOYDB_HOST": "Selected AlloyDB endpoint",
                "ALLOYDB_PORT": "5432",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "machine_type",
            "cpu_count",
            "database_flags",
            "query_insights",
            "observability",
            "connection_pool",
            "cluster_patch",
            "primary_patch",
            "read_pools",
        ]

    def _cluster_body(self, spec: ProvisionSpec, password: str) -> dict[str, Any]:
        cfg = spec.config or {}
        body = copy.deepcopy(_mapping(cfg.get("cluster"), "cluster"))
        for field in ("name", "uid", "state", "etag", "createTime", "updateTime"):
            body.pop(field, None)
        labels = dict(body.get("labels") or {})
        labels.update(_labels_for(spec))
        body["labels"] = labels
        body["displayName"] = str(body.get("displayName") or f"{spec.app_slug} {spec.environment_name}")
        body["databaseVersion"] = str(cfg.get("database_version") or self._config.database_version)
        body["initialUser"] = {"user": "postgres", "password": password}

        network = str(cfg.get("network") or self._config.network or "")
        allocated = str(cfg.get("allocated_ip_range") or self._config.allocated_ip_range or "")
        if network or allocated:
            network_config = dict(body.get("networkConfig") or {})
            if network:
                network_config["network"] = network
            if allocated:
                network_config["allocatedIpRange"] = allocated
            body["networkConfig"] = network_config
        if "psc_enabled" in cfg:
            psc = dict(body.get("pscConfig") or {})
            psc["pscEnabled"] = bool(cfg["psc_enabled"])
            body["pscConfig"] = psc
        elif not network and not body.get("pscConfig"):
            raise AlloyDBError("AlloyDB requires network/private IP or psc_enabled=true")

        kms_key = str(cfg.get("kms_key_name") or "")
        if kms_key:
            body["encryptionConfig"] = {"kmsKeyName": kms_key}
        backup_policy = dict(body.get("automatedBackupPolicy") or {})
        backup_policy["enabled"] = bool(cfg.get("backup_enabled", True))
        backup_policy.setdefault(
            "timeBasedRetention",
            {
                "retentionPeriod": (
                    f"{int(cfg.get('backup_retention_days', self._config.backup_retention_days)) * 86400}s"
                )
            },
        )
        body["automatedBackupPolicy"] = backup_policy
        continuous = dict(body.get("continuousBackupConfig") or {})
        continuous["enabled"] = bool(cfg.get("continuous_backup_enabled", True))
        continuous["recoveryWindowDays"] = int(
            cfg.get("continuous_backup_recovery_days", self._config.backup_retention_days),
        )
        body["continuousBackupConfig"] = continuous
        return body

    def _primary_body(self, spec: ProvisionSpec) -> dict[str, Any]:
        cfg = spec.config or {}
        body = copy.deepcopy(_mapping(cfg.get("primary_instance"), "primary_instance"))
        for field in ("name", "uid", "state", "etag", "createTime", "updateTime"):
            body.pop(field, None)
        labels = dict(body.get("labels") or {})
        labels.update(_labels_for(spec))
        body["labels"] = labels
        body["displayName"] = str(body.get("displayName") or f"{spec.app_slug} primary")
        body["instanceType"] = "PRIMARY"
        body["machineConfig"] = self._machine_config(spec.size, cfg)
        body["availabilityType"] = (
            "REGIONAL" if bool(cfg.get("high_availability", self._config.high_availability_default)) else "ZONAL"
        )
        if "database_flags" in cfg:
            body["databaseFlags"] = _mapping(cfg["database_flags"], "database_flags")
        if "connection_pool" in cfg:
            body["connectionPoolConfig"] = _mapping(cfg["connection_pool"], "connection_pool")
        if "query_insights" in cfg:
            body["queryInsightsConfig"] = _mapping(cfg["query_insights"], "query_insights")
        if "observability" in cfg:
            body["observabilityConfig"] = _mapping(cfg["observability"], "observability")
        if "require_connectors" in cfg or "ssl_config" in cfg:
            client = dict(body.get("clientConnectionConfig") or {})
            if "require_connectors" in cfg:
                client["requireConnectors"] = bool(cfg["require_connectors"])
            if "ssl_config" in cfg:
                client["sslConfig"] = _mapping(cfg["ssl_config"], "ssl_config")
            body["clientConnectionConfig"] = client
        network = dict(body.get("networkConfig") or {})
        for key, api_key in (
            ("public_ip", "enablePublicIp"),
            ("outbound_public_ip", "enableOutboundPublicIp"),
        ):
            if key in cfg:
                network[api_key] = bool(cfg[key])
        if "authorized_external_networks" in cfg:
            value = cfg["authorized_external_networks"]
            if not isinstance(value, list):
                raise AlloyDBError("authorized_external_networks must be an array")
            network["authorizedExternalNetworks"] = copy.deepcopy(value)
        if network:
            body["networkConfig"] = network
        if "allowed_consumer_projects" in cfg:
            psc = dict(body.get("pscInstanceConfig") or {})
            psc["allowedConsumerProjects"] = [str(item) for item in cfg["allowed_consumer_projects"]]
            body["pscInstanceConfig"] = psc
        return body

    def _read_pool_body(self, spec: ProvisionSpec, item: dict[str, Any]) -> dict[str, Any]:
        body = copy.deepcopy(_mapping(item.get("instance"), "read_pools[].instance"))
        for field in ("name", "uid", "state", "etag", "createTime", "updateTime"):
            body.pop(field, None)
        labels = dict(body.get("labels") or {})
        labels.update(_labels_for(spec))
        body["labels"] = labels
        body["displayName"] = str(body.get("displayName") or item["id"])
        body["instanceType"] = "READ_POOL"
        body["readPoolConfig"] = {"nodeCount": int(item.get("node_count", 1))}
        machine: dict[str, Any] = {}
        if item.get("machine_type"):
            machine["machineType"] = str(item["machine_type"])
        if item.get("cpu_count"):
            machine["cpuCount"] = int(item["cpu_count"])
        body["machineConfig"] = machine or self._machine_config(spec.size, spec.config or {})
        return body

    def _ensure_read_pools(
        self,
        cluster_name: str,
        spec: ProvisionSpec,
        deadline: float,
    ) -> None:
        value = (spec.config or {}).get("read_pools", [])
        if not isinstance(value, list):
            raise AlloyDBError("read_pools must be an array")
        for raw in value:
            item = _mapping(raw, "read_pools[]")
            if not item.get("id"):
                raise AlloyDBError("each read_pools entry requires id")
            instance_id = _resource_id(str(item["id"]))
            name = f"{cluster_name}/instances/{instance_id}"
            existing = self._get_instance(name)
            if existing is None:
                operation = self._alloydb.create_instance(
                    cluster_name,
                    instance_id,
                    self._read_pool_body(spec, item),
                )
                self._wait_operation(operation, deadline)
            elif not _has_platform_ownership(existing):
                raise AlloyDBError(f"read pool {name} is not owned by Astrolift")
            else:
                self._wait_resource_ready(name, self._alloydb.get_instance, deadline)

    def _update_read_pools(
        self,
        cluster_name: str,
        cluster: dict[str, Any],
        cfg: dict[str, Any],
        deadline: float,
    ) -> None:
        if "read_pools" not in cfg:
            return
        value = cfg["read_pools"]
        if not isinstance(value, list):
            raise AlloyDBError("read_pools must be an array")
        for raw in value:
            item = _mapping(raw, "read_pools[]")
            if not item.get("id"):
                raise AlloyDBError("each read_pools entry requires id")
            instance_id = _resource_id(str(item["id"]))
            name = f"{cluster_name}/instances/{instance_id}"
            existing = self._get_instance(name)
            if bool(item.get("delete")):
                if existing is not None:
                    if not _has_platform_ownership(existing):
                        raise AlloyDBError(f"read pool {name} is not owned by Astrolift")
                    self._wait_operation(self._alloydb.delete_instance(name), deadline)
                continue

            native = _mapping(item.get("instance"), "read_pools[].instance")
            patch = dict(native)
            if "node_count" in item:
                patch["readPoolConfig"] = {"nodeCount": int(item["node_count"])}
            machine: dict[str, Any] = {}
            if item.get("machine_type"):
                machine["machineType"] = str(item["machine_type"])
            if item.get("cpu_count"):
                machine["cpuCount"] = int(item["cpu_count"])
            if machine:
                patch["machineConfig"] = machine
            if existing is None:
                labels = dict(cluster.get("labels") or {})
                labels["astrolift-managed-by"] = "platform"
                body = {
                    **patch,
                    "labels": labels,
                    "displayName": str(patch.get("displayName") or instance_id),
                    "instanceType": "READ_POOL",
                    "readPoolConfig": patch.get("readPoolConfig") or {"nodeCount": 1},
                    "machineConfig": patch.get("machineConfig") or {"machineType": self._config.machine_type_default},
                }
                self._wait_operation(
                    self._alloydb.create_instance(cluster_name, instance_id, body),
                    deadline,
                )
            elif patch:
                if not _has_platform_ownership(existing):
                    raise AlloyDBError(f"read pool {name} is not owned by Astrolift")
                patch = _safe_patch(patch, existing.get("labels") or {}, instance=True)
                self._wait_operation(
                    self._alloydb.patch_instance(name, patch, update_mask=sorted(patch)),
                    deadline,
                )

    def _aggregate_status(self, cluster_name: str) -> ServiceStatus:
        cluster = self._alloydb.get_cluster(cluster_name)
        cluster_state = str(cluster.get("state") or "STATE_UNSPECIFIED")
        if cluster_state == "FAILED":
            return ServiceStatus(_handle_for(cluster_name.rsplit("/", 1)[-1]), "error", "AlloyDB cluster failed")
        if cluster_state == "DELETING":
            state = "deprovisioning"
        elif cluster_state in _PROVISIONING_STATES:
            state = "provisioning"
        elif cluster_state in _UPDATING_STATES:
            state = "updating"
        elif cluster_state in {"EMPTY", "STOPPED"}:
            state = "error"
        else:
            state = "available"
        instances = self._alloydb.list_instances(cluster_name)
        instance_states = [str(item.get("state") or "STATE_UNSPECIFIED") for item in instances]
        if any(value == "FAILED" for value in instance_states):
            state = "error"
        elif any(value == "DELETING" for value in instance_states):
            state = "deprovisioning"
        elif any(value == "STOPPED" for value in instance_states):
            state = "error"
        elif any(value in _UPDATING_STATES for value in instance_states):
            state = "updating"
        elif state == "available" and (
            not any(str(item.get("instanceType")) == "PRIMARY" for item in instances)
            or any(value != _READY for value in instance_states)
        ):
            state = "provisioning"
        summary = (
            ", ".join(
                f"{str(item.get('name', '')).rsplit('/', 1)[-1]}={item.get('state', 'UNKNOWN')}" for item in instances
            )
            or "no instances"
        )
        return ServiceStatus(
            _handle_for(cluster_name.rsplit("/", 1)[-1]),
            state,
            f"cluster={cluster_state}; {summary}",
        )

    def _wait_operation(self, operation: dict[str, Any], deadline: float) -> dict[str, Any]:
        current = operation
        while True:
            if current.get("done"):
                if current.get("error"):
                    error = current["error"]
                    raise AlloyDBError(str(error.get("message") if isinstance(error, dict) else error))
                response = current.get("response")
                return dict(response) if isinstance(response, dict) else {}
            name = str(current.get("name") or "")
            if not name:
                raise AlloyDBError("AlloyDB long-running operation response has no name")
            self._pause(deadline)
            current = self._alloydb.get_operation(name)

    def _wait_resource_ready(
        self,
        name: str,
        getter: Callable[[str], dict[str, Any]],
        deadline: float,
    ) -> dict[str, Any]:
        while True:
            resource = getter(name)
            state = str(resource.get("state") or "STATE_UNSPECIFIED")
            if state == _READY:
                return resource
            if state in {"FAILED", "EMPTY"}:
                raise AlloyDBError(f"AlloyDB resource {name} entered {state}")
            self._pause(deadline)

    def _pause(self, deadline: float) -> None:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise AlloyDBError("timed out waiting for AlloyDB operation")
        self._sleep(min(self._config.operation_poll_interval_seconds, remaining))

    def _deadline(self) -> float:
        return self._monotonic() + self._config.operation_timeout_seconds

    def _get_cluster(self, name: str) -> dict[str, Any] | None:
        try:
            return self._alloydb.get_cluster(name)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _get_instance(self, name: str) -> dict[str, Any] | None:
        try:
            return self._alloydb.get_instance(name)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _machine_config(self, size: str | None, cfg: dict[str, Any]) -> dict[str, Any]:
        machine: dict[str, Any] = {}
        machine_type = str(
            cfg.get("machine_type") or _SIZE_TO_MACHINE_TYPE.get(size or "") or self._config.machine_type_default,
        )
        if machine_type:
            machine["machineType"] = machine_type
        if cfg.get("cpu_count"):
            machine["cpuCount"] = int(cfg["cpu_count"])
        return machine

    def _cluster_id_for(self, spec: ProvisionSpec) -> str:
        configured = str((spec.config or {}).get("cluster_id") or "")
        if configured:
            return _resource_id(configured)
        return _resource_id(
            f"{self._config.cluster_name_prefix}-{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}-{spec.service_handle_hint or 'pg'}",
        )

    def _location_parent(self) -> str:
        return f"projects/{self._config.project_id}/locations/{self._config.region}"

    def _cluster_name(self, cluster_id: str) -> str:
        return f"{self._location_parent()}/clusters/{cluster_id}"

    def _primary_id(self, cfg: dict[str, Any]) -> str:
        return _resource_id(str(cfg.get("primary_instance_id") or self._config.primary_instance_id))

    def _primary_name(self, cluster_name: str, cfg: dict[str, Any] | None = None) -> str:
        return f"{cluster_name}/instances/{self._primary_id(cfg or {})}"

    def _master_secret(self, cluster_id: str) -> str:
        return f"{self._config.secret_manager_prefix}/{cluster_id}/master"

    def _url_secret(self, cluster_id: str) -> str:
        return f"{self._config.secret_manager_prefix}/{cluster_id}/url"

    def _delete_connection_secrets(self, cluster_id: str, *, strict: bool = False) -> None:
        errors: list[str] = []
        for path in (self._master_secret(cluster_id), self._url_secret(cluster_id)):
            try:
                self._secret_store.delete(path)
            except ManagedSecretStoreError as exc:
                errors.append(str(exc))
        if errors and strict:
            raise AlloyDBError("; ".join(errors))


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AlloyDBError(f"{field} must be an object")
    return dict(value)


def _validate_config(cfg: dict[str, Any]) -> None:
    if cfg.get("database_version") == "POSTGRES_13":
        raise AlloyDBError("AlloyDB POSTGRES_13 is deprecated; choose POSTGRES_14 or newer")
    connectivity = str(cfg.get("connectivity") or "auto").lower()
    if connectivity not in {"auto", "private", "psc", "public"}:
        raise AlloyDBError("connectivity must be auto, private, psc, or public")
    for key in ("backup_retention_days", "continuous_backup_recovery_days"):
        if key in cfg and not 1 <= int(cfg[key]) <= 35:
            raise AlloyDBError(f"{key} must be between 1 and 35")


def _generate_master_password(length: int = 32) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def _resource_id(value: str) -> str:
    raw = value.lower().replace("_", "-")
    normalized = "".join(char if char.isalnum() or char == "-" else "-" for char in raw)
    normalized = normalized.strip("-")[:63].rstrip("-")
    if not normalized:
        raise AlloyDBError("AlloyDB resource id is empty after normalization")
    if not normalized[0].isalpha():
        normalized = f"a-{normalized}"[:63].rstrip("-")
    return normalized


def _snapshot_id(cluster_id: str, now: datetime) -> str:
    suffix = f"snap-{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    prefix_length = 63 - len(suffix) - 1
    prefix = cluster_id[:prefix_length].rstrip("-")
    return _resource_id(f"{prefix}-{suffix}")


def _handle_for(cluster_id: str) -> str:
    return f"{KIND}/{cluster_id}"


def _parse_handle(handle: str) -> str:
    kind, separator, cluster_id = handle.partition("/")
    if separator != "/" or kind != KIND or not cluster_id or "/" in cluster_id:
        raise AlloyDBError(f"handle {handle!r} must be 'postgres/<cluster-id>'")
    return _resource_id(cluster_id)


def _labels_for(spec: ProvisionSpec) -> dict[str, str]:
    def sanitize(value: str) -> str:
        return "".join(char if char.isalnum() or char in "-_" else "-" for char in value.lower())[:63]

    labels = {
        "astrolift-managed-by": "platform",
        "astrolift-organization": sanitize(spec.organization_slug),
        "astrolift-app": sanitize(spec.app_slug),
        "astrolift-environment": sanitize(spec.environment_name),
        "astrolift-cluster": sanitize(spec.tenant_cluster_id),
        "astrolift-isolation": sanitize(spec.isolation),
    }
    if spec.binding_id:
        labels["astrolift-binding"] = sanitize(spec.binding_id)
    if spec.managed_service_id:
        labels["astrolift-managed-service-id"] = sanitize(spec.managed_service_id)
        labels[MANAGED_SERVICE_ID_LABEL] = sanitize(spec.managed_service_id)
    for key, value in (spec.tags or {}).items():
        labels[f"astrolift-extra-{sanitize(key)}"[:63]] = sanitize(str(value))
    return labels


def _has_platform_ownership(resource: dict[str, Any]) -> bool:
    return str((resource.get("labels") or {}).get("astrolift-managed-by")) == "platform"


def _is_owned(resource: dict[str, Any], spec: ProvisionSpec) -> bool:
    labels = resource.get("labels") or {}
    expected = _labels_for(spec)
    return _has_platform_ownership(resource) and all(
        labels.get(key) == expected[key]
        for key in (
            "astrolift-organization",
            "astrolift-app",
            "astrolift-environment",
            "astrolift-cluster",
        )
    )


def _safe_patch(
    patch: dict[str, Any],
    existing_labels: dict[str, Any],
    *,
    instance: bool,
) -> dict[str, Any]:
    body = copy.deepcopy(patch)
    forbidden = {
        "name",
        "uid",
        "state",
        "etag",
        "createTime",
        "updateTime",
        "deleteTime",
        "reconciling",
    }
    if instance:
        forbidden.update({"instanceType", "networkConfig.network"})
    for field in forbidden:
        body.pop(field, None)
    if "labels" in body:
        labels = dict(existing_labels)
        labels.update(_mapping(body["labels"], "labels"))
        for key, value in existing_labels.items():
            if str(key).startswith("astrolift-"):
                labels[str(key)] = value
        body["labels"] = labels
    return body


def _not_found(exc: Exception) -> bool:
    return isinstance(exc, AlloyDBNotFound) or type(exc).__name__ == "NotFound" or "404" in str(exc)


__all__ = [
    "AlloyDBConfig",
    "AlloyDBError",
    "AlloyDBNotFound",
    "AlloyDBPostgresDriver",
    "AlloyDBRestClient",
]
