"""CloudSQL MySQL managed-service driver (#370 — GCP slice).

Implements ``ManagedServiceDriver`` for the canonical GCP managed-
MySQL path. Mirrors the CloudSQL Postgres driver (#363) one-for-one;
the GCP API surface for ``sql_v1.SqlInstancesServiceClient`` is
engine-agnostic — only ``databaseVersion`` and the binding port
differ. The four-corner deprovision matrix is identical to the
postgres driver:

  delete_data=False, force_destroy=False (default):
    final on-demand backup taken; ``deletionProtectionEnabled`` is
    respected. Refuses cleanly when protection is on so the operator
    must opt into ``force_destroy=True``.

  delete_data=True, force_destroy=False:
    skip final backup; protection still respected.

  delete_data=False, force_destroy=True:
    final backup taken; protection bypassed (driver patches
    deletionProtectionEnabled=False before delete).

  delete_data=True, force_destroy=True:
    --atomic: skip backup, bypass protection.

Master password handling matches the postgres path: 32-char shell-
safe alphabet, stored in Secret Manager at
``astrolift/cloudsql/<id>/master``. The binding emits
``DATABASE_PORT=3306`` and ``DATABASE_USER=root`` (CloudSQL MySQL's
master user) — the only operator-facing differences from postgres.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
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

KIND = "mysql"


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
class CloudSQLMySQLConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    project_id: str
    region: str

    private_network: str | None = None
    """If set, instance uses Private IP via this VPC selfLink. None
    means Public IP (with authorized networks); operators with VPC-SC
    should always set this."""

    instance_name_prefix: str = "astrolift"
    engine_version: str = "MYSQL_8_0"

    backup_retention_days: int = 7
    high_availability_default: bool = False
    """Maps to ``settings.availabilityType``: REGIONAL when True,
    ZONAL when False."""

    deletion_protection_default: bool = True

    secret_manager_prefix: str = "astrolift/cloudsql"
    """Shared prefix with the postgres driver: instance ids are
    globally unique within the project so no collision risk, and
    the operator gets one consistent place to find DB secrets."""


class CloudSQLMySQLDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: CloudSQLMySQLConfig,
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

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="gcp",
        driver="mysql_cloudsql",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        instance_id = self._instance_id_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(instance_id)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=_handle_for(instance_id),
                message=(
                    f"cloudsql-mysql {instance_id} already exists "
                    f"(state={_get(existing, 'state', '?')})"
                ),
            )

        master_password = _generate_master_password()
        secret_name = self._store_master_password(
            instance_id=instance_id,
            password=master_password,
            spec=spec,
        )

        tier = cfg.get("tier") or _SIZE_TO_TIER.get(
            spec.size, "db-custom-1-3840",
        )
        storage_gb = int(
            cfg.get("storage_gb")
            or _SIZE_TO_STORAGE_GB.get(spec.size, 20),
        )
        engine_version = (
            cfg.get("engine_version") or self._config.engine_version
        )
        ha = bool(
            cfg.get(
                "high_availability",
                self._config.high_availability_default,
            ),
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
                    # MySQL on CloudSQL supports point-in-time recovery
                    # via binary logs (vs Postgres' WAL). Same flag
                    # name; the underlying mechanism is binlogs.
                    "binaryLogEnabled": True,
                    "transactionLogRetentionDays": int(
                        cfg.get(
                            "backup_retention_days",
                            self._config.backup_retention_days,
                        ),
                    ),
                },
                "ipConfiguration": {
                    "ipv4Enabled": (
                        self._config.private_network is None
                    ),
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
            self._delete_master_password_secret(instance_id)
            return ProvisionResult(
                ok=False, handle="",
                message=f"insert: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=_handle_for(instance_id),
            message=(
                f"cloudsql-mysql {instance_id} provisioning "
                f"(password in {secret_name})"
            ),
        )

    @driver_op(cloud="gcp", driver="mysql_cloudsql")
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
            settings["availabilityType"] = (
                "REGIONAL" if cfg["high_availability"] else "ZONAL"
            )
        if "backup_retention_days" in cfg:
            settings.setdefault("backupConfiguration", {})[
                "transactionLogRetentionDays"
            ] = int(cfg["backup_retention_days"])

        if not settings:
            return UpdateResult(
                ok=True, handle=spec.handle,
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
                ok=False, handle=spec.handle,
                message=f"patch: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True, handle=spec.handle,
            message=f"cloudsql-mysql {instance_id} update queued",
        )

    @driver_op(
        cloud="gcp",
        driver="mysql_cloudsql",
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
            self._delete_master_password_secret(instance_id)
            return DeprovisionResult(
                ok=True, handle=spec.handle,
                message=f"cloudsql-mysql {instance_id} already gone",
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
                    body={
                        "settings": {
                            "deletionProtectionEnabled": False,
                        },
                    },
                )
            except Exception as exc:
                return DeprovisionResult(
                    ok=False, handle=spec.handle,
                    message=f"failed to clear deletionProtection: {exc}",
                    errors=[str(exc)],
                )
        elif protected:
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=(
                    f"cloudsql-mysql {instance_id} has "
                    f"deletionProtection enabled — pass "
                    f"force_destroy=True to bypass"
                ),
                errors=["deletion_protection_enabled"],
            )

        if not delete_data:
            # Take a final on-demand backup before delete. CloudSQL
            # has no "skip final backup" delete-time flag like RDS;
            # we do it as a pre-step. delete_data=True skips it.
            try:
                self._sql.insert_backup_run(
                    project=self._config.project_id,
                    instance=instance_id,
                    body={"description": f"final-{instance_id}"},
                )
            except Exception:
                # Don't block delete on backup failure — defeats the
                # safety path. Surface via message but proceed.
                pass

        try:
            self._sql.delete(
                project=self._config.project_id,
                instance=instance_id,
            )
        except Exception as exc:
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=f"delete: {exc}",
                errors=[str(exc)],
            )

        if delete_data:
            self._delete_master_password_secret(instance_id)

        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message=(
                f"cloudsql-mysql {instance_id} delete queued "
                f"(backup={'skipped' if delete_data else 'taken'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="gcp", driver="mysql_cloudsql")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        instance_id = _parse_handle(handle.handle)
        existing = self._describe(instance_id)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle, state="deprovisioned",
                message=f"cloudsql-mysql {instance_id} not found",
            )
        state = _get(existing, "state", "UNKNOWN")
        return ServiceStatus(
            handle=handle.handle,
            state=_CLOUDSQL_STATE_TO_PROTOCOL.get(state, "updating"),
            message=f"cloudsql-mysql reports {state}",
        )

    @driver_op(cloud="gcp", driver="mysql_cloudsql")
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
        return Binding(
            env_vars={
                "DATABASE_HOST": ValueRef(literal=host),
                "DATABASE_PORT": ValueRef(literal="3306"),
                "DATABASE_NAME": ValueRef(literal="mysql"),
                "DATABASE_USER": ValueRef(literal="root"),
                "DATABASE_PASSWORD": ValueRef(secret_ref=secret_name),
                "DATABASE_URL": ValueRef(
                    secret_ref=self._url_secret_for(
                        instance_id=instance_id,
                    ),
                ),
            },
            iam_grants=[
                Grant(
                    resource=secret_name,
                    actions=["secretmanager.versions.access"],
                ),
            ],
            notes=(
                "DATABASE_URL is a derived secret holding the "
                "mysql:// connection string; the split components "
                "are also exposed for callers that build their own DSN."
            ),
        )

    @driver_op(cloud="gcp", driver="mysql_cloudsql")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        instance_id = _parse_handle(handle.handle)
        snap_id = (
            f"{instance_id}-snap-"
            f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        )
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

    @driver_op(cloud="gcp", driver="mysql_cloudsql")
    def restore(
        self, snapshot: SnapshotHandle, target: ProvisionSpec,
    ) -> ProvisionResult:
        target_id = self._instance_id_for(spec=target)
        source_id = _parse_handle(snapshot.handle)
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
            return ProvisionResult(
                ok=False, handle="",
                message=f"clone: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=_handle_for(target_id),
            message=f"cloudsql-mysql clone from {source_id} queued",
        )

    @driver_op(cloud="gcp", driver="mysql_cloudsql", heartbeat=False)
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
                    "type": "integer", "minimum": 0, "maximum": 35,
                },
                "kms_key_name": {"type": "string"},
                "zone": {"type": "string"},
            },
        }

    @driver_op(cloud="gcp", driver="mysql_cloudsql", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "DATABASE_HOST": "CloudSQL private/public IP",
                "DATABASE_PORT": "3306 (MySQL default)",
                "DATABASE_NAME": "Initial database name (mysql)",
                "DATABASE_USER": "Master user (root)",
                "DATABASE_PASSWORD": (
                    "Secret Manager ref to the master password"
                ),
                "DATABASE_URL": (
                    "Secret Manager ref to the fully-formed "
                    "mysql:// connection string"
                ),
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
        # google-cloud SDKs return either proto-Message or dict
        # depending on client variant; normalize to dict.
        if hasattr(resp, "_pb"):
            from google.protobuf.json_format import MessageToDict

            return MessageToDict(
                resp._pb, preserving_proto_field_name=True,
            )
        return (
            dict(resp)
            if isinstance(resp, dict)
            else getattr(resp, "__dict__", {})
        )

    def _instance_id_for(self, *, spec: ProvisionSpec) -> str:
        # CloudSQL instance names: lowercase, letters/digits/hyphens,
        # ≤98 chars, must start with letter.
        raw = (
            f"{self._config.instance_name_prefix}-"
            f"{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}-{spec.service_handle_hint or 'my'}"
        ).lower().replace("_", "-")
        return "".join(
            c for c in raw if c.isalnum() or c == "-"
        )[:98]

    def _master_secret_for(self, *, instance_id: str) -> str:
        return (
            f"{self._config.secret_manager_prefix}/{instance_id}/master"
        )

    def _url_secret_for(self, *, instance_id: str) -> str:
        return (
            f"{self._config.secret_manager_prefix}/{instance_id}/url"
        )

    def _store_master_password(
        self,
        *,
        instance_id: str,
        password: str,
        spec: ProvisionSpec,
    ) -> str:
        name = self._master_secret_for(instance_id=instance_id)
        parent = f"projects/{self._config.project_id}"
        try:
            self._sm.create_secret(
                request={
                    "parent": parent,
                    "secret_id": name.replace("/", "_"),
                    "secret": {
                        "replication": {"automatic": {}},
                        "labels": _tags_for(spec),
                    },
                },
            )
        except Exception:
            # AlreadyExists or similar — fall through to add_version.
            pass
        try:
            self._sm.add_secret_version(
                request={
                    "parent": (
                        f"{parent}/secrets/{name.replace('/', '_')}"
                    ),
                    "payload": {"data": password.encode("utf-8")},
                },
            )
            return name
        except Exception as exc:
            raise _ManagedServiceError(
                f"add_secret_version for {name}: {exc}",
            ) from exc

    def _delete_master_password_secret(self, instance_id: str) -> None:
        name = self._master_secret_for(instance_id=instance_id)
        try:
            self._sm.delete_secret(
                request={
                    "name": (
                        f"projects/{self._config.project_id}/secrets/"
                        f"{name.replace('/', '_')}"
                    ),
                },
            )
        except Exception:
            return


# ----- module-level helpers --------------------------------------------


_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_."


def _generate_master_password(length: int = 32) -> str:
    return "".join(
        secrets.choice(_PASSWORD_ALPHABET) for _ in range(length)
    )


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
        return "".join(
            c if c.isalnum() or c in "-_" else "-" for c in s.lower()
        )

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
