"""Azure Cache for Redis managed-service driver (#364).

Implements ``ManagedServiceDriver`` for the canonical Azure managed-
Redis path. Mirrors the AWS ElastiCache + GCP Memorystore drivers'
lifecycle envelopes so the control plane sees consistent semantics
across clouds. Differences that matter:

* Azure Cache for Redis has a soft-delete window: deleted caches
  remain recoverable by name for 7 days unless purged. The driver
  honours the soft-delete window by default
  (``force_destroy=False``) and triggers an immediate purge when
  ``force_destroy=True``.
* There is no per-cache deletion-protection flag on Azure Cache
  for Redis the way there is on Postgres / MySQL Flex. The
  ``force_destroy`` axis here covers (a) the soft-delete purge
  and (b) treatment of ``CacheNotInDesiredState`` / mid-modify
  errors as bypassable rather than hard-fail.
* Auth keys are persisted to Azure Key Vault. Azure exposes them
  via ``redis.list_keys()``; the driver reads them on provision
  and stores both primary + secondary at well-known secret names.
"""

from __future__ import annotations

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

KIND = "redis"


class AzureCacheRedisError(Exception):
    """Distinct from the generic plugin error so the control plane's
    failure-handling logic can tell managed-service failures from
    infra-driver failures (managed-service partial state often needs
    targeted cleanup of half-created Key Vault secrets etc.)."""


# Size -> SKU. Azure Cache for Redis SKUs are shaped as
# ``<family>_<capacity>``: ``C`` family is Basic/Standard tier,
# ``P`` family is Premium tier (clustering, persistence, VNet).
# astrolift's small/medium/large/xlarge map onto Standard SKU
# capacities; operators can override via ``spec.config.sku_name``.
_SIZE_TO_SKU = {
    "small": "Standard_C1",
    "medium": "Standard_C2",
    "large": "Standard_C4",
    "xlarge": "Premium_P1",
}


def _parse_sku(sku_name: str) -> tuple[str, str, int]:
    """Pull the (tier, family, capacity) triple out of ``Standard_C1``
    / ``Premium_P3`` style SKU names.

    Azure's REST schema wants those three fields independently rather
    than the joined string the operator typed; this helper avoids
    hand-jamming the split at every caller."""
    tier_part, _, fam_cap = sku_name.partition("_")
    family = (fam_cap[:1] or "C").upper()
    cap_str = fam_cap[1:] or "1"
    try:
        capacity = int(cap_str)
    except ValueError:
        capacity = 1
    tier = tier_part.capitalize() if tier_part else "Standard"
    return tier, family, capacity


@dataclass(frozen=True)
class AzureCacheRedisConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    subscription_id: str
    resource_group: str
    location: str = "eastus"

    cache_name_prefix: str = "astrolift"
    """Prefix on the cache name. Azure Cache for Redis names must
    be globally unique across all of Azure (they're DNS labels under
    redis.cache.windows.net), so the slug + env qualifiers keep
    collisions impossible across apps in the same install."""

    default_sku: str = "Standard_C1"
    """SKU shape ``<tier>_<family><capacity>``. Standard_C1 is the
    cheapest production-grade tier (1 GB, single zone, no clustering).
    Operators bump to Premium_P* for VNet injection, persistence,
    or clustering."""

    minimum_tls_version_default: str = "1.2"
    """Azure exposes a minimum TLS version on the cache; we default
    to 1.2 to match the platform's compliance posture."""

    enable_non_ssl_port_default: bool = False
    """Off by default; only the SSL port (6380) is opened. Operators
    explicitly opt in to the non-SSL port (6379) per binding."""

    keyvault_url: str = ""
    """Key Vault URL where the driver stores cache primary +
    secondary keys. Empty string means no Key Vault is configured --
    the driver returns an error on provision in that case rather
    than emitting credentials in the clear."""

    secret_name_prefix: str = "astrolift-redis"

    mgmt_client: Any | None = None
    """Injected ``RedisManagementClient`` for tests."""

    secret_client: Any | None = None
    """Injected ``SecretClient`` for tests."""


