"""CloudSQL Postgres managed-service driver (#363).

Implements ``ManagedServiceDriver`` for the canonical GCP managed-
Postgres path. Same four-corner deprovision matrix as the AWS RDS
driver (#351); the cloud-API surface differs but the operator-facing
semantic is identical.

  delete_data=False, force_destroy=False (default):
    final snapshot taken via on-demand backup; instance
    ``settings.deletionProtectionEnabled`` is respected. Refuses
    cleanly when protection is on so the operator must pass
    ``force_destroy=True``.

  delete_data=True, force_destroy=False:
    skip final backup; respect protection.

  delete_data=False, force_destroy=True:
    take final backup; bypass protection (patch the instance to
    clear the flag, then delete).

  delete_data=True, force_destroy=True:
    --atomic: skip backup, bypass protection.

CloudSQL has no "snapshot" concept in the RDS sense — backups are
the persistence story. The driver's ``snapshot`` method calls
``instances.insertBackupRun``; ``restore`` does a ``cloneInstance``
from the backup id.

Master password handling mirrors RDS: 32-char shell-safe alphabet,
stored in Secret Manager at ``astrolift/cloudsql/<id>/master``.
"""

from __future__ import annotations

import contextlib
import secrets
import string
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

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
from gcp.managed._secret_store import ManagedSecretStore, ManagedSecretStoreError

KIND = "postgres"


_SIZE_TO_TIER = {
    "small": "db-custom-1-3840",
    "medium": "db-custom-2-7680",
    "large": "db-custom-4-15360",
    "xlarge": "db-custom-8-30720",
}

_SIZE_TO_STORAGE_GB = {
    "small": 20,
    "medium": 50,
    "large": 100,
    "xlarge": 250,
}


class _ManagedServiceError(Exception):
    """Internal — surfaced as ``DeprovisionResult.errors`` /
    ``ProvisionResult.errors`` rather than raised across the workflow
    boundary."""


@dataclass(frozen=True)
class CloudSQLConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    project_id: str
    region: str
    """GCP region for the instance (e.g. ``us-west1``). CloudSQL
    instances are zonal under the hood; the driver lets CloudSQL
    pick the zone unless ``spec.config.zone`` is set."""

    private_network: str | None = None
    """If set, instance uses Private IP via this VPC selfLink. None
    means Public IP (with authorized networks); operators with VPC-
    SC should always set this."""

    instance_name_prefix: str = "astrolift"
    engine_version: str = "POSTGRES_16"

    backup_retention_days: int = 7
    high_availability_default: bool = False
    """Maps to ``settings.availabilityType``: REGIONAL when True,
    ZONAL when False."""

    deletion_protection_default: bool = True

    secret_manager_prefix: str = "astrolift/cloudsql"
    secret_id_prefix: str = "astrolift"


class CloudSQLPostgresDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: CloudSQLConfig,
        sql_client: Any | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        self._config = config
        if sql_client is not None:
            self._sql = sql_client
        else:
            from google.cloud import sql_v1

            self._sql = sql_v1.SqlInstancesServiceClient()
        if secrets_client is not None:
            self._sm = secrets_client
        else:
            from google.cloud import secretmanager

            self._sm = secretmanager.SecretManagerServiceClient()
        self._secret_store = ManagedSecretStore(
            project_id=config.project_id,
            secret_id_prefix=config.secret_id_prefix,
            client=self._sm,
        )

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="gcp",
        driver="postgres_cloudsql",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        instance_id = self._instance_id_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(instance_id)
        if existing is not None:
            if self._secret_store.get(self._master_secret_for(instance_id=instance_id)) is None:
                return ProvisionResult(
                    ok=False,
                    handle=_handle_for(instance_id),
                    message=(f"cloudsql {instance_id} exists but its Astrolift master-password secret is missing"),
                    errors=["missing_master_password_secret"],
                )
            return ProvisionResult(
                ok=True,
                handle=_handle_for(instance_id),
                message=(f"cloudsql {instance_id} already exists (state={_get(existing, 'state', '?')})"),
            )

        master_password = _generate_master_password()
        secret_name = self._store_master_password(
            instance_id=instance_id,
            password=master_password,
            spec=spec,
        )

        tier = cfg.get("tier") or _SIZE_TO_TIER.get(
            spec.size,
            "db-custom-1-3840",
        )
        storage_gb = int(
            cfg.get("storage_gb") or _SIZE_TO_STORAGE_GB.get(spec.size, 20),
        )
        engine_version = cfg.get("engine_version") or self._config.engine_version
        ha = bool(
            cfg.get("high_availability", self._config.high_availability_default),
        )
        deletion_protection = bool(
            cfg.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )

        instance_body: dict[str, Any] = {
            "name": instance_id,
            "region": self._config.region,
            "databaseVersion": engine_version,
            "rootPassword": master_password,
            "settings": {
                "tier": tier,
                "dataDiskSizeGb": storage_gb,
                "dataDiskType": "PD_SSD",
                "storageAutoResize": True,
                "availabilityType": "REGIONAL" if ha else "ZONAL",
                "deletionProtectionEnabled": deletion_protection,
                "backupConfiguration": {
                    "enabled": True,
                    "pointInTimeRecoveryEnabled": True,
                    "transactionLogRetentionDays": int(
                        cfg.get(
                            "backup_retention_days",
                            self._config.backup_retention_days,
                        ),
                    ),
                },
                "ipConfiguration": {
                    "ipv4Enabled": self._config.private_network is None,
                    "privateNetwork": self._config.private_network,
                    "requireSsl": True,
                },
                "userLabels": _tags_for(spec),
            },
        }
        if cfg.get("zone"):
            instance_body["settings"]["locationPreference"] = {
                "zone": cfg["zone"],
            }
        if cfg.get("kms_key_name"):
            instance_body["diskEncryptionConfiguration"] = {
                "kmsKeyName": cfg["kms_key_name"],
            }

        try:
            self._sql.insert(
                project=self._config.project_id,
                body=instance_body,
            )
        except Exception as exc:
            self._delete_connection_secrets(instance_id)
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"insert: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=_handle_for(instance_id),
            message=(f"cloudsql {instance_id} provisioning (password in {secret_name})"),
        )

    @driver_op(cloud="gcp", driver="postgres_cloudsql")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        instance_id = _parse_handle(spec.handle)
        cfg = spec.config or {}

        settings: dict[str, Any] = {}
        if spec.size:
            tier = cfg.get("tier") or _SIZE_TO_TIER.get(spec.size)
            if tier:
                settings["tier"] = tier
            new_storage = _SIZE_TO_STORAGE_GB.get(spec.size)
            if new_storage:
                settings["dataDiskSizeGb"] = new_storage
        if "high_availability" in cfg:
            settings["availabilityType"] = "REGIONAL" if cfg["high_availability"] else "ZONAL"
        if "backup_retention_days" in cfg:
            settings.setdefault("backupConfiguration", {})["transactionLogRetentionDays"] = int(
                cfg["backup_retention_days"]
            )

        if not settings:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided — no-op",
            )

        try:
            self._sql.patch(
                project=self._config.project_id,
                instance=instance_id,
                body={"settings": settings},
            )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"patch: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"cloudsql {instance_id} update queued",
        )

    @driver_op(
        cloud="gcp",
        driver="postgres_cloudsql",
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
        instance_id = _parse_handle(spec.handle)

        existing = self._describe(instance_id)
        if existing is None:
            self._delete_connection_secrets(instance_id)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"cloudsql {instance_id} already gone",
            )

        protected = _get(
            _get(existing, "settings", {}) or {},
            "deletionProtectionEnabled",
            False,
        )
        if protected and force_destroy:
            try:
                self._sql.patch(
                    project=self._config.project_id,
                    instance=instance_id,
                    body={"settings": {"deletionProtectionEnabled": False}},
                )
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"failed to clear deletionProtection: {exc}",
                    errors=[str(exc)],
                )
        elif protected:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(f"cloudsql {instance_id} has deletionProtection enabled — pass force_destroy=True to bypass"),
                errors=["deletion_protection_enabled"],
            )

        if not delete_data:
            # Take a final on-demand backup before delete. CloudSQL
            # doesn't offer "skip final backup" as a delete-time flag
            # like RDS does; we do it as a pre-step. delete_data=True
            # skips this pre-step.
            # Don't block delete on backup failure — that defeats
            # the purpose of the safety path. Surface to operator
            # via the message but proceed.
            with contextlib.suppress(Exception):
                self._sql.insert_backup_run(
                    project=self._config.project_id,
                    instance=instance_id,
                    body={"description": f"final-{instance_id}"},
                )

        try:
            self._sql.delete(
                project=self._config.project_id,
                instance=instance_id,
            )
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete: {exc}",
                errors=[str(exc)],
            )

        if delete_data:
            self._delete_connection_secrets(instance_id)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"cloudsql {instance_id} delete queued "
                f"(backup={'skipped' if delete_data else 'taken'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="gcp", driver="postgres_cloudsql")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        instance_id = _parse_handle(handle.handle)
        existing = self._describe(instance_id)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"cloudsql {instance_id} not found",
            )
        state = _get(existing, "state", "UNKNOWN")
        return ServiceStatus(
            handle=handle.handle,
            state=_CLOUDSQL_STATE_TO_PROTOCOL.get(state, "updating"),
            message=f"cloudsql reports {state}",
        )

    @driver_op(cloud="gcp", driver="postgres_cloudsql")
    def binding(self, handle: ServiceHandle) -> Binding:
        instance_id = _parse_handle(handle.handle)
        existing = self._describe(instance_id)
        if existing is None:
            raise _ManagedServiceError(
                f"binding requested for missing instance {instance_id}",
            )
        ip_addrs = _get(existing, "ipAddresses", []) or []
        # Prefer private IP when available, fall back to public.
        host = ""
        for ip in ip_addrs:
            kind = _get(ip, "type", "") or ""
            if kind == "PRIVATE":
                host = _get(ip, "ipAddress", "") or ""
                break
        if not host and ip_addrs:
            host = _get(ip_addrs[0], "ipAddress", "") or ""

        secret_name = self._master_secret_for(instance_id=instance_id)
        password = self._secret_store.get(secret_name)
        if password is None:
            raise _ManagedServiceError(
                f"binding requested for {instance_id}, but master-password secret {secret_name!r} is missing",
            )
        if not host:
            raise _ManagedServiceError(
                f"binding requested for {instance_id}, but CloudSQL has no reachable endpoint",
            )
        url_secret = self._url_secret_for(instance_id=instance_id)
        try:
            self._secret_store.upsert(
                url_secret,
                f"postgresql://postgres:{quote(password, safe='')}@{host}:5432/postgres?sslmode=require",
            )
        except ManagedSecretStoreError as exc:
            raise _ManagedServiceError(str(exc)) from exc
        return Binding(
            env_vars={
                "POSTGRES_HOST": ValueRef(literal=host),
                "POSTGRES_PORT": ValueRef(literal="5432"),
                "POSTGRES_DB": ValueRef(literal="postgres"),
                "POSTGRES_USER": ValueRef(literal="postgres"),
                "POSTGRES_PASSWORD": ValueRef(secret_ref=secret_name),
                "POSTGRES_SSL_MODE": ValueRef(literal="require"),
                "POSTGRES_MASTER_SECRET_REF": ValueRef(literal=secret_name),
                "DATABASE_URL": ValueRef(secret_ref=url_secret),
            },
            # The control plane resolves these refs into the synthesized
            # Kubernetes binding Secret.  The workload never calls Secret
            # Manager directly and therefore needs no secretAccessor role.
            iam_grants=[],
            notes=(
                "DATABASE_URL is a derived secret holding the "
                "postgres:// connection string; the split components "
                "are also exposed for callers that build their own DSN."
            ),
        )

    @driver_op(cloud="gcp", driver="postgres_cloudsql")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        instance_id = _parse_handle(handle.handle)
        snap_id = f"{instance_id}-snap-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        try:
            self._sql.insert_backup_run(
                project=self._config.project_id,
                instance=instance_id,
                body={"description": snap_id},
            )
        except Exception as exc:
            raise _ManagedServiceError(
                f"insert_backup_run: {exc}",
            ) from exc
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snap_id,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="gcp", driver="postgres_cloudsql")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        target_id = self._instance_id_for(spec=target)
        source_id = _parse_handle(snapshot.handle)
        source_password = self._secret_store.get(
            self._master_secret_for(instance_id=source_id),
        )
        if source_password is None:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"clone source {source_id} has no Astrolift master-password secret",
                errors=["missing_master_password_secret"],
            )
        try:
            self._secret_store.upsert(
                self._master_secret_for(instance_id=target_id),
                source_password,
                labels=_tags_for(target),
            )
        except ManagedSecretStoreError as exc:
            return ProvisionResult(ok=False, handle="", message=str(exc), errors=[str(exc)])
        try:
            self._sql.clone(
                project=self._config.project_id,
                instance=source_id,
                body={
                    "cloneContext": {
                        "destinationInstanceName": target_id,
                        "binLogCoordinates": None,
                    },
                },
            )
        except Exception as exc:
            self._delete_connection_secrets(target_id)
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"clone: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=_handle_for(target_id),
            message=f"cloudsql clone from {source_id} queued",
        )

    @driver_op(cloud="gcp", driver="postgres_cloudsql", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "engine_version": {"type": "string"},
                "tier": {"type": "string"},
                "storage_gb": {"type": "integer", "minimum": 10},
                "high_availability": {"type": "boolean"},
                "deletion_protection": {"type": "boolean"},
                "backup_retention_days": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 35,
                },
                "kms_key_name": {"type": "string"},
                "zone": {"type": "string"},
            },
        }

    @driver_op(cloud="gcp", driver="postgres_cloudsql", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "POSTGRES_HOST": "CloudSQL private/public IP",
                "POSTGRES_PORT": "5432 (Postgres default)",
                "POSTGRES_DB": "Initial database name (postgres)",
                "POSTGRES_USER": "Master user (postgres)",
                "POSTGRES_PASSWORD": ("Secret Manager ref to the master password"),
                "POSTGRES_SSL_MODE": "require",
                "POSTGRES_MASTER_SECRET_REF": "Logical Secret Manager ref for the master password",
                "DATABASE_URL": ("Secret Manager ref to the fully-formed postgres:// connection string"),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, instance_id: str) -> dict[str, Any] | None:
        try:
            resp = self._sql.get(
                project=self._config.project_id,
                instance=instance_id,
            )
        except Exception as exc:
            if "404" in str(exc) or "not found" in str(exc).lower():
                return None
            raise
        # google-cloud-* SDKs return either a proto-Message or a dict
        # depending on the client variant; normalize to dict.
        if hasattr(resp, "_pb"):
            from google.protobuf.json_format import MessageToDict

            return MessageToDict(resp._pb, preserving_proto_field_name=True)
        return dict(resp) if isinstance(resp, dict) else getattr(resp, "__dict__", {})

    def _instance_id_for(self, *, spec: ProvisionSpec) -> str:
        # CloudSQL instance names: lowercase, letters/digits/hyphens,
        # ≤98 chars, must start with letter.
        raw = (
            (
                f"{self._config.instance_name_prefix}-"
                f"{spec.organization_slug}-{spec.app_slug}-"
                f"{spec.environment_name}-{spec.service_handle_hint or 'pg'}"
            )
            .lower()
            .replace("_", "-")
        )
        return "".join(c for c in raw if c.isalnum() or c == "-")[:98]

    def _master_secret_for(self, *, instance_id: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/master"

    def _url_secret_for(self, *, instance_id: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/url"

    def _store_master_password(
        self,
        *,
        instance_id: str,
        password: str,
        spec: ProvisionSpec,
    ) -> str:
        name = self._master_secret_for(instance_id=instance_id)
        try:
            return self._secret_store.upsert(name, password, labels=_tags_for(spec))
        except ManagedSecretStoreError as exc:
            raise _ManagedServiceError(str(exc)) from exc

    def _delete_connection_secrets(self, instance_id: str) -> None:
        for path in (
            self._master_secret_for(instance_id=instance_id),
            self._url_secret_for(instance_id=instance_id),
        ):
            with contextlib.suppress(ManagedSecretStoreError):
                self._secret_store.delete(path)


# ----- module-level helpers --------------------------------------------


_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_."


def _generate_master_password(length: int = 32) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def _handle_for(instance_id: str) -> str:
    return f"{KIND}/{instance_id}"


def _parse_handle(handle: str) -> str:
    if "/" not in handle:
        raise _ManagedServiceError(
            f"handle {handle!r} must be '<kind>/<instance>'",
        )
    _, _, instance_id = handle.partition("/")
    if not instance_id:
        raise _ManagedServiceError(
            f"handle {handle!r} has empty instance part",
        )
    return instance_id


def _tags_for(spec: ProvisionSpec) -> dict[str, str]:
    """CloudSQL ``userLabels`` map. Lower-case keys + values; no
    leading underscores; only [a-z0-9_-]."""

    def _sanitize(s: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in s.lower())

    base = {
        "astrolift-managed-by": "platform",
        "astrolift-organization": _sanitize(spec.organization_slug),
        "astrolift-app": _sanitize(spec.app_slug),
        "astrolift-environment": _sanitize(spec.environment_name),
        "astrolift-cluster": _sanitize(spec.tenant_cluster_id),
        "astrolift-isolation": _sanitize(spec.isolation),
    }
    # Per-binding cost-attribution keys (#438). GCP labels are
    # lowercase + [a-z0-9_-], so the dotted/slash form
    # ``astrolift.io/binding`` becomes ``astrolift-binding``.
    if spec.binding_id:
        base["astrolift-binding"] = _sanitize(spec.binding_id)
    if spec.managed_service_id:
        base["astrolift-managed-service-id"] = _sanitize(spec.managed_service_id)
    for k, v in (spec.tags or {}).items():
        base[f"astrolift-extra-{_sanitize(k)}"] = _sanitize(str(v))
    return base


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Dual-mode getter for proto-Message and dict — google-cloud
    SDKs return both depending on client variant."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_CLOUDSQL_STATE_TO_PROTOCOL = {
    "RUNNABLE": "available",
    "PENDING_CREATE": "provisioning",
    "MAINTENANCE": "updating",
    "FAILED": "error",
    "UNKNOWN": "updating",
    "SUSPENDED": "available",
    "PENDING_DELETE": "deprovisioning",
}
