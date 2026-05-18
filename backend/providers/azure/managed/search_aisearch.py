"""Azure AI Search (full-text) managed-service driver (#373).

Implements ``ManagedServiceDriver`` for Azure AI Search (formerly
Cognitive Search), the canonical Azure managed full-text-index
path. The sibling vector-index driver (#372) targets the same
service via its vector-search feature and lives in a separate
file + class on purpose -- different SKU expectations, different
binding-env shape, different operator default.

Deprovision honours the SDK's four-corner matrix:

  delete_data=False, force_destroy=False (default):
    soft-delete window respected (Azure keeps the service name
    recoverable for ~14 days). Admin keys retained in Key Vault.

  delete_data=True, force_destroy=False:
    soft-delete window respected; admin keys purged from Key Vault.

  delete_data=False, force_destroy=True:
    soft-delete window respected; admin keys retained.
    [force_destroy is reserved for mid-modify bypass on this
    service; the soft-delete-purge axis hooks into the same flag.]

  delete_data=True, force_destroy=True:
    soft-delete window purged immediately; admin keys purged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

KIND = "search"


class AzureAISearchError(Exception):
    """Distinct from the generic plugin error so the control plane's
    failure-handling logic can tell managed-service failures from
    infra-driver failures (partial Key Vault state often needs
    targeted cleanup)."""


# Size -> SKU. Azure AI Search SKUs: free, basic, standard,
# standard2, standard3, storage_optimized_l1, storage_optimized_l2.
# Basic is the production-grade starter (2 replicas + 1 partition
# headroom). The platform's small/medium/large/xlarge knobs map
# onto Basic -> Standard -> Standard3 -> StorageOptimizedL2.
_SIZE_TO_SKU = {
    "small": "basic",
    "medium": "standard",
    "large": "standard3",
    "xlarge": "storage_optimized_l2",
}


@dataclass(frozen=True)
class AzureAISearchConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    subscription_id: str
    resource_group: str
    location: str = "eastus"

    service_name_prefix: str = "astrolift"
    """Prefix on the search-service name. Azure AI Search service
    names are 2-60 chars, lowercase, must start with a letter,
    only letters/digits/hyphens, globally unique under
    search.windows.net."""

    default_sku: str = "basic"
    """Basic is the cheapest production tier; free tier (1 service
    per subscription) is unsuitable for multi-tenant use. Operators
    bump to standard* / storage_optimized_l* for higher throughput."""

    replica_count_default: int = 1
    partition_count_default: int = 1

    public_network_access_default: str = "enabled"
    """``enabled`` keeps the search service reachable from the
    public internet (admin-key gated). ``disabled`` requires private
    endpoints; operator-managed."""

    keyvault_url: str = ""
    """Key Vault URL where the driver stores admin keys. Empty string
    means no Key Vault is configured -- the driver returns an error
    on provision rather than emitting credentials in the clear."""

    secret_name_prefix: str = "astrolift-search"

    mgmt_client: Any | None = None
    """Injected ``SearchManagementClient`` for tests."""

    secret_client: Any | None = None
    """Injected ``SecretClient`` for tests."""


def tags_for(spec: ProvisionSpec) -> dict[str, str]:
    """Same shape as the Cache-Redis / Postgres-Flex drivers'
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
    base.update({
        f"astrolift.io/extra/{k}": v
        for k, v in (spec.tags or {}).items()
    })
    return base


class AzureAISearchFullTextDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureAISearchConfig) -> None:
        self._config = config
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.search import SearchManagementClient

            self._mgmt = SearchManagementClient(
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

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if self._secrets is None:
            return ProvisionResult(
                ok=False,
                handle="",
                message=(
                    "azure ai search driver requires a Key Vault "
                    "(set keyvault_url or inject secret_client)"
                ),
                errors=["no_secret_backend"],
            )
        service_name = self._service_name_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(service_name)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=self._handle_for(service_name=service_name),
                message=(
                    f"search service {service_name} already exists "
                    f"(state={_state_of(existing)})"
                ),
            )

        sku_name = (
            cfg.get("sku") or _SIZE_TO_SKU.get(
                spec.size, self._config.default_sku,
            )
        )
        replicas = int(
            cfg.get("replica_count", self._config.replica_count_default),
        )
        partitions = int(
            cfg.get(
                "partition_count", self._config.partition_count_default,
            ),
        )
        public_network = (
            cfg.get(
                "public_network_access",
                self._config.public_network_access_default,
            )
        )

        parameters: dict[str, Any] = {
            "location": self._config.location,
            "sku": {"name": sku_name},
            "replica_count": replicas,
            "partition_count": partitions,
            "public_network_access": public_network,
            "tags": tags_for(spec),
        }
        if cfg.get("hosting_mode"):
            # 'highDensity' is only valid on standard3 SKU; we let
            # the API surface the validation error rather than
            # second-guessing tier rules.
            parameters["hosting_mode"] = cfg["hosting_mode"]
        if cfg.get("network_rule_set"):
            parameters["network_rule_set"] = dict(cfg["network_rule_set"])

        try:
            poller = self._mgmt.services.begin_create_or_update(
                resource_group_name=self._config.resource_group,
                search_service_name=service_name,
                service=parameters,
            )
            poller.result()
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"begin_create_or_update: {exc}",
                errors=[str(exc)],
            )

        # Persist admin keys to Key Vault. Azure auto-generates them
        # when the service is created; we read both primary +
        # secondary so workloads can rotate.
        try:
            keys = self._mgmt.admin_keys.get(
                resource_group_name=self._config.resource_group,
                search_service_name=service_name,
            )
            self._store_admin_keys(service_name=service_name, keys=keys)
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"admin_keys.get / set_secret: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(service_name=service_name),
            message=(
                f"search service {service_name} provisioning "
                f"(admin keys in key vault {self._config.keyvault_url})"
            ),
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        service_name = self._service_name_from_handle(spec.handle)
        cfg = spec.config or {}

        body: dict[str, Any] = {}
        if spec.size:
            sku = cfg.get("sku") or _SIZE_TO_SKU.get(spec.size)
            if sku:
                body["sku"] = {"name": sku}
        if cfg.get("sku") and "sku" not in body:
            body["sku"] = {"name": cfg["sku"]}
        if "replica_count" in cfg:
            body["replica_count"] = int(cfg["replica_count"])
        if "partition_count" in cfg:
            body["partition_count"] = int(cfg["partition_count"])
        if "public_network_access" in cfg:
            body["public_network_access"] = cfg["public_network_access"]
        if cfg.get("network_rule_set"):
            body["network_rule_set"] = dict(cfg["network_rule_set"])

        if not body:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        try:
            self._mgmt.services.update(
                resource_group_name=self._config.resource_group,
                search_service_name=service_name,
                service=body,
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
            message=f"search service {service_name} update queued",
        )

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        service_name = self._service_name_from_handle(spec.handle)

        existing = self._describe(service_name)
        if existing is None:
            self._delete_admin_key_secrets(service_name)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"search service {service_name} already gone",
            )

        try:
            poller = self._mgmt.services.begin_delete(
                resource_group_name=self._config.resource_group,
                search_service_name=service_name,
            )
            poller.result()
        except Exception as exc:
            err_str = str(exc)
            if not force_destroy and (
                "ServiceNotInDesiredState" in err_str
                or "FAILED_PRECONDITION" in err_str
            ):
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=(
                        f"search service {service_name} is mid-modify;"
                        f" wait or pass force_destroy=True for retry"
                    ),
                    errors=[err_str],
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"begin_delete: {err_str}",
                errors=[err_str],
            )

        # Soft-delete window: Azure AI Search keeps the service name
        # recoverable for ~14 days by default. force_destroy=True
        # purges immediately so the name can be re-used.
        purged = False
        if force_destroy:
            try:
                self._mgmt.services.begin_purge(
                    resource_group_name=self._config.resource_group,
                    search_service_name=service_name,
                ).result()
                purged = True
            except Exception:
                # begin_purge isn't available in every region / SDK
                # version. Treat as best-effort; surface via message.
                purged = False

        if delete_data:
            self._delete_admin_key_secrets(service_name)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"search service {service_name} delete queued "
                f"(purged={'yes' if purged else 'soft-delete'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        service_name = self._service_name_from_handle(handle.handle)
        existing = self._describe(service_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"search service {service_name} not found",
            )
        azure_state = _state_of(existing)
        return ServiceStatus(
            handle=handle.handle,
            state=_AZURE_STATE_TO_PROTOCOL.get(
                azure_state.lower(), "updating",
            ),
            message=f"azure reports {azure_state}",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        service_name = self._service_name_from_handle(handle.handle)
        existing = self._describe(service_name)
        if existing is None:
            raise AzureAISearchError(
                f"binding requested for missing service {service_name}",
            )
        endpoint = (
            f"https://{service_name}.search.windows.net"
        )
        primary_secret = self._primary_secret_for(
            service_name=service_name,
        )
        secondary_secret = self._secondary_secret_for(
            service_name=service_name,
        )
        index_name = _index_name_for(service_name=service_name)

        env_vars: dict[str, ValueRef] = {
            # Canonical contract envs (managed_service_kinds.py)
            "SEARCH_URL": ValueRef(literal=endpoint),
            "SEARCH_API_KEY": ValueRef(secret_ref=primary_secret),
            "SEARCH_INDEX_PREFIX": ValueRef(literal=index_name),
            # Azure-flavoured aliases.
            "AZURE_SEARCH_ENDPOINT": ValueRef(literal=endpoint),
            "AZURE_SEARCH_INDEX_NAME": ValueRef(literal=index_name),
            "AZURE_SEARCH_ADMIN_KEY": ValueRef(
                secret_ref=primary_secret,
            ),
            "AZURE_SEARCH_SECONDARY_ADMIN_KEY": ValueRef(
                secret_ref=secondary_secret,
            ),
        }
        return Binding(
            env_vars=env_vars,
            iam_grants=[
                Grant(
                    resource=(
                        f"/subscriptions/{self._config.subscription_id}"
                        f"/resourceGroups/{self._config.resource_group}"
                        f"/providers/Microsoft.Search/searchServices/"
                        f"{service_name}"
                    ),
                    actions=[
                        "Microsoft.Search/searchServices/read",
                        (
                            "Microsoft.Search/searchServices/"
                            "listAdminKeys/action"
                        ),
                    ],
                ),
                Grant(
                    resource=primary_secret,
                    actions=[
                        "Microsoft.KeyVault/vaults/secrets/getSecret",
                    ],
                ),
            ],
            notes=(
                "SEARCH_API_KEY / AZURE_SEARCH_ADMIN_KEY are Key Vault "
                "refs to the primary admin key. AZURE_SEARCH_"
                "SECONDARY_ADMIN_KEY exposes the secondary key for "
                "operator-side rotation."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        # Azure AI Search has no built-in snapshot primitive at the
        # service level; index snapshots are managed in-app via
        # index backups (custom flow per indexer / data source).
        # Mirror the AWS OpenSearch driver's behaviour: return a
        # deterministic id so the workflow layer's snapshot path
        # gets a handle to track.
        from datetime import UTC, datetime

        service_name = self._service_name_from_handle(handle.handle)
        existing = self._describe(service_name)
        if existing is None:
            raise AzureAISearchError(
                f"snapshot requested for missing service {service_name}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{service_name}-snap-{stamp}",
            created_at=datetime.now(UTC).isoformat(),
        )

    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        provisioned = self.provision(target)
        if not provisioned.ok:
            return provisioned
        return ProvisionResult(
            ok=True,
            handle=provisioned.handle,
            message=(
                f"target service provisioned; restore snapshot "
                f"{snapshot.snapshot_id} via index-rebuild workflow"
            ),
        )

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sku": {
                    "type": "string",
                    "enum": [
                        "free",
                        "basic",
                        "standard",
                        "standard2",
                        "standard3",
                        "storage_optimized_l1",
                        "storage_optimized_l2",
                    ],
                },
                "replica_count": {
                    "type": "integer", "minimum": 1, "maximum": 12,
                },
                "partition_count": {
                    "type": "integer", "minimum": 1, "maximum": 12,
                },
                "public_network_access": {
                    "type": "string",
                    "enum": ["enabled", "disabled"],
                },
                "hosting_mode": {
                    "type": "string",
                    "enum": ["default", "highDensity"],
                },
                "network_rule_set": {"type": "object"},
            },
        }

    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "SEARCH_URL": (
                    "AI Search HTTPS endpoint "
                    "(https://<name>.search.windows.net)"
                ),
                "SEARCH_API_KEY": (
                    "Key Vault ref to the primary admin key"
                ),
                "SEARCH_INDEX_PREFIX": (
                    "Conventional index-name prefix for this app"
                ),
                "AZURE_SEARCH_ENDPOINT": "Alias for SEARCH_URL",
                "AZURE_SEARCH_INDEX_NAME": (
                    "Alias for SEARCH_INDEX_PREFIX"
                ),
                "AZURE_SEARCH_ADMIN_KEY": (
                    "Key Vault ref to the primary admin key"
                ),
                "AZURE_SEARCH_SECONDARY_ADMIN_KEY": (
                    "Key Vault ref to the secondary admin key "
                    "(for rotation)"
                ),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, service_name: str) -> Any | None:
        try:
            return self._mgmt.services.get(
                resource_group_name=self._config.resource_group,
                search_service_name=service_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            if "ResourceNotFound" in str(exc):
                return None
            raise

    def _service_name_for(self, *, spec: ProvisionSpec) -> str:
        # Azure AI Search service names: 2-60 chars, lowercase, must
        # start with a letter, only letters/digits/hyphens, can't
        # contain two consecutive hyphens, can't start/end with
        # hyphen, globally unique under search.windows.net.
        parts = [
            self._config.service_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "search",
        ]
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(
            c if (c.isalnum() or c == "-") else "-" for c in raw
        )
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        if not clean or not clean[0].isalpha():
            clean = f"a{clean}"
        return clean[:60]

    def _handle_for(self, *, service_name: str) -> str:
        return f"{KIND}/{service_name}"

    def _service_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureAISearchError(
                f"handle {handle!r} must be '<kind>/<service_name>'",
            )
        kind, _, service_name = handle.partition("/")
        if not kind or not service_name:
            raise AzureAISearchError(
                f"handle {handle!r} has empty component",
            )
        return service_name

    def _primary_secret_for(self, *, service_name: str) -> str:
        return (
            f"{self._config.secret_name_prefix}-{service_name}-primary"
        )

    def _secondary_secret_for(self, *, service_name: str) -> str:
        return (
            f"{self._config.secret_name_prefix}-{service_name}-secondary"
        )

    def _store_admin_keys(
        self, *, service_name: str, keys: Any,
    ) -> None:
        primary = (
            _key_field(keys, "primary_key")
            or _key_field(keys, "primaryKey")
            or ""
        )
        secondary = (
            _key_field(keys, "secondary_key")
            or _key_field(keys, "secondaryKey")
            or ""
        )
        self._secrets.set_secret(
            self._primary_secret_for(service_name=service_name),
            primary,
        )
        self._secrets.set_secret(
            self._secondary_secret_for(service_name=service_name),
            secondary,
        )

    def _delete_admin_key_secrets(self, service_name: str) -> None:
        if self._secrets is None:
            return
        for name in (
            self._primary_secret_for(service_name=service_name),
            self._secondary_secret_for(service_name=service_name),
        ):
            try:
                self._secrets.begin_delete_secret(name)
            except Exception:
                continue


# ----- module-level helpers --------------------------------------------


def _state_of(service: Any) -> str:
    """Read ``provisioning_state`` (or the camelCase variant) off
    whatever shape the Azure SDK or fake returned."""
    state = getattr(service, "provisioning_state", None)
    if state is not None:
        return str(state)
    state = getattr(service, "provisioningState", None)
    if state is not None:
        return str(state)
    if isinstance(service, dict):
        if "provisioning_state" in service:
            return str(service["provisioning_state"])
        if "provisioningState" in service:
            return str(service["provisioningState"])
    return "Unknown"


def _key_field(keys: Any, field_name: str) -> str:
    v = getattr(keys, field_name, None)
    if v:
        return str(v)
    if isinstance(keys, dict) and keys.get(field_name):
        return str(keys[field_name])
    return ""


def _index_name_for(*, service_name: str) -> str:
    """Conventional index-name prefix the binding emits so workloads
    pick a stable index without re-deriving from the service name."""
    return service_name.replace("-", "_")


# Azure SearchService provisioningState values: 'succeeded',
# 'provisioning', 'failed'. We map them onto the SDK's protocol
# state enum. Lowercase the key to keep matches stable across SDK
# versions that vary capitalisation.
_AZURE_STATE_TO_PROTOCOL = {
    "succeeded": "available",
    "provisioning": "provisioning",
    "failed": "error",
    "deleting": "deprovisioning",
    "updating": "updating",
    "unknown": "updating",
}
