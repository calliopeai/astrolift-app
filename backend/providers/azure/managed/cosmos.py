"""Azure Cosmos DB managed-service driver (#371).

Implements ``ManagedServiceDriver`` for the canonical Azure managed
key-value store path. Defaults to the **Mongo API** because it is
the most cloud-portable surface Cosmos exposes (Mongo wire compat
means workloads can move between Cosmos / DocumentDB / MongoDB
Atlas without an SDK rewrite). Operators can override with
``spec.config.api_kind`` to ``Sql`` / ``Cassandra`` / ``Table`` /
``Gremlin`` when they explicitly want Cosmos-flavoured APIs.

Four-corner deprovision matrix:

  delete_data=False, force_destroy=False (default):
    enable continuous backup so the account is restorable for the
    retention window after delete; resource lock is respected.
    Refuses cleanly when a CanNotDelete / ReadOnly lock is present.

  delete_data=True, force_destroy=False:
    skip the continuous-backup enable; still respect locks.

  delete_data=False, force_destroy=True:
    enable continuous backup; remove blocking resource locks via
    ``management_locks.delete_at_resource_level``.

  delete_data=True, force_destroy=True:
    skip backup, remove locks, delete. The --atomic path.

Connection strings are persisted to Azure Key Vault. The driver
reads them via ``database_accounts.list_connection_strings`` after
provision and stores both primary read-write + primary read-only
at well-known secret names so workloads can rotate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
from azure.secrets_keyvault import key_vault_secret_ref

KIND = "kv_store"


class AzureCosmosError(Exception):
    """Distinct from the generic plugin error so the control plane's
    failure-handling logic can tell managed-service failures from
    infra-driver failures (managed-service partial state often needs
    targeted cleanup of half-created Key Vault secrets etc.)."""


# Size -> default throughput (RU/s). Cosmos charges per provisioned
# RU/s plus storage; the small/medium/large/xlarge ladder maps onto
# common operator-side starting points. Operators override via
# ``spec.config.throughput`` or switch to autoscale via
# ``spec.config.autoscale_max_throughput``.
_SIZE_TO_THROUGHPUT = {
    "small": 400,
    "medium": 1000,
    "large": 4000,
    "xlarge": 10000,
}


# Cosmos API kind defaults. ``MongoDB`` is the most portable surface;
# operators can override per-spec.
_DEFAULT_API_KIND = "MongoDB"
_VALID_API_KINDS = {
    "MongoDB",
    "GlobalDocumentDB",  # The SQL/Core API uses this kind under the hood
    "Cassandra",
    "Table",
    "Gremlin",
}


@dataclass(frozen=True)
class AzureCosmosConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    subscription_id: str
    resource_group: str
    location: str = "eastus"

    account_name_prefix: str = "astrolift"
    """Prefix on the Cosmos account name. Account names must be
    globally unique across all of Azure (they're DNS labels under
    documents.azure.com / mongo.cosmos.azure.com); the slug + env
    qualifiers keep collisions impossible across apps."""

    database_name_default: str = "astrolift"
    """Default database name created inside the account."""

    default_api_kind: str = _DEFAULT_API_KIND

    backup_policy_default: str = "Continuous"
    """``Continuous`` (default, restorable to any second within the
    retention window) or ``Periodic``. Continuous is what powers the
    delete_data=False retain path."""

    keyvault_url: str = ""
    """Key Vault URL where the driver stores connection strings.
    Empty string means no Key Vault is configured -- the driver
    returns an error on provision in that case."""

    secret_name_prefix: str = "astrolift-cosmos"

    mgmt_client: Any | None = None
    """Injected ``CosmosDBManagementClient`` for tests."""

    locks_client: Any | None = None
    """Injected ``ManagementLockClient`` for tests; production builds
    construct one via ``DefaultAzureCredential``."""

    secret_client: Any | None = None
    """Injected ``SecretClient`` for tests."""


class AzureCosmosDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureCosmosConfig) -> None:
        self._config = config
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
            try:
                from azure.identity import DefaultAzureCredential
                from azure.mgmt.resource.locks import ManagementLockClient

                self._locks = ManagementLockClient(
                    credential=DefaultAzureCredential(),
                    subscription_id=config.subscription_id,
                )
            except Exception:
                # Locks client is optional -- the driver falls back to
                # surfacing the underlying API error if a delete is
                # blocked and the locks client isn't available.
                self._locks = None
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
        driver="cosmos",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if self._secrets is None:
            return ProvisionResult(
                ok=False,
                handle="",
                message=("azure cosmos driver requires a Key Vault (set keyvault_url or inject secret_client)"),
                errors=["no_secret_backend"],
            )
        account_name = self._account_name_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(account_name)
        if existing is not None:
            try:
                self._assert_owned(existing, spec, AzureOperation.PROVISION, account_name)
            except AzureOwnershipError as exc:
                return ProvisionResult(ok=False, handle="", message=str(exc), errors=[OWNERSHIP_ERROR_CODE])
            return ProvisionResult(
                ok=True,
                handle=self._handle_for(account_name=account_name),
                message=(f"cosmos account {account_name} already exists (state={_state_of(existing)})"),
            )

        api_kind = cfg.get("api_kind") or self._config.default_api_kind
        if api_kind not in _VALID_API_KINDS:
            return ProvisionResult(
                ok=False,
                handle="",
                message=(f"unknown api_kind {api_kind!r}; expected one of {sorted(_VALID_API_KINDS)}"),
                errors=["invalid_api_kind"],
            )
        throughput = int(
            cfg.get(
                "throughput",
                _SIZE_TO_THROUGHPUT.get(spec.size, 400),
            ),
        )
        backup_policy_type = cfg.get(
            "backup_policy_type",
            self._config.backup_policy_default,
        )
        database_name = cfg.get("database_name") or self._config.database_name_default

        parameters: dict[str, Any] = {
            "location": self._config.location,
            "kind": api_kind,
            "properties": {
                "database_account_offer_type": "Standard",
                "locations": [
                    {
                        "location_name": self._config.location,
                        "failover_priority": 0,
                    },
                ],
                "backup_policy": {"type": backup_policy_type},
                "capabilities": _capabilities_for_api(api_kind),
                "public_network_access": cfg.get(
                    "public_network_access",
                    "Enabled",
                ),
            },
            "tags": tags_for(spec),
        }
        if cfg.get("autoscale_max_throughput"):
            parameters["properties"]["autoscale_settings"] = {
                "max_throughput": int(
                    cfg["autoscale_max_throughput"],
                ),
            }
        if cfg.get("disable_local_auth"):
            parameters["properties"]["disable_local_auth"] = True

        try:
            poller = self._mgmt.database_accounts.begin_create_or_update(
                resource_group_name=self._config.resource_group,
                account_name=account_name,
                create_update_parameters=parameters,
            )
            poller.result()
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"begin_create_or_update: {exc}",
                errors=[str(exc)],
            )

        # Create the database inside the account. Mongo / SQL APIs
        # use different REST surfaces; we pick the one matching the
        # api_kind. We stash throughput on the database; per-collection
        # provisioning is left to the workload.
        try:
            self._create_database(
                account_name=account_name,
                database_name=database_name,
                api_kind=api_kind,
                throughput=throughput,
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_database: {exc}",
                errors=[str(exc)],
            )

        try:
            conn = self._mgmt.database_accounts.list_connection_strings(
                resource_group_name=self._config.resource_group,
                account_name=account_name,
            )
            self._store_connection_strings(
                account_name=account_name,
                conn=conn,
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"list_connection_strings: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(account_name=account_name),
            message=(f"cosmos account {account_name} provisioning (api={api_kind}, db={database_name})"),
        )

    @driver_op(cloud="azure", driver="cosmos")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        account_name = self._account_name_from_handle(spec.handle)
        cfg = spec.config or {}

        existing = self._describe(account_name)
        if existing is None:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"cosmos account {account_name} does not exist",
                errors=["not_found"],
                retryable=False,
            )
        try:
            self._assert_owned(existing, spec, AzureOperation.UPDATE, account_name)
        except AzureOwnershipError as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=[OWNERSHIP_ERROR_CODE],
                retryable=False,
            )

        body: dict[str, Any] = {}
        properties: dict[str, Any] = {}
        if "public_network_access" in cfg:
            properties["public_network_access"] = cfg["public_network_access"]
        if cfg.get("backup_policy_type"):
            properties["backup_policy"] = {
                "type": cfg["backup_policy_type"],
            }
        if "disable_local_auth" in cfg:
            properties["disable_local_auth"] = bool(
                cfg["disable_local_auth"],
            )
        if properties:
            body["properties"] = properties

        if not body and not spec.size and "throughput" not in cfg:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        try:
            if body:
                poller = self._mgmt.database_accounts.begin_update(
                    resource_group_name=self._config.resource_group,
                    account_name=account_name,
                    update_parameters=body,
                )
                poller.result()
            # Throughput updates live on the database, not the account;
            # we only patch when the operator changed size or threw
            # an explicit throughput value.
            new_throughput: int | None = None
            if "throughput" in cfg:
                new_throughput = int(cfg["throughput"])
            elif spec.size:
                new_throughput = _SIZE_TO_THROUGHPUT.get(spec.size)
            if new_throughput is not None:
                self._update_database_throughput(
                    account_name=account_name,
                    database_name=(cfg.get("database_name") or self._config.database_name_default),
                    throughput=new_throughput,
                )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"begin_update: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"cosmos account {account_name} update queued",
        )

    @driver_op(
        cloud="azure",
        driver="cosmos",
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
        account_name = self._account_name_from_handle(spec.handle)

        existing = self._describe(account_name)
        if existing is None:
            self._delete_connection_secrets(account_name)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"cosmos account {account_name} already gone",
            )

        try:
            self._assert_owned(existing, spec, AzureOperation.DELETE, account_name)
        except AzureOwnershipError as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=[OWNERSHIP_ERROR_CODE],
                retryable=False,
            )

        # Resource-lock guard: enumerate any CanNotDelete / ReadOnly
        # locks on the account. force_destroy must remove them first.
        locks = self._list_locks(account_name=account_name)
        if locks and force_destroy:
            for lock in locks:
                try:
                    self._delete_lock(
                        account_name=account_name,
                        lock_name=_get(lock, "name", ""),
                    )
                except Exception as exc:
                    return DeprovisionResult(
                        ok=False,
                        handle=spec.handle,
                        message=f"failed to clear resource lock: {exc}",
                        errors=[str(exc)],
                    )
        elif locks:
            lock_names = ", ".join(str(_get(lock, "name", "?")) for lock in locks)
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"cosmos account {account_name} has resource "
                    f"locks ({lock_names}); pass force_destroy=True "
                    f"to bypass"
                ),
                errors=["resource_lock_present"],
            )

        # delete_data=False: ensure continuous backup is on so the
        # account remains restorable for the retention window after
        # delete. Cosmos doesn't have a "final snapshot" semantic the
        # way RDS / Bigtable do; continuous backup is the durability
        # story.
        retained = False
        if not delete_data:
            try:
                self._ensure_continuous_backup(account_name=account_name)
                retained = True
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=(
                        f"cosmos account {account_name} cannot be removed "
                        f"safely because continuous backup could not be "
                        f"enabled: {exc}"
                    ),
                    errors=["backup_required", str(exc)],
                    retryable=False,
                )

        try:
            poller = self._mgmt.database_accounts.begin_delete(
                resource_group_name=self._config.resource_group,
                account_name=account_name,
            )
            poller.result()
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"begin_delete: {exc}",
                errors=[str(exc)],
            )

        if delete_data:
            self._delete_connection_secrets(account_name)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"cosmos account {account_name} delete queued "
                f"(continuous_backup={'retained' if retained else 'skipped'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="azure", driver="cosmos")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        account_name = self._account_name_from_handle(handle.handle)
        existing = self._describe(account_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"cosmos account {account_name} not found",
            )
        azure_state = _state_of(existing)
        return ServiceStatus(
            handle=handle.handle,
            state=_AZURE_STATE_TO_PROTOCOL.get(
                str(azure_state),
                "updating",
            ),
            message=f"azure reports {azure_state}",
        )

    @driver_op(cloud="azure", driver="cosmos")
    def binding(self, handle: ServiceHandle) -> Binding:
        account_name = self._account_name_from_handle(handle.handle)
        existing = self._describe(account_name)
        if existing is None:
            raise AzureCosmosError(
                f"binding requested for missing account {account_name}",
            )
        endpoint = _endpoint_of(existing) or (f"https://{account_name}.documents.azure.com:443/")
        database_name = self._config.database_name_default
        primary_conn = self._primary_conn_secret_for(
            account_name=account_name,
        )
        readonly_conn = self._readonly_conn_secret_for(
            account_name=account_name,
        )

        return Binding(
            env_vars={
                "COSMOS_ENDPOINT": ValueRef(literal=endpoint),
                "COSMOS_DATABASE_NAME": ValueRef(literal=database_name),
                "COSMOS_CONNECTION_STRING": ValueRef(
                    secret_ref=key_vault_secret_ref(self._config.keyvault_url, primary_conn),
                ),
                "COSMOS_READONLY_CONNECTION_STRING": ValueRef(
                    secret_ref=key_vault_secret_ref(self._config.keyvault_url, readonly_conn),
                ),
            },
            iam_grants=[
                Grant(
                    resource=(
                        f"/subscriptions/{self._config.subscription_id}"
                        f"/resourceGroups/{self._config.resource_group}"
                        f"/providers/Microsoft.DocumentDB"
                        f"/databaseAccounts/{account_name}"
                    ),
                    actions=[
                        "Microsoft.DocumentDB/databaseAccounts/read",
                        ("Microsoft.DocumentDB/databaseAccounts/listConnectionStrings/action"),
                    ],
                ),
                Grant(
                    resource=primary_conn,
                    actions=[
                        "Microsoft.KeyVault/vaults/secrets/getSecret",
                    ],
                ),
            ],
            notes=(
                "COSMOS_CONNECTION_STRING is a Key Vault ref to the "
                "primary read-write Mongo-compatible connection string. "
                "Read-only is exposed separately for analytics workloads."
            ),
        )

    @driver_op(cloud="azure", driver="cosmos")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """Cosmos's restore story is continuous-backup point-in-time
        restore, not snapshot-per-call. We surface a SnapshotHandle
        keyed on the current UTC timestamp so callers can later
        restore_from_point_in_time(target=created_at)."""
        from datetime import UTC, datetime

        account_name = self._account_name_from_handle(handle.handle)
        existing = self._describe(account_name)
        if existing is None:
            raise AzureCosmosError(
                f"snapshot requested for missing account {account_name}",
            )
        self._assert_owned(existing, handle, AzureOperation.SNAPSHOT, account_name)
        # Ensure continuous backup is on so the timestamp is restorable.
        try:
            self._ensure_continuous_backup(account_name=account_name)
        except Exception as exc:
            raise AzureCosmosError(
                f"ensure_continuous_backup: {exc}",
            ) from exc
        now = datetime.now(UTC)
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{account_name}-pit-{now.strftime('%Y%m%dT%H%M%SZ')}",
            created_at=now.isoformat(),
        )

    @driver_op(cloud="azure", driver="cosmos")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        target_account = self._account_name_for(spec=target)
        source_account = self._account_name_from_handle(snapshot.handle)
        existing_target = self._describe(target_account)
        if existing_target is not None:
            try:
                self._assert_owned(existing_target, target, AzureOperation.RESTORE, target_account)
            except AzureOwnershipError as exc:
                return ProvisionResult(ok=False, handle="", message=str(exc), errors=[OWNERSHIP_ERROR_CODE])
        try:
            poller = self._mgmt.database_accounts.begin_create_or_update(
                resource_group_name=self._config.resource_group,
                account_name=target_account,
                create_update_parameters={
                    "location": self._config.location,
                    "kind": self._config.default_api_kind,
                    "properties": {
                        "create_mode": "Restore",
                        "restore_parameters": {
                            "restore_source": (
                                f"/subscriptions/"
                                f"{self._config.subscription_id}"
                                f"/providers/Microsoft.DocumentDB"
                                f"/locations/{self._config.location}"
                                f"/restorableDatabaseAccounts/"
                                f"{source_account}"
                            ),
                            "restore_timestamp_in_utc": (snapshot.created_at),
                        },
                        "database_account_offer_type": "Standard",
                        "locations": [
                            {
                                "location_name": (self._config.location),
                                "failover_priority": 0,
                            },
                        ],
                    },
                    "tags": tags_for(target),
                },
            )
            poller.result()
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"restore begin_create_or_update: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=self._handle_for(account_name=target_account),
            message=(f"cosmos restore from {snapshot.snapshot_id} queued"),
        )

    @driver_op(cloud="azure", driver="cosmos", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "api_kind": {
                    "type": "string",
                    "enum": sorted(_VALID_API_KINDS),
                },
                "throughput": {"type": "integer", "minimum": 400},
                "autoscale_max_throughput": {
                    "type": "integer",
                    "minimum": 1000,
                },
                "backup_policy_type": {
                    "type": "string",
                    "enum": ["Continuous", "Periodic"],
                },
                "database_name": {"type": "string"},
                "public_network_access": {
                    "type": "string",
                    "enum": ["Enabled", "Disabled"],
                },
                "disable_local_auth": {"type": "boolean"},
            },
        }

    @driver_op(cloud="azure", driver="cosmos", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "COSMOS_ENDPOINT": ("Account endpoint URL (https://<name>.documents.azure.com:443/)"),
                "COSMOS_DATABASE_NAME": ("Default database name created inside the account"),
                "COSMOS_CONNECTION_STRING": ("Key Vault ref to the primary read-write connection string"),
                "COSMOS_READONLY_CONNECTION_STRING": ("Key Vault ref to the primary read-only connection string"),
            },
        )

    # ---- internals ----------------------------------------------------

    @staticmethod
    def _assert_owned(account: Any, source: object, operation: AzureOperation, account_name: str) -> None:
        verify_azure_ownership(
            arm_tags_of(account),
            owner_of(source),
            operation=operation,
            resource=f"Cosmos DB account {account_name}",
        )

    def _describe(self, account_name: str) -> Any | None:
        try:
            return self._mgmt.database_accounts.get(
                resource_group_name=self._config.resource_group,
                account_name=account_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            if "ResourceNotFound" in str(exc):
                return None
            return None

    def _account_name_for(self, *, spec: ProvisionSpec) -> str:
        # Cosmos account names: 3-44 chars, lowercase letters/digits/
        # hyphens; must start with a letter or digit and cannot end
        # with a hyphen.
        parts = [
            self._config.account_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "kv",
        ]
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:44]

    def _handle_for(self, *, account_name: str) -> str:
        return f"{KIND}/{account_name}"

    def _account_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureCosmosError(
                f"handle {handle!r} must be '<kind>/<account>'",
            )
        kind, _, account_name = handle.partition("/")
        if not kind or not account_name:
            raise AzureCosmosError(
                f"handle {handle!r} has empty component",
            )
        return account_name

    def _primary_conn_secret_for(self, *, account_name: str) -> str:
        return f"{self._config.secret_name_prefix}-{account_name}-primary"

    def _readonly_conn_secret_for(self, *, account_name: str) -> str:
        return f"{self._config.secret_name_prefix}-{account_name}-readonly"

    def _store_connection_strings(
        self,
        *,
        account_name: str,
        conn: Any,
    ) -> None:
        primary = ""
        readonly = ""
        # The SDK returns ``conn.connection_strings`` (list of objects
        # with ``connection_string`` + ``description`` fields). We
        # prefer the primary Mongo/SQL read-write entry, falling back
        # to the first available connection_string.
        entries = (
            getattr(conn, "connection_strings", None)
            or (conn.get("connection_strings") if isinstance(conn, dict) else None)
            or []
        )
        for entry in entries:
            desc = (_get(entry, "description", "") or "").lower()
            cs = _get(entry, "connection_string", "") or ""
            if not cs:
                continue
            if "read-only" in desc or "readonly" in desc:
                if not readonly:
                    readonly = cs
            elif not primary:
                primary = cs
        if not primary and entries:
            primary = _get(entries[0], "connection_string", "") or ""
        self._secrets.set_secret(
            self._primary_conn_secret_for(account_name=account_name),
            primary,
        )
        self._secrets.set_secret(
            self._readonly_conn_secret_for(account_name=account_name),
            readonly or primary,
        )

    def _delete_connection_secrets(self, account_name: str) -> None:
        if self._secrets is None:
            return
        for name in (
            self._primary_conn_secret_for(account_name=account_name),
            self._readonly_conn_secret_for(account_name=account_name),
        ):
            try:
                self._secrets.begin_delete_secret(name)
            except Exception:
                continue

    def _create_database(
        self,
        *,
        account_name: str,
        database_name: str,
        api_kind: str,
        throughput: int,
    ) -> None:
        """Route to the correct database-create API for the api_kind."""
        common_kwargs = {
            "resource_group_name": self._config.resource_group,
            "account_name": account_name,
        }
        body = {
            "resource": {"id": database_name},
            "options": {"throughput": throughput},
        }
        if api_kind == "MongoDB":
            poller = self._mgmt.mongo_db_resources.begin_create_update_mongo_db_database(
                **common_kwargs,
                database_name=database_name,
                create_update_mongo_db_database_parameters=body,
            )
        elif api_kind in {"GlobalDocumentDB", "Sql"}:
            poller = self._mgmt.sql_resources.begin_create_update_sql_database(
                **common_kwargs,
                database_name=database_name,
                create_update_sql_database_parameters=body,
            )
        elif api_kind == "Cassandra":
            poller = self._mgmt.cassandra_resources.begin_create_update_cassandra_keyspace(
                **common_kwargs,
                keyspace_name=database_name,
                create_update_cassandra_keyspace_parameters=body,
            )
        elif api_kind == "Table":
            poller = self._mgmt.table_resources.begin_create_update_table(
                **common_kwargs,
                table_name=database_name,
                create_update_table_parameters=body,
            )
        elif api_kind == "Gremlin":
            poller = self._mgmt.gremlin_resources.begin_create_update_gremlin_database(
                **common_kwargs,
                database_name=database_name,
                create_update_gremlin_database_parameters=body,
            )
        else:
            # Unreachable given _VALID_API_KINDS guard, but keeps mypy
            # happy without an explicit assert.
            return
        poller.result()

    def _update_database_throughput(
        self,
        *,
        account_name: str,
        database_name: str,
        throughput: int,
    ) -> None:
        try:
            poller = self._mgmt.mongo_db_resources.begin_update_mongo_db_database_throughput(
                resource_group_name=self._config.resource_group,
                account_name=account_name,
                database_name=database_name,
                update_throughput_parameters={
                    "resource": {"throughput": throughput},
                },
            )
            poller.result()
        except Exception:
            # Best-effort: throughput updates can be no-ops on autoscale
            # databases; we surface the original update result rather
            # than blocking the whole call on this side-channel.
            return

    def _ensure_continuous_backup(self, *, account_name: str) -> None:
        poller = self._mgmt.database_accounts.begin_update(
            resource_group_name=self._config.resource_group,
            account_name=account_name,
            update_parameters={
                "properties": {
                    "backup_policy": {"type": "Continuous"},
                },
            },
        )
        poller.result()

    def _list_locks(self, *, account_name: str) -> list[Any]:
        if self._locks is None:
            return []
        try:
            return list(
                self._locks.management_locks.list_at_resource_level(
                    resource_group_name=self._config.resource_group,
                    resource_provider_namespace="Microsoft.DocumentDB",
                    parent_resource_path="",
                    resource_type="databaseAccounts",
                    resource_name=account_name,
                ),
            )
        except Exception:
            return []

    def _delete_lock(
        self,
        *,
        account_name: str,
        lock_name: str,
    ) -> None:
        if self._locks is None:
            return
        self._locks.management_locks.delete_at_resource_level(
            resource_group_name=self._config.resource_group,
            resource_provider_namespace="Microsoft.DocumentDB",
            parent_resource_path="",
            resource_type="databaseAccounts",
            resource_name=account_name,
            lock_name=lock_name,
        )


# ----- module-level helpers --------------------------------------------


def _capabilities_for_api(api_kind: str) -> list[dict[str, str]]:
    """Cosmos exposes Mongo / Cassandra / Table / Gremlin via the
    ``capabilities`` array on a SQL-flavoured account body; the
    operator-facing api_kind translates to a specific capability
    name."""
    mapping = {
        "MongoDB": [{"name": "EnableMongo"}],
        "Cassandra": [{"name": "EnableCassandra"}],
        "Table": [{"name": "EnableTable"}],
        "Gremlin": [{"name": "EnableGremlin"}],
        "GlobalDocumentDB": [],
    }
    return mapping.get(api_kind, [])


def _state_of(account: Any) -> str:
    state = getattr(account, "provisioning_state", None)
    if state is not None:
        return str(state)
    state = getattr(account, "provisioningState", None)
    if state is not None:
        return str(state)
    if isinstance(account, dict):
        if "provisioning_state" in account:
            return str(account["provisioning_state"])
        if "provisioningState" in account:
            return str(account["provisioningState"])
    return "Unknown"


def _endpoint_of(account: Any) -> str:
    ep = getattr(account, "document_endpoint", None) or getattr(account, "documentEndpoint", None)
    if ep:
        return str(ep)
    if isinstance(account, dict):
        v = account.get("document_endpoint") or account.get("documentEndpoint")
        if v:
            return str(v)
    return ""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_AZURE_STATE_TO_PROTOCOL = {
    "Succeeded": "available",
    "Creating": "provisioning",
    "Updating": "updating",
    "Deleting": "deprovisioning",
    "Failed": "error",
    "Canceled": "error",
    "Unknown": "updating",
}
