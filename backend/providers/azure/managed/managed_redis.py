"""Azure Managed Redis driver for the portable ``redis`` kind.

The current Azure service is represented by the Redis Enterprise ARM API,
despite the product name being Azure Managed Redis.  Each Astrolift managed
service owns one cluster and one database.  Credentials and the complete
``rediss://`` connection URI are stored in Key Vault; no access key is returned
through the provider protocol or emitted into telemetry.

Private Endpoint creation and a portable backup/restore artifact contract are
deliberately fail-closed.  The ARM export API accepts a directory SAS URI but
does not return the names of the blobs it created, while import requires blob
SAS URIs.  Astrolift therefore cannot claim a restorable snapshot without a
storage inventory/control-plane contract that records those artifacts.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from _sdk import UnsupportedOperationError
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
from azure.managed.tags import arm_tags_for as tags_for

KIND = "redis"
DEFAULT_DATABASE_NAME = "default"
DEFAULT_PORT = 10000

# Current Azure Managed Redis SKUs from Microsoft.Cache/redisEnterprise API
# 2025-07-01.  Legacy Enterprise/EnterpriseFlash shapes remain accepted by the
# API but are intentionally omitted from a new-product driver.
MANAGED_REDIS_SKUS = (
    "Balanced_B0",
    "Balanced_B1",
    "Balanced_B3",
    "Balanced_B5",
    "Balanced_B10",
    "Balanced_B20",
    "Balanced_B50",
    "Balanced_B100",
    "Balanced_B150",
    "Balanced_B250",
    "Balanced_B350",
    "Balanced_B500",
    "Balanced_B700",
    "Balanced_B1000",
    "MemoryOptimized_M10",
    "MemoryOptimized_M20",
    "MemoryOptimized_M50",
    "MemoryOptimized_M100",
    "MemoryOptimized_M150",
    "MemoryOptimized_M250",
    "MemoryOptimized_M350",
    "MemoryOptimized_M500",
    "MemoryOptimized_M700",
    "MemoryOptimized_M1000",
    "MemoryOptimized_M1500",
    "MemoryOptimized_M2000",
    "ComputeOptimized_X3",
    "ComputeOptimized_X5",
    "ComputeOptimized_X10",
    "ComputeOptimized_X20",
    "ComputeOptimized_X50",
    "ComputeOptimized_X100",
    "ComputeOptimized_X150",
    "ComputeOptimized_X250",
    "ComputeOptimized_X350",
    "ComputeOptimized_X500",
    "ComputeOptimized_X700",
    "FlashOptimized_A250",
    "FlashOptimized_A500",
    "FlashOptimized_A700",
    "FlashOptimized_A1000",
    "FlashOptimized_A1500",
    "FlashOptimized_A2000",
    "FlashOptimized_A4500",
)

_SIZE_TO_SKU = {
    "small": "Balanced_B3",
    "medium": "Balanced_B10",
    "large": "Balanced_B50",
    "xlarge": "Balanced_B100",
}

_CLUSTERING_POLICIES = ("EnterpriseCluster", "OSSCluster", "NoCluster")
_EVICTION_POLICIES = (
    "AllKeysLFU",
    "AllKeysLRU",
    "AllKeysRandom",
    "VolatileLRU",
    "VolatileLFU",
    "VolatileTTL",
    "VolatileRandom",
    "NoEviction",
)

_CLUSTER_STATES = {
    "Succeeded": "available",
    "Creating": "provisioning",
    "Updating": "updating",
    "Deleting": "deprovisioning",
    "Failed": "error",
    "Canceled": "error",
}

_DATABASE_STATES = {
    "Running": "available",
    "Creating": "provisioning",
    "Updating": "updating",
    "Deleting": "deprovisioning",
    "Importing": "updating",
    "Exporting": "updating",
    "Failed": "error",
}


class AzureManagedRedisError(Exception):
    """Azure Managed Redis lifecycle failure with actionable context."""


@dataclass(frozen=True)
class AzureManagedRedisConfig:
    subscription_id: str
    resource_group: str
    location: str = "eastus"
    cluster_name_prefix: str = "astrolift-amr"
    database_name: str = DEFAULT_DATABASE_NAME
    default_sku: str = "Balanced_B3"
    high_availability_default: str = "Enabled"
    minimum_tls_version_default: str = "1.2"
    public_network_access_default: str = "Enabled"
    clustering_policy_default: str = "OSSCluster"
    eviction_policy_default: str = "AllKeysLRU"
    secret_name_prefix: str = "astrolift-amr"
    keyvault_url: str = ""
    mgmt_client: Any | None = None
    secret_client: Any | None = None

    def __post_init__(self) -> None:
        if self.default_sku not in MANAGED_REDIS_SKUS:
            raise ValueError(f"unsupported Azure Managed Redis SKU {self.default_sku!r}")
        if self.high_availability_default not in {"Enabled", "Disabled"}:
            raise ValueError("high_availability_default must be Enabled or Disabled")
        if self.minimum_tls_version_default != "1.2":
            raise ValueError("Azure Managed Redis requires minimum TLS 1.2")


class AzureManagedRedisDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureManagedRedisConfig) -> None:
        self._config = config
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.redisenterprise import RedisEnterpriseManagementClient

            self._mgmt = RedisEnterpriseManagementClient(
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

    @driver_op(
        cloud="azure",
        driver="managed_redis",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        validation = self._validate_provision(spec)
        if validation is not None:
            return validation
        cluster_name = self._cluster_name_for(spec)
        database_name = self._database_name_for(spec)
        handle = self._handle_for(cluster_name, database_name)
        cfg = spec.config or {}

        try:
            cluster = self._describe_cluster(cluster_name)
            if cluster is not None:
                self._assert_owned(cluster, spec, AzureOperation.PROVISION, cluster_name)
            if cluster is None:
                cluster = self._mgmt.redis_enterprise.begin_create(
                    resource_group_name=self._config.resource_group,
                    cluster_name=cluster_name,
                    parameters=self._cluster_parameters(spec),
                ).result()
            else:
                cluster = self._mgmt.redis_enterprise.begin_update(
                    resource_group_name=self._config.resource_group,
                    cluster_name=cluster_name,
                    parameters=self._cluster_update(spec),
                ).result()

            database = self._describe_database(cluster_name, database_name)
            if database is None:
                database = self._mgmt.databases.begin_create(
                    resource_group_name=self._config.resource_group,
                    cluster_name=cluster_name,
                    database_name=database_name,
                    parameters=self._database_parameters(cfg),
                ).result()
            else:
                current_policy = str(_field(database, "clustering_policy", default=""))
                desired_policy = str(
                    cfg.get("clustering_policy", self._config.clustering_policy_default),
                )
                if current_policy and current_policy != desired_policy:
                    raise AzureManagedRedisError(
                        "Azure Managed Redis clustering_policy cannot be reconciled in place "
                        f"({current_policy} -> {desired_policy}); reprovision the service",
                    )
                database = self._mgmt.databases.begin_update(
                    resource_group_name=self._config.resource_group,
                    cluster_name=cluster_name,
                    database_name=database_name,
                    parameters=self._database_update(cfg),
                ).result()
            self._reconcile_access_policies(cluster_name, database_name, cfg)
            self._store_binding_secrets(cluster_name, database_name, cluster, database)
        except AzureOwnershipError as exc:
            return ProvisionResult(False, handle, str(exc), [OWNERSHIP_ERROR_CODE])
        except Exception as exc:
            return ProvisionResult(
                False,
                handle,
                f"provision Azure Managed Redis: {_redacted(exc)}",
                [_redacted(exc)],
            )
        return ProvisionResult(
            True,
            handle,
            f"Azure Managed Redis {cluster_name}/{database_name} is ready",
            ready=True,
        )

    @driver_op(cloud="azure", driver="managed_redis")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        cluster_name, database_name = self._parse_handle(spec.handle)
        cluster = self._describe_cluster(cluster_name)
        if cluster is None:
            return UpdateResult(False, spec.handle, "Azure Managed Redis cluster does not exist", ["not_found"])
        try:
            self._assert_owned(cluster, spec, AzureOperation.UPDATE, cluster_name)
        except AzureOwnershipError as exc:
            return UpdateResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        if self._describe_database(cluster_name, database_name) is None:
            return UpdateResult(False, spec.handle, "Azure Managed Redis database does not exist", ["not_found"])
        cfg = spec.config or {}
        error = self._validate_runtime_controls(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_runtime_controls"])
        immutable = sorted(
            key
            for key in (
                "database_name",
                "zones",
                "client_protocol",
                "clustering_policy",
                "geo_replication",
                "access_keys_authentication",
            )
            if key in cfg
        )
        if immutable:
            return UpdateResult(
                False,
                spec.handle,
                f"Azure Managed Redis fields require reprovision: {', '.join(immutable)}",
                ["reprovision_required"],
            )
        try:
            cluster_update = self._cluster_update_from_values(spec.size, cfg)
            database_update = self._database_update(cfg, partial=True)
            if spec.size or any(
                key in cfg
                for key in (
                    "sku_name",
                    "capacity",
                    "high_availability",
                    "minimum_tls_version",
                    "public_network_access",
                    "customer_managed_key_url",
                    "customer_managed_identity_resource_id",
                )
            ):
                self._mgmt.redis_enterprise.begin_update(
                    resource_group_name=self._config.resource_group,
                    cluster_name=cluster_name,
                    parameters=cluster_update,
                ).result()
            if any(
                key in cfg
                for key in (
                    "eviction_policy",
                    "persistence",
                    "modules",
                    "defer_upgrade",
                )
            ):
                self._mgmt.databases.begin_update(
                    resource_group_name=self._config.resource_group,
                    cluster_name=cluster_name,
                    database_name=database_name,
                    parameters=database_update,
                ).result()
            if "access_policy_assignments" in cfg:
                self._reconcile_access_policies(cluster_name, database_name, cfg)
            cluster = self._describe_cluster(cluster_name)
            database = self._describe_database(cluster_name, database_name)
            self._store_binding_secrets(cluster_name, database_name, cluster, database)
        except Exception as exc:
            return UpdateResult(
                False,
                spec.handle,
                f"update Azure Managed Redis: {_redacted(exc)}",
                [_redacted(exc)],
            )
        return UpdateResult(True, spec.handle, f"Azure Managed Redis {cluster_name}/{database_name} updated")

    @driver_op(
        cloud="azure",
        driver="managed_redis",
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
        cluster_name, database_name = self._parse_handle(spec.handle)
        cluster = self._describe_cluster(cluster_name)
        if cluster is not None:
            try:
                self._assert_owned(cluster, spec, AzureOperation.DELETE, cluster_name)
            except AzureOwnershipError as exc:
                return DeprovisionResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        if cluster is not None and not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                (
                    "Azure Managed Redis export does not return an inventory of restorable blobs; "
                    "refusing non-destructive teardown. Export and verify a backup externally, then "
                    "retry with delete_data=True"
                ),
                ["data_preserving_teardown_unsupported"],
                retryable=False,
            )
        if cluster is not None:
            try:
                if self._describe_database(cluster_name, database_name) is not None:
                    self._mgmt.databases.begin_delete(
                        resource_group_name=self._config.resource_group,
                        cluster_name=cluster_name,
                        database_name=database_name,
                    ).result()
                self._mgmt.redis_enterprise.begin_delete(
                    resource_group_name=self._config.resource_group,
                    cluster_name=cluster_name,
                ).result()
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"delete Azure Managed Redis: {_redacted(exc)}",
                    [_redacted(exc)],
                )
        try:
            self._delete_binding_secrets(cluster_name, database_name)
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Azure Managed Redis is gone but credential cleanup failed: {_redacted(exc)}",
                [_redacted(exc)],
            )
        return DeprovisionResult(
            True,
            spec.handle,
            (
                f"Azure Managed Redis {cluster_name}/{database_name} deleted "
                f"(delete_data={delete_data}, force_destroy={force_destroy})"
            ),
        )

    @driver_op(cloud="azure", driver="managed_redis")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        cluster_name, database_name = self._parse_handle(handle.handle)
        cluster = self._describe_cluster(cluster_name)
        if cluster is None:
            return ServiceStatus(handle.handle, "deprovisioned", "Azure Managed Redis cluster not found")
        database = self._describe_database(cluster_name, database_name)
        if database is None:
            return ServiceStatus(
                handle.handle,
                "error",
                f"Azure Managed Redis cluster exists but database {database_name} is missing",
            )
        cluster_state = str(_field(cluster, "provisioning_state", default="Unknown"))
        database_state = str(
            _field(database, "resource_state", "provisioning_state", default="Unknown"),
        )
        cluster_protocol = _CLUSTER_STATES.get(cluster_state, "updating")
        database_protocol = _DATABASE_STATES.get(database_state, "updating")
        state = _worst_state(cluster_protocol, database_protocol)
        return ServiceStatus(
            handle.handle,
            state,
            f"Azure Managed Redis cluster={cluster_state}, database={database_state}",
        )

    @driver_op(cloud="azure", driver="managed_redis")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        cluster_name, database_name = self._parse_handle(handle.handle)
        cluster = self._describe_cluster(cluster_name)
        database = self._describe_database(cluster_name, database_name)
        if cluster is None or database is None:
            raise AzureManagedRedisError(
                f"binding requested for missing Azure Managed Redis {cluster_name}/{database_name}",
            )
        self._store_binding_secrets(cluster_name, database_name, cluster, database)
        host = str(_field(cluster, "host_name", default=""))
        if not host:
            raise AzureManagedRedisError(f"Azure Managed Redis cluster {cluster_name} has no hostname")
        port = str(_field(database, "port", default=DEFAULT_PORT))
        primary = self._primary_secret(cluster_name, database_name)
        secondary = self._secondary_secret(cluster_name, database_name)
        url = self._url_secret(cluster_name, database_name)
        return Binding(
            env_vars={
                "REDIS_HOST": ValueRef(literal=host),
                "REDIS_PORT": ValueRef(literal=port),
                "REDIS_TLS": ValueRef(literal="1"),
                "REDIS_AUTH_TOKEN": ValueRef(secret_ref=primary),
                "REDIS_SECONDARY_AUTH_TOKEN": ValueRef(secret_ref=secondary),
                "REDIS_URL": ValueRef(secret_ref=url),
            },
            iam_grants=[
                Grant(
                    self._database_resource_id(cluster_name, database_name),
                    ["Microsoft.Cache/redisEnterprise/databases/read"],
                ),
                Grant(primary, ["Microsoft.KeyVault/vaults/secrets/getSecret"]),
                Grant(secondary, ["Microsoft.KeyVault/vaults/secrets/getSecret"]),
                Grant(url, ["Microsoft.KeyVault/vaults/secrets/getSecret"]),
            ],
            notes=("TLS-only Azure Managed Redis binding. Access keys and rediss:// URI are Key Vault references."),
        )

    @driver_op(cloud="azure", driver="managed_redis")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise UnsupportedOperationError(
            "Azure Managed Redis export accepts a directory SAS URI but does not return the exported blob "
            "inventory required for a portable, verifiable snapshot handle",
        )

    @driver_op(cloud="azure", driver="managed_redis")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise UnsupportedOperationError(
            "Azure Managed Redis import requires exact blob SAS URIs; the portable snapshot contract does "
            "not yet store that provider artifact inventory",
        )

    @driver_op(cloud="azure", driver="managed_redis", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "database_name": {"type": "string", "pattern": r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"},
                "sku_name": {"type": "string", "enum": list(MANAGED_REDIS_SKUS)},
                "capacity": {"type": "integer", "minimum": 1},
                "high_availability": {"type": "string", "enum": ["Enabled", "Disabled"]},
                "minimum_tls_version": {"type": "string", "enum": ["1.2"]},
                "public_network_access": {
                    "type": "string",
                    "enum": ["Enabled", "Disabled"],
                    "description": (
                        "Disabled is represented but rejected until Astrolift provisions "
                        "and verifies the Private Endpoint."
                    ),
                },
                "zones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Availability zones selected when the cluster is created.",
                },
                "customer_managed_key_url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Versioned Key Vault key URL for encryption at rest.",
                },
                "customer_managed_identity_resource_id": {
                    "type": "string",
                    "description": "User-assigned identity resource ID authorized to unwrap the CMK.",
                },
                "access_keys_authentication": {
                    "type": "string",
                    "enum": ["Enabled", "Disabled"],
                    "description": (
                        "Disabled is represented but rejected until the portable binding "
                        "supports Entra access policies."
                    ),
                },
                "client_protocol": {"type": "string", "enum": ["Encrypted", "Plaintext"]},
                "clustering_policy": {
                    "type": "string",
                    "enum": list(_CLUSTERING_POLICIES),
                },
                "eviction_policy": {
                    "type": "string",
                    "enum": list(_EVICTION_POLICIES),
                },
                "persistence": {
                    "type": "object",
                    "properties": {
                        "aof_enabled": {"type": "boolean"},
                        "aof_frequency": {
                            "type": "string",
                            "enum": ["1s", "always"],
                            "description": "Azure still accepts always, but marks it deprecated.",
                        },
                        "rdb_enabled": {"type": "boolean"},
                        "rdb_frequency": {"type": "string", "enum": ["1h", "6h", "12h"]},
                    },
                },
                "modules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}, "args": {"type": "string"}},
                    },
                },
                "defer_upgrade": {"type": "string", "enum": ["Deferred", "NotDeferred"]},
                "geo_replication": {
                    "type": "object",
                    "required": ["group_nickname", "linked_database_ids"],
                    "properties": {
                        "group_nickname": {"type": "string"},
                        "linked_database_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                    },
                    "description": ("Active geo-replication group and exact linked database ARM resource IDs."),
                },
                "access_policy_assignments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "access_policy_name", "object_id"],
                        "properties": {
                            "name": {"type": "string"},
                            "access_policy_name": {"type": "string"},
                            "object_id": {"type": "string"},
                        },
                    },
                    "description": ("Additive Entra data-plane access assignments; external assignments are retained."),
                },
            },
        }

    @driver_op(cloud="azure", driver="managed_redis", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "REDIS_HOST": "Azure Managed Redis hostname",
                "REDIS_PORT": "Encrypted Redis port (normally 10000)",
                "REDIS_TLS": "Always 1",
                "REDIS_AUTH_TOKEN": "Key Vault reference to the primary access key",
                "REDIS_SECONDARY_AUTH_TOKEN": "Key Vault reference to the secondary access key",
                "REDIS_URL": "Key Vault reference to the complete rediss:// URI",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "size",
            "sku_name",
            "capacity",
            "high_availability",
            "minimum_tls_version",
            "public_network_access",
            "customer_managed_key_url",
            "customer_managed_identity_resource_id",
            "eviction_policy",
            "persistence",
            "modules",
            "defer_upgrade",
            "access_policy_assignments",
        ]

    def _validate_provision(self, spec: ProvisionSpec) -> ProvisionResult | None:
        if self._secrets is None:
            return ProvisionResult(False, "", "Azure Managed Redis requires a Key Vault", ["no_secret_backend"])
        error = self._validate_runtime_controls(spec.config or {})
        if error:
            return ProvisionResult(False, "", error, ["invalid_runtime_controls"])
        sku_name = str((spec.config or {}).get("sku_name") or _SIZE_TO_SKU.get(spec.size, self._config.default_sku))
        if sku_name not in MANAGED_REDIS_SKUS:
            return ProvisionResult(False, "", f"unsupported Azure Managed Redis SKU {sku_name!r}", ["invalid_sku"])
        return None

    def _validate_runtime_controls(self, cfg: dict[str, Any]) -> str:
        public_access = str(cfg.get("public_network_access", self._config.public_network_access_default))
        if public_access == "Disabled":
            return (
                "Azure Managed Redis public_network_access=Disabled requires a managed Private Endpoint; "
                "that driver is not installed yet"
            )
        if public_access != "Enabled":
            return "Azure Managed Redis public_network_access must be Enabled or Disabled"
        auth = str(cfg.get("access_keys_authentication", "Enabled"))
        if auth == "Disabled":
            return (
                "Azure Managed Redis access_keys_authentication=Disabled requires an Entra access-policy binding; "
                "that binding is not installed yet"
            )
        if auth != "Enabled":
            return "Azure Managed Redis access_keys_authentication must be Enabled or Disabled"
        if str(cfg.get("client_protocol", "Encrypted")) != "Encrypted":
            return "Azure Managed Redis portable bindings require client_protocol=Encrypted"
        if str(cfg.get("minimum_tls_version", self._config.minimum_tls_version_default)) != "1.2":
            return "Azure Managed Redis portable bindings require TLS minimum_tls_version=1.2"
        key_url = str(cfg.get("customer_managed_key_url") or "")
        identity_id = str(cfg.get("customer_managed_identity_resource_id") or "")
        if bool(key_url) != bool(identity_id):
            return (
                "Azure Managed Redis customer-managed encryption requires both "
                "customer_managed_key_url and customer_managed_identity_resource_id"
            )
        if key_url and not key_url.startswith("https://"):
            return "Azure Managed Redis customer_managed_key_url must be an HTTPS Key Vault key URL"
        if identity_id and not identity_id.startswith("/subscriptions/"):
            return "Azure Managed Redis customer_managed_identity_resource_id must be an ARM resource ID"
        high_availability = str(cfg.get("high_availability", self._config.high_availability_default))
        if high_availability not in {"Enabled", "Disabled"}:
            return "Azure Managed Redis high_availability must be Enabled or Disabled"
        clustering_policy = str(cfg.get("clustering_policy", self._config.clustering_policy_default))
        if clustering_policy not in _CLUSTERING_POLICIES:
            return f"unsupported Azure Managed Redis clustering_policy {clustering_policy!r}"
        eviction_policy = str(cfg.get("eviction_policy", self._config.eviction_policy_default))
        if eviction_policy not in _EVICTION_POLICIES:
            return f"unsupported Azure Managed Redis eviction_policy {eviction_policy!r}"
        if cfg.get("defer_upgrade") not in (None, "Deferred", "NotDeferred"):
            return "Azure Managed Redis defer_upgrade must be Deferred or NotDeferred"
        persistence = cfg.get("persistence") or {}
        if persistence.get("aof_frequency") not in (None, "1s", "always"):
            return "Azure Managed Redis aof_frequency must be 1s or always"
        if persistence.get("rdb_frequency") not in (None, "1h", "6h", "12h"):
            return "Azure Managed Redis rdb_frequency must be 1h, 6h, or 12h"
        for module in cfg.get("modules", []):
            if not str(module.get("name") or ""):
                return "Azure Managed Redis module entries require a name"
        if cfg.get("geo_replication"):
            geo = cfg["geo_replication"]
            if not str(geo.get("group_nickname") or "") or not geo.get("linked_database_ids"):
                return "Azure Managed Redis geo_replication requires group_nickname and linked_database_ids"
            if any(not str(resource_id).startswith("/subscriptions/") for resource_id in geo["linked_database_ids"]):
                return "Azure Managed Redis linked_database_ids must be ARM resource IDs"
        for assignment in cfg.get("access_policy_assignments", []):
            if any(not str(assignment.get(key) or "") for key in ("name", "access_policy_name", "object_id")):
                return "Azure Managed Redis access_policy_assignments require name, access_policy_name, and object_id"
        return ""

    def _cluster_parameters(self, spec: ProvisionSpec) -> Any:
        from azure.mgmt.redisenterprise.models import Cluster

        cfg = spec.config or {}
        identity, encryption = self._identity_and_encryption(cfg)
        return Cluster(
            location=self._config.location,
            sku=self._sku(spec.size, cfg),
            tags=tags_for(spec),
            zones=[str(zone) for zone in cfg.get("zones", [])] or None,
            high_availability=str(
                cfg.get("high_availability", self._config.high_availability_default),
            ),
            minimum_tls_version="1.2",
            public_network_access="Enabled",
            identity=identity,
            encryption=encryption,
        )

    def _cluster_update(self, spec: ProvisionSpec) -> Any:
        return self._cluster_update_from_values(spec.size, spec.config or {}, tags=tags_for(spec))

    def _cluster_update_from_values(
        self,
        size: str | None,
        cfg: dict[str, Any],
        *,
        tags: dict[str, str] | None = None,
    ) -> Any:
        from azure.mgmt.redisenterprise.models import ClusterUpdate

        values: dict[str, Any] = {}
        if size or any(key in cfg for key in ("sku_name", "capacity")):
            values["sku"] = self._sku(size or "small", cfg)
        if tags is not None:
            values["tags"] = tags
            # Provision reconciliation applies all declared/default cluster
            # controls. A partial UpdateSpec must leave omitted controls alone.
            values["high_availability"] = str(
                cfg.get("high_availability", self._config.high_availability_default),
            )
            values["minimum_tls_version"] = "1.2"
            values["public_network_access"] = "Enabled"
        else:
            if "high_availability" in cfg:
                values["high_availability"] = str(cfg["high_availability"])
            if "minimum_tls_version" in cfg:
                values["minimum_tls_version"] = "1.2"
            if "public_network_access" in cfg:
                values["public_network_access"] = "Enabled"
        if any(key in cfg for key in ("customer_managed_key_url", "customer_managed_identity_resource_id")):
            identity, encryption = self._identity_and_encryption(cfg)
            values["identity"] = identity
            values["encryption"] = encryption
        return ClusterUpdate(**values)

    def _sku(self, size: str, cfg: dict[str, Any]) -> Any:
        from azure.mgmt.redisenterprise.models import Sku

        name = str(cfg.get("sku_name") or _SIZE_TO_SKU.get(size, self._config.default_sku))
        if name not in MANAGED_REDIS_SKUS:
            raise AzureManagedRedisError(f"unsupported Azure Managed Redis SKU {name!r}")
        capacity = cfg.get("capacity")
        return Sku(name=name, capacity=int(capacity) if capacity is not None else None)

    def _database_parameters(self, cfg: dict[str, Any]) -> Any:
        from azure.mgmt.redisenterprise.models import Database

        values = self._database_values(cfg)
        return Database(**values)

    def _database_update(self, cfg: dict[str, Any], *, partial: bool = False) -> Any:
        from azure.mgmt.redisenterprise.models import DatabaseUpdate

        values = self._database_values(cfg)
        if partial:
            mutable = {"eviction_policy", "persistence", "modules", "defer_upgrade"}
            values = {key: value for key, value in values.items() if key in cfg and key in mutable}
        return DatabaseUpdate(**values)

    def _database_values(self, cfg: dict[str, Any]) -> dict[str, Any]:
        from azure.mgmt.redisenterprise.models import (
            DatabasePropertiesGeoReplication,
            LinkedDatabase,
            Module,
            Persistence,
        )

        persistence_cfg = cfg.get("persistence") or {}
        persistence = None
        if persistence_cfg:
            persistence = Persistence(
                aof_enabled=bool(persistence_cfg.get("aof_enabled", False)),
                aof_frequency=persistence_cfg.get("aof_frequency"),
                rdb_enabled=bool(persistence_cfg.get("rdb_enabled", False)),
                rdb_frequency=persistence_cfg.get("rdb_frequency"),
            )
        modules = None
        if "modules" in cfg:
            modules = [
                Module(name=str(module["name"]), args=module.get("args")) for module in (cfg.get("modules") or [])
            ]
        geo_replication = None
        if cfg.get("geo_replication"):
            geo_cfg = cfg["geo_replication"]
            geo_replication = DatabasePropertiesGeoReplication(
                group_nickname=str(geo_cfg["group_nickname"]),
                linked_databases=[
                    LinkedDatabase(id=str(resource_id)) for resource_id in geo_cfg.get("linked_database_ids", [])
                ],
            )
        return {
            "client_protocol": "Encrypted",
            "port": DEFAULT_PORT,
            "clustering_policy": str(
                cfg.get("clustering_policy", self._config.clustering_policy_default),
            ),
            "eviction_policy": str(cfg.get("eviction_policy", self._config.eviction_policy_default)),
            "persistence": persistence,
            "modules": modules,
            "geo_replication": geo_replication,
            "defer_upgrade": cfg.get("defer_upgrade"),
            "access_keys_authentication": "Enabled",
        }

    def _identity_and_encryption(self, cfg: dict[str, Any]) -> tuple[Any | None, Any | None]:
        key_url = str(cfg.get("customer_managed_key_url") or "")
        identity_id = str(cfg.get("customer_managed_identity_resource_id") or "")
        if not key_url and not identity_id:
            return None, None
        from azure.mgmt.redisenterprise.models import (
            ClusterPropertiesEncryption,
            ClusterPropertiesEncryptionCustomerManagedKeyEncryption,
            ClusterPropertiesEncryptionCustomerManagedKeyEncryptionKeyIdentity,
            ManagedServiceIdentity,
            UserAssignedIdentity,
        )

        identity = ManagedServiceIdentity(
            type="UserAssigned",
            user_assigned_identities={identity_id: UserAssignedIdentity()},
        )
        encryption = ClusterPropertiesEncryption(
            customer_managed_key_encryption=ClusterPropertiesEncryptionCustomerManagedKeyEncryption(
                key_encryption_key_identity=(
                    ClusterPropertiesEncryptionCustomerManagedKeyEncryptionKeyIdentity(
                        user_assigned_identity_resource_id=identity_id,
                        identity_type="userAssignedIdentity",
                    )
                ),
                key_encryption_key_url=key_url,
            ),
        )
        return identity, encryption

    def _reconcile_access_policies(
        self,
        cluster_name: str,
        database_name: str,
        cfg: dict[str, Any],
    ) -> None:
        from azure.mgmt.redisenterprise.models import (
            AccessPolicyAssignment,
            AccessPolicyAssignmentPropertiesUser,
        )

        for assignment in cfg.get("access_policy_assignments", []):
            self._mgmt.access_policy_assignment.begin_create_update(
                resource_group_name=self._config.resource_group,
                cluster_name=cluster_name,
                database_name=database_name,
                access_policy_assignment_name=_safe_name(str(assignment["name"]), 60),
                parameters=AccessPolicyAssignment(
                    access_policy_name=str(assignment["access_policy_name"]),
                    user=AccessPolicyAssignmentPropertiesUser(
                        object_id=str(assignment["object_id"]),
                    ),
                ),
            ).result()

    @staticmethod
    def _assert_owned(cluster: Any, source: object, operation: AzureOperation, cluster_name: str) -> None:
        verify_azure_ownership(
            arm_tags_of(cluster),
            owner_of(source),
            operation=operation,
            resource=f"Azure Managed Redis cluster {cluster_name}",
        )

    def _describe_cluster(self, cluster_name: str) -> Any | None:
        try:
            return self._mgmt.redis_enterprise.get(
                resource_group_name=self._config.resource_group,
                cluster_name=cluster_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _describe_database(self, cluster_name: str, database_name: str) -> Any | None:
        try:
            return self._mgmt.databases.get(
                resource_group_name=self._config.resource_group,
                cluster_name=cluster_name,
                database_name=database_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _store_binding_secrets(
        self,
        cluster_name: str,
        database_name: str,
        cluster: Any,
        database: Any,
    ) -> None:
        if self._secrets is None:
            raise AzureManagedRedisError("Azure Managed Redis requires a Key Vault")
        keys = self._mgmt.databases.list_keys(
            resource_group_name=self._config.resource_group,
            cluster_name=cluster_name,
            database_name=database_name,
        )
        primary = str(_field(keys, "primary_key", default=""))
        secondary = str(_field(keys, "secondary_key", default=""))
        if not primary or not secondary:
            raise AzureManagedRedisError("Azure Managed Redis returned empty access keys")
        host = str(_field(cluster, "host_name", default=""))
        if not host:
            raise AzureManagedRedisError("Azure Managed Redis returned an empty hostname")
        port = int(_field(database, "port", default=DEFAULT_PORT))
        self._secrets.set_secret(self._primary_secret(cluster_name, database_name), primary)
        self._secrets.set_secret(self._secondary_secret(cluster_name, database_name), secondary)
        self._secrets.set_secret(
            self._url_secret(cluster_name, database_name),
            f"rediss://:{quote(primary, safe='')}@{host}:{port}",
        )

    def _delete_binding_secrets(self, cluster_name: str, database_name: str) -> None:
        if self._secrets is None:
            raise AzureManagedRedisError("Azure Managed Redis credential cleanup requires a Key Vault")
        for name in (
            self._primary_secret(cluster_name, database_name),
            self._secondary_secret(cluster_name, database_name),
            self._url_secret(cluster_name, database_name),
        ):
            try:
                self._secrets.begin_delete_secret(name)
            except Exception as exc:
                if not _not_found(exc):
                    raise AzureManagedRedisError(f"delete Key Vault secret {name!r}: {exc}") from exc

    def _cluster_name_for(self, spec: ProvisionSpec) -> str:
        base = "-".join(
            part
            for part in (
                self._config.cluster_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "redis",
            )
            if part
        )
        entropy = spec.managed_service_id or spec.app_id or spec.environment_id or base
        return _unique_name(base, 60, entropy=entropy)

    def _database_name_for(self, spec: ProvisionSpec) -> str:
        raw = str((spec.config or {}).get("database_name") or self._config.database_name)
        return _safe_name(raw, 60)

    def _handle_for(self, cluster_name: str, database_name: str) -> str:
        return f"{KIND}/{cluster_name}/{database_name}"

    def _parse_handle(self, handle: str) -> tuple[str, str]:
        parts = handle.split("/")
        if len(parts) != 3 or parts[0] != KIND or not parts[1] or not parts[2]:
            raise AzureManagedRedisError(f"invalid Azure Managed Redis handle {handle!r}")
        return parts[1], parts[2]

    def _database_resource_id(self, cluster_name: str, database_name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.Cache/redisEnterprise/{cluster_name}/databases/{database_name}"
        )

    def _primary_secret(self, cluster_name: str, database_name: str) -> str:
        return _keyvault_secret_name(
            f"{self._config.secret_name_prefix}-{cluster_name}-{database_name}",
            suffix="primary",
        )

    def _secondary_secret(self, cluster_name: str, database_name: str) -> str:
        return _keyvault_secret_name(
            f"{self._config.secret_name_prefix}-{cluster_name}-{database_name}",
            suffix="secondary",
        )

    def _url_secret(self, cluster_name: str, database_name: str) -> str:
        return _keyvault_secret_name(
            f"{self._config.secret_name_prefix}-{cluster_name}-{database_name}",
            suffix="url",
        )


def _field(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return default


def _not_found(exc: Exception) -> bool:
    return type(exc).__name__ == "ResourceNotFoundError" or "ResourceNotFound" in str(exc) or "NotFound" in str(exc)


def _safe_name(value: str, max_length: int) -> str:
    clean = "".join(char if char.isalnum() or char == "-" else "-" for char in value)
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip("-")
    if not clean:
        raise AzureManagedRedisError("Azure Managed Redis name cannot be empty")
    return clean[:max_length].rstrip("-")


def _unique_name(value: str, max_length: int, *, entropy: str) -> str:
    clean = _safe_name(value.lower(), max_length)
    digest = hashlib.sha256(entropy.encode()).hexdigest()[:10]
    prefix = clean[: max_length - len(digest) - 1].rstrip("-")
    return f"{prefix}-{digest}"


def _keyvault_secret_name(value: str, *, suffix: str) -> str:
    clean = _safe_name(f"{value}-{suffix}".lower(), 1024)
    if len(clean) <= 127:
        return clean
    digest = hashlib.sha256(clean.encode()).hexdigest()[:10]
    return f"{clean[:116].rstrip('-')}-{digest}"


def _redacted(exc: Exception) -> str:
    """Avoid leaking SAS query strings returned in provider exceptions."""

    text = str(exc)
    if "?" not in text:
        return text
    head, _, tail = text.partition("?")
    boundary = min((index for index in (tail.find(" "), tail.find("'"), tail.find('"')) if index >= 0), default=-1)
    remainder = tail[boundary:] if boundary >= 0 else ""
    return f"{head}?[REDACTED]{remainder}"


def _worst_state(*states: str) -> str:
    priority = {
        "error": 6,
        "deprovisioning": 5,
        "updating": 4,
        "provisioning": 3,
        "deprovisioned": 2,
        "available": 1,
    }
    return max(states, key=lambda state: priority.get(state, 4))
