"""Explicit Azure Cosmos DB API drivers.

The legacy ``kv_store/cosmos`` driver remains available for compatibility.
This module gives each Cosmos API its correct portable kind and binding:
NoSQL and MongoDB are document stores, Gremlin is a graph database,
Cassandra is wide-column, and Table is key/value.
"""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.azure_ownership import (
    OWNERSHIP_ERROR_CODE,
    AzureOperation,
    AzureOwnershipError,
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


@dataclass(frozen=True)
class _Profile:
    kind: str
    account_kind: str
    capability: str
    operation_group: str
    get_method: str
    create_method: str
    update_throughput_method: str
    resource_name_parameter: str
    create_parameter: str
    model_prefix: str
    endpoint_suffix: str
    port: int


PROFILES: dict[str, _Profile] = {
    "cosmos_nosql": _Profile(
        "document_db",
        "GlobalDocumentDB",
        "",
        "sql_resources",
        "get_sql_database",
        "begin_create_update_sql_database",
        "begin_update_sql_database_throughput",
        "database_name",
        "create_update_sql_database_parameters",
        "SqlDatabase",
        "documents.azure.com",
        443,
    ),
    "cosmos_mongodb": _Profile(
        "document_db",
        "MongoDB",
        "EnableMongo",
        "mongo_db_resources",
        "get_mongo_db_database",
        "begin_create_update_mongo_db_database",
        "begin_update_mongo_db_database_throughput",
        "database_name",
        "create_update_mongo_db_database_parameters",
        "MongoDBDatabase",
        "mongo.cosmos.azure.com",
        10255,
    ),
    "cosmos_gremlin": _Profile(
        "graph_db",
        "GlobalDocumentDB",
        "EnableGremlin",
        "gremlin_resources",
        "get_gremlin_database",
        "begin_create_update_gremlin_database",
        "begin_update_gremlin_database_throughput",
        "database_name",
        "create_update_gremlin_database_parameters",
        "GremlinDatabase",
        "gremlin.cosmos.azure.com",
        443,
    ),
    "cosmos_cassandra": _Profile(
        "wide_column",
        "GlobalDocumentDB",
        "EnableCassandra",
        "cassandra_resources",
        "get_cassandra_keyspace",
        "begin_create_update_cassandra_keyspace",
        "begin_update_cassandra_keyspace_throughput",
        "keyspace_name",
        "create_update_cassandra_keyspace_parameters",
        "CassandraKeyspace",
        "cassandra.cosmos.azure.com",
        10350,
    ),
    "cosmos_table": _Profile(
        "kv_store",
        "GlobalDocumentDB",
        "EnableTable",
        "table_resources",
        "get_table",
        "begin_create_update_table",
        "begin_update_table_throughput",
        "table_name",
        "create_update_table_parameters",
        "Table",
        "table.cosmos.azure.com",
        443,
    ),
}

_SIZE_TO_THROUGHPUT = {"small": 400, "medium": 1000, "large": 4000, "xlarge": 10000}
_CONSISTENCY_LEVELS = ("Eventual", "Session", "BoundedStaleness", "Strong", "ConsistentPrefix")
_ACCOUNT_STATES = {
    "Succeeded": "available",
    "Creating": "provisioning",
    "Updating": "updating",
    "Deleting": "deprovisioning",
    "Failed": "error",
    "Canceled": "error",
}


class AzureCosmosApiError(Exception):
    """Explicit Cosmos API lifecycle failure."""


@dataclass(frozen=True)
class AzureCosmosApiConfig:
    subscription_id: str
    resource_group: str
    variant: str
    location: str = "eastus"
    account_name_prefix: str = "astrolift-cosmos"
    database_name_default: str = "astrolift"
    backup_policy_default: str = "Continuous"
    continuous_backup_tier_default: str = "Continuous30Days"
    public_network_access_default: str = "Enabled"
    consistency_level_default: str = "Session"
    keyvault_url: str = ""
    secret_name_prefix: str = "astrolift-cosmos-api"
    mgmt_client: Any | None = None
    locks_client: Any | None = None
    secret_client: Any | None = None

    def __post_init__(self) -> None:
        if self.variant not in PROFILES:
            raise ValueError(f"unsupported Cosmos API variant {self.variant!r}")
        if self.backup_policy_default not in {"Continuous", "Periodic"}:
            raise ValueError("Cosmos backup policy must be Continuous or Periodic")
        if self.continuous_backup_tier_default not in {"Continuous7Days", "Continuous30Days"}:
            raise ValueError("Cosmos continuous backup tier must be Continuous7Days or Continuous30Days")
        if self.public_network_access_default not in {"Enabled", "Disabled", "SecuredByPerimeter"}:
            raise ValueError("unsupported Cosmos public network access default")
        if self.consistency_level_default not in _CONSISTENCY_LEVELS:
            raise ValueError(f"unsupported Cosmos consistency level {self.consistency_level_default!r}")


class AzureCosmosApiDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureCosmosApiConfig) -> None:
        self._config = config
        self._profile = PROFILES[config.variant]
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.cosmosdb import CosmosDBManagementClient

            self._mgmt = CosmosDBManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        if config.locks_client is not None:
            self._locks = config.locks_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.resource.locks import ManagementLockClient

            self._locks = ManagementLockClient(
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

    @driver_op(cloud="azure", driver="cosmos_api", audit=True, sensitive_kind="managed_service_provision")
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        validation = self._validate(spec.config or {})
        if validation:
            return ProvisionResult(False, "", validation, ["invalid_runtime_controls"])
        if self._secrets is None:
            return ProvisionResult(False, "", "Cosmos API variants require a Key Vault", ["no_secret_backend"])
        account_name = self._account_name_for(spec)
        resource_name = self._resource_name_for(spec)
        handle = self._handle_for(account_name, resource_name)
        cfg = spec.config or {}
        try:
            account = self._describe_account(account_name)
            if account is None:
                account = self._mgmt.database_accounts.begin_create_or_update(
                    resource_group_name=self._config.resource_group,
                    account_name=account_name,
                    create_update_parameters=self._account_create_parameters(spec),
                ).result()
            else:
                self._assert_owned(account, spec, AzureOperation.PROVISION, account_name)
                self._assert_create_only_controls(account, cfg)
                account = self._mgmt.database_accounts.begin_update(
                    resource_group_name=self._config.resource_group,
                    account_name=account_name,
                    update_parameters=self._account_update_parameters(cfg, tags=tags_for(spec)),
                ).result()
            if self._describe_resource(account_name, resource_name) is None:
                self._create_resource(account_name, resource_name, spec.size, cfg)
            elif any(key in cfg for key in ("throughput", "autoscale_max_throughput")):
                self._update_throughput(account_name, resource_name, spec.size, cfg)
            self._store_connection_secrets(account_name)
        except AzureOwnershipError as exc:
            return ProvisionResult(False, handle, str(exc), [OWNERSHIP_ERROR_CODE])
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Cosmos API resource: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Cosmos {self._config.variant} {account_name}/{resource_name} is ready",
            ready=True,
        )

    @driver_op(cloud="azure", driver="cosmos_api")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        account_name, resource_name = self._parse_handle(spec.handle)
        cfg = spec.config or {}
        validation = self._validate(cfg)
        if validation:
            return UpdateResult(False, spec.handle, validation, ["invalid_runtime_controls"])
        if self._secrets is None:
            return UpdateResult(
                False,
                spec.handle,
                "Cosmos API variants require a Key Vault",
                ["no_secret_backend"],
            )
        immutable = sorted(
            key
            for key in (
                "resource_name",
                "database_name",
                "keyspace_name",
                "table_name",
                "enable_free_tier",
                "mongo_server_version",
                "extra_capabilities",
            )
            if key in cfg
        )
        if immutable:
            return UpdateResult(
                False,
                spec.handle,
                f"Cosmos fields require reprovision: {', '.join(immutable)}",
                ["reprovision_required"],
            )
        account = self._describe_account(account_name)
        if account is None or self._describe_resource(account_name, resource_name) is None:
            return UpdateResult(False, spec.handle, "Cosmos API resource does not exist", ["not_found"])
        try:
            self._assert_owned(account, spec, AzureOperation.UPDATE, account_name)
        except AzureOwnershipError as exc:
            return UpdateResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        try:
            account_fields = {
                "backup_policy_type",
                "continuous_backup_tier",
                "periodic_backup_interval_minutes",
                "periodic_backup_retention_hours",
                "backup_storage_redundancy",
                "public_network_access",
                "disable_local_auth",
                "consistency_level",
                "max_staleness_prefix",
                "max_interval_in_seconds",
                "enable_automatic_failover",
                "enable_multiple_write_locations",
                "enable_analytical_storage",
                "network_acl_bypass",
                "network_acl_bypass_resource_ids",
                "enable_partition_merge",
                "enable_burst_capacity",
                "enable_priority_based_execution",
                "default_priority_level",
                "enable_per_region_per_partition_autoscale",
                "total_throughput_limit",
                "customer_managed_key_uri",
                "customer_managed_identity_resource_id",
                "regions",
                "virtual_network_rule_ids",
                "ignore_missing_vnet_service_endpoint",
                "ip_rules",
            }
            if account_fields.intersection(cfg):
                self._mgmt.database_accounts.begin_update(
                    resource_group_name=self._config.resource_group,
                    account_name=account_name,
                    update_parameters=self._account_update_parameters(cfg),
                ).result()
            if spec.size or any(key in cfg for key in ("throughput", "autoscale_max_throughput")):
                self._update_throughput(account_name, resource_name, spec.size, cfg)
            self._store_connection_secrets(account_name)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Cosmos API resource: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Cosmos {self._config.variant} updated")

    @driver_op(
        cloud="azure",
        driver="cosmos_api",
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
        account_name, resource_name = self._parse_handle(spec.handle)
        account = self._describe_account(account_name)
        if account is None:
            try:
                self._delete_connection_secrets(account_name)
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Cosmos account is gone but credential cleanup failed: {exc}",
                    [str(exc)],
                )
            return DeprovisionResult(True, spec.handle, f"Cosmos account {account_name} already gone")
        try:
            self._assert_owned(account, spec, AzureOperation.DELETE, account_name)
        except AzureOwnershipError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        try:
            locks = self._list_locks(account_name)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"list Cosmos resource locks: {exc}", [str(exc)])
        if locks and not force_destroy:
            names = ", ".join(str(_field(lock, "name", default="?")) for lock in locks)
            return DeprovisionResult(
                False,
                spec.handle,
                f"Cosmos account has resource locks ({names}); pass force_destroy=True",
                ["resource_lock_present"],
            )
        if locks:
            try:
                for lock in locks:
                    self._delete_lock(account_name, str(_field(lock, "name", default="")))
            except Exception as exc:
                return DeprovisionResult(False, spec.handle, f"delete Cosmos resource lock: {exc}", [str(exc)])
        retained = ""
        if not delete_data:
            try:
                retained = self.snapshot(
                    ServiceHandle(
                        spec.handle,
                        binding_id=spec.binding_id,
                        managed_service_id=spec.managed_service_id,
                    ),
                ).snapshot_id
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Cosmos data-preserving teardown failed: {exc}",
                    [str(exc)],
                    retryable=False,
                )
        try:
            self._mgmt.database_accounts.begin_delete(
                resource_group_name=self._config.resource_group,
                account_name=account_name,
            ).result()
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Cosmos account: {exc}", [str(exc)])
        try:
            self._delete_connection_secrets(account_name)
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Cosmos account deleted but credential cleanup failed: {exc}",
                [str(exc)],
            )
        suffix = f", retained_pitr={retained}" if retained else ""
        return DeprovisionResult(
            True,
            spec.handle,
            f"Cosmos {account_name}/{resource_name} deleted (force_destroy={force_destroy}{suffix})",
        )

    @driver_op(cloud="azure", driver="cosmos_api")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        account_name, resource_name = self._parse_handle(handle.handle)
        account = self._describe_account(account_name)
        if account is None:
            return ServiceStatus(handle.handle, "deprovisioned", "Cosmos account not found")
        resource = self._describe_resource(account_name, resource_name)
        if resource is None:
            return ServiceStatus(
                handle.handle,
                "error",
                f"Cosmos account exists but {self._profile.kind} resource {resource_name} is missing",
            )
        state = str(_field(account, "provisioning_state", default="Unknown"))
        return ServiceStatus(
            handle.handle,
            _ACCOUNT_STATES.get(state, "updating"),
            f"Azure Cosmos reports {state}",
        )

    @driver_op(cloud="azure", driver="cosmos_api")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        account_name, resource_name = self._parse_handle(handle.handle)
        account = self._describe_account(account_name)
        if account is None or self._describe_resource(account_name, resource_name) is None:
            raise AzureCosmosApiError(f"binding requested for missing Cosmos resource {account_name}/{resource_name}")
        self._store_connection_secrets(account_name)
        endpoint = self._endpoint(account_name)
        primary = self._primary_secret(account_name)
        readonly = self._readonly_secret(account_name)
        env_vars = self._binding_envs(account_name, endpoint, resource_name, primary, readonly)
        return Binding(
            env_vars=env_vars,
            iam_grants=[
                Grant(
                    self._account_resource_id(account_name),
                    ["Microsoft.DocumentDB/databaseAccounts/read"],
                ),
                Grant(primary, ["Microsoft.KeyVault/vaults/secrets/getSecret"]),
                Grant(readonly, ["Microsoft.KeyVault/vaults/secrets/getSecret"]),
            ],
            notes=(f"Cosmos {self._config.variant} TLS binding; connection material is referenced from Key Vault."),
        )

    @driver_op(cloud="azure", driver="cosmos_api")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        account_name, resource_name = self._parse_handle(handle.handle)
        account = self._describe_account(account_name)
        if account is None:
            raise AzureCosmosApiError(f"snapshot requested for missing Cosmos account {account_name}")
        self._assert_owned(account, handle, AzureOperation.SNAPSHOT, account_name)
        try:
            self._ensure_continuous_backup(account_name)
            account = self._describe_account(account_name)
        except Exception as exc:
            raise AzureCosmosApiError(f"enable continuous backup: {exc}") from exc
        instance_id = str(_field(_field(account, "properties", default=account), "instance_id", default=""))
        if not instance_id:
            instance_id = str(_field(account, "instance_id", default=""))
        if not instance_id:
            raise AzureCosmosApiError("Cosmos account did not return the PITR instance_id")
        now = datetime.now(UTC)
        snapshot_id = "|".join((instance_id, self._config.variant, resource_name))
        return SnapshotHandle(handle.handle, snapshot_id, now.isoformat())

    @driver_op(cloud="azure", driver="cosmos_api")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        parts = snapshot.snapshot_id.split("|")
        if len(parts) != 3:
            return ProvisionResult(False, "", "invalid Cosmos PITR snapshot id", ["invalid_snapshot"])
        instance_id, variant, source_resource = parts
        if variant != self._config.variant:
            return ProvisionResult(False, "", "Cosmos restore variant does not match snapshot", ["variant_mismatch"])
        validation = self._validate(target.config or {})
        if validation:
            return ProvisionResult(False, "", validation, ["invalid_runtime_controls"])
        if self._secrets is None:
            return ProvisionResult(
                False,
                "",
                "Cosmos API variants require a Key Vault",
                ["no_secret_backend"],
            )
        target_resource = self._resource_name_for(target)
        if target_resource != source_resource:
            return ProvisionResult(
                False,
                "",
                "Cosmos full-account PITR requires the target database/keyspace/table name to match the source",
                ["resource_name_mismatch"],
            )
        account_name = self._account_name_for(target)
        handle = self._handle_for(account_name, source_resource)
        try:
            existing = self._describe_account(account_name)
            if existing is not None:
                self._assert_owned(existing, target, AzureOperation.RESTORE, account_name)
                if self._describe_resource(account_name, source_resource) is None:
                    raise AzureCosmosApiError(
                        "restore target account already exists without the expected resource",
                    )
                self._store_connection_secrets(account_name)
                return ProvisionResult(
                    True,
                    handle,
                    f"Cosmos PITR restore {account_name} is already ready",
                    ready=True,
                )
            self._mgmt.database_accounts.begin_create_or_update(
                resource_group_name=self._config.resource_group,
                account_name=account_name,
                create_update_parameters=self._restore_parameters(instance_id, snapshot.created_at, target),
            ).result()
            if self._describe_resource(account_name, source_resource) is None:
                raise AzureCosmosApiError(
                    f"restored account is missing expected resource {source_resource}",
                )
            self._store_connection_secrets(account_name)
        except AzureOwnershipError as exc:
            return ProvisionResult(False, handle, str(exc), [OWNERSHIP_ERROR_CODE])
        except Exception as exc:
            return ProvisionResult(False, handle, f"restore Cosmos PITR: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"Cosmos PITR restore {account_name} is ready", ready=True)

    @driver_op(cloud="azure", driver="cosmos_api", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        resource_label = {
            "cosmos_cassandra": "keyspace",
            "cosmos_table": "table",
        }.get(self._config.variant, "database")
        properties: dict[str, Any] = {
            "resource_name": {
                "type": "string",
                "description": f"Cosmos {resource_label} name.",
            },
            "throughput": {"type": "integer", "minimum": 400},
            "autoscale_max_throughput": {"type": "integer", "minimum": 1000},
            "backup_policy_type": {"type": "string", "enum": ["Continuous", "Periodic"]},
            "continuous_backup_tier": {
                "type": "string",
                "enum": ["Continuous7Days", "Continuous30Days"],
            },
            "periodic_backup_interval_minutes": {"type": "integer", "minimum": 60, "maximum": 1440},
            "periodic_backup_retention_hours": {"type": "integer", "minimum": 8, "maximum": 720},
            "backup_storage_redundancy": {"type": "string", "enum": ["Geo", "Local", "Zone"]},
            "regions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["location_name"],
                    "additionalProperties": False,
                    "properties": {
                        "location_name": {"type": "string"},
                        "failover_priority": {"type": "integer", "minimum": 0},
                        "zone_redundant": {"type": "boolean"},
                    },
                },
            },
            "enable_automatic_failover": {"type": "boolean"},
            "enable_multiple_write_locations": {"type": "boolean"},
            "consistency_level": {"type": "string", "enum": list(_CONSISTENCY_LEVELS)},
            "max_staleness_prefix": {"type": "integer", "minimum": 1},
            "max_interval_in_seconds": {"type": "integer", "minimum": 5, "maximum": 86400},
            "public_network_access": {
                "type": "string",
                "enum": ["Enabled", "Disabled", "SecuredByPerimeter"],
                "description": "Disabled and SecuredByPerimeter fail closed until their network attachment is managed.",
            },
            "virtual_network_rule_ids": {"type": "array", "items": {"type": "string"}},
            "ignore_missing_vnet_service_endpoint": {"type": "boolean"},
            "ip_rules": {"type": "array", "items": {"type": "string"}},
            "network_acl_bypass": {"type": "string", "enum": ["None", "AzureServices"]},
            "network_acl_bypass_resource_ids": {"type": "array", "items": {"type": "string"}},
            "disable_local_auth": {
                "type": "boolean",
                "description": "True fails closed until the workload Entra data-plane role binding is verified.",
            },
            "customer_managed_key_uri": {"type": "string", "format": "uri"},
            "customer_managed_identity_resource_id": {"type": "string"},
            "enable_free_tier": {"type": "boolean"},
            "enable_analytical_storage": {"type": "boolean"},
            "enable_partition_merge": {"type": "boolean"},
            "enable_burst_capacity": {"type": "boolean"},
            "enable_priority_based_execution": {"type": "boolean"},
            "default_priority_level": {"type": "string", "enum": ["High", "Low"]},
            "enable_per_region_per_partition_autoscale": {"type": "boolean"},
            "total_throughput_limit": {"type": "integer", "minimum": -1},
            "extra_capabilities": {"type": "array", "items": {"type": "string"}},
        }
        if self._config.variant == "cosmos_mongodb":
            properties["mongo_server_version"] = {
                "type": "string",
                "enum": ["3.2", "3.6", "4.0", "4.2", "5.0", "6.0", "7.0"],
            }
        if self._config.variant == "cosmos_cassandra":
            properties["keyspace_name"] = {"type": "string"}
        elif self._config.variant == "cosmos_table":
            properties["table_name"] = {"type": "string"}
        else:
            properties["database_name"] = {"type": "string"}
        return {"type": "object", "additionalProperties": False, "properties": properties}

    @driver_op(cloud="azure", driver="cosmos_api", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(env_vars={key: value for key, value in self._binding_descriptions().items()})

    def editable_fields(self) -> list[str]:
        return [
            "size",
            "throughput",
            "autoscale_max_throughput",
            "backup_policy_type",
            "continuous_backup_tier",
            "periodic_backup_interval_minutes",
            "periodic_backup_retention_hours",
            "backup_storage_redundancy",
            "public_network_access",
            "disable_local_auth",
            "consistency_level",
            "max_staleness_prefix",
            "max_interval_in_seconds",
            "enable_automatic_failover",
            "enable_multiple_write_locations",
            "enable_analytical_storage",
            "network_acl_bypass",
            "network_acl_bypass_resource_ids",
            "enable_partition_merge",
            "enable_burst_capacity",
            "enable_priority_based_execution",
            "default_priority_level",
            "enable_per_region_per_partition_autoscale",
            "total_throughput_limit",
            "customer_managed_key_uri",
            "customer_managed_identity_resource_id",
            "regions",
            "virtual_network_rule_ids",
            "ignore_missing_vnet_service_endpoint",
            "ip_rules",
        ]

    def _validate(self, cfg: dict[str, Any]) -> str:
        unknown = sorted(set(cfg) - set(self.config_schema()["properties"]))
        if unknown:
            return f"unsupported Cosmos config fields: {', '.join(unknown)}"
        aliases = [name for name in ("resource_name", "database_name", "keyspace_name", "table_name") if name in cfg]
        if len(aliases) > 1:
            return f"Cosmos resource name aliases are mutually exclusive: {', '.join(aliases)}"
        if cfg.get("throughput") and cfg.get("autoscale_max_throughput"):
            return "Cosmos throughput and autoscale_max_throughput are mutually exclusive"
        try:
            if "throughput" in cfg:
                throughput = int(cfg["throughput"])
                if throughput < 400 or throughput % 100:
                    return "Cosmos throughput must be at least 400 RU/s in increments of 100"
            if "autoscale_max_throughput" in cfg:
                autoscale = int(cfg["autoscale_max_throughput"])
                if autoscale < 1000 or autoscale % 1000:
                    return "Cosmos autoscale_max_throughput must be at least 1000 RU/s in increments of 1000"
        except (TypeError, ValueError):
            return "Cosmos throughput controls must be integers"
        public_access = str(cfg.get("public_network_access", self._config.public_network_access_default))
        if public_access != "Enabled":
            return (
                f"Cosmos public_network_access={public_access} requires a managed Private Endpoint "
                "or Network Security Perimeter attachment"
            )
        if cfg.get("disable_local_auth"):
            return "Cosmos disable_local_auth requires a verified Entra workload data-plane role binding"
        consistency = str(cfg.get("consistency_level", self._config.consistency_level_default))
        if consistency not in _CONSISTENCY_LEVELS:
            return f"unsupported Cosmos consistency level {consistency!r}"
        if consistency == "BoundedStaleness" and not (
            cfg.get("max_staleness_prefix") and cfg.get("max_interval_in_seconds")
        ):
            return "BoundedStaleness requires max_staleness_prefix and max_interval_in_seconds"
        if cfg.get("enable_multiple_write_locations") and consistency == "Strong":
            return "Cosmos multi-region writes do not support Strong consistency"
        if consistency == "BoundedStaleness":
            try:
                prefix = int(cfg["max_staleness_prefix"])
                interval = int(cfg["max_interval_in_seconds"])
            except (TypeError, ValueError):
                return "Cosmos bounded staleness controls must be integers"
            if prefix < 1 or not 5 <= interval <= 86400:
                return "Cosmos bounded staleness controls are outside supported limits"
        backup = str(cfg.get("backup_policy_type", self._config.backup_policy_default))
        if backup not in {"Continuous", "Periodic"}:
            return "Cosmos backup_policy_type must be Continuous or Periodic"
        continuous_tier = str(
            cfg.get("continuous_backup_tier", self._config.continuous_backup_tier_default),
        )
        if continuous_tier not in {"Continuous7Days", "Continuous30Days"}:
            return "unsupported Cosmos continuous backup tier"
        if backup == "Periodic":
            try:
                interval = int(cfg.get("periodic_backup_interval_minutes", 240))
                retention = int(cfg.get("periodic_backup_retention_hours", 8))
            except (TypeError, ValueError):
                return "Cosmos periodic backup interval and retention must be integers"
            if not 60 <= interval <= 1440:
                return "Cosmos periodic backup interval must be between 60 and 1440 minutes"
            if not 8 <= retention <= 720:
                return "Cosmos periodic backup retention must be between 8 and 720 hours"
        redundancy = str(cfg.get("backup_storage_redundancy", "Geo"))
        if redundancy not in {"Geo", "Local", "Zone"}:
            return "unsupported Cosmos backup storage redundancy"
        key_uri = str(cfg.get("customer_managed_key_uri") or "")
        identity_id = str(cfg.get("customer_managed_identity_resource_id") or "")
        if bool(key_uri) != bool(identity_id):
            return "Cosmos customer-managed encryption requires both key URI and identity resource ID"
        if key_uri and not key_uri.startswith("https://"):
            return "Cosmos customer_managed_key_uri must be HTTPS"
        if identity_id and not identity_id.startswith("/subscriptions/"):
            return "Cosmos customer_managed_identity_resource_id must be an ARM resource ID"
        for field in ("virtual_network_rule_ids", "network_acl_bypass_resource_ids"):
            for resource_id in cfg.get(field, []) or []:
                if not isinstance(resource_id, str) or not resource_id.startswith("/subscriptions/"):
                    return f"Cosmos {field} entries must be ARM resource IDs"
        if str(cfg.get("network_acl_bypass", "None")) not in {"None", "AzureServices"}:
            return "unsupported Cosmos network ACL bypass mode"
        for rule in cfg.get("ip_rules", []) or []:
            try:
                ipaddress.ip_network(str(rule), strict=False)
            except ValueError:
                return f"Cosmos ip_rules entry is not an IP address or CIDR: {rule!r}"
        if "total_throughput_limit" in cfg:
            try:
                total_throughput_limit = int(cfg["total_throughput_limit"])
            except (TypeError, ValueError):
                return "Cosmos total_throughput_limit must be an integer"
            if total_throughput_limit < -1:
                return "Cosmos total_throughput_limit must be -1 or greater"
        if "default_priority_level" in cfg and cfg["default_priority_level"] not in {"High", "Low"}:
            return "unsupported Cosmos default priority level"
        if "mongo_server_version" in cfg and cfg["mongo_server_version"] not in {
            "3.2",
            "3.6",
            "4.0",
            "4.2",
            "5.0",
            "6.0",
            "7.0",
        }:
            return "unsupported Cosmos MongoDB server version"
        if "extra_capabilities" in cfg:
            capabilities = cfg["extra_capabilities"]
            if not isinstance(capabilities, list) or any(
                not isinstance(value, str) or not value for value in capabilities
            ):
                return "Cosmos extra_capabilities must be a list of non-empty strings"
            api_capabilities = {
                "EnableMongo",
                "EnableGremlin",
                "EnableCassandra",
                "EnableTable",
            }
            if api_capabilities.intersection(capabilities):
                return "Cosmos API selector capabilities are controlled by the selected variant"
        regions = cfg.get(
            "regions",
            [{"location_name": self._config.location, "failover_priority": 0}],
        )
        if not isinstance(regions, list) or not regions:
            return "Cosmos regions must be a non-empty list"
        if any(not isinstance(region, dict) or not str(region.get("location_name") or "") for region in regions):
            return "every Cosmos region requires a location_name"
        try:
            priorities = [int(region.get("failover_priority", index)) for index, region in enumerate(regions)]
        except (TypeError, ValueError):
            return "Cosmos region failover priorities must be integers"
        if sorted(priorities) != list(range(len(priorities))):
            return "Cosmos region failover priorities must be unique and contiguous from zero"
        locations = [str(region["location_name"]).lower() for region in regions]
        if len(locations) != len(set(locations)):
            return "Cosmos region location names must be unique"
        return ""

    def _account_create_parameters(self, spec: ProvisionSpec) -> Any:
        from azure.mgmt.cosmosdb.models import DatabaseAccountCreateUpdateParameters

        identity, default_identity = self._identity(spec.config or {})
        return DatabaseAccountCreateUpdateParameters(
            location=self._config.location,
            tags=tags_for(spec),
            kind=self._profile.account_kind,
            identity=identity,
            properties=self._account_properties(spec.config or {}, default_identity=default_identity),
        )

    def _account_update_parameters(self, cfg: dict[str, Any], *, tags: dict[str, str] | None = None) -> Any:
        from azure.mgmt.cosmosdb.models import DatabaseAccountUpdateParameters

        identity, default_identity = self._identity(cfg)
        properties = self._account_properties(cfg, partial=True, default_identity=default_identity)
        return DatabaseAccountUpdateParameters(identity=identity, tags=tags, properties=properties)

    def _account_properties(self, cfg: dict[str, Any], *, partial: bool = False, default_identity: str = "") -> Any:
        from azure.mgmt.cosmosdb.models import (
            ApiProperties,
            Capability,
            Capacity,
            ConsistencyPolicy,
            DatabaseAccountCreateUpdateProperties,
            DatabaseAccountUpdateProperties,
            IpAddressOrRange,
            Location,
            VirtualNetworkRule,
        )

        values: dict[str, Any] = {}
        if not partial:
            values.update(
                database_account_offer_type="Standard",
                capabilities=[
                    Capability(name=name)
                    for name in ([self._profile.capability] if self._profile.capability else [])
                    + [str(value) for value in cfg.get("extra_capabilities", [])]
                ],
                public_network_access="Enabled",
                minimal_tls_version="Tls12",
                enable_free_tier=bool(cfg.get("enable_free_tier", False)),
            )
            if self._config.variant == "cosmos_mongodb":
                values["api_properties"] = ApiProperties(
                    server_version=str(cfg.get("mongo_server_version", "7.0")),
                )
        if not partial or "regions" in cfg:
            regions = cfg.get("regions") or [{"location_name": self._config.location, "failover_priority": 0}]
            values["locations"] = [
                Location(
                    location_name=str(region["location_name"]),
                    failover_priority=int(region.get("failover_priority", index)),
                    is_zone_redundant=bool(region.get("zone_redundant", False)),
                )
                for index, region in enumerate(regions)
            ]
        if "virtual_network_rule_ids" in cfg:
            values["virtual_network_rules"] = [
                VirtualNetworkRule(
                    id=str(resource_id),
                    ignore_missing_v_net_service_endpoint=bool(
                        cfg.get("ignore_missing_vnet_service_endpoint", False),
                    ),
                )
                for resource_id in cfg.get("virtual_network_rule_ids", [])
            ]
            values["is_virtual_network_filter_enabled"] = bool(values["virtual_network_rules"])
        if "ip_rules" in cfg:
            values["ip_rules"] = [IpAddressOrRange(ip_address_or_range=str(value)) for value in cfg["ip_rules"]]
        mapping = {
            "public_network_access": "public_network_access",
            "disable_local_auth": "disable_local_auth",
            "enable_automatic_failover": "enable_automatic_failover",
            "enable_multiple_write_locations": "enable_multiple_write_locations",
            "enable_analytical_storage": "enable_analytical_storage",
            "network_acl_bypass": "network_acl_bypass",
            "network_acl_bypass_resource_ids": "network_acl_bypass_resource_ids",
            "enable_partition_merge": "enable_partition_merge",
            "enable_burst_capacity": "enable_burst_capacity",
            "enable_priority_based_execution": "enable_priority_based_execution",
            "default_priority_level": "default_priority_level",
            "enable_per_region_per_partition_autoscale": "enable_per_region_per_partition_autoscale",
        }
        for source, target in mapping.items():
            if source in cfg:
                values[target] = cfg[source]
        if (
            any(key in cfg for key in ("consistency_level", "max_staleness_prefix", "max_interval_in_seconds"))
            or not partial
        ):
            values["consistency_policy"] = ConsistencyPolicy(
                default_consistency_level=str(
                    cfg.get("consistency_level", self._config.consistency_level_default),
                ),
                max_staleness_prefix=cfg.get("max_staleness_prefix"),
                max_interval_in_seconds=cfg.get("max_interval_in_seconds"),
            )
        if any(key.startswith("backup_") or key == "continuous_backup_tier" for key in cfg) or not partial:
            values["backup_policy"] = self._backup_policy(cfg)
        if "total_throughput_limit" in cfg:
            values["capacity"] = Capacity(total_throughput_limit=int(cfg["total_throughput_limit"]))
        if cfg.get("customer_managed_key_uri"):
            values["key_vault_key_uri"] = str(cfg["customer_managed_key_uri"])
            values["default_identity"] = default_identity
        model = DatabaseAccountUpdateProperties if partial else DatabaseAccountCreateUpdateProperties
        return model(**values)

    def _backup_policy(self, cfg: dict[str, Any]) -> Any:
        from azure.mgmt.cosmosdb.models import (
            ContinuousModeBackupPolicy,
            ContinuousModeProperties,
            PeriodicModeBackupPolicy,
            PeriodicModeProperties,
        )

        policy = str(cfg.get("backup_policy_type", self._config.backup_policy_default))
        if policy == "Continuous":
            return ContinuousModeBackupPolicy(
                continuous_mode_properties=ContinuousModeProperties(
                    tier=str(
                        cfg.get("continuous_backup_tier", self._config.continuous_backup_tier_default),
                    ),
                ),
            )
        return PeriodicModeBackupPolicy(
            periodic_mode_properties=PeriodicModeProperties(
                backup_interval_in_minutes=int(cfg.get("periodic_backup_interval_minutes", 240)),
                backup_retention_interval_in_hours=int(cfg.get("periodic_backup_retention_hours", 8)),
                backup_storage_redundancy=str(cfg.get("backup_storage_redundancy", "Geo")),
            ),
        )

    def _identity(self, cfg: dict[str, Any]) -> tuple[Any | None, str]:
        identity_id = str(cfg.get("customer_managed_identity_resource_id") or "")
        if not identity_id:
            return None, ""
        from azure.mgmt.cosmosdb.models import (
            ManagedServiceIdentity,
            ManagedServiceIdentityUserAssignedIdentity,
        )

        return (
            ManagedServiceIdentity(
                type="UserAssigned",
                user_assigned_identities={identity_id: ManagedServiceIdentityUserAssignedIdentity()},
            ),
            f"UserAssignedIdentity={identity_id}",
        )

    def _create_resource(self, account_name: str, resource_name: str, size: str, cfg: dict[str, Any]) -> None:
        from azure.mgmt.cosmosdb import models

        resource_cls = getattr(models, f"{self._profile.model_prefix}Resource")
        properties_cls = getattr(models, f"{self._profile.model_prefix}CreateUpdateProperties")
        parameters_cls = getattr(models, f"{self._profile.model_prefix}CreateUpdateParameters")
        options = self._create_update_options(size, cfg)
        parameters = parameters_cls(
            properties=properties_cls(
                resource=resource_cls(id=resource_name),
                options=options,
            ),
        )
        operation_group = getattr(self._mgmt, self._profile.operation_group)
        kwargs = {
            "resource_group_name": self._config.resource_group,
            "account_name": account_name,
            self._profile.resource_name_parameter: resource_name,
            self._profile.create_parameter: parameters,
        }
        getattr(operation_group, self._profile.create_method)(**kwargs).result()

    def _create_update_options(self, size: str, cfg: dict[str, Any]) -> Any:
        from azure.mgmt.cosmosdb.models import AutoscaleSettings, CreateUpdateOptions

        if cfg.get("autoscale_max_throughput"):
            return CreateUpdateOptions(
                autoscale_settings=AutoscaleSettings(
                    max_throughput=int(cfg["autoscale_max_throughput"]),
                ),
            )
        return CreateUpdateOptions(
            throughput=int(cfg.get("throughput", _SIZE_TO_THROUGHPUT.get(size, 400))),
        )

    def _update_throughput(
        self,
        account_name: str,
        resource_name: str,
        size: str | None,
        cfg: dict[str, Any],
    ) -> None:
        from azure.mgmt.cosmosdb.models import (
            AutoscaleSettingsResource,
            ThroughputSettingsResource,
            ThroughputSettingsUpdateParameters,
            ThroughputSettingsUpdateProperties,
        )

        if cfg.get("autoscale_max_throughput"):
            resource = ThroughputSettingsResource(
                autoscale_settings=AutoscaleSettingsResource(
                    max_throughput=int(cfg["autoscale_max_throughput"]),
                ),
            )
        else:
            resource = ThroughputSettingsResource(
                throughput=int(cfg.get("throughput", _SIZE_TO_THROUGHPUT.get(size or "small", 400))),
            )
        parameters = ThroughputSettingsUpdateParameters(
            properties=ThroughputSettingsUpdateProperties(resource=resource),
        )
        operation_group = getattr(self._mgmt, self._profile.operation_group)
        kwargs = {
            "resource_group_name": self._config.resource_group,
            "account_name": account_name,
            self._profile.resource_name_parameter: resource_name,
            "update_throughput_parameters": parameters,
        }
        getattr(operation_group, self._profile.update_throughput_method)(**kwargs).result()

    def _describe_account(self, account_name: str) -> Any | None:
        try:
            return self._mgmt.database_accounts.get(
                resource_group_name=self._config.resource_group,
                account_name=account_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _describe_resource(self, account_name: str, resource_name: str) -> Any | None:
        operation_group = getattr(self._mgmt, self._profile.operation_group)
        kwargs = {
            "resource_group_name": self._config.resource_group,
            "account_name": account_name,
            self._profile.resource_name_parameter: resource_name,
        }
        try:
            return getattr(operation_group, self._profile.get_method)(**kwargs)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _ensure_continuous_backup(self, account_name: str) -> None:
        from azure.mgmt.cosmosdb.models import DatabaseAccountUpdateParameters, DatabaseAccountUpdateProperties

        self._mgmt.database_accounts.begin_update(
            resource_group_name=self._config.resource_group,
            account_name=account_name,
            update_parameters=DatabaseAccountUpdateParameters(
                properties=DatabaseAccountUpdateProperties(
                    backup_policy=self._backup_policy(
                        {
                            "backup_policy_type": "Continuous",
                            "continuous_backup_tier": self._config.continuous_backup_tier_default,
                        },
                    ),
                ),
            ),
        ).result()

    def _restore_parameters(self, instance_id: str, created_at: str, target: ProvisionSpec) -> Any:
        from azure.mgmt.cosmosdb.models import (
            DatabaseAccountCreateUpdateParameters,
            DatabaseAccountCreateUpdateProperties,
            Location,
            RestoreParameters,
        )

        restore_source = (
            f"/subscriptions/{self._config.subscription_id}/providers/Microsoft.DocumentDB/"
            f"locations/{self._config.location}/restorableDatabaseAccounts/{instance_id}"
        )
        return DatabaseAccountCreateUpdateParameters(
            location=self._config.location,
            tags=tags_for(target),
            kind=self._profile.account_kind,
            properties=DatabaseAccountCreateUpdateProperties(  # type: ignore[call-overload]
                locations=[Location(location_name=self._config.location, failover_priority=0)],
                database_account_offer_type="Standard",
                create_mode="Restore",
                restore_parameters=RestoreParameters(
                    restore_mode="PointInTime",
                    restore_source=restore_source,
                    restore_timestamp_in_utc=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
                ),
            ),
        )

    def _store_connection_secrets(self, account_name: str) -> None:
        if self._secrets is None:
            raise AzureCosmosApiError("Cosmos credential storage requires a Key Vault")
        result = self._mgmt.database_accounts.list_connection_strings(
            resource_group_name=self._config.resource_group,
            account_name=account_name,
        )
        entries = _field(result, "connection_strings", default=[]) or []
        primary = ""
        readonly = ""
        for entry in entries:
            value = str(_field(entry, "connection_string", default=""))
            description = str(_field(entry, "description", default="")).lower()
            if not value:
                continue
            if "read-only" in description or "readonly" in description:
                readonly = readonly or value
            else:
                primary = primary or value
        if not primary:
            raise AzureCosmosApiError("Cosmos returned no read-write connection string")
        self._secrets.set_secret(self._primary_secret(account_name), primary)
        self._secrets.set_secret(self._readonly_secret(account_name), readonly or primary)

    def _delete_connection_secrets(self, account_name: str) -> None:
        if self._secrets is None:
            raise AzureCosmosApiError("Cosmos credential cleanup requires a Key Vault")
        for name in (self._primary_secret(account_name), self._readonly_secret(account_name)):
            try:
                poller = self._secrets.begin_delete_secret(name)
                if hasattr(poller, "result"):
                    poller.result()
            except Exception as exc:
                if not _not_found(exc):
                    raise AzureCosmosApiError(f"delete Key Vault secret {name!r}: {exc}") from exc

    def _list_locks(self, account_name: str) -> list[Any]:
        return list(
            self._locks.management_locks.list_at_resource_level(
                resource_group_name=self._config.resource_group,
                resource_provider_namespace="Microsoft.DocumentDB",
                parent_resource_path="",
                resource_type="databaseAccounts",
                resource_name=account_name,
            ),
        )

    def _delete_lock(self, account_name: str, lock_name: str) -> None:
        self._locks.management_locks.delete_at_resource_level(
            resource_group_name=self._config.resource_group,
            resource_provider_namespace="Microsoft.DocumentDB",
            parent_resource_path="",
            resource_type="databaseAccounts",
            resource_name=account_name,
            lock_name=lock_name,
        )

    @staticmethod
    def _assert_owned(account: Any, source: object, operation: AzureOperation, account_name: str) -> None:
        verify_azure_ownership(
            dict(_field(account, "tags", default={}) or {}),
            owner_of(source),
            operation=operation,
            resource=f"Cosmos account {account_name}",
        )

    def _assert_create_only_controls(self, account: Any, cfg: dict[str, Any]) -> None:
        properties = _field(account, "properties", default=None)

        def current(name: str, default: Any = None) -> Any:
            value = _field(account, name, default=None)
            if value is None and properties is not None:
                value = _field(properties, name, default=None)
            return default if value is None else value

        if "enable_free_tier" in cfg and bool(current("enable_free_tier", False)) != bool(
            cfg["enable_free_tier"],
        ):
            raise AzureCosmosApiError("Cosmos enable_free_tier differs from the existing account; reprovision required")
        if "mongo_server_version" in cfg:
            api_properties = current("api_properties")
            actual_version = str(_field(api_properties, "server_version", default=""))
            if actual_version != str(cfg["mongo_server_version"]):
                raise AzureCosmosApiError(
                    "Cosmos mongo_server_version differs from the existing account; reprovision required",
                )
        if "extra_capabilities" in cfg:
            actual_capabilities = {
                str(_field(capability, "name", default=""))
                for capability in (current("capabilities", []) or [])
                if _field(capability, "name", default="")
            }
            expected = {str(value) for value in cfg["extra_capabilities"]}
            if self._profile.capability:
                expected.add(self._profile.capability)
            if actual_capabilities != expected:
                raise AzureCosmosApiError(
                    "Cosmos extra_capabilities differ from the existing account; reprovision required",
                )

    def _account_name_for(self, spec: ProvisionSpec) -> str:
        base = "-".join(
            part
            for part in (
                self._config.account_name_prefix,
                self._config.variant.removeprefix("cosmos_"),
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "data",
            )
            if part
        )
        entropy = spec.managed_service_id or spec.app_id or spec.environment_id or base
        return _unique_name(base, 44, entropy=entropy)

    def _resource_name_for(self, spec: ProvisionSpec) -> str:
        cfg = spec.config or {}
        profile_alias = {
            "cosmos_cassandra": "keyspace_name",
            "cosmos_table": "table_name",
        }.get(self._config.variant, "database_name")
        value = str(cfg.get("resource_name") or cfg.get(profile_alias) or self._config.database_name_default)
        return _bounded_name(value, 255)

    def _handle_for(self, account_name: str, resource_name: str) -> str:
        return f"{self._profile.kind}/{account_name}/{resource_name}"

    def _parse_handle(self, handle: str) -> tuple[str, str]:
        parts = handle.split("/")
        if len(parts) != 3 or parts[0] != self._profile.kind or not parts[1] or not parts[2]:
            raise AzureCosmosApiError(f"invalid {self._config.variant} handle {handle!r}")
        return parts[1], parts[2]

    def _account_resource_id(self, account_name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.DocumentDB/databaseAccounts/{account_name}"
        )

    def _endpoint(self, account_name: str) -> str:
        if self._config.variant == "cosmos_gremlin":
            return f"wss://{account_name}.{self._profile.endpoint_suffix}:{self._profile.port}/"
        if self._config.variant == "cosmos_mongodb":
            return f"mongodb://{account_name}.{self._profile.endpoint_suffix}:{self._profile.port}/"
        if self._config.variant == "cosmos_cassandra":
            return f"{account_name}.{self._profile.endpoint_suffix}"
        return f"https://{account_name}.{self._profile.endpoint_suffix}:{self._profile.port}/"

    def _primary_secret(self, account_name: str) -> str:
        return _secret_name(f"{self._config.secret_name_prefix}-{account_name}-primary")

    def _readonly_secret(self, account_name: str) -> str:
        return _secret_name(f"{self._config.secret_name_prefix}-{account_name}-readonly")

    def _binding_envs(
        self,
        account_name: str,
        endpoint: str,
        resource_name: str,
        primary: str,
        readonly: str,
    ) -> dict[str, ValueRef]:
        common = {
            "COSMOS_ENDPOINT": ValueRef(literal=endpoint),
            "COSMOS_DATABASE_NAME": ValueRef(literal=resource_name),
            "COSMOS_CONNECTION_STRING": ValueRef(secret_ref=primary),
            "COSMOS_READONLY_CONNECTION_STRING": ValueRef(secret_ref=readonly),
        }
        if self._profile.kind == "document_db":
            common.update(
                DOCDB_URI=ValueRef(secret_ref=primary),
                DOCDB_DB=ValueRef(literal=resource_name),
                DOCDB_AUTH_MODE=ValueRef(literal="connection_string"),
                DOCDB_TLS=ValueRef(literal="true"),
                DOCDB_RESOURCE_ARN=ValueRef(literal=self._account_resource_id(account_name)),
            )
        elif self._profile.kind == "graph_db":
            common.update(
                GRAPH_DB_URL=ValueRef(secret_ref=primary),
                GRAPH_DB_ENDPOINT=ValueRef(literal=endpoint),
                GRAPH_DB_PORT=ValueRef(literal="443"),
                GRAPH_DB_PROTOCOL=ValueRef(literal="gremlin"),
                GRAPH_DB_TLS=ValueRef(literal="true"),
                GRAPH_DB_AUTH_MODE=ValueRef(literal="connection_string"),
                GRAPH_DB_REGION=ValueRef(literal=self._config.location),
                GRAPH_DB_RESOURCE_ARN=ValueRef(literal=self._account_resource_id(account_name)),
            )
        elif self._profile.kind == "wide_column":
            common.update(
                WIDE_COLUMN_ENDPOINT=ValueRef(literal=endpoint),
                WIDE_COLUMN_KEYSPACE=ValueRef(literal=resource_name),
                WIDE_COLUMN_REGION=ValueRef(literal=self._config.location),
                WIDE_COLUMN_PORT=ValueRef(literal="10350"),
                WIDE_COLUMN_AUTH_MODE=ValueRef(literal="connection_string"),
                WIDE_COLUMN_RESOURCE_ARN=ValueRef(literal=self._account_resource_id(account_name)),
            )
        else:
            common.update(
                KV_TABLE_NAME=ValueRef(literal=resource_name),
                KV_REGION=ValueRef(literal=self._config.location),
                KV_ENDPOINT_OVERRIDE=ValueRef(literal=endpoint),
            )
        return common

    def _binding_descriptions(self) -> dict[str, str]:
        common = {
            "COSMOS_ENDPOINT": "API-specific TLS endpoint",
            "COSMOS_DATABASE_NAME": "Database, keyspace, or table name",
            "COSMOS_CONNECTION_STRING": "Key Vault read-write connection-string reference",
            "COSMOS_READONLY_CONNECTION_STRING": "Key Vault read-only connection-string reference",
        }
        if self._profile.kind == "document_db":
            common.update(
                DOCDB_URI="Key Vault connection-string reference",
                DOCDB_DB="Database name",
                DOCDB_AUTH_MODE="Authentication mode",
                DOCDB_TLS="TLS requirement",
                DOCDB_RESOURCE_ARN="Azure resource ID",
            )
            return common
        if self._profile.kind == "graph_db":
            common.update(
                GRAPH_DB_URL="Key Vault Gremlin connection reference",
                GRAPH_DB_ENDPOINT="Gremlin endpoint",
                GRAPH_DB_PORT="Gremlin TLS port",
                GRAPH_DB_PROTOCOL="Graph protocol",
                GRAPH_DB_TLS="TLS requirement",
                GRAPH_DB_AUTH_MODE="Authentication mode",
                GRAPH_DB_REGION="Azure region",
                GRAPH_DB_RESOURCE_ARN="Azure resource ID",
            )
            return common
        if self._profile.kind == "wide_column":
            common.update(
                WIDE_COLUMN_ENDPOINT="Cosmos Cassandra endpoint",
                WIDE_COLUMN_KEYSPACE="Cassandra keyspace",
                WIDE_COLUMN_REGION="Azure region",
                WIDE_COLUMN_PORT="Cassandra TLS port",
                WIDE_COLUMN_AUTH_MODE="Authentication mode",
                WIDE_COLUMN_RESOURCE_ARN="Azure resource ID",
            )
            return common
        common.update(
            KV_TABLE_NAME="Cosmos Table name",
            KV_REGION="Azure region",
            KV_ENDPOINT_OVERRIDE="Cosmos Table endpoint",
        )
        return common


def _field(value: Any, name: str, *, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _not_found(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return status_code == 404 or type(exc).__name__ == "ResourceNotFoundError"


def _safe_name(value: str, max_length: int) -> str:
    clean = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip("-")
    if not clean:
        raise AzureCosmosApiError("Cosmos resource name cannot be empty")
    return clean[:max_length].rstrip("-")


def _unique_name(value: str, max_length: int, *, entropy: str) -> str:
    clean = _safe_name(value.lower(), max_length).replace("_", "-")
    digest = hashlib.sha256(entropy.encode()).hexdigest()[:10]
    prefix = clean[: max_length - len(digest) - 1].rstrip("-")
    return f"{prefix}-{digest}"


def _bounded_name(value: str, max_length: int) -> str:
    clean = _safe_name(value, max(len(value), max_length))
    if len(clean) <= max_length:
        return clean
    digest = hashlib.sha256(clean.encode()).hexdigest()[:10]
    return f"{clean[: max_length - len(digest) - 1].rstrip('-')}-{digest}"


def _secret_name(value: str) -> str:
    clean = _safe_name(value.lower(), 1024).replace("_", "-")
    if len(clean) <= 127:
        return clean
    digest = hashlib.sha256(clean.encode()).hexdigest()[:10]
    return f"{clean[:116].rstrip('-')}-{digest}"