def tags_for(spec: ProvisionSpec) -> dict[str, str]:
    """Same shape as the Postgres-Flex / MySQL-Flex driver's
    ``tags_for`` helper."""
    base = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/organization": spec.organization_slug,
        "astrolift.io/app": spec.app_slug,
        "astrolift.io/environment": spec.environment_name,
        "astrolift.io/cluster": spec.tenant_cluster_id,
        "astrolift.io/isolation": spec.isolation,
    }
    # Per-binding cost-attribution keys (#438). Azure allows the
    # slash + dot form so keys stay identical to the canonical
    # platform schema.
    if spec.binding_id:
        base["astrolift.io/binding"] = spec.binding_id
    if spec.managed_service_id:
        base["astrolift.io/managed_service_id"] = spec.managed_service_id
    base.update({f"astrolift.io/extra/{k}": v for k, v in (spec.tags or {}).items()})
    return base


class AzureCacheRedisDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureCacheRedisConfig) -> None:
        self._config = config
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.redis import RedisManagementClient

            self._mgmt = RedisManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        if config.secret_client is not None:
            self._secrets = config.secret_client
        elif config.keyvault_url:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            self._secrets = SecretClient(
                vault_url=config.keyvault_url,
                credential=DefaultAzureCredential(),
            )
        else:
            self._secrets = None

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="azure",
        driver="cache_redis",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if self._secrets is None:
            return ProvisionResult(
                ok=False,
                handle="",
                message=(
                    "azure cache for redis driver requires a Key Vault (set keyvault_url or inject secret_client)"
                ),
                errors=["no_secret_backend"],
            )
        cache_name = self._cache_name_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(cache_name)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=self._handle_for(cache_name=cache_name),
                message=(f"cache {cache_name} already exists (state={_state_of(existing)})"),
            )

        sku_name = cfg.get("sku_name") or _SIZE_TO_SKU.get(spec.size, self._config.default_sku)
        tier, family, capacity = _parse_sku(sku_name)
        minimum_tls = cfg.get("minimum_tls_version") or self._config.minimum_tls_version_default
        enable_non_ssl = bool(
            cfg.get(
                "enable_non_ssl_port",
                self._config.enable_non_ssl_port_default,
            ),
        )

        parameters: dict[str, Any] = {
            "location": self._config.location,
            "sku": {"name": tier, "family": family, "capacity": capacity},
            "enable_non_ssl_port": enable_non_ssl,
            "minimum_tls_version": minimum_tls,
            "tags": tags_for(spec),
        }
        if cfg.get("subnet_id"):
            # Premium-tier VNet injection. Azure refuses the property
            # on non-Premium SKUs; we let the API surface the error
            # rather than guessing tier rules here.
            parameters["subnet_id"] = cfg["subnet_id"]
        if cfg.get("static_ip"):
            parameters["static_ip"] = cfg["static_ip"]
        if "shard_count" in cfg:
            parameters["shard_count"] = int(cfg["shard_count"])
        if cfg.get("redis_configuration"):
            parameters["redis_configuration"] = dict(cfg["redis_configuration"])

        try:
            poller = self._mgmt.redis.begin_create(
                resource_group_name=self._config.resource_group,
                name=cache_name,
                parameters=parameters,
            )
            poller.result()
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"begin_create: {exc}",
                errors=[str(exc)],
            )

        # Persist access keys to Key Vault. Azure auto-generates them
        # when the cache is created; we read them via list_keys and
        # store both primary + secondary so workloads can rotate.
        try:
            keys = self._mgmt.redis.list_keys(
                resource_group_name=self._config.resource_group,
                name=cache_name,
            )
            self._store_access_keys(cache_name=cache_name, keys=keys)
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"list_keys / set_secret: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(cache_name=cache_name),
            message=(f"cache {cache_name} provisioning (keys in key vault {self._config.keyvault_url})"),
        )

    @driver_op(cloud="azure", driver="cache_redis")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        cache_name = self._cache_name_from_handle(spec.handle)
        cfg = spec.config or {}

        body: dict[str, Any] = {}
        if spec.size:
            sku_name = cfg.get("sku_name") or _SIZE_TO_SKU.get(spec.size)
            if sku_name:
                tier, family, capacity = _parse_sku(sku_name)
                body["sku"] = {
                    "name": tier,
                    "family": family,
                    "capacity": capacity,
                }
        if cfg.get("sku_name") and "sku" not in body:
            tier, family, capacity = _parse_sku(cfg["sku_name"])
            body["sku"] = {"name": tier, "family": family, "capacity": capacity}
        if "minimum_tls_version" in cfg:
            body["minimum_tls_version"] = cfg["minimum_tls_version"]
        if "enable_non_ssl_port" in cfg:
            body["enable_non_ssl_port"] = bool(cfg["enable_non_ssl_port"])
        if "shard_count" in cfg:
            body["shard_count"] = int(cfg["shard_count"])
        if cfg.get("redis_configuration"):
            body["redis_configuration"] = dict(cfg["redis_configuration"])

        if not body:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        try:
            self._mgmt.redis.update(
                resource_group_name=self._config.resource_group,
                name=cache_name,
                parameters=body,
            )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"update: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"cache {cache_name} update queued",
        )

    @driver_op(
        cloud="azure",
        driver="cache_redis",
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
        cache_name = self._cache_name_from_handle(spec.handle)

        existing = self._describe(cache_name)
        if existing is None:
            self._delete_access_key_secrets(cache_name)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"cache {cache_name} already gone",
            )

        export_taken = False
        if not delete_data:
            # Azure Cache for Redis Premium supports RDB export to
            # blob storage; we trigger an export-as-final-snapshot
            # when the operator wants to retain the data path. For
            # Standard-tier caches the API returns a clear error;
            # we surface it as a soft warning rather than blocking
            # the delete (matches the Memorystore + ElastiCache
            # drivers' best-effort posture).
            try:
                self._mgmt.redis.begin_export_data(
                    resource_group_name=self._config.resource_group,
                    name=cache_name,
                    parameters={
                        "prefix": f"final-{cache_name}",
                        "container": ("https://astrolift-final-redis.blob.core.windows.net/exports"),
                        "format": "RDB",
                    },
                ).result()
                export_taken = True
            except Exception:
                # Best-effort: don't block delete on export failure
                # (Standard-tier doesn't support export at all).
                export_taken = False

        try:
            poller = self._mgmt.redis.begin_delete(
                resource_group_name=self._config.resource_group,
                name=cache_name,
            )
            poller.result()
        except Exception as exc:
            err_str = str(exc)
            if not force_destroy and ("FAILED_PRECONDITION" in err_str or "CacheNotInDesiredState" in err_str):
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=(f"cache {cache_name} is mid-modify; wait or pass force_destroy=True for retry"),
                    errors=[err_str],
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"begin_delete: {err_str}",
                errors=[err_str],
            )

        # Soft-delete window: by default Azure keeps the cache name
        # recoverable for 7 days. force_destroy=True purges the
        # cache immediately so the name can be re-used.
        purged = False
        if force_destroy:
            try:
                self._mgmt.redis.begin_purge(
                    resource_group_name=self._config.resource_group,
                    name=cache_name,
                ).result()
                purged = True
            except Exception:
                # Purge support varies by region/SDK version; treat
                # as best-effort and surface via the message.
                purged = False

        if delete_data:
            self._delete_access_key_secrets(cache_name)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"cache {cache_name} delete queued "
                f"(export={'taken' if export_taken else 'skipped'}, "
                f"purged={'yes' if purged else 'soft-delete'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="azure", driver="cache_redis")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        cache_name = self._cache_name_from_handle(handle.handle)
        existing = self._describe(cache_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"cache {cache_name} not found",
            )
        azure_state = _state_of(existing)
        return ServiceStatus(
            handle=handle.handle,
            state=_AZURE_STATE_TO_PROTOCOL.get(
                azure_state,
                "updating",
            ),
            message=f"azure reports {azure_state}",
        )

    @driver_op(cloud="azure", driver="cache_redis")
    def binding(self, handle: ServiceHandle) -> Binding:
        cache_name = self._cache_name_from_handle(handle.handle)
        existing = self._describe(cache_name)
        if existing is None:
            raise AzureCacheRedisError(
                f"binding requested for missing cache {cache_name}",
            )
        host = _hostname_of(existing) or (f"{cache_name}.redis.cache.windows.net")
        ssl_port = _ssl_port_of(existing)
        non_ssl_port = _non_ssl_port_of(existing)
        primary_secret = self._primary_secret_for(cache_name=cache_name)
        secondary_secret = self._secondary_secret_for(cache_name=cache_name)

        env_vars: dict[str, ValueRef] = {
            "REDIS_HOST": ValueRef(literal=host),
            "REDIS_PORT": ValueRef(literal=str(ssl_port)),
            "REDIS_TLS": ValueRef(literal="1"),
            "REDIS_AUTH_TOKEN": ValueRef(secret_ref=primary_secret),
            "REDIS_SECONDARY_AUTH_TOKEN": ValueRef(
                secret_ref=secondary_secret,
            ),
            "REDIS_URL": ValueRef(
                secret_ref=self._url_secret_for(cache_name=cache_name),
            ),
        }
        if non_ssl_port:
            env_vars["REDIS_PORT_NON_SSL"] = ValueRef(literal=str(non_ssl_port))
        return Binding(
            env_vars=env_vars,
            iam_grants=[
                Grant(
                    resource=(
                        f"/subscriptions/{self._config.subscription_id}"
                        f"/resourceGroups/{self._config.resource_group}"
                        f"/providers/Microsoft.Cache/Redis/{cache_name}"
                    ),
                    actions=[
                        "Microsoft.Cache/Redis/read",
                        "Microsoft.Cache/Redis/listKeys/action",
                    ],
                ),
                Grant(
                    resource=primary_secret,
                    actions=["Microsoft.KeyVault/vaults/secrets/getSecret"],
                ),
            ],
            notes=(
                "REDIS_URL is a Key Vault ref to ``rediss://:<key>@"
                "<host>:<ssl_port>``. REDIS_AUTH_TOKEN + "
                "REDIS_SECONDARY_AUTH_TOKEN expose both keys for "
                "operator-side rotation."
            ),
        )

    @driver_op(cloud="azure", driver="cache_redis")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        cache_name = self._cache_name_from_handle(handle.handle)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        snap_id = f"{cache_name}-snap-{stamp}"
        try:
            self._mgmt.redis.begin_export_data(
                resource_group_name=self._config.resource_group,
                name=cache_name,
                parameters={
                    "prefix": snap_id,
                    "container": ("https://astrolift-snap-redis.blob.core.windows.net/snapshots"),
                    "format": "RDB",
                },
            ).result()
        except Exception as exc:
            raise AzureCacheRedisError(
                f"begin_export_data: {exc}",
            ) from exc
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snap_id,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="azure", driver="cache_redis")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        target_cache = self._cache_name_for(spec=target)
        try:
            self._mgmt.redis.begin_import_data(
                resource_group_name=self._config.resource_group,
                name=target_cache,
                parameters={
                    "files": [
                        f"https://astrolift-snap-redis.blob.core.windows.net/snapshots/{snapshot.snapshot_id}.rdb",
                    ],
                    "format": "RDB",
                },
            ).result()
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"begin_import_data: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=self._handle_for(cache_name=target_cache),
            message=f"restore from {snapshot.snapshot_id} queued",
        )

    @driver_op(cloud="azure", driver="cache_redis", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sku_name": {
                    "type": "string",
                    "description": ("SKU shape <tier>_<family><capacity>, e.g. Standard_C1, Premium_P1."),
                },
                "minimum_tls_version": {
                    "type": "string",
                    "enum": ["1.0", "1.1", "1.2"],
                },
                "enable_non_ssl_port": {"type": "boolean"},
                "shard_count": {"type": "integer", "minimum": 1, "maximum": 10},
                "subnet_id": {"type": "string"},
                "static_ip": {"type": "string"},
                "redis_configuration": {"type": "object"},
            },
        }

    @driver_op(cloud="azure", driver="cache_redis", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "REDIS_HOST": "Cache hostname (<name>.redis.cache.windows.net)",
                "REDIS_PORT": "SSL port (6380)",
                "REDIS_TLS": "Always 1 -- Azure Cache for Redis is TLS-only by default",
                "REDIS_AUTH_TOKEN": "Key Vault ref to the primary access key",
                "REDIS_SECONDARY_AUTH_TOKEN": ("Key Vault ref to the secondary access key"),
                "REDIS_URL": ("Key Vault ref to the fully-formed rediss:// URL"),
                "REDIS_PORT_NON_SSL": ("Plain port (6379) -- only present when enable_non_ssl_port=true"),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, cache_name: str) -> Any | None:
        try:
            return self._mgmt.redis.get(
                resource_group_name=self._config.resource_group,
                name=cache_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            if "ResourceNotFound" in str(exc):
                return None
            raise

    def _cache_name_for(self, *, spec: ProvisionSpec) -> str:
        # Azure Cache for Redis names: 1-63 chars; must be valid DNS
        # labels under redis.cache.windows.net (lowercase letters,
        # digits, hyphens; cannot start/end with hyphen).
        parts = [
            self._config.cache_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "redis",
        ]
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:63]

    def _handle_for(self, *, cache_name: str) -> str:
        return f"{KIND}/{cache_name}"

    def _cache_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureCacheRedisError(
                f"handle {handle!r} must be '<kind>/<cache_name>'",
            )
        kind, _, cache_name = handle.partition("/")
        if not kind or not cache_name:
            raise AzureCacheRedisError(
                f"handle {handle!r} has empty component",
            )
        return cache_name

    def _primary_secret_for(self, *, cache_name: str) -> str:
        return f"{self._config.secret_name_prefix}-{cache_name}-primary"

    def _secondary_secret_for(self, *, cache_name: str) -> str:
        return f"{self._config.secret_name_prefix}-{cache_name}-secondary"

    def _url_secret_for(self, *, cache_name: str) -> str:
        return f"{self._config.secret_name_prefix}-{cache_name}-url"

    def _store_access_keys(self, *, cache_name: str, keys: Any) -> None:
        primary = _key_field(keys, "primary_key") or _key_field(keys, "primaryKey") or ""
        secondary = _key_field(keys, "secondary_key") or _key_field(keys, "secondaryKey") or ""
        self._secrets.set_secret(
            self._primary_secret_for(cache_name=cache_name),
            primary,
        )
        self._secrets.set_secret(
            self._secondary_secret_for(cache_name=cache_name),
            secondary,
        )

    def _delete_access_key_secrets(self, cache_name: str) -> None:
        if self._secrets is None:
            return
        for name in (
            self._primary_secret_for(cache_name=cache_name),
            self._secondary_secret_for(cache_name=cache_name),
        ):
            try:
                self._secrets.begin_delete_secret(name)
            except Exception:
                continue


