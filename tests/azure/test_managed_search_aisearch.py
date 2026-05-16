"""Tests for AzureAISearchFullTextDriver (#373).

Mirrors the AzureCacheRedisDriver test surface: provision
(idempotent, tagged, admin keys persisted to Key Vault), update,
deprovision four-corner matrix (delete_data x force_destroy with
the soft-delete-purge axis), status state-mapping, binding env-var
shape, snapshot + restore.

Uses in-process fakes for the SearchManagementClient + Key Vault
SecretClient — no Azure account required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from azure.managed.search_aisearch import (
    KIND,
    AzureAISearchConfig,
    AzureAISearchError,
    AzureAISearchFullTextDriver,
    _index_name_for,
)

# ---- fakes ------------------------------------------------------


class _NotFound(Exception):  # noqa: N818
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
    provisioning_state: str = "succeeded"
    sku: dict[str, Any] = field(default_factory=dict)
    replica_count: int = 1
    partition_count: int = 1
    public_network_access: str = "enabled"
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeAdminKeys:
    primary_key: str = "primary-admin-key-zzzzzzzzzzzzzzzz"
    secondary_key: str = "secondary-admin-key-yyyyyyyyyyyy"


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
            {"name": search_service_name, "parameters": service},
        )
        svc = FakeSearchService(
            name=search_service_name,
            provisioning_state="succeeded",
            sku=dict(service.get("sku", {})),
            replica_count=int(service.get("replica_count", 1)),
            partition_count=int(service.get("partition_count", 1)),
            public_network_access=service.get(
                "public_network_access", "enabled",
            ),
            properties=dict(service),
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
            {"name": search_service_name, "parameters": service},
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

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        search_service_name: str,
    ) -> FakePoller:
        self.delete_calls.append(search_service_name)
        if search_service_name not in self.services:
            raise _NotFound(search_service_name)
        del self.services[search_service_name]
        return FakePoller(value=None)

    def begin_purge(
        self,
        *,
        resource_group_name: str,
        search_service_name: str,
    ) -> FakePoller:
        if not self.purge_supported:
            raise RuntimeError("purge not available in this region")
        self.purge_calls.append(search_service_name)
        return FakePoller(value=None)


@dataclass
class FakeAdminKeysOperations:
    fail: bool = False

    def get(
        self,
        *,
        resource_group_name: str,
        search_service_name: str,
    ) -> FakeAdminKeys:
        if self.fail:
            raise RuntimeError("admin_keys.get boom")
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


# ---- fixtures ---------------------------------------------------


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
) -> AzureAISearchFullTextDriver:
    return AzureAISearchFullTextDriver(
        config=AzureAISearchConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=secrets_client,
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
        service_handle_hint="search",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_service(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert result.handle.startswith(f"{KIND}/")
    assert len(mgmt.services_obj.services) == 1


def test_provision_defaults_to_basic_sku(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    create = mgmt.services_obj.create_calls[0]
    assert create["parameters"]["sku"]["name"] == "basic"
    assert create["parameters"]["replica_count"] == 1
    assert create["parameters"]["partition_count"] == 1


def test_provision_idempotent(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    assert len(mgmt.services_obj.create_calls) == 1


def test_provision_persists_admin_keys_in_key_vault(
    driver: AzureAISearchFullTextDriver,
    secrets_client: FakeSecretClient,
) -> None:
    result = driver.provision(_spec())
    service_name = result.handle.split("/", 1)[1]
    primary = f"astrolift-search-{service_name}-primary"
    secondary = f"astrolift-search-{service_name}-secondary"
    assert primary in secrets_client.secrets
    assert secondary in secrets_client.secrets
    assert secrets_client.secrets[primary]
    assert secrets_client.secrets[secondary]


def test_provision_honours_size_to_sku(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec(size="xlarge"))
    create = mgmt.services_obj.create_calls[0]
    assert create["parameters"]["sku"]["name"] == "storage_optimized_l2"


def test_provision_honours_spec_config_override(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(
        _spec(
            config={
                "sku": "standard3",
                "replica_count": 3,
                "partition_count": 2,
                "public_network_access": "disabled",
                "hosting_mode": "highDensity",
            },
        ),
    )
    params = mgmt.services_obj.create_calls[0]["parameters"]
    assert params["sku"]["name"] == "standard3"
    assert params["replica_count"] == 3
    assert params["partition_count"] == 2
    assert params["public_network_access"] == "disabled"
    assert params["hosting_mode"] == "highDensity"


def test_provision_tags_service_with_astrolift_namespace(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    tags = mgmt.services_obj.create_calls[0]["parameters"]["tags"]
    assert tags["astrolift.io/managed-by"] == "platform"
    assert tags["astrolift.io/app"] == "api"
    assert tags["astrolift.io/environment"] == "prod"


def test_provision_without_keyvault_returns_error() -> None:
    d = AzureAISearchFullTextDriver(
        config=AzureAISearchConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            mgmt_client=FakeMgmtClient(),
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "Key Vault" in result.message


def test_provision_surfaces_admin_key_fetch_failure(
    secrets_client: FakeSecretClient,
) -> None:
    mgmt = FakeMgmtClient(admin_keys_obj=FakeAdminKeysOperations(fail=True))
    d = AzureAISearchFullTextDriver(
        config=AzureAISearchConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=secrets_client,
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "admin_keys.get" in result.message


# ---- update -----------------------------------------------------


def test_update_resize(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="medium"),
    )
    assert result.ok
    last = mgmt.services_obj.update_calls[-1]
    assert last["parameters"]["sku"]["name"] == "standard"


def test_update_replica_partition_counts(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"replica_count": 4, "partition_count": 2},
        ),
    )
    assert result.ok
    last = mgmt.services_obj.update_calls[-1]
    assert last["parameters"]["replica_count"] == 4
    assert last["parameters"]["partition_count"] == 2


def test_update_noop_when_nothing_to_change(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    before = len(mgmt.services_obj.update_calls)
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message
    assert len(mgmt.services_obj.update_calls) == before


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_keeps_soft_delete_and_keys(
    driver: AzureAISearchFullTextDriver,
    secrets_client: FakeSecretClient,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    primary = f"astrolift-search-{service_name}-primary"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "purged=soft-delete" in result.message
    # Admin keys retained on retained-data path
    assert primary in secrets_client.secrets
    assert service_name in mgmt.services_obj.delete_calls
    assert service_name not in mgmt.services_obj.purge_calls


def test_deprovision_delete_data_purges_keys(
    driver: AzureAISearchFullTextDriver,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    primary = f"astrolift-search-{service_name}-primary"
    secondary = f"astrolift-search-{service_name}-secondary"
    assert primary in secrets_client.secrets

    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert primary not in secrets_client.secrets
    assert secondary not in secrets_client.secrets


def test_deprovision_force_destroy_only_purges_soft_delete(
    driver: AzureAISearchFullTextDriver,
    secrets_client: FakeSecretClient,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    primary = f"astrolift-search-{service_name}-primary"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert result.ok
    assert "purged=yes" in result.message
    assert service_name in mgmt.services_obj.purge_calls
    # Keys retained on the retained-data path even under force_destroy
    assert primary in secrets_client.secrets


def test_deprovision_atomic_both_flags(
    driver: AzureAISearchFullTextDriver,
    secrets_client: FakeSecretClient,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    primary = f"astrolift-search-{service_name}-primary"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "purged=yes" in result.message
    assert service_name in mgmt.services_obj.purge_calls
    assert primary not in secrets_client.secrets


def test_deprovision_idempotent_when_already_gone(
    driver: AzureAISearchFullTextDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="search/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_treats_mid_modify_as_retryable_without_force(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())

    def boom(**_kwargs):
        raise RuntimeError("ServiceNotInDesiredState: scaling")

    mgmt.services_obj.begin_delete = boom  # type: ignore[assignment]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert not result.ok
    assert "mid-modify" in result.message


def test_deprovision_force_destroy_bypasses_mid_modify_message(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())

    def boom(**_kwargs):
        raise RuntimeError("ServiceNotInDesiredState: scaling")

    mgmt.services_obj.begin_delete = boom  # type: ignore[assignment]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert not result.ok
    assert "mid-modify" not in result.message


def test_deprovision_handles_purge_unavailable(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    """Some regions / SDK versions don't expose begin_purge.
    Force-destroy must still succeed at the delete step."""
    mgmt.services_obj.purge_supported = False
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert result.ok
    assert "purged=soft-delete" in result.message


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AzureAISearchFullTextDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="search/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_succeeded_to_available(
    driver: AzureAISearchFullTextDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_maps_provisioning(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    service_name = provisioned.handle.split("/", 1)[1]
    mgmt.services_obj.services[service_name].provisioning_state = (
        "provisioning"
    )
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "provisioning"


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: AzureAISearchFullTextDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    for key in (
        "SEARCH_URL",
        "SEARCH_API_KEY",
        "SEARCH_INDEX_PREFIX",
        "AZURE_SEARCH_ENDPOINT",
        "AZURE_SEARCH_INDEX_NAME",
        "AZURE_SEARCH_ADMIN_KEY",
        "AZURE_SEARCH_SECONDARY_ADMIN_KEY",
    ):
        assert key in env
    assert env["SEARCH_API_KEY"].secret_ref is not None
    assert env["SEARCH_API_KEY"].literal is None
    assert env["AZURE_SEARCH_SECONDARY_ADMIN_KEY"].secret_ref is not None
    assert env["SEARCH_URL"].literal.startswith("https://")
    assert env["SEARCH_URL"].literal.endswith(".search.windows.net")


def test_binding_iam_grants_cover_service_and_secret(
    driver: AzureAISearchFullTextDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert any("Microsoft.Search" in a for a in actions)
    assert any("KeyVault" in a for a in actions)


def test_binding_for_missing_raises(
    driver: AzureAISearchFullTextDriver,
) -> None:
    with pytest.raises(AzureAISearchError):
        driver.binding(ServiceHandle(handle="search/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_returns_deterministic_id(
    driver: AzureAISearchFullTextDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    service_name = provisioned.handle.split("/", 1)[1]
    assert snap.snapshot_id.startswith(service_name)


def test_snapshot_for_missing_raises(
    driver: AzureAISearchFullTextDriver,
) -> None:
    with pytest.raises(AzureAISearchError):
        driver.snapshot(ServiceHandle(handle="search/missing"))


def test_restore_provisions_target_service(
    driver: AzureAISearchFullTextDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))

    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    target_name = result.handle.split("/", 1)[1]
    assert target_name in mgmt.services_obj.services


# ---- naming + helpers -------------------------------------------


def test_service_name_canonicalization(
    driver: AzureAISearchFullTextDriver,
) -> None:
    name = driver._service_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="search",
        ),
    )
    assert 2 <= len(name) <= 60
    assert name[0].isalpha()
    for c in name:
        assert c.islower() or c.isdigit() or c == "-"
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")


def test_handle_round_trip(
    driver: AzureAISearchFullTextDriver,
) -> None:
    handle = driver._handle_for(  # type: ignore[attr-defined]
        service_name="my-search",
    )
    assert handle.startswith(f"{KIND}/")
    assert driver._service_name_from_handle(handle) == "my-search"  # type: ignore[attr-defined]


def test_handle_rejects_malformed(
    driver: AzureAISearchFullTextDriver,
) -> None:
    with pytest.raises(AzureAISearchError):
        driver._service_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(AzureAISearchError):
        driver._service_name_from_handle("search/")  # type: ignore[attr-defined]


def test_index_name_for_swaps_hyphens_to_underscores() -> None:
    assert _index_name_for(service_name="acme-prod-search") == (
        "acme_prod_search"
    )


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(
    driver: AzureAISearchFullTextDriver,
) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "sku",
        "replica_count",
        "partition_count",
        "public_network_access",
        "hosting_mode",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AzureAISearchFullTextDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "SEARCH_URL",
        "SEARCH_API_KEY",
        "SEARCH_INDEX_PREFIX",
        "AZURE_SEARCH_ENDPOINT",
        "AZURE_SEARCH_INDEX_NAME",
        "AZURE_SEARCH_ADMIN_KEY",
        "AZURE_SEARCH_SECONDARY_ADMIN_KEY",
    ):
        assert key in schema.env_vars
