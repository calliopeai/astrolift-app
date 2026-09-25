"""Memorystore for Valkey lifecycle using the public Memorystore v1 REST API."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
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
from gcp.managed._ownership import label_adoption_refusal
from gcp.managed._secret_store import ManagedSecretStore, ManagedSecretStoreError

KIND = "redis"
_API_ROOT = "https://memorystore.googleapis.com/v1"
_SHARED_CA_ROOT = "https://storage.googleapis.com/memorystore-ca-bundles-prod/prod/regions"
_GA_VERSIONS = {"VALKEY_7_2", "VALKEY_8_0", "VALKEY_9_0"}
_PREVIEW_VERSIONS = {"VALKEY_9_1"}
_NODE_TYPES = {
    "SHARED_CORE_NANO",
    "CUSTOM_PICO",
    "CUSTOM_MICRO",
    "CUSTOM_MINI",
    "STANDARD_SMALL",
    "STANDARD_LARGE",
    "HIGHCPU_MEDIUM",
    "HIGHMEM_MEDIUM",
    "HIGHMEM_XLARGE",
    "HIGHMEM_2XLARGE",
}
_DAYS = {"MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"}
_CA_MODES = {
    "GOOGLE_MANAGED_PER_INSTANCE_CA",
    "GOOGLE_MANAGED_SHARED_CA",
    "CUSTOMER_MANAGED_CAS_CA",
}
_STATE = {
    "CREATING": "provisioning",
    "ACTIVE": "available",
    "UPDATING": "updating",
    "MIGRATING": "updating",
    "DELETING": "deprovisioning",
}
_MUTABLE_FIELDS = {
    "shard_count": "shardCount",
    "replica_count": "replicaCount",
    "node_type": "nodeType",
    "engine_version": "engineVersion",
    "engine_configs": "engineConfigs",
    "deletion_protection": "deletionProtectionEnabled",
    "async_endpoint_deletion": "asyncInstanceEndpointsDeletionEnabled",
}
_PRIMARY_SYNCED_UPDATE_FIELDS = frozenset(
    {
        "shard_count",
        "node_type",
        "engine_version",
        "engine_configs",
        "persistence_mode",
        "rdb_snapshot_period",
        "rdb_snapshot_start_time",
        "aof_append_fsync",
    }
)


class MemorystoreValkeyError(RuntimeError):
    pass


class MemorystoreValkeyNotFound(MemorystoreValkeyError):
    pass


@dataclass(frozen=True)
class MemorystoreValkeyConfig:
    project_id: str
    region: str
    network: str = ""
    instance_name_prefix: str = "astrolift"
    engine_version: str = "VALKEY_9_0"
    node_type: str = "HIGHMEM_MEDIUM"
    mode: str = "CLUSTER"
    shard_count: int = 1
    replica_count: int = 1
    authorization_mode: str = "IAM_AUTH"
    token_auth_user: str = "default"
    token_auth_rotation_generation: int = 1
    token_auth_retire_generation: int = 0
    transit_encryption_default: bool = True
    persistence_mode: str = "RDB"
    automated_backup_default: bool = True
    backup_retention_days: int = 35
    deletion_protection_default: bool = True
    kms_key: str = ""
    server_ca_mode: str = ""
    server_ca_pool: str = ""
    secret_manager_prefix: str = "astrolift/memorystore-valkey"
    secret_id_prefix: str = "astrolift"
    allow_preview_features: bool = False
    api_endpoint: str = _API_ROOT
    operation_timeout_seconds: float = 1800.0
    poll_interval_seconds: float = 3.0
    adopt_existing_instance: bool = False


class MemorystoreValkeyRestClient:
    """Authenticated request-shaped adapter for Memorystore for Valkey."""

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

    def create_instance(self, parent: str, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/instances",
            params={"instanceId": instance_id, "requestId": _request_id("create", parent, instance_id, body)},
            json=body,
        )

    def patch_instance(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        return self._request(
            "PATCH",
            name,
            params={
                "updateMask": ",".join(update_mask),
                "requestId": _request_id("patch", name, update_mask, body),
            },
            json={"name": name, **body},
        )

    def delete_instance(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", name, params={"requestId": _request_id("delete", name)})

    def backup_instance(self, name: str, backup_id: str, ttl: str) -> dict[str, Any]:
        return self._request("POST", f"{name}:backup", json={"backupId": backup_id, "ttl": ttl})

    def get_backup(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def list_backups(self, backup_collection: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            params = {"pageSize": "1000"}
            if token:
                params["pageToken"] = token
            payload = self._request("GET", f"{backup_collection}/backups", params=params)
            rows.extend(payload.get("backups") or [])
            token = str(payload.get("nextPageToken") or "")
            if not token:
                return rows

    def list_token_auth_users(self, instance: str) -> list[dict[str, Any]]:
        return self._list("tokenAuthUsers", f"{instance}/tokenAuthUsers")

    def add_token_auth_user(self, instance: str, username: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{instance}:addTokenAuthUser",
            json={"tokenAuthUser": username},
        )

    def list_auth_tokens(self, token_auth_user: str) -> list[dict[str, Any]]:
        return self._list("authTokens", f"{token_auth_user}/authTokens")

    def get_auth_token(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def add_auth_token(self, token_auth_user: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{token_auth_user}:addAuthToken",
            json={"authToken": {}},
        )

    def delete_auth_token(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", name)

    def get_certificate_authority(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"{name}/certificateAuthority")

    def get_shared_certificate_authority(self, region: str) -> str:
        response = self._session.request(
            "GET",
            f"{_SHARED_CA_ROOT}/{region}/ca_bundle.pem",
            timeout=30,
        )
        if not 200 <= response.status_code < 300:
            raise MemorystoreValkeyError(f"Memorystore shared CA bundle HTTP {response.status_code}: {response.text}")
        return str(response.text)

    def get_operation(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def _list(self, collection_key: str, resource: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            params = {"pageSize": "1000"}
            if token:
                params["pageToken"] = token
            payload = self._request("GET", resource, params=params)
            rows.extend(payload.get(collection_key) or [])
            token = str(payload.get("nextPageToken") or "")
            if not token:
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
            raise MemorystoreValkeyNotFound(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with contextlib.suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise MemorystoreValkeyError(f"Memorystore HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class MemorystoreValkeyDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: MemorystoreValkeyConfig,
        client: Any | None = None,
        secrets_client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._valkey = client or MemorystoreValkeyRestClient(endpoint=config.api_endpoint)
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

    @driver_op(cloud="gcp", driver="redis_memorystore_valkey", audit=True, sensitive_kind="managed_service_provision")
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        return self._provision(spec)

    def _provision(self, spec: ProvisionSpec, *, import_source: dict[str, Any] | None = None) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_memorystore_valkey_config"])
        role = str(cfg.get("cross_instance_role") or "NONE")
        if import_source and role == "SECONDARY":
            return ProvisionResult(
                False,
                "",
                "a cross-region secondary cannot also restore or import data",
                ["secondary_restore_not_supported"],
            )
        instance_id = self._instance_id(spec, cfg)
        handle = _handle(instance_id)
        try:
            current = self._get_instance(instance_id)
            restore_hash = _source_hash(import_source) if import_source else ""
            if current is None:
                if role == "SECONDARY":
                    primary = self._valkey.get_instance(str(cfg["primary_instance"]))
                    self._assert_secondary_create_compatible(primary, cfg)
                    primary_auth = str(primary.get("authorizationMode") or "AUTH_DISABLED")
                    if primary_auth == "TOKEN_AUTH" and not bool(
                        cfg.get("allow_preview_features", self._config.allow_preview_features)
                    ):
                        raise MemorystoreValkeyError(
                            "the cross-region primary uses Preview TOKEN_AUTH; "
                            "set allow_preview_features=true for the secondary"
                        )
                    cfg.setdefault("authorization_mode", primary_auth)
                body = self._instance_body(
                    spec,
                    cfg,
                    import_source=import_source,
                    secondary=role == "SECONDARY",
                )
                self._wait_operation(self._valkey.create_instance(self._parent(), instance_id, body))
                current = self._valkey.get_instance(self._instance_name(instance_id))
                self._reconcile(current, cfg)
                current = self._valkey.get_instance(self._instance_name(instance_id))
            else:
                self._assert_owned(current, cfg, spec=spec)
                if restore_hash and (current.get("labels") or {}).get("astrolift-restore-source") != restore_hash:
                    raise MemorystoreValkeyError("restore target exists but was created from a different source")
                self._assert_immutable_compatible(current, cfg)
                self._reconcile(current, cfg)
                current = self._valkey.get_instance(self._instance_name(instance_id))
            self._reconcile_token_auth(instance_id, current, cfg, spec=spec)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Memorystore for Valkey: {exc}", [str(exc)])
        state = str(current.get("state") or "STATE_UNSPECIFIED")
        return ProvisionResult(
            True,
            handle,
            f"Memorystore for Valkey {instance_id} reconciled ({state})",
            ready=state == "ACTIVE",
        )

    @driver_op(cloud="gcp", driver="redis_memorystore_valkey", audit=True, sensitive_kind="managed_service_update")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            instance_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        if spec.size and "node_type" not in cfg:
            cfg["node_type"] = _node_type_for_size(spec.size, self._config.node_type)
        try:
            current = self._valkey.get_instance(self._instance_name(instance_id))
            error = self._validate_config(cfg, current=current)
            if error:
                return UpdateResult(False, spec.handle, error, ["invalid_memorystore_valkey_config"])
            self._assert_owned(current, cfg)
            self._assert_immutable_compatible(current, cfg, include_defaults=False)
            self._reconcile(current, cfg)
            current = self._valkey.get_instance(self._instance_name(instance_id))
            self._reconcile_token_auth(instance_id, current, cfg)
        except MemorystoreValkeyNotFound:
            return UpdateResult(False, spec.handle, f"Memorystore for Valkey {instance_id} not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Memorystore for Valkey: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Memorystore for Valkey {instance_id} reconciled")

    @driver_op(cloud="gcp", driver="redis_memorystore_valkey", audit=True, sensitive_kind="managed_service_deprovision")
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        try:
            instance_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        try:
            current = self._get_instance(instance_id)
        except Exception as exc:
            return _deprovision_error(spec.handle, "describe Memorystore for Valkey", exc)
        if current is None:
            self._delete_token_secrets(
                instance_id,
                str(cfg.get("token_auth_user") or self._config.token_auth_user),
            )
            return DeprovisionResult(True, spec.handle, f"Memorystore for Valkey {instance_id} already gone")
        owned = (current.get("labels") or {}).get("astrolift-managed-by") == "platform"
        if not owned and not cfg.get("delete_adopted_instance"):
            return DeprovisionResult(
                False,
                spec.handle,
                "refusing to delete an adopted Valkey instance; set delete_adopted_instance=true",
                ["adopted_instance_delete_requires_opt_in"],
                retryable=False,
            )
        protected = bool(current.get("deletionProtectionEnabled"))
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Memorystore for Valkey {instance_id} has deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        retained = ""
        if not delete_data:
            try:
                retained = self.snapshot(ServiceHandle(spec.handle)).snapshot_id
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"retain Valkey data before delete: {exc}",
                    [str(exc)],
                    retryable=False,
                )
        try:
            name = self._instance_name(instance_id)
            protection_disabled = False
            if protected:
                self._wait_operation(
                    self._valkey.patch_instance(
                        name,
                        {"deletionProtectionEnabled": False},
                        update_mask=["deletionProtectionEnabled"],
                    )
                )
                protection_disabled = True
            self._wait_operation(self._valkey.delete_instance(name))
        except MemorystoreValkeyNotFound:
            pass
        except Exception as exc:
            rollback = ""
            if protection_disabled:
                try:
                    self._wait_operation(
                        self._valkey.patch_instance(
                            name,
                            {"deletionProtectionEnabled": True},
                            update_mask=["deletionProtectionEnabled"],
                        )
                    )
                except Exception as rollback_exc:
                    rollback = f"; deletion protection rollback also failed: {rollback_exc}"
            if rollback:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"delete Memorystore for Valkey: {exc}{rollback}",
                    [str(exc)],
                    retryable=True,
                )
            return _deprovision_error(spec.handle, "delete Memorystore for Valkey", exc)
        self._delete_token_secrets(
            instance_id,
            str((current.get("labels") or {}).get("astrolift-token-user") or self._config.token_auth_user),
        )
        message = f"Memorystore for Valkey {instance_id} deleted"
        if retained:
            message += f"; retained backup {retained}"
        return DeprovisionResult(True, spec.handle, message)

    @driver_op(cloud="gcp", driver="redis_memorystore_valkey")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            instance_id = _parse_handle(handle.handle)
            current = self._valkey.get_instance(self._instance_name(instance_id))
        except MemorystoreValkeyNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Memorystore for Valkey does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Memorystore for Valkey: {exc}")
        provider_state = str(current.get("state") or "STATE_UNSPECIFIED")
        state = _STATE.get(provider_state, "error")
        detail = current.get("stateInfo") or {}
        return ServiceStatus(handle.handle, state, f"Memorystore for Valkey is {provider_state}: {detail}")

    @driver_op(cloud="gcp", driver="redis_memorystore_valkey")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        instance_id = _parse_handle(handle.handle)
        current = self._valkey.get_instance(self._instance_name(instance_id))
        if str(current.get("state") or "") != "ACTIVE":
            raise MemorystoreValkeyError(f"Memorystore for Valkey {instance_id} is not ACTIVE")
        endpoint, reader, all_endpoints = _connection_endpoints(current)
        if not endpoint:
            raise MemorystoreValkeyError("Memorystore instance has no active PSC endpoint")
        host, port, _ = endpoint
        tls = current.get("transitEncryptionMode") == "SERVER_AUTHENTICATION"
        auth_mode = str(current.get("authorizationMode") or "AUTH_DISABLED")
        scheme = "rediss" if tls else "redis"
        auth_label = (current.get("labels") or {}).get("astrolift-token-user")
        token_user = str((config or {}).get("token_auth_user") or auth_label or self._config.token_auth_user)
        env = {
            "REDIS_HOST": ValueRef(literal=host),
            "REDIS_PORT": ValueRef(literal=str(port)),
            "REDIS_TLS": ValueRef(literal=str(tls).lower()),
            "REDIS_URL": ValueRef(literal=f"{scheme}://{host}:{port}"),
            "REDIS_AUTH_MODE": ValueRef(
                literal=("gcp_iam" if auth_mode == "IAM_AUTH" else "token" if auth_mode == "TOKEN_AUTH" else "disabled")
            ),
            "REDIS_RESOURCE_ARN": ValueRef(literal=str(current.get("name") or self._instance_name(instance_id))),
            "GCP_MEMORYSTORE_PROJECT": ValueRef(literal=self._config.project_id),
            "GCP_MEMORYSTORE_REGION": ValueRef(literal=self._config.region),
            "GCP_MEMORYSTORE_INSTANCE": ValueRef(literal=instance_id),
            "GCP_MEMORYSTORE_UID": ValueRef(literal=str(current.get("uid") or "")),
            "GCP_MEMORYSTORE_MODE": ValueRef(literal=str(current.get("mode") or "")),
            "GCP_MEMORYSTORE_ENGINE_VERSION": ValueRef(literal=str(current.get("engineVersion") or "")),
            "GCP_MEMORYSTORE_ENDPOINTS": ValueRef(literal=json.dumps(all_endpoints, separators=(",", ":"))),
            "GCP_MEMORYSTORE_SERVER_CA_MODE": ValueRef(literal=str(current.get("serverCaMode") or "")),
            "GCP_MEMORYSTORE_SERVER_CA_POOL": ValueRef(literal=str(current.get("serverCaPool") or "")),
            "GCP_MEMORYSTORE_ACL_POLICY": ValueRef(literal=str(current.get("aclPolicy") or "")),
            "GCP_MEMORYSTORE_ACL_POLICY_IN_SYNC": ValueRef(literal=str(bool(current.get("aclPolicyInSync"))).lower()),
        }
        if auth_mode == "IAM_AUTH":
            env["REDIS_USER"] = ValueRef(literal="default")
        elif auth_mode == "TOKEN_AUTH":
            secret_name = self._token_secret(instance_id, token_user)
            token = self._secret_store.get(secret_name)
            if token is None:
                raise MemorystoreValkeyError(
                    f"binding requested for {instance_id}, but token secret {secret_name!r} is missing"
                )
            url_secret = self._token_url_secret(instance_id, token_user)
            try:
                self._secret_store.upsert(
                    url_secret,
                    f"{scheme}://{quote(token_user, safe='')}:{quote(token, safe='')}@{host}:{port}",
                )
                if reader:
                    self._secret_store.upsert(
                        self._token_reader_url_secret(instance_id, token_user),
                        f"{scheme}://{quote(token_user, safe='')}:{quote(token, safe='')}@{reader[0]}:{reader[1]}",
                    )
            except ManagedSecretStoreError as exc:
                raise MemorystoreValkeyError(str(exc)) from exc
            env["REDIS_USER"] = ValueRef(literal=token_user)
            env["REDIS_PASSWORD"] = ValueRef(secret_ref=secret_name)
            env["REDIS_URL"] = ValueRef(secret_ref=url_secret)
            if reader:
                env["REDIS_READER_URL"] = ValueRef(secret_ref=self._token_reader_url_secret(instance_id, token_user))
        elif reader:
            env["REDIS_READER_URL"] = ValueRef(literal=f"{scheme}://{reader[0]}:{reader[1]}")
        ca_mode = str(current.get("serverCaMode") or "")
        if tls and ca_mode == "GOOGLE_MANAGED_SHARED_CA":
            certificates = [self._valkey.get_shared_certificate_authority(self._config.region)]
            if certificates[0]:
                # REDIS_CA_CERT is a newline-joined PEM chain on the other
                # branch and in binding_schema, so join here too (#1403). The
                # shared CA is a single certificate, so the string is
                # byte-identical -- this only makes the contract explicit.
                env["REDIS_CA_CERT"] = ValueRef(literal="\n".join(certificates))
        elif tls and ca_mode != "CUSTOMER_MANAGED_CAS_CA":
            ca = self._valkey.get_certificate_authority(str(current.get("name") or self._instance_name(instance_id)))
            certificates = _ca_certificates(ca)
            if certificates:
                env["REDIS_CA_CERT"] = ValueRef(literal="\n".join(certificates))
        grants = []
        if auth_mode == "IAM_AUTH":
            grants.append(
                Grant(
                    str(current.get("name") or self._instance_name(instance_id)), ["roles/memorystore.dbConnectionUser"]
                )
            )
        return Binding(
            env_vars=env,
            iam_grants=grants,
            notes=(
                "Private Service Connect Valkey endpoint. IAM-auth clients mint short-lived Google access tokens; "
                "TOKEN_AUTH credentials are stored in Secret Manager and rotated in two phases."
            ),
        )

    @driver_op(cloud="gcp", driver="redis_memorystore_valkey", audit=True, sensitive_kind="managed_service_snapshot")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        instance_id = _parse_handle(handle.handle)
        name = self._instance_name(instance_id)
        current = self._valkey.get_instance(name)
        persistence = current.get("persistenceConfig") or {}
        if persistence.get("mode") == "DISABLED":
            raise MemorystoreValkeyError("Valkey persistence is disabled; managed backups are unavailable")
        created = datetime.now(UTC)
        backup_id = _backup_id(instance_id, created)
        retention = int(
            (current.get("labels") or {}).get("astrolift-backup-days") or self._config.backup_retention_days
        )
        if not 1 <= retention <= 36500:
            raise MemorystoreValkeyError("backup retention must be between 1 day and 100 years")
        operation = self._wait_operation(self._valkey.backup_instance(name, backup_id, f"{retention * 86400}s"))
        backup_name = str((operation.get("response") or {}).get("name") or "")
        refreshed = self._valkey.get_instance(name)
        collection = str(refreshed.get("backupCollection") or current.get("backupCollection") or "")
        if not backup_name and collection:
            backup_name = f"{collection}/backups/{backup_id}"
        if not backup_name:
            raise MemorystoreValkeyError("backup completed without a backup resource name")
        backup = self._valkey.get_backup(backup_name)
        if str(backup.get("state") or "") != "READY":
            raise MemorystoreValkeyError(f"Valkey backup {backup_name} is not READY")
        return SnapshotHandle(handle.handle, backup_name, str(backup.get("createTime") or created.isoformat()))

    @driver_op(cloud="gcp", driver="redis_memorystore_valkey", audit=True, sensitive_kind="managed_service_restore")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        source: dict[str, Any]
        if snapshot.snapshot_id.startswith("gs://"):
            source = {"gcsSource": {"uris": [snapshot.snapshot_id]}}
        else:
            try:
                backup = self._valkey.get_backup(snapshot.snapshot_id)
            except Exception as exc:
                return ProvisionResult(False, "", f"describe Valkey backup: {exc}", [str(exc)])
            if str(backup.get("state") or "") != "READY":
                return ProvisionResult(
                    False, "", f"Valkey backup {snapshot.snapshot_id} is not READY", ["backup_not_ready"]
                )
            source = {"managedBackupSource": {"backup": snapshot.snapshot_id}}
        return self._provision(target, import_source=source)

    def editable_fields(self) -> list[str]:
        return sorted(
            set(_MUTABLE_FIELDS)
            | {
                "persistence_mode",
                "rdb_snapshot_period",
                "rdb_snapshot_start_time",
                "aof_append_fsync",
                "maintenance_day",
                "maintenance_hour_utc",
                "maintenance_minute_utc",
                "automated_backup",
                "backup_retention_days",
                "backup_start_hour_utc",
                "backup_start_minute_utc",
                "cross_instance_role",
                "primary_instance",
                "secondary_instances",
                "maintenance_version",
                "acl_policy",
                "clear_acl_policy",
                "token_auth_rotation_generation",
                "token_auth_retire_generation",
            }
        )

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{2,61}[a-z0-9]$"},
                "engine_version": {"type": "string", "enum": sorted(_GA_VERSIONS | _PREVIEW_VERSIONS)},
                "allow_preview_features": {"type": "boolean"},
                "node_type": {"type": "string", "enum": sorted(_NODE_TYPES)},
                "mode": {"type": "string", "enum": ["CLUSTER", "CLUSTER_DISABLED"]},
                "shard_count": {"type": "integer", "minimum": 1, "maximum": 250},
                "replica_count": {"type": "integer", "minimum": 0, "maximum": 5},
                "authorization_mode": {
                    "type": "string",
                    "enum": ["IAM_AUTH", "TOKEN_AUTH", "AUTH_DISABLED"],
                },
                "token_auth_user": {
                    "type": "string",
                    "pattern": "^[a-z][a-z0-9_-]{0,62}$",
                    "description": "Astrolift-managed local Valkey user for TOKEN_AUTH.",
                },
                "token_auth_rotation_generation": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Monotonic desired token generation; creates and publishes a new token.",
                },
                "token_auth_retire_generation": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Highest previous generation safe to revoke after consumers have rolled to the new secret."
                    ),
                },
                "transit_encryption": {"type": "boolean"},
                "persistence_mode": {"type": "string", "enum": ["DISABLED", "RDB", "AOF"]},
                "rdb_snapshot_period": {
                    "type": "string",
                    "enum": ["ONE_HOUR", "SIX_HOURS", "TWELVE_HOURS", "TWENTY_FOUR_HOURS"],
                },
                "rdb_snapshot_start_time": {"type": "string", "format": "date-time"},
                "aof_append_fsync": {"type": "string", "enum": ["NEVER", "EVERY_SEC", "ALWAYS"]},
                "engine_configs": {"type": "object", "additionalProperties": {"type": "string"}},
                "zone_distribution_mode": {"type": "string", "enum": ["MULTI_ZONE", "SINGLE_ZONE"]},
                "zone": {"type": "string"},
                "psc_networks": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}},
                "endpoints": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "maintenance_day": {"type": "string", "enum": sorted(_DAYS)},
                "maintenance_hour_utc": {"type": "integer", "minimum": 0, "maximum": 23},
                "maintenance_minute_utc": {"type": "integer", "minimum": 0, "maximum": 59},
                "automated_backup": {"type": "boolean"},
                "backup_retention_days": {"type": "integer", "minimum": 1, "maximum": 365},
                "backup_start_hour_utc": {"type": "integer", "minimum": 0, "maximum": 23},
                "backup_start_minute_utc": {"type": "integer", "const": 0},
                "allow_fewer_zones_deployment": {
                    "type": "boolean",
                    "deprecated": True,
                    "description": (
                        "Deprecated by Google; omitted unless explicitly set for an existing compatibility case."
                    ),
                },
                "deletion_protection": {"type": "boolean"},
                "async_endpoint_deletion": {"type": "boolean"},
                "kms_key": {"type": "string"},
                "server_ca_mode": {"type": "string", "enum": sorted(_CA_MODES)},
                "server_ca_pool": {"type": "string"},
                "maintenance_version": {"type": "string", "minLength": 1},
                "acl_policy": {"type": "string", "minLength": 1},
                "clear_acl_policy": {"type": "boolean"},
                "cross_instance_role": {"type": "string", "enum": ["NONE", "PRIMARY", "SECONDARY"]},
                "primary_instance": {"type": "string"},
                "secondary_instances": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                "adopt_existing_instance": {"type": "boolean"},
                "delete_adopted_instance": {"type": "boolean"},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="redis_memorystore_valkey", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "REDIS_HOST": "Primary or discovery PSC endpoint",
                "REDIS_PORT": "Valkey port",
                "REDIS_USER": "IAM-auth Valkey username",
                "REDIS_PASSWORD": "Secret Manager ref to TOKEN_AUTH credential",
                "REDIS_TLS": "Whether server-authenticated TLS is enabled",
                "REDIS_URL": "Redis-protocol connection URL without ephemeral credentials",
                "REDIS_READER_URL": "Optional reader endpoint",
                "REDIS_CA_CERT": "Managed server CA chain",
                "REDIS_AUTH_MODE": "gcp_iam, token, or disabled",
                "REDIS_RESOURCE_ARN": "Memorystore instance resource name",
                "GCP_MEMORYSTORE_PROJECT": "Google Cloud project ID",
                "GCP_MEMORYSTORE_REGION": "Google Cloud region",
                "GCP_MEMORYSTORE_INSTANCE": "Memorystore instance ID",
                "GCP_MEMORYSTORE_UID": "Memorystore instance UID",
                "GCP_MEMORYSTORE_MODE": "Valkey cluster mode",
                "GCP_MEMORYSTORE_ENGINE_VERSION": "Valkey engine version",
                "GCP_MEMORYSTORE_ENDPOINTS": "All PSC endpoints as compact JSON",
                "GCP_MEMORYSTORE_SERVER_CA_MODE": "Server certificate authority mode",
                "GCP_MEMORYSTORE_SERVER_CA_POOL": "Customer-managed CA pool resource name",
                "GCP_MEMORYSTORE_ACL_POLICY": "Attached Valkey ACL policy resource name",
                "GCP_MEMORYSTORE_ACL_POLICY_IN_SYNC": "Whether the attached ACL policy is synchronized",
            }
        )

    def _reconcile_token_auth(
        self,
        instance_id: str,
        current: dict[str, Any],
        cfg: dict[str, Any],
        *,
        spec: ProvisionSpec | None = None,
    ) -> None:
        if str(current.get("authorizationMode") or "AUTH_DISABLED") != "TOKEN_AUTH":
            return
        username = self._token_auth_username(current, cfg)
        instance_name = str(current.get("name") or self._instance_name(instance_id))
        labels = dict(current.get("labels") or {})
        if not labels.get("astrolift-token-user"):
            labels["astrolift-token-user"] = username
            self._wait_operation(
                self._valkey.patch_instance(
                    instance_name,
                    {"labels": labels},
                    update_mask=["labels"],
                )
            )
            current["labels"] = labels
        user_name = f"{instance_name}/tokenAuthUsers/{username}"
        users = self._valkey.list_token_auth_users(instance_name)
        if not any(str(row.get("name") or "") == user_name for row in users):
            self._wait_operation(self._valkey.add_token_auth_user(instance_name, username))

        tokens = self._active_auth_tokens(user_name)
        if not tokens:
            raise MemorystoreValkeyError(f"TOKEN_AUTH user {username!r} has no ACTIVE authentication token")
        secret_path = self._token_secret(instance_id, username)
        metadata_path = self._token_metadata_secret(instance_id, username)
        secret = self._secret_store.get(secret_path)
        metadata = self._token_metadata(metadata_path)
        current_token = next((row for row in tokens if str(row.get("token") or "") == secret), None)

        desired_generation = int(
            cfg.get(
                "token_auth_rotation_generation",
                metadata.get("generation", self._config.token_auth_rotation_generation),
            )
        )
        retire_generation = int(
            cfg.get(
                "token_auth_retire_generation",
                metadata.get("retire_generation", self._config.token_auth_retire_generation),
            )
        )
        if retire_generation >= desired_generation:
            raise MemorystoreValkeyError(
                "token_auth_retire_generation must be lower than token_auth_rotation_generation"
            )

        if current_token is None:
            current_token = max(tokens, key=_auth_token_sort_key)
            current_generation = 1
            self._publish_token_auth(
                instance_id,
                username,
                current_token,
                generation=current_generation,
                retire_generation=retire_generation,
                previous_token_name="",
                previous_generation=0,
                spec=spec,
            )
        else:
            current_name = str(current_token.get("name") or "")
            metadata_name = str(metadata.get("token_name") or "")
            if metadata_name and metadata_name != current_name:
                # Secret publication succeeded but the metadata write was
                # interrupted.  Complete that same rotation instead of
                # generating a third credential on retry.
                current_generation = desired_generation
                previous_name = metadata_name
                previous_generation = int(metadata.get("generation") or 1)
                self._publish_token_auth(
                    instance_id,
                    username,
                    current_token,
                    generation=current_generation,
                    retire_generation=retire_generation,
                    previous_token_name=previous_name,
                    previous_generation=previous_generation,
                    spec=spec,
                )
                metadata = self._token_metadata(metadata_path)
            else:
                current_generation = int(metadata.get("generation") or 1)
                if not metadata:
                    self._publish_token_auth(
                        instance_id,
                        username,
                        current_token,
                        generation=current_generation,
                        retire_generation=retire_generation,
                        previous_token_name="",
                        previous_generation=0,
                        spec=spec,
                    )
                    metadata = self._token_metadata(metadata_path)

        if desired_generation < current_generation:
            raise MemorystoreValkeyError(
                "token_auth_rotation_generation is monotonic and cannot be lower than the applied generation "
                f"{current_generation}"
            )

        previous_name = str(metadata.get("previous_token_name") or "")
        previous_generation = int(metadata.get("previous_generation") or 0)
        if previous_name and retire_generation >= previous_generation:
            if any(str(row.get("name") or "") == previous_name for row in tokens):
                self._wait_operation(self._valkey.delete_auth_token(previous_name))
            previous_name = ""
            previous_generation = 0
            self._publish_token_auth(
                instance_id,
                username,
                current_token,
                generation=current_generation,
                retire_generation=retire_generation,
                previous_token_name="",
                previous_generation=0,
                spec=spec,
            )
            tokens = self._active_auth_tokens(user_name)

        if desired_generation == current_generation:
            return
        if previous_name:
            raise MemorystoreValkeyError(
                f"generation {previous_generation} is still retained; set token_auth_retire_generation="
                f"{previous_generation} after consumers have rolled before requesting another rotation"
            )

        alternatives = [row for row in tokens if str(row.get("name") or "") != str(current_token.get("name") or "")]
        if alternatives:
            next_token = max(alternatives, key=_auth_token_sort_key)
        else:
            self._wait_operation(self._valkey.add_auth_token(user_name))
            refreshed = self._active_auth_tokens(user_name)
            alternatives = [
                row for row in refreshed if str(row.get("name") or "") != str(current_token.get("name") or "")
            ]
            if not alternatives:
                raise MemorystoreValkeyError("token rotation completed without a second ACTIVE token")
            next_token = max(alternatives, key=_auth_token_sort_key)

        self._publish_token_auth(
            instance_id,
            username,
            next_token,
            generation=desired_generation,
            retire_generation=retire_generation,
            previous_token_name=str(current_token.get("name") or ""),
            previous_generation=current_generation,
            spec=spec,
        )

    def _active_auth_tokens(self, user_name: str) -> list[dict[str, Any]]:
        rows = self._valkey.list_auth_tokens(user_name)
        active: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("state") or "") != "ACTIVE":
                continue
            token = str(row.get("token") or "")
            if not token and row.get("name"):
                row = self._valkey.get_auth_token(str(row["name"]))
                token = str(row.get("token") or "")
            if not token:
                raise MemorystoreValkeyError(
                    f"Memorystore returned ACTIVE token {row.get('name')!r} without credential material"
                )
            active.append(row)
        return active

    def _publish_token_auth(
        self,
        instance_id: str,
        username: str,
        token: dict[str, Any],
        *,
        generation: int,
        retire_generation: int,
        previous_token_name: str,
        previous_generation: int,
        spec: ProvisionSpec | None,
    ) -> None:
        token_value = str(token.get("token") or "")
        token_name = str(token.get("name") or "")
        if not token_value or not token_name:
            raise MemorystoreValkeyError("cannot publish an unnamed or empty TOKEN_AUTH credential")
        labels = None
        if spec is not None:
            labels = _labels(spec, self._config.backup_retention_days, None)
        try:
            self._secret_store.upsert(
                self._token_secret(instance_id, username),
                token_value,
                labels=labels,
            )
            current = self._valkey.get_instance(self._instance_name(instance_id))
            endpoint, reader, _ = _connection_endpoints(current)
            if not endpoint:
                raise MemorystoreValkeyError(
                    "cannot publish TOKEN_AUTH URL secrets before an active PSC endpoint exists"
                )
            scheme = "rediss" if current.get("transitEncryptionMode") == "SERVER_AUTHENTICATION" else "redis"
            quoted_user = quote(username, safe="")
            quoted_token = quote(token_value, safe="")
            self._secret_store.upsert(
                self._token_url_secret(instance_id, username),
                f"{scheme}://{quoted_user}:{quoted_token}@{endpoint[0]}:{endpoint[1]}",
                labels=labels,
            )
            if reader:
                self._secret_store.upsert(
                    self._token_reader_url_secret(instance_id, username),
                    f"{scheme}://{quoted_user}:{quoted_token}@{reader[0]}:{reader[1]}",
                    labels=labels,
                )
            self._secret_store.upsert(
                self._token_metadata_secret(instance_id, username),
                json.dumps(
                    {
                        "generation": generation,
                        "previous_generation": previous_generation,
                        "previous_token_name": previous_token_name,
                        "retire_generation": retire_generation,
                        "token_name": token_name,
                        "username": username,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                labels=labels,
            )
        except ManagedSecretStoreError as exc:
            raise MemorystoreValkeyError(str(exc)) from exc

    def _token_metadata(self, path: str) -> dict[str, Any]:
        raw = self._secret_store.get(path)
        if raw is None:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MemorystoreValkeyError(f"TOKEN_AUTH metadata secret {path!r} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise MemorystoreValkeyError(f"TOKEN_AUTH metadata secret {path!r} must contain an object")
        return dict(value)

    def _token_auth_username(self, current: dict[str, Any], cfg: dict[str, Any]) -> str:
        label = str((current.get("labels") or {}).get("astrolift-token-user") or "")
        requested = str(cfg.get("token_auth_user") or label or self._config.token_auth_user)
        if label and requested != label:
            raise MemorystoreValkeyError(
                f"token_auth_user is immutable for this managed binding ({requested!r} != {label!r})"
            )
        return requested

    def _token_secret(self, instance_id: str, username: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/{username}/auth"

    def _token_url_secret(self, instance_id: str, username: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/{username}/url"

    def _token_metadata_secret(self, instance_id: str, username: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/{username}/metadata"

    def _token_reader_url_secret(self, instance_id: str, username: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/{username}/reader-url"

    def _delete_token_secrets(self, instance_id: str, username: str | None = None) -> None:
        user = username or self._config.token_auth_user
        for path in (
            self._token_secret(instance_id, user),
            self._token_url_secret(instance_id, user),
            self._token_reader_url_secret(instance_id, user),
            self._token_metadata_secret(instance_id, user),
        ):
            with contextlib.suppress(ManagedSecretStoreError):
                self._secret_store.delete(path)

    def _instance_body(
        self,
        spec: ProvisionSpec,
        cfg: dict[str, Any],
        *,
        import_source: dict[str, Any] | None = None,
        secondary: bool = False,
    ) -> dict[str, Any]:
        mode = str(cfg.get("mode") or self._config.mode)
        body: dict[str, Any] = {
            "labels": _labels(
                spec, int(cfg.get("backup_retention_days", self._config.backup_retention_days)), import_source
            ),
            "authorizationMode": str(cfg.get("authorization_mode") or self._config.authorization_mode),
            "transitEncryptionMode": (
                "SERVER_AUTHENTICATION"
                if bool(cfg.get("transit_encryption", self._config.transit_encryption_default))
                else "TRANSIT_ENCRYPTION_DISABLED"
            ),
            "shardCount": int(cfg.get("shard_count", self._config.shard_count)),
            "replicaCount": int(cfg.get("replica_count", self._config.replica_count)),
            "nodeType": str(cfg.get("node_type") or _node_type_for_size(spec.size, self._config.node_type)),
            "engineVersion": str(cfg.get("engine_version") or self._config.engine_version),
            "mode": mode,
            "persistenceConfig": self._persistence_config(cfg),
            "endpoints": self._endpoints_config(cfg),
            "automatedBackupConfig": self._automated_backup_config(cfg),
            "deletionProtectionEnabled": bool(cfg.get("deletion_protection", self._config.deletion_protection_default)),
            "asyncInstanceEndpointsDeletionEnabled": bool(cfg.get("async_endpoint_deletion", False)),
        }
        if body.get("authorizationMode") == "TOKEN_AUTH":
            body["labels"]["astrolift-token-user"] = str(cfg.get("token_auth_user") or self._config.token_auth_user)
        if secondary:
            for field in (
                "authorizationMode",
                "transitEncryptionMode",
                "shardCount",
                "nodeType",
                "engineVersion",
                "mode",
                "persistenceConfig",
            ):
                body.pop(field)
        if "allow_fewer_zones_deployment" in cfg:
            body["allowFewerZonesDeployment"] = bool(cfg["allow_fewer_zones_deployment"])
        for key, value in (
            ("engineConfigs", None if secondary else cfg.get("engine_configs")),
            ("zoneDistributionConfig", self._zone_config(cfg)),
            ("maintenancePolicy", self._maintenance_config(cfg)),
            ("crossInstanceReplicationConfig", self._replication_config(cfg)),
            ("kmsKey", cfg.get("kms_key") or self._config.kms_key),
            ("serverCaMode", cfg.get("server_ca_mode") or self._config.server_ca_mode),
            ("serverCaPool", cfg.get("server_ca_pool") or self._config.server_ca_pool),
            ("aclPolicy", cfg.get("acl_policy")),
        ):
            if value:
                body[key] = value
        if import_source:
            body.update(import_source)
        return body

    def _reconcile(self, current: dict[str, Any], cfg: dict[str, Any]) -> None:
        desired: dict[str, Any] = {}
        mask: list[str] = []
        secondary = (
            str((current.get("crossInstanceReplicationConfig") or {}).get("instanceRole") or "NONE") == "SECONDARY"
        )
        for key, api_field in _MUTABLE_FIELDS.items():
            if key in cfg:
                if secondary and key in _PRIMARY_SYNCED_UPDATE_FIELDS:
                    continue
                value = cfg[key]
                if current.get(api_field) != value:
                    desired[api_field] = value
                    mask.append(api_field)
        builders = {
            "persistenceConfig": (
                "persistence_mode",
                "rdb_snapshot_period",
                "rdb_snapshot_start_time",
                "aof_append_fsync",
            ),
            "maintenancePolicy": ("maintenance_day", "maintenance_hour_utc", "maintenance_minute_utc"),
            "automatedBackupConfig": (
                "automated_backup",
                "backup_retention_days",
                "backup_start_hour_utc",
                "backup_start_minute_utc",
            ),
            "crossInstanceReplicationConfig": ("cross_instance_role", "primary_instance", "secondary_instances"),
        }
        values = {
            "persistenceConfig": self._persistence_config(cfg, current.get("persistenceConfig")),
            "maintenancePolicy": self._maintenance_config(cfg, current.get("maintenancePolicy")),
            "automatedBackupConfig": self._automated_backup_config(cfg, current.get("automatedBackupConfig")),
            "crossInstanceReplicationConfig": self._replication_config(
                cfg, current.get("crossInstanceReplicationConfig")
            ),
        }
        for api_field, keys in builders.items():
            if secondary and api_field == "persistenceConfig":
                continue
            if any(key in cfg for key in keys) and not _contains_desired(current.get(api_field), values[api_field]):
                desired[api_field] = values[api_field]
                mask.append(api_field)
        if "backup_retention_days" in cfg:
            labels = dict(current.get("labels") or {})
            retention = str(cfg["backup_retention_days"])
            if labels.get("astrolift-backup-days") != retention:
                labels["astrolift-backup-days"] = retention
                desired["labels"] = labels
                mask.append("labels")
        if "maintenance_version" in cfg and current.get("maintenanceVersion") != cfg["maintenance_version"]:
            available = [str(version) for version in current.get("availableMaintenanceVersions") or []]
            if str(cfg["maintenance_version"]) not in available:
                raise MemorystoreValkeyError(
                    f"maintenance version {cfg['maintenance_version']!r} is not currently available; "
                    f"available versions: {available or 'none'}"
                )
            desired["maintenanceVersion"] = cfg["maintenance_version"]
            mask.append("maintenanceVersion")
        if "acl_policy" in cfg and current.get("aclPolicy") != cfg["acl_policy"]:
            desired["aclPolicy"] = cfg["acl_policy"]
            mask.append("aclPolicy")
        if cfg.get("clear_acl_policy") and current.get("aclPolicy") is not None:
            desired["aclPolicy"] = None
            if "aclPolicy" not in mask:
                mask.append("aclPolicy")
        if mask:
            self._wait_operation(self._valkey.patch_instance(str(current["name"]), desired, update_mask=mask))

    def _assert_immutable_compatible(
        self,
        current: dict[str, Any],
        cfg: dict[str, Any],
        *,
        include_defaults: bool = True,
    ) -> None:
        desired: dict[str, Any] = {}
        if include_defaults or "authorization_mode" in cfg:
            desired["authorizationMode"] = str(cfg.get("authorization_mode") or self._config.authorization_mode)
        if include_defaults or "transit_encryption" in cfg:
            desired["transitEncryptionMode"] = (
                "SERVER_AUTHENTICATION"
                if bool(cfg.get("transit_encryption", self._config.transit_encryption_default))
                else "TRANSIT_ENCRYPTION_DISABLED"
            )
        if include_defaults or "mode" in cfg:
            desired["mode"] = str(cfg.get("mode") or self._config.mode)
        if "allow_fewer_zones_deployment" in cfg:
            desired["allowFewerZonesDeployment"] = bool(cfg.get("allow_fewer_zones_deployment", False))
        if include_defaults or any(key in cfg for key in ("zone_distribution_mode", "zone")):
            desired["zoneDistributionConfig"] = self._zone_config(cfg)
        if include_defaults or any(key in cfg for key in ("psc_networks", "endpoints")):
            desired["endpoints"] = self._endpoints_config(cfg)
        if include_defaults or "kms_key" in cfg:
            kms_key = str(cfg.get("kms_key") or self._config.kms_key)
            if kms_key:
                desired["kmsKey"] = kms_key
        ca_mode = str(cfg.get("server_ca_mode") or self._config.server_ca_mode)
        if ca_mode and (include_defaults or "server_ca_mode" in cfg):
            desired["serverCaMode"] = ca_mode
        ca_pool = str(cfg.get("server_ca_pool") or self._config.server_ca_pool)
        if ca_pool and (include_defaults or "server_ca_pool" in cfg):
            desired["serverCaPool"] = ca_pool
        for field, value in desired.items():
            actual = current.get(field)
            if actual is not None and not _contains_desired(actual, value):
                raise MemorystoreValkeyError(f"immutable {field} mismatch ({actual!r} != {value!r})")

    def _assert_owned(self, current: dict[str, Any], cfg: dict[str, Any], *, spec: ProvisionSpec | None = None) -> None:
        owned = (current.get("labels") or {}).get("astrolift-managed-by") == "platform"
        # Provision: a platform instance must be this service's, whatever
        # adopt_existing_instance says; the instance id is tenant-settable (#1961).
        refusal = (
            label_adoption_refusal(current.get("labels") or {}, spec, resource="Valkey instance")
            if spec and owned
            else None
        )
        if refusal:
            raise MemorystoreValkeyError(refusal)
        adopted = bool(cfg.get("adopt_existing_instance", self._config.adopt_existing_instance))
        if not owned and not adopted:
            raise MemorystoreValkeyError(
                "existing Valkey instance is not Astrolift-managed; set adopt_existing_instance=true"
            )

    def _assert_secondary_create_compatible(
        self,
        primary: dict[str, Any],
        cfg: dict[str, Any],
    ) -> None:
        if str(primary.get("state") or "") != "ACTIVE":
            raise MemorystoreValkeyError("cross-region replication primary must be ACTIVE")
        primary_role = str((primary.get("crossInstanceReplicationConfig") or {}).get("instanceRole") or "NONE")
        if primary_role == "SECONDARY":
            raise MemorystoreValkeyError("a cross-region secondary cannot be used as the primary instance")
        checks: tuple[tuple[str, str, Any], ...] = (
            ("shard_count", "shardCount", cfg.get("shard_count")),
            ("authorization_mode", "authorizationMode", cfg.get("authorization_mode")),
            (
                "transit_encryption",
                "transitEncryptionMode",
                ("SERVER_AUTHENTICATION" if cfg.get("transit_encryption") is True else "TRANSIT_ENCRYPTION_DISABLED"),
            ),
            ("engine_configs", "engineConfigs", cfg.get("engine_configs")),
            ("engine_version", "engineVersion", cfg.get("engine_version")),
            ("node_type", "nodeType", cfg.get("node_type")),
            ("mode", "mode", cfg.get("mode")),
        )
        for config_field, api_field, desired in checks:
            if config_field not in cfg:
                continue
            actual = primary.get(api_field)
            if config_field == "engine_configs":
                actual = actual or {}
            if not _contains_desired(actual, desired):
                raise MemorystoreValkeyError(
                    f"secondary {config_field} is copied from the primary and does not match "
                    f"({desired!r} != {actual!r})"
                )
        persistence_fields = {
            "persistence_mode",
            "rdb_snapshot_period",
            "rdb_snapshot_start_time",
            "aof_append_fsync",
        }
        if persistence_fields.intersection(cfg):
            desired_persistence = self._persistence_config(cfg, primary.get("persistenceConfig"))
            if not _contains_desired(primary.get("persistenceConfig"), desired_persistence):
                raise MemorystoreValkeyError(
                    "secondary persistence settings are copied from the primary and do not match"
                )

    def _validate_config(self, cfg: dict[str, Any], *, current: dict[str, Any] | None = None) -> str:
        unknown = sorted(set(cfg) - set(self.config_schema()["properties"]))
        if unknown:
            return f"unknown Memorystore for Valkey config keys: {', '.join(unknown)}"
        instance_id = cfg.get("instance_id")
        if instance_id is not None and (
            not isinstance(instance_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,61}[a-z0-9]", instance_id)
        ):
            return "invalid Memorystore for Valkey instance_id"
        version = str(cfg.get("engine_version") or (current or {}).get("engineVersion") or self._config.engine_version)
        if version not in _GA_VERSIONS | _PREVIEW_VERSIONS:
            return f"unsupported Valkey engine_version {version!r}"
        if (
            version in _PREVIEW_VERSIONS
            and (current is None or "engine_version" in cfg)
            and not bool(cfg.get("allow_preview_features", self._config.allow_preview_features))
        ):
            return f"{version} is Preview; set allow_preview_features=true"
        node_type = str(cfg.get("node_type") or (current or {}).get("nodeType") or self._config.node_type)
        if node_type not in _NODE_TYPES:
            return f"unsupported Valkey node_type {node_type!r}"
        mode = str(cfg.get("mode") or (current or {}).get("mode") or self._config.mode)
        if mode not in {"CLUSTER", "CLUSTER_DISABLED"}:
            return f"unsupported Valkey mode {mode!r}"
        shard_count = cfg.get("shard_count", (current or {}).get("shardCount", self._config.shard_count))
        replica_count = cfg.get("replica_count", (current or {}).get("replicaCount", self._config.replica_count))
        if isinstance(shard_count, bool) or not isinstance(shard_count, int) or not 1 <= shard_count <= 250:
            return "shard_count must be an integer between 1 and 250"
        if mode == "CLUSTER_DISABLED" and shard_count != 1:
            return "CLUSTER_DISABLED requires shard_count=1"
        if isinstance(replica_count, bool) or not isinstance(replica_count, int) or not 0 <= replica_count <= 5:
            return "replica_count must be an integer between 0 and 5"
        auth = str(
            cfg.get("authorization_mode") or (current or {}).get("authorizationMode") or self._config.authorization_mode
        )
        if auth not in {"IAM_AUTH", "TOKEN_AUTH", "AUTH_DISABLED"}:
            return "authorization_mode must be IAM_AUTH, TOKEN_AUTH, or AUTH_DISABLED"
        preview_allowed = bool(cfg.get("allow_preview_features", self._config.allow_preview_features))
        if auth == "TOKEN_AUTH" and (current is None or "authorization_mode" in cfg) and not preview_allowed:
            return "TOKEN_AUTH is Preview; set allow_preview_features=true"
        transit_encryption = cfg.get(
            "transit_encryption",
            (
                (current or {}).get("transitEncryptionMode") == "SERVER_AUTHENTICATION"
                if current is not None
                else self._config.transit_encryption_default
            ),
        )
        if auth in {"IAM_AUTH", "TOKEN_AUTH"} and transit_encryption is False:
            return f"{auth} requires transit_encryption=true to protect credentials"
        token_user = str(
            cfg.get("token_auth_user")
            or ((current or {}).get("labels") or {}).get("astrolift-token-user")
            or self._config.token_auth_user
        )
        if auth == "TOKEN_AUTH" and not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", token_user):
            return (
                "token_auth_user must start with a lowercase letter and contain at most 63 "
                "lowercase letters, digits, _ or -"
            )
        token_fields = {
            "token_auth_user",
            "token_auth_rotation_generation",
            "token_auth_retire_generation",
        }
        if auth != "TOKEN_AUTH" and token_fields.intersection(cfg):
            return "token_auth_* settings require authorization_mode=TOKEN_AUTH"
        for key, minimum in (
            ("token_auth_rotation_generation", 1),
            ("token_auth_retire_generation", 0),
        ):
            if key in cfg and (isinstance(cfg[key], bool) or not isinstance(cfg[key], int) or cfg[key] < minimum):
                return f"{key} must be an integer greater than or equal to {minimum}"
        if {
            "token_auth_rotation_generation",
            "token_auth_retire_generation",
        }.issubset(cfg) and cfg["token_auth_retire_generation"] >= cfg["token_auth_rotation_generation"]:
            return "token_auth_retire_generation must be lower than token_auth_rotation_generation"
        ca_mode = str(cfg.get("server_ca_mode") or (current or {}).get("serverCaMode") or self._config.server_ca_mode)
        ca_pool = str(cfg.get("server_ca_pool") or (current or {}).get("serverCaPool") or self._config.server_ca_pool)
        if ca_mode and ca_mode not in _CA_MODES:
            return f"unsupported server_ca_mode {ca_mode!r}"
        if ca_mode and transit_encryption is False:
            return "server_ca_mode requires transit_encryption=true"
        if ca_mode == "CUSTOMER_MANAGED_CAS_CA":
            if not _valid_regional_resource(ca_pool, self._config.region, "caPools"):
                return "CUSTOMER_MANAGED_CAS_CA requires a same-region server_ca_pool resource name"
        elif ca_pool:
            return "server_ca_pool is valid only with CUSTOMER_MANAGED_CAS_CA"
        current_persistence = (current or {}).get("persistenceConfig") or {}
        persistence = str(
            cfg.get("persistence_mode") or current_persistence.get("mode") or self._config.persistence_mode
        )
        if persistence not in {"DISABLED", "RDB", "AOF"}:
            return f"unsupported persistence_mode {persistence!r}"
        if persistence != "RDB" and any(key in cfg for key in ("rdb_snapshot_period", "rdb_snapshot_start_time")):
            return "RDB settings require persistence_mode=RDB"
        if persistence != "AOF" and "aof_append_fsync" in cfg:
            return "aof_append_fsync requires persistence_mode=AOF"
        if cfg.get("rdb_snapshot_period", "SIX_HOURS") not in {
            "ONE_HOUR",
            "SIX_HOURS",
            "TWELVE_HOURS",
            "TWENTY_FOUR_HOURS",
        }:
            return "unsupported rdb_snapshot_period"
        if cfg.get("aof_append_fsync", "EVERY_SEC") not in {"NEVER", "EVERY_SEC", "ALWAYS"}:
            return "unsupported aof_append_fsync"
        if "rdb_snapshot_start_time" in cfg and not _valid_rfc3339(cfg["rdb_snapshot_start_time"]):
            return "rdb_snapshot_start_time must be an RFC 3339 timestamp"
        current_backup = (current or {}).get("automatedBackupConfig") or {}
        automated = bool(
            cfg.get(
                "automated_backup",
                (
                    current_backup.get("automatedBackupMode") == "ENABLED"
                    if current is not None
                    else self._config.automated_backup_default
                ),
            )
        )
        if persistence == "DISABLED" and automated:
            return "automated backups require persistence"
        retention = cfg.get("backup_retention_days", self._config.backup_retention_days)
        if isinstance(retention, bool) or not isinstance(retention, int) or not 1 <= retention <= 365:
            return "backup_retention_days must be an integer between 1 and 365"
        current_zone = (current or {}).get("zoneDistributionConfig") or {}
        zone_mode = cfg.get("zone_distribution_mode", current_zone.get("mode", "MULTI_ZONE"))
        if zone_mode not in {"MULTI_ZONE", "SINGLE_ZONE"}:
            return "unsupported zone_distribution_mode"
        effective_zone = cfg.get("zone", current_zone.get("zone"))
        if zone_mode == "SINGLE_ZONE" and not effective_zone:
            return "SINGLE_ZONE requires zone"
        if effective_zone and zone_mode != "SINGLE_ZONE":
            return "zone is valid only with SINGLE_ZONE"
        if cfg.get("psc_networks") and cfg.get("endpoints"):
            return "psc_networks and endpoints are mutually exclusive"
        networks = cfg.get("psc_networks")
        if networks is not None and (
            not isinstance(networks, list)
            or not networks
            or not all(isinstance(network, str) and network for network in networks)
        ):
            return "psc_networks must be a non-empty list of network names"
        if "endpoints" in cfg and not _valid_endpoints(cfg["endpoints"]):
            return "endpoints must contain PSC auto or user-created connection objects"
        if cfg.get("maintenance_day", "SUNDAY") not in _DAYS:
            return "unsupported maintenance_day"
        for key in ("maintenance_hour_utc", "backup_start_hour_utc"):
            if key in cfg and (isinstance(cfg[key], bool) or not isinstance(cfg[key], int) or not 0 <= cfg[key] <= 23):
                return f"{key} must be an integer between 0 and 23"
        for key in ("maintenance_minute_utc",):
            if key in cfg and (isinstance(cfg[key], bool) or not isinstance(cfg[key], int) or not 0 <= cfg[key] <= 59):
                return f"{key} must be an integer between 0 and 59"
        if "backup_start_minute_utc" in cfg and cfg["backup_start_minute_utc"] != 0:
            return "backup_start_minute_utc must be 0 because Valkey backups start on the hour"
        current_replication = (current or {}).get("crossInstanceReplicationConfig") or {}
        current_role = str(current_replication.get("instanceRole") or "NONE")
        role = str(cfg.get("cross_instance_role") or current_role)
        if role not in {"NONE", "PRIMARY", "SECONDARY"}:
            return "unsupported cross_instance_role"
        primary_instance = cfg.get("primary_instance")
        if primary_instance is None and role == "SECONDARY" and current_role == "SECONDARY":
            primary_instance = (current_replication.get("primaryInstance") or {}).get("instance")
        if role == "SECONDARY" and not primary_instance:
            return "SECONDARY cross-instance replication requires primary_instance"
        if primary_instance is not None and not _valid_instance_resource(primary_instance, self._config.project_id):
            return "primary_instance must be an instance resource name"
        if role != "SECONDARY" and primary_instance:
            return "primary_instance is valid only for SECONDARY replication"
        secondaries = cfg.get("secondary_instances")
        if secondaries is None and role == "PRIMARY" and current_role == "PRIMARY":
            secondaries = [row.get("instance") for row in current_replication.get("secondaryInstances") or []]
        if secondaries is not None and (
            not isinstance(secondaries, list)
            or not all(_valid_instance_resource(row, self._config.project_id) for row in secondaries)
        ):
            return "secondary_instances must be a list of instance resource names"
        if role != "PRIMARY" and secondaries:
            return "secondary_instances is valid only for PRIMARY replication"
        if current is not None and role == "SECONDARY":
            primary_synced = sorted(_PRIMARY_SYNCED_UPDATE_FIELDS.intersection(cfg))
            if primary_synced:
                return (
                    "update these fields on the cross-region primary; Memorystore copies them to secondaries: "
                    + ", ".join(primary_synced)
                )
        engine_configs = cfg.get("engine_configs")
        if engine_configs is not None and (
            not isinstance(engine_configs, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in engine_configs.items())
        ):
            return "engine_configs must map strings to strings"
        acl_policy = cfg.get("acl_policy")
        if acl_policy is not None and not _valid_regional_resource(acl_policy, self._config.region, "aclPolicies"):
            return "acl_policy must be a same-region Memorystore ACL policy resource name"
        if acl_policy is not None and cfg.get("clear_acl_policy"):
            return "acl_policy and clear_acl_policy are mutually exclusive"
        if "maintenance_version" in cfg and (
            not isinstance(cfg["maintenance_version"], str) or not cfg["maintenance_version"]
        ):
            return "maintenance_version must be a non-empty string"
        for key in (
            "allow_preview_features",
            "transit_encryption",
            "automated_backup",
            "allow_fewer_zones_deployment",
            "deletion_protection",
            "async_endpoint_deletion",
            "adopt_existing_instance",
            "delete_adopted_instance",
            "clear_acl_policy",
        ):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        return ""

    def _persistence_config(
        self,
        cfg: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = current or {}
        mode = str(cfg.get("persistence_mode") or current.get("mode") or self._config.persistence_mode)
        result: dict[str, Any] = {"mode": mode}
        if mode == "RDB":
            current_rdb = current.get("rdbConfig") or {}
            rdb: dict[str, Any] = {
                "rdbSnapshotPeriod": str(
                    cfg.get("rdb_snapshot_period") or current_rdb.get("rdbSnapshotPeriod") or "SIX_HOURS"
                )
            }
            if "rdb_snapshot_start_time" in cfg:
                rdb["rdbSnapshotStartTime"] = str(cfg["rdb_snapshot_start_time"])
            elif current_rdb.get("rdbSnapshotStartTime"):
                rdb["rdbSnapshotStartTime"] = str(current_rdb["rdbSnapshotStartTime"])
            result["rdbConfig"] = rdb
        elif mode == "AOF":
            current_aof = current.get("aofConfig") or {}
            result["aofConfig"] = {
                "appendFsync": str(cfg.get("aof_append_fsync") or current_aof.get("appendFsync") or "EVERY_SEC")
            }
        return result

    def _automated_backup_config(
        self,
        cfg: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = current or {}
        current_enabled = current.get("automatedBackupMode") == "ENABLED"
        enabled = bool(
            cfg.get(
                "automated_backup",
                current_enabled if current else self._config.automated_backup_default,
            )
        )
        if not enabled:
            return {"automatedBackupMode": "DISABLED"}
        current_start = (current.get("fixedFrequencySchedule") or {}).get("startTime") or {}
        retention = cfg.get("backup_retention_days")
        retention_value = (
            f"{int(retention) * 86400}s"
            if retention is not None
            else str(current.get("retention") or f"{self._config.backup_retention_days * 86400}s")
        )
        return {
            "automatedBackupMode": "ENABLED",
            "retention": retention_value,
            "fixedFrequencySchedule": {
                "startTime": {
                    "hours": int(cfg.get("backup_start_hour_utc", current_start.get("hours", 2))),
                    "minutes": 0,
                    "seconds": 0,
                    "nanos": 0,
                }
            },
        }

    def _maintenance_config(
        self,
        cfg: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not any(key in cfg for key in ("maintenance_day", "maintenance_hour_utc", "maintenance_minute_utc")):
            return {}
        windows = (current or {}).get("weeklyMaintenanceWindow") or []
        current_window = windows[0] if windows else {}
        current_start = current_window.get("startTime") or {}
        return {
            "weeklyMaintenanceWindow": [
                {
                    "day": str(cfg.get("maintenance_day") or current_window.get("day") or "SUNDAY"),
                    "startTime": {
                        "hours": int(cfg.get("maintenance_hour_utc", current_start.get("hours", 3))),
                        "minutes": int(cfg.get("maintenance_minute_utc", current_start.get("minutes", 0))),
                        "seconds": 0,
                        "nanos": 0,
                    },
                }
            ]
        }

    def _zone_config(self, cfg: dict[str, Any]) -> dict[str, Any]:
        mode = str(cfg.get("zone_distribution_mode") or "MULTI_ZONE")
        result: dict[str, Any] = {"mode": mode}
        if mode == "SINGLE_ZONE":
            result["zone"] = str(cfg["zone"])
        return result

    def _replication_config(
        self,
        cfg: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not any(key in cfg for key in ("cross_instance_role", "primary_instance", "secondary_instances")):
            return {}
        current = current or {}
        role = str(cfg.get("cross_instance_role") or current.get("instanceRole") or "NONE")
        result: dict[str, Any] = {"instanceRole": role}
        if role == "SECONDARY":
            primary = cfg.get("primary_instance") or (current.get("primaryInstance") or {}).get("instance")
            result["primaryInstance"] = {"instance": str(primary)}
        elif role == "PRIMARY":
            secondaries = cfg.get("secondary_instances")
            if secondaries is None:
                secondaries = [row.get("instance") for row in current.get("secondaryInstances") or []]
            result["secondaryInstances"] = [{"instance": str(row)} for row in secondaries or []]
        return result

    def _endpoints_config(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        if cfg.get("endpoints"):
            return list(cfg["endpoints"])
        networks = [self._network_name(str(row)) for row in cfg.get("psc_networks") or [self._network()]]
        return [
            {"connections": [{"pscAutoConnection": {"projectId": self._config.project_id, "network": network}}]}
            for network in networks
        ]

    def _wait_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation:
            return {}
        current = operation
        name = str(operation.get("name") or "")
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        while not current.get("done"):
            if not name:
                raise MemorystoreValkeyError("Memorystore operation response has no name")
            if self._monotonic() >= deadline:
                raise MemorystoreValkeyError(f"Memorystore operation {name} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._valkey.get_operation(name)
        if current.get("error"):
            error = current["error"]
            raise MemorystoreValkeyError(f"Memorystore operation {name} failed: {error.get('message') or error}")
        return current

    def _get_instance(self, instance_id: str) -> dict[str, Any] | None:
        try:
            return self._valkey.get_instance(self._instance_name(instance_id))
        except MemorystoreValkeyNotFound:
            return None

    def _instance_id(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        if cfg.get("instance_id"):
            return str(cfg["instance_id"])
        parts = (
            self._config.instance_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "valkey",
        )
        raw = "-".join(parts)
        return _resource_id(raw)

    def _parent(self) -> str:
        return f"projects/{self._config.project_id}/locations/{self._config.region}"

    def _instance_name(self, instance_id: str) -> str:
        return f"{self._parent()}/instances/{instance_id}"

    def _network(self) -> str:
        network = self._config.network or f"projects/{self._config.project_id}/global/networks/default"
        return self._network_name(network)

    def _network_name(self, network: str) -> str:
        if network.startswith("projects/"):
            return network
        return f"projects/{self._config.project_id}/global/networks/{network}"


def _handle(instance_id: str) -> str:
    return f"{KIND}/{instance_id}"


def _parse_handle(handle: str) -> str:
    parts = handle.split("/")
    if len(parts) != 2 or parts[0] != KIND or not parts[1]:
        raise ValueError(f"handle {handle!r} must be 'redis/<instance>'")
    return parts[1]


def _resource_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")[:63].rstrip("-")
    if len(normalized) < 4:
        normalized = f"alft-{normalized}"[:63].rstrip("-")
    return normalized


def _node_type_for_size(size: str, fallback: str) -> str:
    return {
        "small": "STANDARD_SMALL",
        "medium": "HIGHMEM_MEDIUM",
        "large": "STANDARD_LARGE",
        "xlarge": "HIGHMEM_2XLARGE",
    }.get(size, fallback)


def _backup_id(instance_id: str, created: datetime) -> str:
    return _resource_id(f"{instance_id}-backup-{created.strftime('%Y%m%d%H%M%S%f')}")


def _request_id(*parts: Any) -> str:
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"astrolift:memorystore-valkey:{canonical}"))


def _source_hash(source: dict[str, Any] | None) -> str:
    if not source:
        return ""
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def _labels(spec: ProvisionSpec, backup_days: int, source: dict[str, Any] | None) -> dict[str, str]:
    labels = {
        "astrolift-managed-by": "platform",
        "astrolift-purpose": "valkey",
        "astrolift-backup-days": str(backup_days),
    }
    if spec.binding_id:
        labels["astrolift-binding"] = spec.binding_id[:63]
    if spec.managed_service_id:
        labels["astrolift-service"] = spec.managed_service_id[:63]
    if source:
        labels["astrolift-restore-source"] = _source_hash(source)
    return labels


def _valid_endpoints(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for endpoint in value:
        if (
            not isinstance(endpoint, dict)
            or not isinstance(endpoint.get("connections"), list)
            or not endpoint["connections"]
        ):
            return False
        for row in endpoint["connections"]:
            if not isinstance(row, dict):
                return False
            auto = row.get("pscAutoConnection")
            manual = row.get("pscConnection")
            if bool(auto) == bool(manual):
                return False
            if auto and (
                not isinstance(auto, dict)
                or not isinstance(auto.get("projectId"), str)
                or not isinstance(auto.get("network"), str)
            ):
                return False
            if manual and (
                not isinstance(manual, dict)
                or not all(
                    isinstance(manual.get(key), str) and manual[key]
                    for key in (
                        "pscConnectionId",
                        "ipAddress",
                        "forwardingRule",
                        "network",
                        "serviceAttachment",
                    )
                )
            ):
                return False
    return True


def _valid_rfc3339(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    with contextlib.suppress(ValueError):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None
    return False


def _valid_regional_resource(value: Any, region: str, collection: str) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(
        rf"projects/[^/]+/locations/([^/]+)/{re.escape(collection)}/[^/]+",
        value,
    )
    return bool(match and match.group(1) == region)


def _valid_instance_resource(value: Any, project_id: str) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(
        r"projects/([^/]+)/locations/[^/]+/instances/[^/]+",
        value,
    )
    return bool(match and match.group(1) == project_id)


def _contains_desired(actual: Any, desired: Any) -> bool:
    """Compare request-shaped configuration while ignoring output-only API fields."""
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_desired(actual[key], value) for key, value in desired.items()
        )
    if isinstance(desired, list):
        if not isinstance(actual, list) or len(actual) != len(desired):
            return False
        unmatched = set(range(len(actual)))
        for desired_row in desired:
            match = next(
                (index for index in unmatched if _contains_desired(actual[index], desired_row)),
                None,
            )
            if match is None:
                return False
            unmatched.remove(match)
        return True
    return bool(actual == desired)


def _auth_token_sort_key(token: dict[str, Any]) -> tuple[str, str]:
    return (str(token.get("createTime") or ""), str(token.get("name") or ""))


def _connection_endpoints(
    instance: dict[str, Any],
) -> tuple[tuple[str, int, str] | None, tuple[str, int, str] | None, list[dict[str, Any]]]:
    parsed: list[tuple[str, int, str]] = []
    records: list[dict[str, Any]] = []
    for endpoint in instance.get("endpoints") or []:
        for detail in endpoint.get("connections") or []:
            connection = detail.get("pscAutoConnection") or detail.get("pscConnection") or {}
            if connection.get("pscConnectionStatus") not in {None, "", "ACTIVE"}:
                continue
            host = str(connection.get("ipAddress") or "")
            if not host:
                continue
            port = int(connection.get("port") or 6379)
            connection_type = str(connection.get("connectionType") or "CONNECTION_TYPE_DISCOVERY")
            parsed.append((host, port, connection_type))
            records.append(
                {"host": host, "port": port, "type": connection_type, "network": connection.get("network", "")}
            )
    if not parsed:
        for endpoint in instance.get("discoveryEndpoints") or []:
            if endpoint.get("address"):
                parsed.append(
                    (str(endpoint["address"]), int(endpoint.get("port") or 6379), "CONNECTION_TYPE_DISCOVERY")
                )
                records.append(
                    {
                        "host": parsed[-1][0],
                        "port": parsed[-1][1],
                        "type": parsed[-1][2],
                        "network": endpoint.get("network", ""),
                    }
                )
    primary = next((row for row in parsed if row[2] == "CONNECTION_TYPE_PRIMARY"), None)
    discovery = next((row for row in parsed if row[2] == "CONNECTION_TYPE_DISCOVERY"), None)
    reader = next((row for row in parsed if row[2] == "CONNECTION_TYPE_READER"), None)
    return primary or discovery or (parsed[0] if parsed else None), reader, records


def _ca_certificates(payload: dict[str, Any]) -> list[str]:
    chains = (payload.get("managedServerCa") or {}).get("caCerts") or []
    return [str(cert) for chain in chains for cert in chain.get("certificates") or [] if cert]


def _deprovision_error(handle: str, action: str, exc: Exception) -> DeprovisionResult:
    return DeprovisionResult(False, handle, f"{action}: {exc}", [str(exc)], retryable=True)
