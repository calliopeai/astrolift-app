"""Contract tests for redis/azure_managed_redis."""

from __future__ import annotations

from types import SimpleNamespace
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
from _sdk.managed_service_tags import ownership_key
from azure.managed.managed_redis import (
    MANAGED_REDIS_SKUS,
    AzureManagedRedisConfig,
    AzureManagedRedisDriver,
    AzureManagedRedisError,
    _keyvault_secret_name,
    _redacted,
)


class ResourceNotFoundError(Exception):
    pass


class Poller:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    def result(self) -> Any:
        return self.value


class FakeRedisEnterprise:
    def __init__(self, owner: FakeManagement) -> None:
        self.owner = owner

    def get(self, *, resource_group_name: str, cluster_name: str) -> Any:
        self.owner.calls.append(("cluster.get", resource_group_name, cluster_name))
        if cluster_name not in self.owner.clusters:
            raise ResourceNotFoundError("ResourceNotFound")
        return self.owner.clusters[cluster_name]

    def begin_create(self, *, resource_group_name: str, cluster_name: str, parameters: Any) -> Poller:
        self.owner.calls.append(("cluster.create", resource_group_name, cluster_name, parameters))
        cluster = SimpleNamespace(
            host_name=f"{cluster_name}.eastus.redis.azure.net",
            provisioning_state="Succeeded",
            tags=dict(parameters.tags or {}),
        )
        self.owner.clusters[cluster_name] = cluster
        return Poller(cluster)

    def begin_update(self, *, resource_group_name: str, cluster_name: str, parameters: Any) -> Poller:
        self.owner.calls.append(("cluster.update", resource_group_name, cluster_name, parameters))
        return Poller(self.owner.clusters[cluster_name])

    def begin_delete(self, *, resource_group_name: str, cluster_name: str) -> Poller:
        self.owner.calls.append(("cluster.delete", resource_group_name, cluster_name))
        self.owner.clusters.pop(cluster_name, None)
        return Poller()


class FakeDatabases:
    def __init__(self, owner: FakeManagement) -> None:
        self.owner = owner

    def get(self, *, resource_group_name: str, cluster_name: str, database_name: str) -> Any:
        self.owner.calls.append(("database.get", resource_group_name, cluster_name, database_name))
        key = (cluster_name, database_name)
        if key not in self.owner.databases:
            raise ResourceNotFoundError("ResourceNotFound")
        return self.owner.databases[key]

    def begin_create(
        self,
        *,
        resource_group_name: str,
        cluster_name: str,
        database_name: str,
        parameters: Any,
    ) -> Poller:
        self.owner.calls.append(
            ("database.create", resource_group_name, cluster_name, database_name, parameters),
        )
        database = SimpleNamespace(port=10000, resource_state="Running", provisioning_state="Succeeded")
        self.owner.databases[(cluster_name, database_name)] = database
        return Poller(database)

    def begin_update(
        self,
        *,
        resource_group_name: str,
        cluster_name: str,
        database_name: str,
        parameters: Any,
    ) -> Poller:
        self.owner.calls.append(
            ("database.update", resource_group_name, cluster_name, database_name, parameters),
        )
        return Poller(self.owner.databases[(cluster_name, database_name)])

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        cluster_name: str,
        database_name: str,
    ) -> Poller:
        self.owner.calls.append(("database.delete", resource_group_name, cluster_name, database_name))
        self.owner.databases.pop((cluster_name, database_name), None)
        return Poller()

    def list_keys(self, *, resource_group_name: str, cluster_name: str, database_name: str) -> Any:
        self.owner.calls.append(("database.list_keys", resource_group_name, cluster_name, database_name))
        return SimpleNamespace(primary_key="primary:/?#", secondary_key="secondary-key")


