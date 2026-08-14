"""Azure AI Search (vector index) managed-service driver (#372).

Implements ``ManagedServiceDriver`` for the canonical Azure
managed-vector path. Azure AI Search supports both full-text and
vector indexes on the same service; this driver registers under
the ``vector_index`` Kind. A sibling driver class registers under
the ``search`` Kind for the full-text path -- they don't share
implementation because the provision config differs and the
operator-facing knobs are distinct.

Provision flow:

  1. Create the search service (Basic SKU default; B/S0/S1/S2/S3
     for production tiers).
  2. Create the vector index inside that service via the data-plane
     REST API. Index schema includes a vector field with
     HNSW algorithm config -- this is what makes it a "vector"
     index vs. a plain full-text one.
  3. Stash the admin key in Azure Key Vault for the binding to
     reference.

Deprovision matrix:

  delete_data=False, force_destroy=False (default):
    Delete the search service via the control-plane API. Azure
    soft-delete window (14 days) honored so the operator can
    recover by name if they made a mistake.

  delete_data=True, force_destroy=False:
    Delete the search service AND drop the admin-key secret from
    Key Vault. Soft-delete window still honored on the service.

  delete_data=False, force_destroy=True:
    Delete the service AND purge it immediately (no soft-delete
    window). The admin-key secret remains (operator may want it
    for forensic restore).

  delete_data=True, force_destroy=True:
    --atomic. Service purged, admin-key secret dropped.
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
from azure.managed.tags import arm_tags_for as tags_for

KIND = "vector_index"


class AzureAISearchVectorError(Exception):
    """Distinct from the generic plugin error so the control plane's
    failure-handling logic can tell managed-service failures from
    infra-driver failures."""


# Size -> Azure AI Search SKU. Basic is the smallest production-grade
# tier; standard tiers add per-service partition + replica capacity.
# Free tier is excluded -- it forbids vector fields.
_SIZE_TO_SKU = {
    "small": "basic",
    "medium": "standard",
    "large": "standard2",
    "xlarge": "standard3",
}

# Per-tier replica/partition defaults. Higher tiers afford more of both.
_SIZE_TO_REPLICAS = {
    "small": 1,
    "medium": 1,
    "large": 2,
    "xlarge": 3,
}

_SIZE_TO_PARTITIONS = {
    "small": 1,
    "medium": 1,
    "large": 2,
    "xlarge": 3,
}


@dataclass(frozen=True)
class AzureAISearchVectorConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    subscription_id: str
    resource_group: str
    location: str = "eastus"

    service_name_prefix: str = "astrolift-vec"
    """Prefix on the search service name. Azure AI Search names must
    be globally unique (DNS labels under search.windows.net), 2-60
    chars, lowercase alphanumeric or hyphens."""

    default_sku: str = "basic"

    embedding_dimension_default: int = 1536
    """Default vector dimension. 1536 matches the Azure OpenAI
    ``text-embedding-3-small`` model. Operators on a different
    embedding model override via spec.config.dimension."""

    vector_search_profile_default: str = "default-profile"
    algorithm_default: str = "hnsw"
    """One of ``hnsw`` or ``exhaustiveKnn``. HNSW is the default
    because it's the cheapest at query time once the index is built;
    exhaustiveKnn is for tiny corpora where index build cost
    outweighs query cost."""

    keyvault_url: str = ""
    """Key Vault URL where the driver stores the admin key. Empty
    string disables Key Vault and errors out on provision."""

    secret_name_prefix: str = "astrolift-aisearch"

    mgmt_client: Any | None = None
    """Injected ``SearchManagementClient`` for tests."""

    index_client_factory: Any | None = None
    """Injected callable returning an index-client for tests. Signature:
    ``(endpoint: str, admin_key: str) -> IndexClient``. Production
    code constructs ``SearchIndexClient`` lazily."""

    secret_client: Any | None = None
    """Injected ``SecretClient`` for tests."""


class AzureAISearchVectorDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: AzureAISearchVectorConfig,
    ) -> None:
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
        self._index_client_factory = config.index_client_factory

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="azure",
        driver="vector_search",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if self._secrets is None:
            return ProvisionResult(
                ok=False,
                handle="",
                message=(
                    "azure ai search vector driver requires a Key Vault (set keyvault_url or inject secret_client)"
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
                message=(f"search service {service_name} already exists (state={_state_of(existing)})"),
            )

        sku_name = cfg.get("sku_name") or _SIZE_TO_SKU.get(spec.size, self._config.default_sku)
        replicas = int(
            cfg.get("replica_count") or _SIZE_TO_REPLICAS.get(spec.size, 1),
        )
        partitions = int(
            cfg.get("partition_count") or _SIZE_TO_PARTITIONS.get(spec.size, 1),
        )

        parameters: dict[str, Any] = {
            "location": self._config.location,
            "sku": {"name": sku_name},
            "replica_count": replicas,
            "partition_count": partitions,
            "tags": tags_for(spec),
            "public_network_access": (cfg.get("public_network_access") or "enabled"),
        }
        if cfg.get("hosting_mode"):
            parameters["hosting_mode"] = cfg["hosting_mode"]

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

        # Fetch admin keys + persist to Key Vault.
        try:
            keys = self._mgmt.admin_keys.get(
                resource_group_name=self._config.resource_group,
                search_service_name=service_name,
            )
            self._store_admin_key(service_name=service_name, keys=keys)
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"admin_keys.get / set_secret: {exc}",
                errors=[str(exc)],
            )

        # Create the vector index on the data plane.
        index_name = _index_name_from_service(
            service_name=service_name,
        )
        primary_key = _key_field(keys, "primary_key") or ""
        endpoint = f"https://{service_name}.search.windows.net"
        try:
            self._create_vector_index(
                endpoint=endpoint,
                admin_key=primary_key,
                index_name=index_name,
                dimension=int(
                    cfg.get(
                        "dimension",
                        self._config.embedding_dimension_default,
                    ),
                ),
                algorithm=(cfg.get("algorithm") or self._config.algorithm_default),
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_index: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(service_name=service_name),
            message=(
                f"search service {service_name} provisioning "
                f"(index={index_name}, admin key in {self._config.keyvault_url})"
            ),
        )

    @driver_op(cloud="azure", driver="vector_search")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        service_name = self._service_name_from_handle(spec.handle)
        cfg = spec.config or {}

        body: dict[str, Any] = {}
        if spec.size:
            sku = _SIZE_TO_SKU.get(spec.size)
            replicas = _SIZE_TO_REPLICAS.get(spec.size)
            partitions = _SIZE_TO_PARTITIONS.get(spec.size)
            if sku:
                body["sku"] = {"name": sku}
            if replicas is not None:
                body["replica_count"] = replicas
            if partitions is not None:
                body["partition_count"] = partitions
        if cfg.get("sku_name"):
            body["sku"] = {"name": cfg["sku_name"]}
        if "replica_count" in cfg:
            body["replica_count"] = int(cfg["replica_count"])
        if "partition_count" in cfg:
            body["partition_count"] = int(cfg["partition_count"])
        if cfg.get("public_network_access"):
            body["public_network_access"] = cfg["public_network_access"]

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

    @driver_op(
        cloud="azure",
        driver="vector_search",
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
        service_name = self._service_name_from_handle(spec.handle)

        existing = self._describe(service_name)
        if existing is None:
            if delete_data:
                self._delete_admin_key_secret(service_name)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"search service {service_name} already gone",
            )

        try:
            self._mgmt.services.delete(
                resource_group_name=self._config.resource_group,
                search_service_name=service_name,
            )
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"services.delete: {exc}",
                errors=[str(exc)],
            )

        purged = False
        if force_destroy:
            # Azure AI Search supports immediate purge via the
            # delete-purge data plane; the SDK exposes it as
            # ``begin_delete`` on the management client when the
            # ``deletion_options`` parameter is passed. Best-effort:
            # treat purge support as optional and surface via the
            # result message.
            try:
                self._mgmt.services.begin_delete(
                    resource_group_name=self._config.resource_group,
                    search_service_name=service_name,
                    deletion_options="purge",
                ).result()
                purged = True
            except Exception:
                purged = False

        if delete_data:
            self._delete_admin_key_secret(service_name)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"search service {service_name} delete queued "
                f"(purged={'yes' if purged else 'soft-delete'}, "
                f"data={'dropped' if delete_data else 'retained'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="azure", driver="vector_search")
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
            state=_AZURE_STATE_TO_PROTOCOL.get(azure_state, "updating"),
            message=f"azure reports {azure_state}",
        )

    @driver_op(cloud="azure", driver="vector_search")
    def binding(self, handle: ServiceHandle) -> Binding:
        service_name = self._service_name_from_handle(handle.handle)
        existing = self._describe(service_name)
        if existing is None:
            raise AzureAISearchVectorError(
                f"binding requested for missing service {service_name}",
            )
        endpoint = f"https://{service_name}.search.windows.net"
        index_name = _index_name_from_service(service_name=service_name)
        admin_key_secret = self._admin_key_secret_for(
            service_name=service_name,
        )

        return Binding(
            env_vars={
                "AZURE_AI_SEARCH_ENDPOINT": ValueRef(literal=endpoint),
                "AZURE_AI_SEARCH_INDEX_NAME": ValueRef(literal=index_name),
                "AZURE_AI_SEARCH_ADMIN_KEY": ValueRef(
                    secret_ref=admin_key_secret,
                ),
            },
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
                        "Microsoft.Search/searchServices/listAdminKeys/action",
                    ],
                ),
                Grant(
                    resource=admin_key_secret,
                    actions=[
                        "Microsoft.KeyVault/vaults/secrets/getSecret",
                    ],
                ),
            ],
            notes=(
                "AZURE_AI_SEARCH_ADMIN_KEY is a Key Vault ref to the "
                "primary admin key for the search service. The index "
                "named AZURE_AI_SEARCH_INDEX_NAME has a vector field "
                "configured with HNSW algorithm; callers POST documents "
                "with that field's embeddings populated."
            ),
        )

    @driver_op(cloud="azure", driver="vector_search")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """Azure AI Search has no first-party snapshot API. Operators
        export index contents via the REST 'indexes/<name>/docs/search'
        with a paginated query; the driver returns a SnapshotHandle
        that encodes a synthetic snapshot id for the calling workflow
        to drive the export against the workload's REST credentials."""
        from datetime import UTC, datetime

        service_name = self._service_name_from_handle(handle.handle)
        existing = self._describe(service_name)
        if existing is None:
            raise AzureAISearchVectorError(
                f"snapshot requested for missing service {service_name}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{service_name}-snap-{stamp}",
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="azure", driver="vector_search")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        target_provision = self.provision(target)
        if not target_provision.ok:
            return target_provision
        return ProvisionResult(
            ok=True,
            handle=target_provision.handle,
            message=(
                f"target service provisioned; restore from snapshot "
                f"{snapshot.snapshot_id} must be driven via the "
                f"workload's REST credentials"
            ),
        )

    @driver_op(cloud="azure", driver="vector_search", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sku_name": {
                    "type": "string",
                    "enum": [
                        "basic",
                        "standard",
                        "standard2",
                        "standard3",
                        "storage_optimized_l1",
                        "storage_optimized_l2",
                    ],
                },
                "replica_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 12,
                },
                "partition_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 12,
                },
                "public_network_access": {
                    "type": "string",
                    "enum": ["enabled", "disabled"],
                },
                "hosting_mode": {"type": "string"},
                "dimension": {"type": "integer", "minimum": 1},
                "algorithm": {
                    "type": "string",
                    "enum": ["hnsw", "exhaustiveKnn"],
                },
            },
        }

    @driver_op(cloud="azure", driver="vector_search", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "AZURE_AI_SEARCH_ENDPOINT": ("HTTPS endpoint URL for the search service"),
                "AZURE_AI_SEARCH_INDEX_NAME": ("Name of the platform-created vector index"),
                "AZURE_AI_SEARCH_ADMIN_KEY": ("Key Vault ref to the primary admin key"),
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
        parts = [
            self._config.service_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "v",
        ]
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        # Azure AI Search names: 2-60 chars, must start with a letter.
        if not clean or not clean[0].isalpha():
            clean = "v" + clean
        return clean[:60]

    def _handle_for(self, *, service_name: str) -> str:
        return f"{KIND}/{service_name}"

    def _service_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureAISearchVectorError(
                f"handle {handle!r} must be '<kind>/<service_name>'",
            )
        kind, _, service_name = handle.partition("/")
        if not kind or not service_name:
            raise AzureAISearchVectorError(
                f"handle {handle!r} has empty component",
            )
        return service_name

    def _admin_key_secret_for(self, *, service_name: str) -> str:
        return f"{self._config.secret_name_prefix}-{service_name}-admin"

    def _store_admin_key(self, *, service_name: str, keys: Any) -> None:
        primary = _key_field(keys, "primary_key") or _key_field(keys, "primaryKey") or ""
        if self._secrets is None:
            return
        self._secrets.set_secret(
            self._admin_key_secret_for(service_name=service_name),
            primary,
        )

    def _delete_admin_key_secret(self, service_name: str) -> None:
        if self._secrets is None:
            return
        name = self._admin_key_secret_for(service_name=service_name)
        try:
            self._secrets.begin_delete_secret(name)
        except Exception:
            return

    def _create_vector_index(
        self,
        *,
        endpoint: str,
        admin_key: str,
        index_name: str,
        dimension: int,
        algorithm: str,
    ) -> None:
        """Create a vector index on the data plane via the
        SearchIndexClient.

        Index schema: id (key), content (text), embedding (vector).
        The vector field is configured with the requested algorithm
        (hnsw default) so callers can do similarity queries
        immediately."""
        if self._index_client_factory is None:
            # Lazy import; production path constructs the client
            # against the real Azure SDK.
            from azure.core.credentials import AzureKeyCredential
            from azure.search.documents.indexes import SearchIndexClient

            client = SearchIndexClient(
                endpoint=endpoint,
                credential=AzureKeyCredential(admin_key),
            )
        else:
            client = self._index_client_factory(endpoint, admin_key)

        profile_name = self._config.vector_search_profile_default
        algo_name = f"{profile_name}-algo"
        index_body = {
            "name": index_name,
            "fields": [
                {
                    "name": "id",
                    "type": "Edm.String",
                    "key": True,
                    "filterable": True,
                },
                {
                    "name": "content",
                    "type": "Edm.String",
                    "searchable": True,
                },
                {
                    "name": "embedding",
                    "type": "Collection(Edm.Single)",
                    "searchable": True,
                    "vectorSearchDimensions": dimension,
                    "vectorSearchProfile": profile_name,
                },
            ],
            "vectorSearch": {
                "algorithms": [
                    {"name": algo_name, "kind": algorithm},
                ],
                "profiles": [
                    {
                        "name": profile_name,
                        "algorithm": algo_name,
                    },
                ],
            },
        }
        # The real SDK has ``create_or_update_index``; our fakes mirror
        # the signature exactly.
        client.create_or_update_index(index=index_body)


# ----- module-level helpers --------------------------------------------


def _state_of(service: Any) -> str:
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


def _index_name_from_service(*, service_name: str) -> str:
    """Derive the vector index name deterministically from the service
    name so provision + binding agree. Azure AI Search index naming:
    lowercase alphanumeric + hyphen, 2-128 chars, must start with a
    letter or number; cannot end with a hyphen."""
    return f"{service_name}-vec-idx"[:128].rstrip("-")


_AZURE_STATE_TO_PROTOCOL = {
    "Succeeded": "available",
    "Provisioning": "provisioning",
    "Updating": "updating",
    "Deleting": "deprovisioning",
    "Failed": "error",
    "Unknown": "updating",
}