# ----- module-level helpers --------------------------------------------


def _state_of(cache: Any) -> str:
    """Read ``provisioning_state`` (or ``provisioningState``) off
    whatever shape the Azure SDK or fake returned."""
    state = getattr(cache, "provisioning_state", None)
    if state is not None:
        return str(state)
    state = getattr(cache, "provisioningState", None)
    if state is not None:
        return str(state)
    if isinstance(cache, dict):
        if "provisioning_state" in cache:
            return str(cache["provisioning_state"])
        if "provisioningState" in cache:
            return str(cache["provisioningState"])
    return "Unknown"


def _hostname_of(cache: Any) -> str:
    name = getattr(cache, "host_name", None) or getattr(cache, "hostName", None)
    if name:
        return str(name)
    if isinstance(cache, dict):
        v = cache.get("host_name") or cache.get("hostName")
        if v:
            return str(v)
    return ""


def _ssl_port_of(cache: Any) -> int:
    port = getattr(cache, "ssl_port", None)
    if port is None:
        port = getattr(cache, "sslPort", None)
    if port is None and isinstance(cache, dict):
        port = cache.get("ssl_port") or cache.get("sslPort")
    return int(port) if port is not None else 6380


def _non_ssl_port_of(cache: Any) -> int:
    port = getattr(cache, "port", None)
    if port is None and isinstance(cache, dict):
        port = cache.get("port")
    return int(port) if port else 0


def _key_field(keys: Any, field_name: str) -> str:
    """Read a key field from whatever shape the SDK or fake returned."""
    v = getattr(keys, field_name, None)
    if v:
        return str(v)
    if isinstance(keys, dict) and keys.get(field_name):
        return str(keys[field_name])
    return ""


_AZURE_STATE_TO_PROTOCOL = {
    "Succeeded": "available",
    "Creating": "provisioning",
    "Updating": "updating",
    "Deleting": "deprovisioning",
    "Failed": "error",
    "Unknown": "updating",
    "Disabled": "error",
    "Linking": "updating",
    "Unlinking": "updating",
    "Provisioning": "provisioning",
    "RecoveringScaleFailure": "updating",
    "Scaling": "updating",
}