class FakeAccessPolicies:
    def __init__(self, owner: FakeManagement) -> None:
        self.owner = owner

    def begin_create_update(
        self,
        *,
        resource_group_name: str,
        cluster_name: str,
        database_name: str,
        access_policy_assignment_name: str,
        parameters: Any,
    ) -> Poller:
        self.owner.calls.append(
            (
                "access_policy.create_update",
                resource_group_name,
                cluster_name,
                database_name,
                access_policy_assignment_name,
                parameters,
            ),
        )
        return Poller(parameters)


class FakeManagement:
    def __init__(self) -> None:
        self.clusters: dict[str, Any] = {}
        self.databases: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.redis_enterprise = FakeRedisEnterprise(self)
        self.databases = DatabaseStore(self)
        self.access_policy_assignment = FakeAccessPolicies(self)


class DatabaseStore(FakeDatabases):
    """Operation group and mapping storage for concise test fakes."""

    def __init__(self, owner: FakeManagement) -> None:
        super().__init__(owner)
        self._values: dict[tuple[str, str], Any] = {}

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._values

    def __getitem__(self, key: tuple[str, str]) -> Any:
        return self._values[key]

    def __setitem__(self, key: tuple[str, str], value: Any) -> None:
        self._values[key] = value

    def pop(self, key: tuple[str, str], default: Any = None) -> Any:
        return self._values.pop(key, default)


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []
        self.fail_delete = False

    def set_secret(self, name: str, value: str) -> None:
        self.values[name] = value

    def begin_delete_secret(self, name: str) -> Poller:
        if self.fail_delete:
            raise RuntimeError("vault unavailable")
        self.deleted.append(name)
        self.values.pop(name, None)
        return Poller()


def _spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="SteadyMD",
        app_id="app-id",
        app_slug="EMR-Triage",
        environment_id="env-id",
        environment_name="Production",
        tenant_cluster_id="cluster-id",
        service_handle_hint="Cache",
        size="small",
        config=config,
        tags={"owner": "triage"},
        binding_id="binding-id",
        managed_service_id="managed-service-id",
    )


REDIS_UAMI_ID = (
    "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/redis-cmk"
)
FOREIGN_UAMI_ID = (
    "/subscriptions/sub/resourceGroups/rg-other-tenant/providers/Microsoft.ManagedIdentity/"
    "userAssignedIdentities/their-identity"
)


def _driver() -> tuple[AzureManagedRedisDriver, FakeManagement, FakeSecrets]:
    management = FakeManagement()
    secrets = FakeSecrets()
    driver = AzureManagedRedisDriver(
        config=AzureManagedRedisConfig(
            subscription_id="subscription-id",
            resource_group="rg-platform",
            location="eastus",
            keyvault_url="https://vault.vault.azure.net",
            mgmt_client=management,
            secret_client=secrets,
            allowed_identity_resource_ids=(REDIS_UAMI_ID,),
        ),
    )
    return driver, management, secrets


def _provisioned() -> tuple[AzureManagedRedisDriver, FakeManagement, FakeSecrets, str]:
    driver, management, secrets = _driver()
    result = driver.provision(_spec())
    assert result.ok
    return driver, management, secrets, result.handle


