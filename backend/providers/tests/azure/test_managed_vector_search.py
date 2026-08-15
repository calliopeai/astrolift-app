"""Tests for AzureAISearchVectorDriver (#372 -- vector_index/azure_ai_search_vector).

Same fake-client pattern as the AzureCacheRedisDriver tests:
no Azure emulator is broadly usable, so we drive the SDK via
call-recording fakes that return canned responses. Test surface
mirrors AWS OpenSearchVectorDriver + GCP VertexMatchingEngineDriver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from azure.managed.vector_search import (
    KIND,
    AzureAISearchVectorConfig,
    AzureAISearchVectorDriver,
    AzureAISearchVectorError,
    _index_name_from_service,
)

# ---- fakes ------------------------------------------------------


class _NotFound(Exception):
    pass


_NotFound.__name__ = "ResourceNotFoundError"


@dataclass
class FakePoller:
    value: Any = None

    def result(self) -> Any:
        return self.value


@dataclass
class FakeSearchService:
    name: str
    provisioning_state: str = "Succeeded"
    sku: dict[str, Any] = field(default_factory=dict)
    replica_count: int = 1
    partition_count: int = 1
    location: str = "eastus"
    tags: dict[str, str] = field(default_factory=dict)
    public_network_access: str = "enabled"


@dataclass
class FakeAdminKeys:
    primary_key: str = "primary-admin-key-value-aaaaaaaaaaaaaaaa"
    secondary_key: str = "secondary-admin-key-value-bbbbbbbbbbbbbbbb"


@dataclass
class FakeServicesOperations:
    services: dict[str, FakeSearchService] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)
    purge_calls: list[str] = field(default_factory=list)
    purge_supported: bool = True

    def get(
        self,
        *,
        resource_group_name: str,
        search_service_name: str,
    ) -> FakeSearchService:
        if search_service_name not in self.services:
            raise _NotFound(search_service_name)
        return self.services[search_service_name]

    def begin_create_or_update(
        self,
        *,
        resource_group_name: str,
        search_service_name: str,
        service: dict[str, Any],
    ) -> FakePoller:
        self.create_calls.append(
            {"name": search_service_name, "service": service},
        )
        svc = FakeSearchService(
            name=search_service_name,
            sku=dict(service.get("sku", {})),
            replica_count=int(service.get("replica_count", 1)),
            partition_count=int(service.get("partition_count", 1)),
            location=service.get("location", "eastus"),
            tags=dict(service.get("tags", {})),
            public_network_access=service.get(
                "public_network_access",
                "enabled",
            ),
        )
        self.services[search_service_name] = svc
        return FakePoller(value=svc)

    def update(
        self,
        *,
        resource_group_name: str,
        search_service_name: str,
        service: dict[str, Any],
    ) -> FakeSearchService:
        self.update_calls.append(
            {"name": search_service_name, "service": service},
        )
        svc = self.services.get(search_service_name)
        if svc is None:
            raise _NotFound(search_service_name)
        if "sku" in service:
            svc.sku = dict(service["sku"])
        if "replica_count" in service:
            svc.replica_count = int(service["replica_count"])
        if "partition_count" in service:
            svc.partition_count = int(service["partition_count"])
        if "public_network_access" in service:
            svc.public_network_access = service["public_network_access"]
        return svc

    def delete(
        self,
        *,
        resource_group_name: str,
        search_service_name: str,
    ) -> None:
        self.delete_calls.append(search_service_name)
        self.services.pop(search_service_name, None)

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        search_service_name: str,
        deletion_options: str = "",
    ) -> FakePoller:
        if not self.purge_supported:
            raise RuntimeError("purge not supported in this region")
        if deletion_options == "purge":
            self.purge_calls.append(search_service_name)
        self.services.pop(search_service_name, None)
        return FakePoller(value=None)


@dataclass
class FakeAdminKeysOperations:
    def get(
        self,
        *,
        resource_group_name: str,
        search_service_name: str,
    ) -> FakeAdminKeys:
        return FakeAdminKeys()


@dataclass
class FakeMgmtClient:
    services_obj: FakeServicesOperations = field(
        default_factory=FakeServicesOperations,
    )
    admin_keys_obj: FakeAdminKeysOperations = field(
        default_factory=FakeAdminKeysOperations,
    )

    @property
    def services(self) -> FakeServicesOperations:
        return self.services_obj

    @property
    def admin_keys(self) -> FakeAdminKeysOperations:
        return self.admin_keys_obj


@dataclass
class FakeSecretClient:
    secrets: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)

    def set_secret(self, name: str, value: str) -> None:
        self.secrets[name] = value

    def begin_delete_secret(self, name: str) -> Any:
        if name not in self.secrets:
            raise _NotFound(name)
        del self.secrets[name]
        self.deleted.append(name)
        return FakePoller(value=None)


class FakeIndexClient:
    def __init__(self, endpoint: str, admin_key: str) -> None:
        self.endpoint = endpoint
        self.admin_key = admin_key
        self.indexes: dict[str, dict[str, Any]] = {}

    def create_or_update_index(self, *, index: dict[str, Any]) -> None:
        self.indexes[index["name"]] = index


# Module-level capture so tests can assert on the index client's
# index dict, since the driver constructs it via the factory.
_INDEX_CLIENTS: list[FakeIndexClient] = []


def _index_factory(endpoint: str, admin_key: str) -> FakeIndexClient:
    client = FakeIndexClient(endpoint, admin_key)
    _INDEX_CLIENTS.append(client)
    return client


# ---- fixtures ---------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_index_clients() -> None:
    _INDEX_CLIENTS.clear()


@pytest.fixture
def mgmt() -> FakeMgmtClient:
    return FakeMgmtClient()


@pytest.fixture
def secrets_client() -> FakeSecretClient:
    return FakeSecretClient()


@pytest.fixture
def driver(
    mgmt: FakeMgmtClient,
    secrets_client: FakeSecretClient,
) -> AzureAISearchVectorDriver:
    return AzureAISearchVectorDriver(
        config=AzureAISearchVectorConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=secrets_client,
            index_client_factory=_index_factory,
        ),
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="azure-prod",
        service_handle_hint="v",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_service_and_vector_index(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert result.handle.startswith(f"{KIND}/")
    assert len(mgmt.services_obj.services) == 1
    # Vector index created on data plane
    assert _INDEX_CLIENTS
    idx_client = _INDEX_CLIENTS[0]
    assert idx_client.indexes
    index_name = next(iter(idx_client.indexes))
    schema = idx_client.indexes[index_name]
    # Has a vector field
    emb_field = next(f for f in schema["fields"] if f["name"] == "embedding")
    assert emb_field["type"] == "Collection(Edm.Single)"
    assert emb_field["vectorSearchDimensions"] == 1536


def test_provision_defaults_to_basic_sku(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    create = mgmt.services_obj.create_calls[0]
    assert create["service"]["sku"]["name"] == "basic"


def test_provision_defaults_to_hnsw_algorithm(
    driver: AzureAISearchVectorDriver,
) -> None:
    driver.provision(_spec())
    idx_client = _INDEX_CLIENTS[0]
    schema = next(iter(idx_client.indexes.values()))
    algos = schema["vectorSearch"]["algorithms"]
    assert algos[0]["kind"] == "hnsw"


def test_provision_supports_exhaustive_knn(
    driver: AzureAISearchVectorDriver,
) -> None:
    driver.provision(_spec(config={"algorithm": "exhaustiveKnn"}))
    idx_client = _INDEX_CLIENTS[0]
    schema = next(iter(idx_client.indexes.values()))
    algos = schema["vectorSearch"]["algorithms"]
    assert algos[0]["kind"] == "exhaustiveKnn"


def test_provision_honours_dimension_override(
    driver: AzureAISearchVectorDriver,
) -> None:
    driver.provision(_spec(config={"dimension": 768}))
    idx_client = _INDEX_CLIENTS[0]
    schema = next(iter(idx_client.indexes.values()))
    emb_field = next(f for f in schema["fields"] if f["name"] == "embedding")
    assert emb_field["vectorSearchDimensions"] == 768


def test_provision_idempotent(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    assert len(mgmt.services_obj.create_calls) == 1


def test_provision_persists_admin_key_in_key_vault(
    driver: AzureAISearchVectorDriver,
    secrets_client: FakeSecretClient,
) -> None:
    result = driver.provision(_spec())
    service_name = result.handle.split("/", 1)[1]
    admin_secret = f"astrolift-aisearch-{service_name}-admin"
    assert admin_secret in secrets_client.secrets
    assert secrets_client.secrets[admin_secret]


def test_provision_honours_size_to_sku(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec(size="large"))
    create = mgmt.services_obj.create_calls[0]
    assert create["service"]["sku"]["name"] == "standard2"
    assert create["service"]["replica_count"] == 2


def test_provision_honours_spec_config_override(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(
        _spec(
            config={
                "sku_name": "standard3",
                "replica_count": 4,
                "partition_count": 6,
            },
        ),
    )
    create = mgmt.services_obj.create_calls[0]
    assert create["service"]["sku"]["name"] == "standard3"
    assert create["service"]["replica_count"] == 4
    assert create["service"]["partition_count"] == 6


def test_provision_tags_service_with_astrolift_namespace(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    tags = mgmt.services_obj.create_calls[0]["service"]["tags"]
    assert tags["astrolift.io/managed-by"] == "platform"
    assert tags["astrolift.io/app"] == "api"


def test_provision_without_keyvault_returns_error() -> None:
    d = AzureAISearchVectorDriver(
        config=AzureAISearchVectorConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            mgmt_client=FakeMgmtClient(),
            index_client_factory=_index_factory,
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "Key Vault" in result.message


# ---- update -----------------------------------------------------


def test_update_resize(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="medium"),
    )
    assert result.ok
    last = mgmt.services_obj.update_calls[-1]
    assert last["service"]["sku"]["name"] == "standard"


def test_update_explicit_replica(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"replica_count": 5},
        ),
    )
    assert result.ok
    assert mgmt.services_obj.update_calls[-1]["service"]["replica_count"] == 5


def test_update_noop_when_nothing_to_change(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    before = len(mgmt.services_obj.update_calls)
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message
    assert len(mgmt.services_obj.update_calls) == before


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_refuses_without_snapshot_and_keeps_secret(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    admin_secret = f"astrolift-aisearch-{service_name}-admin"
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert not result.ok
    assert result.retryable is False
    assert "delete_data=True" in result.message
    assert service_name not in mgmt.services_obj.delete_calls
    assert admin_secret in secrets_client.secrets


def test_deprovision_delete_data_only_drops_admin_secret(
    driver: AzureAISearchVectorDriver,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    admin_secret = f"astrolift-aisearch-{service_name}-admin"
    assert admin_secret in secrets_client.secrets

    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "data=dropped" in result.message
    assert admin_secret not in secrets_client.secrets


def test_deprovision_force_destroy_does_not_override_data_guard(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    admin_secret = f"astrolift-aisearch-{service_name}-admin"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert not result.ok
    assert result.retryable is False
    assert service_name not in mgmt.services_obj.delete_calls
    assert admin_secret in secrets_client.secrets


def test_deprovision_atomic_both_flags(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    admin_secret = f"astrolift-aisearch-{service_name}-admin"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "data=dropped" in result.message
    assert service_name in mgmt.services_obj.delete_calls
    assert admin_secret not in secrets_client.secrets


def test_deprovision_idempotent_when_already_gone(
    driver: AzureAISearchVectorDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="vector_index/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_does_not_depend_on_fictional_purge_api(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    mgmt.services_obj.purge_supported = False
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "data=dropped" in result.message


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AzureAISearchVectorDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="vector_index/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_succeeded_to_available(
    driver: AzureAISearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_maps_deleting_to_deprovisioning(
    driver: AzureAISearchVectorDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    mgmt.services_obj.services[service_name].provisioning_state = "Deleting"
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "deprovisioning"


# ---- binding ----------------------------------------------------


def test_binding_returns_envelope(
    driver: AzureAISearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    for key in (
        "AZURE_AI_SEARCH_ENDPOINT",
        "AZURE_AI_SEARCH_INDEX_NAME",
        "AZURE_AI_SEARCH_ADMIN_KEY",
    ):
        assert key in env
    assert env["AZURE_AI_SEARCH_ADMIN_KEY"].secret_ref is not None
    assert env["AZURE_AI_SEARCH_ADMIN_KEY"].secret_ref.startswith("azure-kv://kv.vault.azure.net/secrets/")
    assert env["AZURE_AI_SEARCH_ADMIN_KEY"].literal is None
    assert env["AZURE_AI_SEARCH_ENDPOINT"].literal.startswith("https://")
    assert env["AZURE_AI_SEARCH_ENDPOINT"].literal.endswith(
        ".search.windows.net",
    )


def test_binding_index_name_derived_from_service(
    driver: AzureAISearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    assert binding.env_vars["AZURE_AI_SEARCH_INDEX_NAME"].literal == _index_name_from_service(service_name=service_name)


def test_binding_iam_grants_cover_service_and_secret(
    driver: AzureAISearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert any("Microsoft.Search" in a for a in actions)
    assert any("KeyVault" in a for a in actions)


def test_binding_for_missing_raises(
    driver: AzureAISearchVectorDriver,
) -> None:
    with pytest.raises(AzureAISearchVectorError):
        driver.binding(ServiceHandle(handle="vector_index/never"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_is_explicitly_unsupported(
    driver: AzureAISearchVectorDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError, match="no service-level snapshot"):
        driver.snapshot(ServiceHandle(handle="vector_index/anything"))


def test_restore_is_explicitly_unsupported(
    driver: AzureAISearchVectorDriver,
) -> None:
    snap = SnapshotHandle(
        handle="vector_index/some-source",
        snapshot_id="snap-1",
        created_at="2026-05-15T00:00:00+00:00",
    )
    with pytest.raises(UnsupportedOperationError, match="explicit index export"):
        driver.restore(snap, _spec(service_handle_hint="failed"))


# ---- naming + helpers -------------------------------------------


def test_service_name_canonicalization(
    driver: AzureAISearchVectorDriver,
) -> None:
    name = driver._service_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="v",
        ),
    )
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    for c in name:
        assert c.isalnum() or c == "-"
    assert 2 <= len(name) <= 60
    assert name[0].isalpha()


def test_handle_round_trip(driver: AzureAISearchVectorDriver) -> None:
    handle = driver._handle_for(service_name="my-svc")  # type: ignore[attr-defined]
    assert handle.startswith(f"{KIND}/")
    assert (
        driver._service_name_from_handle(handle) == "my-svc"  # type: ignore[attr-defined]
    )


def test_handle_rejects_malformed(
    driver: AzureAISearchVectorDriver,
) -> None:
    with pytest.raises(AzureAISearchVectorError):
        driver._service_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(AzureAISearchVectorError):
        driver._service_name_from_handle("vector_index/")  # type: ignore[attr-defined]


def test_index_name_from_service_suffixes() -> None:
    assert (
        _index_name_from_service(
            service_name="astrolift-vec-acme-api-prod-v",
        )
        == "astrolift-vec-acme-api-prod-v-vec-idx"
    )


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: AzureAISearchVectorDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "sku_name",
        "replica_count",
        "partition_count",
        "public_network_access",
        "dimension",
        "algorithm",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AzureAISearchVectorDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "AZURE_AI_SEARCH_ENDPOINT",
        "AZURE_AI_SEARCH_INDEX_NAME",
        "AZURE_AI_SEARCH_ADMIN_KEY",
    ):
        assert key in schema.env_vars
