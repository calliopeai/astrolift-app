"""Azure Database for MySQL -- Flexible Server managed-service driver (#370).

Implements ``ManagedServiceDriver`` for the canonical Azure managed-
MySQL path. Targets the Flexible Server SKU (the modern, recommended
deployment option) rather than the deprecated Single Server.

Mirrors the AWS RDS-MySQL driver's lifecycle envelope so the
control plane sees consistent semantics across clouds. Differences
that matter:

* The "deletion protection" flag on Azure flexible-servers lives in
  the resource body as ``properties.deletionProtection``. We
  honour the same four-corner ``delete_data`` x ``force_destroy``
  matrix as the AWS driver: when ``force_destroy=True`` the driver
  flips the flag off via ``servers.begin_update`` before deleting;
  otherwise it errors out with a clear operator message.
* Azure's delete operation does not have a built-in
  "skip final snapshot" toggle. To preserve the data path on
  ``delete_data=False``, the driver triggers a manual long-term
  backup (`server_backups.begin_put`) before the delete call. On
  ``delete_data=True`` the backup is skipped and the server is
  deleted directly.
* Master credentials are persisted to Azure Key Vault. The driver
  is idempotent: existing-server probe short-circuits provision;
  existing-secret triggers a put rather than a create.
"""

from __future__ import annotations

import secrets
import string
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
from _sdk.physical_naming import managed_service_identity, physical_name, recorded_resource_name
from azure.managed.tags import arm_tags_for as tags_for
from azure.secrets_keyvault import key_vault_secret_ref

KIND = "mysql"


class AzureMySQLError(Exception):
    """Distinct from the generic plugin error so the control plane's
    failure-handling logic can tell managed-service failures from
    infra-driver failures (managed-service partial state often needs
    targeted cleanup of half-created Key Vault secrets etc.)."""


# Size -> SKU name. Azure exposes Flexible Server SKUs in three
# tiers: Burstable (B), General Purpose (D), Business Critical (E).
# We map astrolift's small/medium/large/xlarge onto a sensible
# starting point per tier; operators can override via
# ``spec.config.sku_name``.
_SIZE_TO_SKU = {
    "small": "Standard_B1ms",
    "medium": "Standard_B2ms",
    "large": "Standard_D2ds_v4",
    "xlarge": "Standard_D4ds_v4",
}

# Size -> SKU tier. Burstable (B-series) for small/medium; General
# Purpose (D-series) for large/xlarge.
_SIZE_TO_TIER = {
    "small": "Burstable",
    "medium": "Burstable",
    "large": "GeneralPurpose",
    "xlarge": "GeneralPurpose",
}

# Size -> storage size (GiB). Azure's minimum is 20 GiB.
_SIZE_TO_STORAGE_GB = {
    "small": 20,
    "medium": 50,
    "large": 128,
    "xlarge": 256,
}


@dataclass(frozen=True)
class AzureMySQLConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    subscription_id: str
    resource_group: str
    location: str = "eastus"
    """Azure region. Distinct field from ``resource_group`` because
    the resource-group is regional but the driver may target any
    region the operator's subscription is enabled for."""

    server_name_prefix: str = "astrolift"
    """Prefix on the flexible-server name. The full name follows
    Azure's globally-unique-within-subscription rule for MySQL;
    the slug + env qualifiers keep collisions impossible across
    apps in the same install."""

    engine_version: str = "8.0.21"
    """MySQL major.minor.patch. Azure exposes specific patch tags.
    Operators can override via ``spec.config.engine_version``."""

    backup_retention_days: int = 7
    """Default automated-backup retention (1-35 days)."""

    high_availability_default: str = "Disabled"
    """If "ZoneRedundant" or "SameZone", new servers come up with HA
    enabled. Default Disabled keeps cost low for dev installs."""

    deletion_protection_default: bool = True
    """Default for new servers. Operators with ``force_destroy=True``
    bypass on delete."""

    keyvault_url: str = ""
    """Key Vault URL (https://<name>.vault.azure.net) where the
    driver stores generated master passwords. Empty string means
    no Key Vault is configured -- the driver returns an error on
    provision in that case rather than emitting credentials in
    the clear."""

    secret_name_prefix: str = "astrolift-mysql"
    """Prefix on the Key Vault secret name. Lets operators apply
    tag-based access policies."""

    mgmt_client: Any | None = None
    """Injected ``MySQLManagementClient`` for tests. Production
    builds let the driver construct one via
    ``DefaultAzureCredential``."""

    secret_client: Any | None = None
    """Injected ``SecretClient`` for tests. Production builds let
    the driver construct one against ``keyvault_url``."""


class AzureMySQLFlexibleDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureMySQLConfig) -> None:
        self._config = config
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.mysqlflexibleservers import MySQLManagementClient

            self._mgmt = MySQLManagementClient(
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
        driver="mysql_flexible",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if self._secrets is None:
            return ProvisionResult(
                ok=False,
                handle="",
                message=("azure mysql driver requires a Key Vault (set keyvault_url or inject secret_client)"),
                errors=["no_secret_backend"],
            )
        server_name = self._server_name_for(spec=spec)
        cfg = spec.config or {}

        # Probe for an existing server first -- provision is idempotent.
        existing = self._describe(server_name)
        if existing is not None:
            try:
                self._assert_owned(existing, spec, AzureOperation.PROVISION, server_name)
            except AzureOwnershipError as exc:
                return ProvisionResult(ok=False, handle="", message=str(exc), errors=[OWNERSHIP_ERROR_CODE])
            return ProvisionResult(
                ok=True,
                handle=self._handle_for(server_name=server_name),
                message=(f"flexible server {server_name} already exists (state={_state_of(existing)})"),
            )

        master_password = _generate_master_password()
        try:
            self._store_master_password(
                server_name=server_name,
                password=master_password,
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"key vault put_secret: {exc}",
                errors=[str(exc)],
            )

        engine_version = cfg.get("engine_version") or self._config.engine_version
        sku_name = cfg.get("sku_name") or _SIZE_TO_SKU.get(spec.size, "Standard_B1ms")
        sku_tier = cfg.get("sku_tier") or _SIZE_TO_TIER.get(spec.size, "Burstable")
        storage_gb = int(
            cfg.get("storage_gb") or _SIZE_TO_STORAGE_GB.get(spec.size, 20),
        )
        ha_mode = cfg.get("high_availability") or self._config.high_availability_default
        deletion_protection = bool(
            cfg.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        backup_retention = int(
            cfg.get(
                "backup_retention_days",
                self._config.backup_retention_days,
            ),
        )

        parameters: dict[str, Any] = {
            "location": self._config.location,
            "sku": {"name": sku_name, "tier": sku_tier},
            "properties": {
                "version": engine_version,
                "administrator_login": "astrolift",
                "administrator_login_password": master_password,
                "storage": {
                    "storage_size_gb": storage_gb,
                    "auto_grow": "Enabled",
                },
                "backup": {
                    "backup_retention_days": backup_retention,
                    "geo_redundant_backup": "Disabled",
                },
                "high_availability": {"mode": ha_mode},
                "create_mode": "Default",
                "data_encryption": {"type": "SystemManaged"},
                "deletion_protection": deletion_protection,
            },
            "tags": tags_for(spec),
        }
        if cfg.get("subnet_id"):
            parameters["properties"]["network"] = {
                "delegated_subnet_resource_id": cfg["subnet_id"],
                "private_dns_zone_resource_id": cfg.get(
                    "private_dns_zone_id",
                    "",
                ),
            }

        try:
            poller = self._mgmt.servers.begin_create(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
                parameters=parameters,
            )
            # Drivers are blocking by contract -- the workflow layer
            # owns timeouts. begin_create returns a poller; calling
            # .result() is the documented way to await completion.
            poller.result()
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"begin_create: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(server_name=server_name),
            message=(f"flexible server {server_name} provisioning (password in key vault {self._config.keyvault_url})"),
        )

    @driver_op(cloud="azure", driver="mysql_flexible")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        server_name = self._server_name_from_handle(spec.handle)
        cfg = spec.config or {}

        existing = self._describe(server_name)
        if existing is None:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"flexible server {server_name} does not exist",
                errors=["not_found"],
                retryable=False,
            )
        try:
            self._assert_owned(existing, spec, AzureOperation.UPDATE, server_name)
        except AzureOwnershipError as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=[OWNERSHIP_ERROR_CODE],
                retryable=False,
            )

        body: dict[str, Any] = {}
        sku_block: dict[str, Any] = {}
        if spec.size:
            sku = cfg.get("sku_name") or _SIZE_TO_SKU.get(spec.size)
            tier = cfg.get("sku_tier") or _SIZE_TO_TIER.get(spec.size)
            if sku:
                sku_block["name"] = sku
            if tier:
                sku_block["tier"] = tier
            new_storage = _SIZE_TO_STORAGE_GB.get(spec.size)
            if new_storage:
                body.setdefault("properties", {})["storage"] = {
                    "storage_size_gb": new_storage,
                }
        if cfg.get("sku_name"):
            sku_block["name"] = cfg["sku_name"]
        if cfg.get("sku_tier"):
            sku_block["tier"] = cfg["sku_tier"]
        if sku_block:
            body["sku"] = sku_block
        if cfg.get("engine_version"):
            body.setdefault("properties", {})["version"] = cfg["engine_version"]
        if "high_availability" in cfg:
            body.setdefault("properties", {})["high_availability"] = {
                "mode": cfg["high_availability"],
            }
        if "backup_retention_days" in cfg:
            body.setdefault("properties", {})["backup"] = {
                "backup_retention_days": int(
                    cfg["backup_retention_days"],
                ),
            }

        if not body:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        try:
            poller = self._mgmt.servers.begin_update(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
                parameters=body,
            )
            poller.result()
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
            message=f"flexible server {server_name} update queued",
        )

    @driver_op(
        cloud="azure",
        driver="mysql_flexible",
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
        server_name = self._server_name_from_handle(spec.handle)

        existing = self._describe(server_name)
        if existing is None:
            self._delete_master_password_secret(server_name)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"flexible server {server_name} already gone",
            )

        try:
            self._assert_owned(existing, spec, AzureOperation.DELETE, server_name)
        except AzureOwnershipError as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=[OWNERSHIP_ERROR_CODE],
                retryable=False,
            )

        protection = _deletion_protection_of(existing)
        if protection and force_destroy:
            try:
                poller = self._mgmt.servers.begin_update(
                    resource_group_name=self._config.resource_group,
                    server_name=server_name,
                    parameters={
                        "properties": {"deletion_protection": False},
                    },
                )
                poller.result()
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"failed to clear deletion_protection: {exc}",
                    errors=[str(exc)],
                )
        elif protection:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"flexible server {server_name} has "
                    f"deletion_protection enabled -- pass "
                    f"force_destroy=True to bypass"
                ),
                errors=["deletion_protection_enabled"],
            )

        snapshot_taken = False
        if not delete_data:
            try:
                self._mgmt.backups.put(
                    resource_group_name=self._config.resource_group,
                    server_name=server_name,
                    backup_name=_final_backup_name(
                        server_name=server_name,
                    ),
                )
                snapshot_taken = True
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"final backup: {exc}",
                    errors=[str(exc)],
                )

        try:
            poller = self._mgmt.servers.begin_delete(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
            )
            poller.result()
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"begin_delete: {exc}",
                errors=[str(exc)],
            )

        # Master-password secret retention mirrors the AWS driver:
        # delete eagerly when delete_data=True; keep when the data
        # path is being retained so a restore from the final backup
        # still has the original credentials.
        if delete_data:
            self._delete_master_password_secret(server_name)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"flexible server {server_name} delete queued "
                f"(snapshot={'taken' if snapshot_taken else 'skipped'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="azure", driver="mysql_flexible")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        server_name = self._server_name_from_handle(handle.handle)
        existing = self._describe(server_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"flexible server {server_name} not found",
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

    @driver_op(cloud="azure", driver="mysql_flexible")
    def binding(self, handle: ServiceHandle) -> Binding:
        server_name = self._server_name_from_handle(handle.handle)
        existing = self._describe(server_name)
        if existing is None:
            raise AzureMySQLError(
                f"binding requested for missing server {server_name}",
            )
        host = _fqdn_of(existing) or (f"{server_name}.mysql.database.azure.com")
        secret_name = self._secret_name_for(server_name=server_name)

        return Binding(
            env_vars={
                # Canonical mysql envelope (#1003, backfilled in #1402).
                "MYSQL_HOST": ValueRef(literal=host),
                "MYSQL_PORT": ValueRef(literal="3306"),
                "MYSQL_DB": ValueRef(literal="mysql"),
                "MYSQL_USER": ValueRef(literal="astrolift"),
                "MYSQL_PASSWORD": ValueRef(
                    secret_ref=key_vault_secret_ref(self._config.keyvault_url, secret_name),
                ),
                # Pre-#1003 names, kept as aliases so workloads already bound
                # to this driver keep the variables they read (#1401).
                "DATABASE_HOST": ValueRef(literal=host),
                "DATABASE_PORT": ValueRef(literal="3306"),
                "DATABASE_NAME": ValueRef(literal="mysql"),
                "DATABASE_USER": ValueRef(literal="astrolift"),
                "DATABASE_PASSWORD": ValueRef(
                    secret_ref=key_vault_secret_ref(self._config.keyvault_url, secret_name),
                ),
                "DATABASE_URL": ValueRef(
                    secret_ref=key_vault_secret_ref(
                        self._config.keyvault_url,
                        self._secret_name_for_url(server_name=server_name),
                    ),
                ),
            },
            iam_grants=[
                Grant(
                    resource=(
                        f"/subscriptions/{self._config.subscription_id}"
                        f"/resourceGroups/{self._config.resource_group}"
                        f"/providers/Microsoft.DBforMySQL"
                        f"/flexibleServers/{server_name}"
                    ),
                    actions=[
                        "Microsoft.DBforMySQL/flexibleServers/read",
                    ],
                ),
                Grant(
                    resource=secret_name,
                    actions=["Microsoft.KeyVault/vaults/secrets/getSecret"],
                ),
            ],
            notes=(
                "DATABASE_URL is a derived Key Vault secret holding "
                "the fully-formed mysql:// connection string; the "
                "split components are also exposed for callers that "
                "build their own DSN."
            ),
        )

    @driver_op(cloud="azure", driver="mysql_flexible")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        server_name = self._server_name_from_handle(handle.handle)
        existing = self._describe(server_name)
        if existing is None:
            raise AzureMySQLError(f"snapshot requested for missing flexible server {server_name}")
        self._assert_owned(existing, handle, AzureOperation.SNAPSHOT, server_name)
        backup_name = f"{server_name}-snap-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        try:
            self._mgmt.backups.put(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
                backup_name=backup_name,
            )
        except Exception as exc:
            raise AzureMySQLError(
                f"backups.put: {exc}",
            ) from exc
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=backup_name,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="azure", driver="mysql_flexible")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        target_server = self._server_name_for(spec=target)
        source_server = self._server_name_from_handle(snapshot.handle)
        existing_target = self._describe(target_server)
        if existing_target is not None:
            try:
                self._assert_owned(existing_target, target, AzureOperation.RESTORE, target_server)
            except AzureOwnershipError as exc:
                return ProvisionResult(ok=False, handle="", message=str(exc), errors=[OWNERSHIP_ERROR_CODE])
        try:
            poller = self._mgmt.servers.begin_create(
                resource_group_name=self._config.resource_group,
                server_name=target_server,
                parameters={
                    "location": self._config.location,
                    "properties": {
                        "create_mode": "PointInTimeRestore",
                        "source_server_resource_id": (
                            f"/subscriptions/"
                            f"{self._config.subscription_id}"
                            f"/resourceGroups/"
                            f"{self._config.resource_group}"
                            f"/providers/Microsoft.DBforMySQL"
                            f"/flexibleServers/{source_server}"
                        ),
                        "restore_point_in_time": snapshot.created_at,
                    },
                    "tags": tags_for(target),
                },
            )
            poller.result()
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"restore begin_create: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=self._handle_for(server_name=target_server),
            message=(f"restore from backup {snapshot.snapshot_id} queued"),
        )

    @driver_op(cloud="azure", driver="mysql_flexible", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "engine_version": {"type": "string"},
                "sku_name": {"type": "string"},
                "sku_tier": {
                    "type": "string",
                    "enum": [
                        "Burstable",
                        "GeneralPurpose",
                        "BusinessCritical",
                    ],
                },
                "storage_gb": {"type": "integer", "minimum": 20},
                "high_availability": {
                    "type": "string",
                    "enum": ["Disabled", "ZoneRedundant", "SameZone"],
                },
                "deletion_protection": {"type": "boolean"},
                "backup_retention_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 35,
                },
                "subnet_id": {"type": "string"},
                "private_dns_zone_id": {"type": "string"},
            },
        }

    @driver_op(cloud="azure", driver="mysql_flexible", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MYSQL_HOST": "Flexible Server FQDN",
                "MYSQL_PORT": "MySQL port (3306)",
                "MYSQL_DB": "Initial database name (mysql)",
                "MYSQL_USER": "Master username (astrolift)",
                "MYSQL_PASSWORD": "Key Vault ref to the master password",
                "DATABASE_HOST": "Flexible Server FQDN",
                "DATABASE_PORT": "MySQL port (3306)",
                "DATABASE_NAME": "Initial database name",
                "DATABASE_USER": "Master username (astrolift)",
                "DATABASE_PASSWORD": ("Key Vault ref to the master password"),
                "DATABASE_URL": ("Key Vault ref to the fully-formed mysql:// connection string"),
            },
        )

    # ---- internals ----------------------------------------------------

    @staticmethod
    def _assert_owned(server: Any, source: object, operation: AzureOperation, server_name: str) -> None:
        verify_azure_ownership(
            arm_tags_of(server),
            owner_of(source),
            operation=operation,
            resource=f"MySQL flexible server {server_name}",
        )

    def _describe(self, server_name: str) -> Any | None:
        try:
            return self._mgmt.servers.get(
                resource_group_name=self._config.resource_group,
                server_name=server_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            if "ResourceNotFound" in str(exc):
                return None
            raise

    def _server_name_for(self, *, spec: ProvisionSpec) -> str:
        managed_service_identity(spec.managed_service_id)
        recorded = recorded_resource_name(spec.recorded_handle, kind=KIND)
        return recorded or physical_name(spec.managed_service_id, prefix=self._config.server_name_prefix, max_length=63)

    def _handle_for(self, *, server_name: str) -> str:
        return f"{KIND}/{server_name}"

    def _server_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureMySQLError(
                f"handle {handle!r} must be '<kind>/<server_name>'",
            )
        kind, _, server_name = handle.partition("/")
        if not kind or not server_name:
            raise AzureMySQLError(
                f"handle {handle!r} has empty component",
            )
        return server_name

    def _secret_name_for(self, *, server_name: str) -> str:
        return f"{self._config.secret_name_prefix}-{server_name}-master"

    def _secret_name_for_url(self, *, server_name: str) -> str:
        return f"{self._config.secret_name_prefix}-{server_name}-url"

    def _store_master_password(
        self,
        *,
        server_name: str,
        password: str,
    ) -> None:
        name = self._secret_name_for(server_name=server_name)
        # set_secret on Azure SDK is upsert semantics; safe across
        # retry-on-failed-provision scenarios.
        self._secrets.set_secret(name, password)

    def _delete_master_password_secret(self, server_name: str) -> None:
        if self._secrets is None:
            return
        name = self._secret_name_for(server_name=server_name)
        try:
            self._secrets.begin_delete_secret(name)
        except Exception:
            return


# ----- module-level helpers --------------------------------------------


_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_."
"""Azure MySQL master password rules: 8-128 chars from at least three
of (upper, lower, digit, non-alphanumeric). Our 32-char generator
draws from a conservative subset that satisfies every category
without including shell-special chars."""


def _generate_master_password(length: int = 32) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def _final_backup_name(*, server_name: str) -> str:
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    # Backup names are limited to 64 chars by the Azure API.
    return f"{server_name}-final-{stamp}"[:64]


def _state_of(server: Any) -> str:
    """Pull the ``state`` attribute off whatever shape the Azure SDK
    or test fake returned. The SDK exposes it on ``properties.state``
    in the REST body but the auto-generated client surfaces it as
    a top-level attribute."""
    state = getattr(server, "state", None)
    if state is not None:
        return str(state)
    props = getattr(server, "properties", None)
    if props is not None:
        s = getattr(props, "state", None)
        if s is not None:
            return str(s)
    if isinstance(server, dict):
        if "state" in server:
            return str(server["state"])
        if (
            "properties" in server
            and isinstance(
                server["properties"],
                dict,
            )
            and "state" in server["properties"]
        ):
            return str(server["properties"]["state"])
    return "Unknown"


def _fqdn_of(server: Any) -> str:
    fqdn = getattr(server, "fully_qualified_domain_name", None)
    if fqdn:
        return str(fqdn)
    if isinstance(server, dict):
        v = server.get("fully_qualified_domain_name")
        if v:
            return str(v)
    return ""


def _deletion_protection_of(server: Any) -> bool:
    """Read the deletion_protection flag from whatever shape the SDK
    or fake returned. Falls back to False when the field is missing
    so a stale-shape response doesn't strand the resource."""
    flag = getattr(server, "deletion_protection", None)
    if flag is not None:
        return bool(flag)
    props = getattr(server, "properties", None)
    if props is not None:
        v = getattr(props, "deletion_protection", None)
        if v is not None:
            return bool(v)
    if isinstance(server, dict):
        if "deletion_protection" in server:
            return bool(server["deletion_protection"])
        if (
            "properties" in server
            and isinstance(
                server["properties"],
                dict,
            )
            and "deletion_protection" in server["properties"]
        ):
            return bool(server["properties"]["deletion_protection"])
    return False


_AZURE_STATE_TO_PROTOCOL = {
    "Ready": "available",
    "Starting": "provisioning",
    "Stopping": "updating",
    "Stopped": "available",
    "Updating": "updating",
    "Provisioning": "provisioning",
    "Disabled": "error",
    "Dropping": "deprovisioning",
    "Deleting": "deprovisioning",
    "Failed": "error",
    "Inaccessible": "error",
    "Restarting": "updating",
}