def test_provision_creates_cluster_database_and_key_vault_binding() -> None:
    driver, management, secrets = _driver()

    result = driver.provision(
        _spec(
            sku_name="MemoryOptimized_M20",
            capacity=2,
            high_availability="Disabled",
            clustering_policy="EnterpriseCluster",
            eviction_policy="NoEviction",
            persistence={"rdb_enabled": True, "rdb_frequency": "6h"},
            modules=[{"name": "RediSearch", "args": "PARTITIONS 2"}],
            customer_managed_key_url="https://vault.vault.azure.net/keys/redis/version",
            customer_managed_identity_resource_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ManagedIdentity/"
                "userAssignedIdentities/redis-cmk"
            ),
            geo_replication={
                "group_nickname": "triage-global",
                "linked_database_ids": [
                    "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Cache/"
                    "redisEnterprise/peer/databases/default",
                ],
            },
            access_policy_assignments=[
                {
                    "name": "triage-reader",
                    "access_policy_name": "Data Contributor",
                    "object_id": "11111111-2222-3333-4444-555555555555",
                },
            ],
        ),
    )

    assert result.ok and result.ready
    assert result.handle.startswith("redis/astrolift-amr-steadymd-emr-triage-production-")
    cluster_call = next(call for call in management.calls if call[0] == "cluster.create")
    cluster = cluster_call[-1]
    assert cluster.location == "eastus"
    assert cluster.sku.name == "MemoryOptimized_M20"
    assert cluster.sku.capacity == 2
    assert cluster.high_availability == "Disabled"
    assert cluster.minimum_tls_version == "1.2"
    assert cluster.public_network_access == "Enabled"
    assert cluster.identity.type == "UserAssigned"
    assert cluster.encryption.customer_managed_key_encryption.key_encryption_key_url.endswith("/version")
    assert cluster.tags[ownership_key("azure", "binding")] == "binding-id"
    database_call = next(call for call in management.calls if call[0] == "database.create")
    database = database_call[-1]
    assert database.client_protocol == "Encrypted"
    assert database.port == 10000
    assert database.clustering_policy == "EnterpriseCluster"
    assert database.eviction_policy == "NoEviction"
    assert database.persistence.rdb_enabled is True
    assert database.persistence.rdb_frequency == "6h"
    assert database.modules[0].name == "RediSearch"
    assert database.geo_replication.group_nickname == "triage-global"
    assert database.geo_replication.linked_databases[0].id.endswith("/peer/databases/default")
    assert database.access_keys_authentication == "Enabled"
    policy = next(call for call in management.calls if call[0] == "access_policy.create_update")
    assert policy[4] == "triage-reader"
    assert policy[-1].user.object_id == "11111111-2222-3333-4444-555555555555"
    assert len(secrets.values) == 3
    assert any(value.startswith("rediss://:primary%3A%2F%3F%23@") for value in secrets.values.values())


def test_provision_existing_resources_reconciles_both_and_rotated_keys() -> None:
    driver, management, secrets, handle = _provisioned()
    management.calls.clear()
    secrets.values.clear()

    result = driver.provision(_spec(eviction_policy="AllKeysLFU"))

    assert result.ok and result.handle == handle
    assert any(call[0] == "cluster.update" for call in management.calls)
    assert any(call[0] == "database.update" for call in management.calls)
    assert any(call[0] == "database.list_keys" for call in management.calls)
    assert len(secrets.values) == 3


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"public_network_access": "Disabled"}, "Private Endpoint"),
        ({"access_keys_authentication": "Disabled"}, "Entra"),
        ({"client_protocol": "Plaintext"}, "Encrypted"),
        ({"minimum_tls_version": "1.1"}, "TLS"),
    ],
)
def test_unsafe_or_unbound_modes_fail_before_cloud_mutation(
    config: dict[str, Any],
    message: str,
) -> None:
    driver, management, _ = _driver()
    result = driver.provision(_spec(**config))
    assert not result.ok
    assert message in result.message
    assert management.calls == []


def test_a_cluster_cannot_unwrap_its_key_as_an_unlisted_identity() -> None:
    driver, management, _ = _driver()

    result = driver.provision(
        _spec(
            customer_managed_key_url="https://vault.vault.azure.net/keys/redis/version",
            customer_managed_identity_resource_id=FOREIGN_UAMI_ID,
        ),
    )

    assert not result.ok
    assert "managed_redis_allowed_identity_resource_ids" in result.message
    assert management.calls == []


def test_missing_key_vault_fails_before_cloud_mutation() -> None:
    management = FakeManagement()
    driver = AzureManagedRedisDriver(
        config=AzureManagedRedisConfig(
            subscription_id="sub",
            resource_group="rg",
            mgmt_client=management,
        ),
    )
    result = driver.provision(_spec())
    assert not result.ok
    assert result.errors == ["no_secret_backend"]
    assert management.calls == []


