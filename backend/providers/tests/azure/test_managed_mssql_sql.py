from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from azure.managed.mssql_sql import (
    AzureSQLDatabaseConfig,
    AzureSQLDatabaseDriver,
    AzureSQLManagedInstanceConfig,
    AzureSQLManagedInstanceDriver,
)


def _param(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class ResourceNotFoundError(Exception):
    pass


@dataclass
class Poller:
    value: Any = None

    def result(self) -> Any:
        return self.value


@dataclass
class FakeSecrets:
    values: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    fail_delete: bool = False

    def set_secret(self, name: str, value: str) -> None:
        self.values[name] = value

    def get_secret(self, name: str) -> SimpleNamespace:
        if name not in self.values:
            raise ResourceNotFoundError(name)
        return SimpleNamespace(value=self.values[name])

    def begin_delete_secret(self, name: str) -> Poller:
        if self.fail_delete:
            raise RuntimeError("key vault unavailable")
        self.values.pop(name, None)
        self.deleted.append(name)
        return Poller()


@dataclass
class FakeServers:
    rows: dict[str, SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def get(self, *, resource_group_name: str, server_name: str) -> SimpleNamespace:
        if server_name not in self.rows:
            raise ResourceNotFoundError(server_name)
        return self.rows[server_name]

    def begin_create_or_update(
        self,
        *,
        resource_group_name: str,
        server_name: str,
        parameters: dict[str, Any],
    ) -> Poller:
        self.create_calls.append({"name": server_name, "parameters": parameters})
        row = SimpleNamespace(
            name=server_name,
            fully_qualified_domain_name=f"{server_name}.database.windows.net",
            tags=_param(parameters, "tags", {}),
        )
        self.rows[server_name] = row
        return Poller(row)

    def begin_delete(self, *, resource_group_name: str, server_name: str) -> Poller:
        self.delete_calls.append(server_name)
        self.rows.pop(server_name, None)
        return Poller()


@dataclass
class FakeDatabases:
    rows: dict[tuple[str, str], SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[tuple[str, str]] = field(default_factory=list)
    fail_copy: bool = False

    def get(
        self,
        *,
        resource_group_name: str,
        server_name: str,
        database_name: str,
    ) -> SimpleNamespace:
        try:
            return self.rows[(server_name, database_name)]
        except KeyError as exc:
            raise ResourceNotFoundError(database_name) from exc

    def begin_create_or_update(
        self,
        *,
        resource_group_name: str,
        server_name: str,
        database_name: str,
        parameters: dict[str, Any],
    ) -> Poller:
        if self.fail_copy and _param(parameters, "create_mode") == "Copy":
            raise RuntimeError("copy quota exceeded")
        self.create_calls.append(
            {"server": server_name, "database": database_name, "parameters": parameters},
        )
        row = SimpleNamespace(
            name=database_name,
            status="Online",
            tags=_param(parameters, "tags", {}),
            id=(
                f"/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Sql/servers/"
                f"{server_name}/databases/{database_name}"
            ),
        )
        self.rows[(server_name, database_name)] = row
        return Poller(row)

    def begin_update(
        self,
        *,
        resource_group_name: str,
        server_name: str,
        database_name: str,
        parameters: dict[str, Any],
    ) -> Poller:
        row = self.get(
            resource_group_name=resource_group_name,
            server_name=server_name,
            database_name=database_name,
        )
        self.update_calls.append(
            {"server": server_name, "database": database_name, "parameters": parameters},
        )
        return Poller(row)

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        server_name: str,
        database_name: str,
    ) -> Poller:
        self.delete_calls.append((server_name, database_name))
        self.rows.pop((server_name, database_name), None)
        return Poller()

    def list_by_server(self, *, resource_group_name: str, server_name: str) -> list[SimpleNamespace]:
        return [row for (candidate, _), row in self.rows.items() if candidate == server_name]


@dataclass
class FakeRetention:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def begin_create_or_update(self, **kwargs: Any) -> Poller:
        self.calls.append(kwargs)
        return Poller()


@dataclass
class FakeVirtualNetworkRules:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def begin_create_or_update(self, **kwargs: Any) -> Poller:
        self.calls.append(kwargs)
        return Poller()


@dataclass
class FakeManagedInstances:
    rows: dict[str, SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def get(self, *, resource_group_name: str, managed_instance_name: str) -> SimpleNamespace:
        if managed_instance_name not in self.rows:
            raise ResourceNotFoundError(managed_instance_name)
        return self.rows[managed_instance_name]

    def begin_create_or_update(
        self,
        *,
        resource_group_name: str,
        managed_instance_name: str,
        parameters: dict[str, Any],
    ) -> Poller:
        self.create_calls.append({"name": managed_instance_name, "parameters": parameters})
        row = SimpleNamespace(
            name=managed_instance_name,
            state="Ready",
            fully_qualified_domain_name=f"{managed_instance_name}.private.database.windows.net",
        )
        self.rows[managed_instance_name] = row
        return Poller(row)

    def begin_update(
        self,
        *,
        resource_group_name: str,
        managed_instance_name: str,
        parameters: dict[str, Any],
    ) -> Poller:
        self.update_calls.append({"name": managed_instance_name, "parameters": parameters})
        return Poller(self.rows[managed_instance_name])

    def begin_delete(self, *, resource_group_name: str, managed_instance_name: str) -> Poller:
        self.delete_calls.append(managed_instance_name)
        self.rows.pop(managed_instance_name, None)
        return Poller()


@dataclass
class FakeManagedDatabases:
    rows: dict[tuple[str, str], SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[tuple[str, str]] = field(default_factory=list)

    def get(
        self,
        *,
        resource_group_name: str,
        managed_instance_name: str,
        database_name: str,
    ) -> SimpleNamespace:
        try:
            return self.rows[(managed_instance_name, database_name)]
        except KeyError as exc:
            raise ResourceNotFoundError(database_name) from exc

    def begin_create_or_update(
        self,
        *,
        resource_group_name: str,
        managed_instance_name: str,
        database_name: str,
        parameters: dict[str, Any],
    ) -> Poller:
        self.create_calls.append(
            {"instance": managed_instance_name, "database": database_name, "parameters": parameters},
        )
        row = SimpleNamespace(name=database_name, status="Online")
        self.rows[(managed_instance_name, database_name)] = row
        return Poller(row)

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        managed_instance_name: str,
        database_name: str,
    ) -> Poller:
        self.delete_calls.append((managed_instance_name, database_name))
        self.rows.pop((managed_instance_name, database_name), None)
        return Poller()


@dataclass
class FakeMgmt:
    servers: FakeServers = field(default_factory=FakeServers)
    databases: FakeDatabases = field(default_factory=FakeDatabases)
    backup_short_term_retention_policies: FakeRetention = field(default_factory=FakeRetention)
    virtual_network_rules: FakeVirtualNetworkRules = field(default_factory=FakeVirtualNetworkRules)
    managed_instances: FakeManagedInstances = field(default_factory=FakeManagedInstances)
    managed_databases: FakeManagedDatabases = field(default_factory=FakeManagedDatabases)
    managed_backup_short_term_retention_policies: FakeRetention = field(default_factory=FakeRetention)


def _spec(**overrides: Any) -> ProvisionSpec:
    values: dict[str, Any] = {
        "organization_id": "org-id",
        "organization_slug": "acme",
        "app_id": "app-id",
        "app_slug": "api",
        "environment_id": "env-id",
        "environment_name": "prod",
        "tenant_cluster_id": "azure-prod",
        "service_handle_hint": "orders",
        "size": "small",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _database_driver(
    variant: str = "azure_sql_database",
) -> tuple[AzureSQLDatabaseDriver, FakeMgmt, FakeSecrets]:
    mgmt = FakeMgmt()
    secret_client = FakeSecrets()
    driver = AzureSQLDatabaseDriver(
        config=AzureSQLDatabaseConfig(
            subscription_id="sub",
            resource_group="rg",
            variant=variant,
            keyvault_url="https://kv.vault.azure.net",
            virtual_network_subnet_id="/subscriptions/sub/subnets/aks",
            mgmt_client=mgmt,
            secret_client=secret_client,
        ),
    )
    return driver, mgmt, secret_client


def _mi_driver(
    *, subnet_id: str = "/subscriptions/sub/subnets/sql-mi"
) -> tuple[
    AzureSQLManagedInstanceDriver,
    FakeMgmt,
    FakeSecrets,
]:
    mgmt = FakeMgmt()
    secret_client = FakeSecrets()
    driver = AzureSQLManagedInstanceDriver(
        config=AzureSQLManagedInstanceConfig(
            subscription_id="sub",
            resource_group="rg",
            subnet_id=subnet_id,
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=secret_client,
        ),
    )
    return driver, mgmt, secret_client


@pytest.mark.parametrize(
    ("variant", "sku", "tier"),
    [
        ("azure_sql_database", "GP_Gen5_2", "GeneralPurpose"),
        ("azure_sql_serverless", "GP_S_Gen5_1", "GeneralPurpose"),
        ("azure_sql_hyperscale", "HS_Gen5_2", "Hyperscale"),
    ],
)
def test_sql_database_profiles_are_real_variants(variant: str, sku: str, tier: str) -> None:
    driver, mgmt, _ = _database_driver(variant)
    result = driver.provision(_spec())
    assert result.ok and result.ready
    params = mgmt.databases.create_calls[0]["parameters"]
    assert params.sku.name == sku
    assert params.sku.tier == tier


def test_sql_database_server_uses_selected_vnet_tls_and_secret_refs() -> None:
    driver, mgmt, secrets_client = _database_driver()
    result = driver.provision(_spec())
    assert result.ok
    server = mgmt.servers.create_calls[0]["parameters"]
    assert server.public_network_access == "Enabled"
    assert server.minimal_tls_version == "1.2"
    rule = mgmt.virtual_network_rules.calls[0]["parameters"]
    assert rule.virtual_network_subnet_id.endswith("/aks")
    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["MSSQL_PASSWORD"].secret_ref
    assert binding.env_vars["MSSQL_PASSWORD"].literal is None
    assert binding.env_vars["DATABASE_URL"].secret_ref in secrets_client.values
    assert "trustServerCertificate=false" in next(
        value for key, value in secrets_client.values.items() if key.endswith("-url")
    )


def test_sql_database_serverless_controls_are_applied() -> None:
    driver, mgmt, _ = _database_driver("azure_sql_serverless")
    driver.provision(_spec(config={"auto_pause_delay": 720, "min_capacity": 1.0}))
    params = mgmt.databases.create_calls[0]["parameters"]
    assert params.auto_pause_delay == 720
    assert params.min_capacity == 1.0


def test_sql_database_sets_short_term_retention() -> None:
    driver, mgmt, _ = _database_driver()
    driver.provision(_spec(config={"backup_retention_days": 21, "diff_backup_interval_in_hours": 24}))
    call = mgmt.backup_short_term_retention_policies.calls[0]
    assert call["parameters"].retention_days == 21
    assert call["parameters"].diff_backup_interval_in_hours == 24


def test_sql_database_provision_is_idempotent() -> None:
    driver, mgmt, secret_client = _database_driver()
    first = driver.provision(_spec())
    url_secret = next(name for name in secret_client.values if name.endswith("-url"))
    secret_client.values.pop(url_secret)
    second = driver.provision(_spec())
    assert first.handle == second.handle
    assert "reconciled" in second.message
    assert len(mgmt.databases.create_calls) == 1
    assert url_secret in secret_client.values
    assert len(mgmt.virtual_network_rules.calls) == 2
    assert len(mgmt.backup_short_term_retention_policies.calls) == 2


def test_sql_database_global_name_uses_stable_managed_service_entropy() -> None:
    driver, _, _ = _database_driver()
    first = driver._server_name_for(_spec(managed_service_id="00000000-0000-0000-0000-000000000001"))
    repeated = driver._server_name_for(_spec(managed_service_id="00000000-0000-0000-0000-000000000001"))
    second = driver._server_name_for(_spec(managed_service_id="00000000-0000-0000-0000-000000000002"))
    assert first == repeated
    assert first != second
    assert len(first) <= 63


def test_sql_database_key_vault_names_are_bounded_and_collision_safe() -> None:
    driver, _, _ = _database_driver()
    server_name = "s" * 63
    first = driver._url_secret(server_name, "d" * 127 + "-one")
    second = driver._url_secret(server_name, "d" * 127 + "-two")
    assert len(first) <= 127
    assert first.endswith("-url")
    assert first != second


def test_sql_database_snapshot_is_an_actual_database_copy_and_restores() -> None:
    driver, mgmt, _ = _database_driver()
    source = driver.provision(_spec())
    snapshot = driver.snapshot(ServiceHandle(source.handle))
    copy_call = mgmt.databases.create_calls[-1]
    assert copy_call["parameters"].create_mode == "Copy"
    assert snapshot.snapshot_id.endswith(copy_call["database"])
    restored = driver.restore(snapshot, _spec(service_handle_hint="restored"))
    assert restored.ok
    restore_call = mgmt.databases.create_calls[-1]
    assert restore_call["parameters"].source_database_id == snapshot.snapshot_id


def test_sql_database_non_destructive_delete_fails_closed_if_copy_fails() -> None:
    driver, mgmt, _ = _database_driver()
    result = driver.provision(_spec())
    mgmt.databases.fail_copy = True
    deleted = driver.deprovision(DeprovisionSpec(result.handle))
    assert not deleted.ok
    assert "copy failed" in deleted.message
    assert not mgmt.databases.delete_calls


def test_sql_database_non_destructive_delete_preserves_copy_and_server() -> None:
    driver, mgmt, secret_client = _database_driver()
    result = driver.provision(_spec())
    server_name = result.handle.split("/")[1]
    deleted = driver.deprovision(DeprovisionSpec(result.handle))
    assert deleted.ok
    assert "retained_copy=" in deleted.message
    assert server_name in mgmt.servers.rows
    assert len(mgmt.databases.rows) == 1
    assert not any(name.endswith("-url") for name in secret_client.values)
    assert any(name.endswith("-password") for name in secret_client.values)


def test_sql_database_destructive_delete_removes_empty_server_and_secrets() -> None:
    driver, mgmt, secret_client = _database_driver()
    result = driver.provision(_spec())
    deleted = driver.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    assert deleted.ok
    assert mgmt.servers.delete_calls
    assert not secret_client.values


def test_sql_database_delete_retries_failed_secret_cleanup_idempotently() -> None:
    driver, mgmt, secret_client = _database_driver()
    result = driver.provision(_spec())
    secret_client.fail_delete = True
    first = driver.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    assert not first.ok
    assert not mgmt.databases.rows
    secret_client.fail_delete = False
    second = driver.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    assert second.ok
    assert mgmt.servers.delete_calls
    assert not secret_client.values


def test_sql_database_update_resizes_and_changes_retention() -> None:
    driver, mgmt, _ = _database_driver()
    result = driver.provision(_spec())
    updated = driver.update(
        UpdateSpec(result.handle, size="medium", config={"backup_retention_days": 14}),
    )
    assert updated.ok
    assert mgmt.databases.update_calls[-1]["parameters"].sku.capacity == 4
    assert mgmt.backup_short_term_retention_policies.calls[-1]["parameters"].retention_days == 14


def test_managed_instance_requires_subnet() -> None:
    driver, _, _ = _mi_driver(subnet_id="")
    result = driver.provision(_spec())
    assert not result.ok
    assert result.errors == ["missing_subnet_id"]


def test_managed_instance_provisions_private_instance_and_database() -> None:
    driver, mgmt, secret_client = _mi_driver()
    result = driver.provision(_spec())
    assert result.ok and result.ready
    params = mgmt.managed_instances.create_calls[0]["parameters"]
    assert params.subnet_id.endswith("/sql-mi")
    assert params.public_data_endpoint_enabled is False
    assert params.minimal_tls_version == "1.2"
    assert mgmt.managed_databases.create_calls
    assert mgmt.managed_backup_short_term_retention_policies.calls
    assert ".private.database.windows.net" in next(
        value for name, value in secret_client.values.items() if name.endswith("-url")
    )


def test_managed_instance_existing_resources_reconcile_retention_and_dsn() -> None:
    driver, mgmt, secret_client = _mi_driver()
    first = driver.provision(_spec())
    url_secret = next(name for name in secret_client.values if name.endswith("-url"))
    secret_client.values.pop(url_secret)
    second = driver.provision(_spec(config={"backup_retention_days": 14}))
    assert second.ok and second.handle == first.handle
    assert len(mgmt.managed_instances.create_calls) == 1
    assert len(mgmt.managed_databases.create_calls) == 1
    assert len(mgmt.managed_backup_short_term_retention_policies.calls) == 2
    assert mgmt.managed_backup_short_term_retention_policies.calls[-1]["parameters"].retention_days == 14
    assert url_secret in secret_client.values


def test_managed_instance_binding_and_status() -> None:
    driver, mgmt, _ = _mi_driver()
    result = driver.provision(_spec())
    assert driver.status(ServiceHandle(result.handle)).state == "available"
    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["MSSQL_HOST"].literal.endswith(".private.database.windows.net")
    assert binding.env_vars["MSSQL_PASSWORD"].secret_ref
    instance_name, database_name = result.handle.split("/")[1:]
    mgmt.managed_databases.rows.pop((instance_name, database_name))
    assert driver.status(ServiceHandle(result.handle)).state == "error"


def test_managed_instance_non_destructive_teardown_refuses_without_snapshot() -> None:
    driver, mgmt, _ = _mi_driver()
    result = driver.provision(_spec())
    deleted = driver.deprovision(DeprovisionSpec(result.handle))
    assert not deleted.ok
    assert deleted.retryable is False
    assert not mgmt.managed_instances.delete_calls


def test_managed_instance_destructive_teardown_is_explicit() -> None:
    driver, mgmt, secrets_client = _mi_driver()
    result = driver.provision(_spec())
    deleted = driver.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    assert deleted.ok
    assert mgmt.managed_databases.delete_calls
    assert mgmt.managed_instances.delete_calls
    assert not secrets_client.values


def test_managed_instance_delete_retries_failed_secret_cleanup_idempotently() -> None:
    driver, mgmt, secret_client = _mi_driver()
    result = driver.provision(_spec())
    secret_client.fail_delete = True
    first = driver.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    assert not first.ok
    assert not mgmt.managed_instances.rows
    secret_client.fail_delete = False
    second = driver.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    assert second.ok
    assert not secret_client.values


def test_managed_instance_snapshot_contract_is_honestly_unsupported() -> None:
    driver, _, _ = _mi_driver()
    result = driver.provision(_spec())
    with pytest.raises(UnsupportedOperationError, match="no immediate service snapshot"):
        driver.snapshot(ServiceHandle(result.handle))


def test_azure_sql_sdk_operation_surface_matches_driver() -> None:
    from azure.mgmt.sql import SqlManagementClient

    client = SqlManagementClient(object(), "00000000-0000-0000-0000-000000000000")
    try:
        assert callable(client.servers.begin_create_or_update)
        assert callable(client.databases.begin_create_or_update)
        assert callable(client.databases.begin_update)
        assert callable(client.backup_short_term_retention_policies.begin_create_or_update)
        assert callable(client.virtual_network_rules.begin_create_or_update)
        assert callable(client.managed_instances.begin_create_or_update)
        assert callable(client.managed_instances.begin_update)
        assert callable(client.managed_databases.begin_create_or_update)
        assert callable(client.managed_backup_short_term_retention_policies.begin_create_or_update)
    finally:
        client.close()


def test_azure_sql_typed_models_serialize_to_arm_property_shape() -> None:
    from azure.mgmt.sql._utils.model_base import SdkJSONEncoder

    database_driver, database_mgmt, _ = _database_driver("azure_sql_serverless")
    provisioned_database = database_driver.provision(_spec())
    assert provisioned_database.ok
    server_payload = json.loads(
        json.dumps(
            database_mgmt.servers.create_calls[0]["parameters"],
            cls=SdkJSONEncoder,
            exclude_readonly=True,
        ),
    )
    database_payload = json.loads(
        json.dumps(
            database_mgmt.databases.create_calls[0]["parameters"],
            cls=SdkJSONEncoder,
            exclude_readonly=True,
        ),
    )
    retention_payload = json.loads(
        json.dumps(
            database_mgmt.backup_short_term_retention_policies.calls[0]["parameters"],
            cls=SdkJSONEncoder,
            exclude_readonly=True,
        ),
    )
    vnet_rule_payload = json.loads(
        json.dumps(
            database_mgmt.virtual_network_rules.calls[0]["parameters"],
            cls=SdkJSONEncoder,
            exclude_readonly=True,
        ),
    )
    assert database_driver.update(UpdateSpec(provisioned_database.handle, size="medium")).ok
    update_payload = json.loads(
        json.dumps(
            database_mgmt.databases.update_calls[0]["parameters"],
            cls=SdkJSONEncoder,
            exclude_readonly=True,
        ),
    )
    snapshot = database_driver.snapshot(ServiceHandle(provisioned_database.handle))
    copy_payload = json.loads(
        json.dumps(
            database_mgmt.databases.create_calls[-1]["parameters"],
            cls=SdkJSONEncoder,
            exclude_readonly=True,
        ),
    )
    assert server_payload["properties"]["administratorLogin"] == "astrolift"
    assert server_payload["properties"]["publicNetworkAccess"] == "Enabled"
    assert database_payload["properties"]["autoPauseDelay"] == 60
    assert database_payload["sku"]["name"] == "GP_S_Gen5_1"
    assert retention_payload["properties"]["retentionDays"] == 7
    assert vnet_rule_payload["properties"]["virtualNetworkSubnetId"].endswith("/aks")
    assert update_payload["properties"]["maxSizeBytes"] == 128 * 1024**3
    assert update_payload["sku"]["name"] == "GP_S_Gen5_2"
    assert copy_payload["properties"]["createMode"] == "Copy"
    assert copy_payload["properties"]["sourceDatabaseId"] in snapshot.snapshot_id

    mi_driver, mi_mgmt, _ = _mi_driver()
    provisioned_mi = mi_driver.provision(_spec())
    assert provisioned_mi.ok
    mi_payload = json.loads(
        json.dumps(
            mi_mgmt.managed_instances.create_calls[0]["parameters"],
            cls=SdkJSONEncoder,
            exclude_readonly=True,
        ),
    )
    mi_retention_payload = json.loads(
        json.dumps(
            mi_mgmt.managed_backup_short_term_retention_policies.calls[0]["parameters"],
            cls=SdkJSONEncoder,
            exclude_readonly=True,
        ),
    )
    assert mi_driver.update(UpdateSpec(provisioned_mi.handle, size="medium")).ok
    mi_update_payload = json.loads(
        json.dumps(
            mi_mgmt.managed_instances.update_calls[0]["parameters"],
            cls=SdkJSONEncoder,
            exclude_readonly=True,
        ),
    )
    assert mi_payload["properties"]["subnetId"].endswith("/sql-mi")
    assert mi_payload["properties"]["publicDataEndpointEnabled"] is False
    assert mi_retention_payload["properties"]["retentionDays"] == 7
    assert mi_update_payload["properties"]["vCores"] == 16
    assert mi_update_payload["sku"]["name"] == "GP_Gen5"
