"""Memorystore Redis managed-service driver (#363).

Implements ``ManagedServiceDriver`` for the canonical GCP managed-
Redis path. Same four-corner deprovision matrix as the AWS
ElastiCache driver (#352); Memorystore's API doesn't have a
"deletion protection" concept, so the ``force_destroy`` axis here
controls whether we propagate ``FAILED_PRECONDITION`` errors (e.g.
instance mid-modify) cleanly or treat them as Temporal-retryable.

Auth-token handling mirrors ElastiCache: when AUTH is enabled the
driver generates a 48-char alnum token and stores it in Secret
Manager at ``astrolift/memorystore/<id>/auth``.
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
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from _sdk.physical_naming import managed_service_identity, physical_name, recorded_resource_name
from gcp.managed._ownership import label_adoption_refusal
from gcp.managed._secret_store import ManagedSecretStore, ManagedSecretStoreError

KIND = "redis"


_SIZE_TO_MEMORY_GB = {
    "small": 1,
    "medium": 4,
    "large": 16,
    "xlarge": 64,
}


class _ManagedServiceError(Exception):
    """Internal — surfaced as DeprovisionResult.errors / ProvisionResult.errors."""


@dataclass(frozen=True)
class MemorystoreConfig:
    project_id: str
    region: str
    authorized_network: str | None = None
    """VPC selfLink for private connectivity. None defaults to GCP's
    'default' VPC; production should always set this explicitly."""

    instance_name_prefix: str = "astrolift"
    redis_version: str = "REDIS_7_2"
    tier_default: str = "BASIC"
    """``BASIC`` (single-zone, no HA) or ``STANDARD_HA`` (HA + failover).
    Spec.config.high_availability=True bumps to STANDARD_HA."""

    transit_encryption_default: bool = True
    auth_enabled_default: bool = True

    secret_manager_prefix: str = "astrolift/memorystore"
    secret_id_prefix: str = "astrolift"


class MemorystoreRedisDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: MemorystoreConfig,
        redis_client: Any | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        self._config = config
        if redis_client is not None:
            self._redis = redis_client
        else:
            from google.cloud import redis_v1

            self._redis = redis_v1.CloudRedisClient()
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
        driver="redis_memorystore",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        instance_id = self._instance_id_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(instance_id)
        if existing is not None:
            refusal = label_adoption_refusal(
                _get(existing, "labels", None) or {}, spec, resource=f"memorystore {instance_id}"
            )
            if refusal is not None:
                return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
            if (
                bool(_get(existing, "auth_enabled", False))
                and self._secret_store.get(
                    self._auth_secret_for(instance_id=instance_id),
                )
                is None
            ):
                return ProvisionResult(
                    ok=False,
                    handle=_handle_for(instance_id),
                    message=(
                        f"memorystore {instance_id} exists with AUTH enabled but its Astrolift token secret is missing"
                    ),
                    errors=["missing_auth_token_secret"],
                )
            return ProvisionResult(
                ok=True,
                handle=_handle_for(instance_id),
                message=(f"memorystore {instance_id} already exists (state={_get(existing, 'state', '?')})"),
            )

        memory_gb = int(
            cfg.get("memory_gb") or _SIZE_TO_MEMORY_GB.get(spec.size, 1),
        )
        tier = cfg.get("tier") or ("STANDARD_HA" if cfg.get("high_availability") else self._config.tier_default)
        transit_encryption = bool(
            cfg.get(
                "transit_encryption",
                self._config.transit_encryption_default,
            ),
        )
        auth_enabled = bool(
            cfg.get("auth_enabled", self._config.auth_enabled_default),
        )

        auth_token: str | None = None
        secret_name: str | None = None
        if auth_enabled:
            auth_token = _generate_auth_token()
            secret_name = self._store_auth_token(
                instance_id=instance_id,
                token=auth_token,
                spec=spec,
            )

        parent = f"projects/{self._config.project_id}/locations/{self._config.region}"
        instance_body: dict[str, Any] = {
            "tier": tier,
            "memory_size_gb": memory_gb,
            "redis_version": cfg.get("redis_version") or self._config.redis_version,
            "authorized_network": self._config.authorized_network,
            "auth_enabled": auth_enabled,
            "transit_encryption_mode": ("SERVER_AUTHENTICATION" if transit_encryption else "DISABLED"),
            "labels": _labels_for(spec),
        }
        if cfg.get("kms_key_name"):
            instance_body["customer_managed_key"] = cfg["kms_key_name"]

        try:
            self._redis.create_instance(
                request={
                    "parent": parent,
                    "instance_id": instance_id,
                    "instance": instance_body,
                },
            )
        except Exception as exc:
            if secret_name is not None:
                self._delete_connection_secrets(instance_id)
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_instance: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=_handle_for(instance_id),
            message=(
                f"memorystore {instance_id} provisioning" + (f" (auth token in {secret_name})" if secret_name else "")
            ),
        )

    @driver_op(cloud="gcp", driver="redis_memorystore")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        instance_id = _parse_handle(spec.handle)
        cfg = spec.config or {}

        update_mask: list[str] = []
        instance: dict[str, Any] = {"name": self._full_name(instance_id)}
        if spec.size:
            new_memory = _SIZE_TO_MEMORY_GB.get(spec.size)
            if new_memory:
                instance["memory_size_gb"] = new_memory
                update_mask.append("memory_size_gb")
        if cfg.get("redis_version"):
            instance["redis_version"] = cfg["redis_version"]
            update_mask.append("redis_version")

        if not update_mask:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided — no-op",
            )

        try:
            self._redis.update_instance(
                request={
                    "update_mask": {"paths": update_mask},
                    "instance": instance,
                },
            )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"update_instance: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"memorystore {instance_id} update queued",
        )

    @driver_op(
        cloud="gcp",
        driver="redis_memorystore",
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
                message=f"memorystore {instance_id} already gone",
            )

        if not delete_data:
            # Take a final export to GCS-ish backup via the export
            # endpoint. Memorystore has no "final snapshot" knob on
            # delete, so we do it as a pre-step. delete_data=True
            # skips it.
            # Don't block delete on export failure; surface the
            # message but proceed.
            with contextlib.suppress(Exception):
                self._redis.export_instance(
                    request={
                        "name": self._full_name(instance_id),
                        "output_config": {
                            "gcs_destination": {
                                "uri": (f"gs://astrolift-final-redis-{self._config.project_id}/{instance_id}.rdb"),
                            },
                        },
                    },
                )

        try:
            self._redis.delete_instance(
                request={"name": self._full_name(instance_id)},
            )
        except Exception as exc:
            err_str = str(exc)
            if not force_destroy and "FAILED_PRECONDITION" in err_str:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=(f"memorystore {instance_id} is mid-modify; wait or pass force_destroy=True for retry"),
                    errors=[err_str],
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_instance: {err_str}",
                errors=[err_str],
            )

        if delete_data:
            self._delete_connection_secrets(instance_id)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"memorystore {instance_id} delete queued "
                f"(export={'skipped' if delete_data else 'taken'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="gcp", driver="redis_memorystore")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        instance_id = _parse_handle(handle.handle)
        existing = self._describe(instance_id)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"memorystore {instance_id} not found",
            )
        state = _get(existing, "state", "UNKNOWN")
        # Memorystore State enum may be an int or a string depending
        # on SDK variant; normalize.
        state_name = state.name if hasattr(state, "name") else str(state)
        return ServiceStatus(
            handle=handle.handle,
            state=_MEMORYSTORE_STATE_TO_PROTOCOL.get(state_name, "updating"),
            message=f"memorystore reports {state_name}",
        )

    @driver_op(cloud="gcp", driver="redis_memorystore")
    def binding(self, handle: ServiceHandle) -> Binding:
        instance_id = _parse_handle(handle.handle)
        existing = self._describe(instance_id)
        if existing is None:
            raise _ManagedServiceError(
                f"binding requested for missing instance {instance_id}",
            )
        host = _get(existing, "host", "") or ""
        port = str(_get(existing, "port", 6379) or 6379)
        transit_mode = _get(existing, "transit_encryption_mode", "")
        tls_on = "SERVER_AUTHENTICATION" in str(transit_mode) if transit_mode else False
        auth_enabled = bool(_get(existing, "auth_enabled", False))

        env_vars: dict[str, ValueRef] = {
            "REDIS_HOST": ValueRef(literal=host),
            "REDIS_PORT": ValueRef(literal=port),
            "REDIS_USER": ValueRef(literal="default"),
            "REDIS_TLS": ValueRef(literal="1" if tls_on else "0"),
            "REDIS_AUTH_MODE": ValueRef(literal="password" if auth_enabled else "open"),
            # Portable field name retained by the cross-cloud envelope; GCP
            # contributes its full resource name rather than an AWS ARN.
            "REDIS_RESOURCE_ARN": ValueRef(literal=self._full_name(instance_id)),
        }
        if auth_enabled:
            secret_name = self._auth_secret_for(instance_id=instance_id)
            token = self._secret_store.get(secret_name)
            if token is None:
                raise _ManagedServiceError(
                    f"binding requested for {instance_id}, but AUTH token secret {secret_name!r} is missing",
                )
            url_secret = self._url_secret_for(instance_id=instance_id)
            scheme = "rediss" if tls_on else "redis"
            try:
                self._secret_store.upsert(
                    url_secret,
                    f"{scheme}://default:{quote(token, safe='')}@{host}:{port}",
                )
            except ManagedSecretStoreError as exc:
                raise _ManagedServiceError(str(exc)) from exc
            env_vars["REDIS_PASSWORD"] = ValueRef(secret_ref=secret_name)
            env_vars["REDIS_URL"] = ValueRef(secret_ref=url_secret)
        else:
            scheme = "rediss" if tls_on else "redis"
            env_vars["REDIS_URL"] = ValueRef(
                literal=f"{scheme}://{host}:{port}",
            )
        return Binding(
            env_vars=env_vars,
            iam_grants=[],
            notes=(
                "REDIS_URL is a literal when auth_enabled is off; "
                "with auth on it's a secret_ref to "
                "``rediss://default:<token>@<host>:<port>``."
            ),
        )

    @driver_op(cloud="gcp", driver="redis_memorystore")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        instance_id = _parse_handle(handle.handle)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        snap_uri = f"gs://astrolift-snap-redis-{self._config.project_id}/{instance_id}-{stamp}.rdb"
        try:
            self._redis.export_instance(
                request={
                    "name": self._full_name(instance_id),
                    "output_config": {
                        "gcs_destination": {"uri": snap_uri},
                    },
                },
            )
        except Exception as exc:
            raise _ManagedServiceError(
                f"export_instance: {exc}",
            ) from exc
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snap_uri,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="gcp", driver="redis_memorystore")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        target_id = self._instance_id_for(spec=target)
        try:
            self._redis.import_instance(
                request={
                    "name": self._full_name(target_id),
                    "input_config": {
                        "gcs_source": {"uri": snapshot.snapshot_id},
                    },
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"import_instance: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=_handle_for(target_id),
            message=f"memorystore restore from {snapshot.snapshot_id} queued",
        )

    @driver_op(cloud="gcp", driver="redis_memorystore", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_gb": {"type": "integer", "minimum": 1},
                "redis_version": {"type": "string"},
                "tier": {"type": "string", "enum": ["BASIC", "STANDARD_HA"]},
                "high_availability": {"type": "boolean"},
                "transit_encryption": {"type": "boolean"},
                "auth_enabled": {"type": "boolean"},
                "kms_key_name": {"type": "string"},
            },
        }

    @driver_op(cloud="gcp", driver="redis_memorystore", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "REDIS_HOST": "Memorystore primary endpoint host",
                "REDIS_PORT": "Memorystore primary endpoint port (6379)",
                "REDIS_USER": "Redis ACL username (default)",
                "REDIS_TLS": "1 if TLS is enabled, 0 otherwise",
                "REDIS_PASSWORD": ("Secret Manager ref to the AUTH token (auth_enabled only)"),
                "REDIS_URL": ("Literal redis(s):// URL when auth is off; Secret Manager ref to rediss:// URL when on"),
                "REDIS_AUTH_MODE": "password when AUTH is enabled, otherwise open",
                "REDIS_RESOURCE_ARN": "Full GCP Memorystore resource name",
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, instance_id: str) -> Any | None:
        try:
            return self._redis.get_instance(
                request={"name": self._full_name(instance_id)},
            )
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc) or "not found" in str(exc).lower():
                return None
            raise

    def _instance_id_for(self, *, spec: ProvisionSpec) -> str:
        managed_service_identity(spec.managed_service_id)
        return recorded_resource_name(spec.recorded_handle, kind=KIND) or physical_name(
            spec.managed_service_id, prefix=self._config.instance_name_prefix, max_length=40
        )

    def _full_name(self, instance_id: str) -> str:
        return f"projects/{self._config.project_id}/locations/{self._config.region}/instances/{instance_id}"

    def _auth_secret_for(self, *, instance_id: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/auth"

    def _url_secret_for(self, *, instance_id: str) -> str:
        return f"{self._config.secret_manager_prefix}/{instance_id}/url"

    def _store_auth_token(
        self,
        *,
        instance_id: str,
        token: str,
        spec: ProvisionSpec,
    ) -> str:
        name = self._auth_secret_for(instance_id=instance_id)
        try:
            return self._secret_store.upsert(name, token, labels=_labels_for(spec))
        except ManagedSecretStoreError as exc:
            raise _ManagedServiceError(str(exc)) from exc

    def _delete_connection_secrets(self, instance_id: str) -> None:
        for path in (
            self._auth_secret_for(instance_id=instance_id),
            self._url_secret_for(instance_id=instance_id),
        ):
            with contextlib.suppress(ManagedSecretStoreError):
                self._secret_store.delete(path)


# ----- module-level helpers --------------------------------------------


_AUTH_ALPHABET = string.ascii_letters + string.digits


def _generate_auth_token(length: int = 48) -> str:
    return "".join(secrets.choice(_AUTH_ALPHABET) for _ in range(length))


def _handle_for(instance_id: str) -> str:
    return f"{KIND}/{instance_id}"


def _parse_handle(handle: str) -> str:
    if "/" not in handle:
        raise _ManagedServiceError(
            f"handle {handle!r} must be '<kind>/<instance>'",
        )
    _, _, instance_id = handle.partition("/")
    return instance_id


def _labels_for(spec: ProvisionSpec) -> dict[str, str]:
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
        base[MANAGED_SERVICE_ID_LABEL] = _sanitize(spec.managed_service_id)
    for k, v in (spec.tags or {}).items():
        base[f"astrolift-extra-{_sanitize(k)}"] = _sanitize(str(v))
    return base


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_MEMORYSTORE_STATE_TO_PROTOCOL = {
    "READY": "available",
    "CREATING": "provisioning",
    "UPDATING": "updating",
    "DELETING": "deprovisioning",
    "REPAIRING": "updating",
    "MAINTENANCE": "updating",
    "IMPORTING": "updating",
    "FAILING_OVER": "updating",
    "STATE_UNSPECIFIED": "updating",
}