@pytest.mark.parametrize(
    "config",
    [
        {"customer_managed_key_url": "https://vault.vault.azure.net/keys/redis/version"},
        {
            "customer_managed_identity_resource_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ManagedIdentity/"
                "userAssignedIdentities/redis-cmk"
            ),
        },
        {
            "customer_managed_key_url": "not-https",
            "customer_managed_identity_resource_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ManagedIdentity/"
                "userAssignedIdentities/redis-cmk"
            ),
        },
    ],
)
def test_invalid_customer_managed_encryption_fails_before_cloud_mutation(
    config: dict[str, str],
) -> None:
    driver, management, _ = _driver()
    result = driver.provision(_spec(**config))
    assert not result.ok
    assert "customer" in result.message.lower()
    assert management.calls == []


@pytest.mark.parametrize(
    "config",
    [
        {"modules": [{}]},
        {"geo_replication": {"group_nickname": "global", "linked_database_ids": []}},
        {"access_policy_assignments": [{"name": "reader"}]},
        {"eviction_policy": "DeleteEverything"},
        {"persistence": {"rdb_frequency": "5m"}},
    ],
)
def test_invalid_database_controls_fail_before_creating_partial_resources(
    config: dict[str, Any],
) -> None:
    driver, management, _ = _driver()
    result = driver.provision(_spec(**config))
    assert not result.ok
    assert result.errors == ["invalid_runtime_controls"]
    assert management.calls == []


def test_unknown_sku_fails_before_cloud_mutation() -> None:
    driver, management, _ = _driver()
    result = driver.provision(_spec(sku_name="CheapAndImaginary_C1"))
    assert not result.ok
    assert result.errors == ["invalid_sku"]
    assert management.calls == []


def test_update_scales_cluster_updates_database_and_refreshes_secrets() -> None:
    driver, management, secrets, handle = _provisioned()
    management.calls.clear()
    secrets.values.clear()

    result = driver.update(
        UpdateSpec(
            handle=handle,
            size="large",
            config={
                "high_availability": "Enabled",
                "eviction_policy": "AllKeysLFU",
                "persistence": {"aof_enabled": True, "aof_frequency": "1s"},
            },
            managed_service_id="managed-service-id",
        ),
    )

    assert result.ok
    cluster_update = next(call[-1] for call in management.calls if call[0] == "cluster.update")
    assert cluster_update.sku.name == "Balanced_B50"
    database_update = next(call[-1] for call in management.calls if call[0] == "database.update")
    assert database_update.eviction_policy == "AllKeysLFU"
    assert database_update.persistence.aof_enabled is True
    assert len(secrets.values) == 3


def test_partial_update_does_not_silently_downsize_or_reset_database_shape() -> None:
    driver, management, _, handle = _provisioned()
    management.calls.clear()

    result = driver.update(
        UpdateSpec(
            handle=handle,
            config={
                "high_availability": "Disabled",
                "eviction_policy": "NoEviction",
                "customer_managed_key_url": "https://vault.vault.azure.net/keys/redis/new-version",
                "customer_managed_identity_resource_id": (
                    "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ManagedIdentity/"
                    "userAssignedIdentities/redis-cmk"
                ),
            },
            managed_service_id="managed-service-id",
        ),
    )

    assert result.ok
    cluster_update = next(call[-1] for call in management.calls if call[0] == "cluster.update")
    assert cluster_update.sku is None
    assert cluster_update.high_availability == "Disabled"
    assert cluster_update.identity.type == "UserAssigned"
    assert cluster_update.encryption.customer_managed_key_encryption.key_encryption_key_url.endswith(
        "/new-version",
    )
    database_update = next(call[-1] for call in management.calls if call[0] == "database.update")
    assert database_update.eviction_policy == "NoEviction"
    assert database_update.clustering_policy is None
    assert database_update.client_protocol is None


def test_immutable_database_shape_requires_reprovision() -> None:
    driver, management, _, handle = _provisioned()
    management.calls.clear()

    result = driver.update(
        UpdateSpec(handle=handle, config={"clustering_policy": "NoCluster"}, managed_service_id="managed-service-id")
    )

    assert not result.ok
    assert result.errors == ["reprovision_required"]
    assert "clustering_policy" in result.message
    assert not any(call[0].endswith("update") for call in management.calls)


def test_provision_reconciliation_rejects_clustering_policy_drift() -> None:
    driver, management, _, handle = _provisioned()
    cluster_name, database_name = handle.split("/")[1:]
    management.databases[(cluster_name, database_name)].clustering_policy = "EnterpriseCluster"

    result = driver.provision(_spec(clustering_policy="OSSCluster"))

    assert not result.ok
    assert "reprovision" in result.message


def test_update_reports_missing_resource() -> None:
    driver, _, _ = _driver()
    result = driver.update(
        UpdateSpec(handle="redis/missing/default", size="medium", managed_service_id="managed-service-id")
    )
    assert not result.ok
    assert result.errors == ["not_found"]


def test_status_composes_cluster_and_database_state() -> None:
    driver, management, _, handle = _provisioned()
    cluster_name, database_name = handle.split("/")[1:]
    management.clusters[cluster_name].provisioning_state = "Succeeded"
    management.databases[(cluster_name, database_name)].resource_state = "Updating"
    status = driver.status(ServiceHandle(handle))
    assert status.state == "updating"
    assert "cluster=Succeeded" in status.message
    assert "database=Updating" in status.message


def test_status_detects_partial_and_absent_resource() -> None:
    driver, management, _, handle = _provisioned()
    cluster_name, database_name = handle.split("/")[1:]
    management.databases.pop((cluster_name, database_name))
    assert driver.status(ServiceHandle(handle)).state == "error"
    management.clusters.pop(cluster_name)
    assert driver.status(ServiceHandle(handle)).state == "deprovisioned"


def test_binding_refreshes_keys_and_returns_only_secret_references() -> None:
    driver, _, secrets, handle = _provisioned()
    secrets.values.clear()

    binding = driver.binding(ServiceHandle(handle))

    assert binding.env_vars["REDIS_TLS"].literal == "1"
    assert binding.env_vars["REDIS_PORT"].literal == "10000"
    assert binding.env_vars["REDIS_AUTH_TOKEN"].secret_ref
    assert binding.env_vars["REDIS_AUTH_TOKEN"].literal is None
    assert binding.env_vars["REDIS_URL"].secret_ref
    assert len(binding.iam_grants) == 4
    assert len(secrets.values) == 3


def test_binding_rejects_partial_resource() -> None:
    driver, management, _, handle = _provisioned()
    cluster_name, database_name = handle.split("/")[1:]
    management.databases.pop((cluster_name, database_name))
    with pytest.raises(AzureManagedRedisError, match="missing"):
        driver.binding(ServiceHandle(handle))


def test_default_teardown_refuses_unverifiable_data_loss() -> None:
    driver, management, _, handle = _provisioned()
    management.calls.clear()

    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id="managed-service-id"))

    assert not result.ok and not result.retryable
    assert result.errors == ["data_preserving_teardown_unsupported"]
    assert not any(call[0].endswith("delete") for call in management.calls)


def test_destructive_teardown_deletes_database_cluster_and_all_secrets() -> None:
    driver, management, secrets, handle = _provisioned()
    management.calls.clear()

    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id="managed-service-id"), delete_data=True)

    assert result.ok
    assert [call[0] for call in management.calls if call[0].endswith("delete")] == [
        "database.delete",
        "cluster.delete",
    ]
    assert len(secrets.deleted) == 3


def test_idempotent_teardown_does_not_hide_secret_cleanup_failure() -> None:
    driver, management, secrets, handle = _provisioned()
    cluster_name, database_name = handle.split("/")[1:]
    management.databases.pop((cluster_name, database_name))
    management.clusters.pop(cluster_name)
    secrets.fail_delete = True

    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id="managed-service-id"), delete_data=True)

    assert not result.ok
    assert "credential cleanup failed" in result.message


def test_idempotent_teardown_requires_the_configured_secret_backend() -> None:
    management = FakeManagement()
    driver = AzureManagedRedisDriver(
        config=AzureManagedRedisConfig(
            subscription_id="sub",
            resource_group="rg",
            mgmt_client=management,
        ),
    )
    result = driver.deprovision(
        DeprovisionSpec("redis/already-gone/default", managed_service_id="managed-service-id"),
        delete_data=True,
    )
    assert not result.ok
    assert "requires a Key Vault" in result.message


def test_snapshot_and_restore_are_honestly_unsupported() -> None:
    driver, _, _, handle = _provisioned()
    with pytest.raises(UnsupportedOperationError, match="blob"):
        driver.snapshot(ServiceHandle(handle))
    with pytest.raises(UnsupportedOperationError, match="blob SAS"):
        driver.restore(
            SnapshotHandle(handle, "snapshot", "2026-08-14T00:00:00Z"),
            _spec(),
        )


def test_schema_exposes_every_current_product_tier_and_fail_closed_option() -> None:
    driver, _, _ = _driver()
    schema = driver.config_schema()
    assert set(schema["properties"]["sku_name"]["enum"]) == set(MANAGED_REDIS_SKUS)
    assert {sku.split("_")[0] for sku in MANAGED_REDIS_SKUS} == {
        "Balanced",
        "MemoryOptimized",
        "ComputeOptimized",
        "FlashOptimized",
    }
    assert schema["properties"]["public_network_access"]["enum"] == ["Enabled", "Disabled"]
    assert "rejected" in schema["properties"]["public_network_access"]["description"]
    assert "customer_managed_key_url" in schema["properties"]
    assert "geo_replication" in schema["properties"]
    assert "access_policy_assignments" in schema["properties"]
    assert "REDIS_URL" in driver.binding_schema().env_vars
    assert "clustering_policy" not in driver.editable_fields()


def test_sdk_operation_surface_and_typed_serialization_match_driver() -> None:
    from azure.mgmt.redisenterprise.models import Cluster, Database
    from azure.mgmt.redisenterprise.operations import DatabasesOperations, RedisEnterpriseOperations

    assert all(
        hasattr(RedisEnterpriseOperations, name) for name in ("get", "begin_create", "begin_update", "begin_delete")
    )
    assert all(
        hasattr(DatabasesOperations, name)
        for name in ("get", "begin_create", "begin_update", "begin_delete", "list_keys")
    )
    driver, _, _ = _driver()
    cluster = driver._cluster_parameters(_spec())
    database = driver._database_parameters({})
    assert isinstance(cluster, Cluster)
    assert isinstance(database, Database)
    assert cluster.serialize()["properties"]["minimumTlsVersion"] == "1.2"
    assert cluster.serialize()["properties"]["publicNetworkAccess"] == "Enabled"
    assert database.serialize()["properties"]["clientProtocol"] == "Encrypted"


def test_names_are_bounded_stable_and_collision_resistant() -> None:
    driver, _, _ = _driver()
    first = driver._cluster_name_for(_spec())
    second = driver._cluster_name_for(_spec())
    other = driver._cluster_name_for(
        ProvisionSpec(**{**_spec().__dict__, "managed_service_id": "other-managed-service"}),
    )
    assert first == second
    assert first != other
    assert len(first) <= 60
    long_secret = _keyvault_secret_name("x" * 300, suffix="primary")
    assert len(long_secret) <= 127
    assert long_secret.endswith(_keyvault_secret_name("x" * 300, suffix="primary")[-10:])


def test_sas_query_is_redacted_from_provider_errors() -> None:
    result = _redacted(RuntimeError("export https://storage/container?sig=top-secret failed"))
    assert "top-secret" not in result
    assert "?[REDACTED]" in result


def test_invalid_config_defaults_fail_at_construction() -> None:
    with pytest.raises(ValueError, match="SKU"):
        AzureManagedRedisConfig("sub", "rg", default_sku="Imaginary")
    with pytest.raises(ValueError, match="TLS"):
        AzureManagedRedisConfig("sub", "rg", minimum_tls_version_default="1.1")
