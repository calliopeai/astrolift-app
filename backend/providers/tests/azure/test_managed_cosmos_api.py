from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from azure.managed.cosmos_api import (
    PROFILES,
    AzureCosmosApiConfig,
    AzureCosmosApiDriver,
    AzureCosmosApiError,
)


class ResourceNotFoundError(Exception):
    status_code = 404


@dataclass
class FakePoller:
    value: Any = None
    error: Exception | None = None
    waited: bool = False

    def result(self) -> Any:
        self.waited = True
        if self.error is not None:
            raise self.error
        return self.value


@dataclass
class FakeAccount:
    name: str
    tags: dict[str, str]
    provisioning_state: str = "Succeeded"
    instance_id: str = "restorable-instance-1"
    enable_free_tier: bool = False
    capabilities: list[Any] = field(default_factory=list)
    api_properties: Any = None


@dataclass
class FakeConnectionString:
    description: str
    connection_string: str


@dataclass
class FakeDatabaseAccounts:
    accounts: dict[str, FakeAccount] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)
    connection_strings: list[FakeConnectionString] = field(
        default_factory=lambda: [
            FakeConnectionString("Primary Connection String", "AccountEndpoint=https://primary;AccountKey=secret"),
            FakeConnectionString(
                "Primary Read-Only Connection String",
                "AccountEndpoint=https://readonly;AccountKey=readonly",
            ),
        ],
    )
    get_error: Exception | None = None
    update_error: Exception | None = None
    delete_error: Exception | None = None
    on_restore: Any = None

    def get(self, *, resource_group_name: str, account_name: str) -> FakeAccount:
        if self.get_error is not None:
            raise self.get_error
        try:
            return self.accounts[account_name]
        except KeyError as exc:
            raise ResourceNotFoundError(account_name) from exc

    def begin_create_or_update(
        self,
        *,
        resource_group_name: str,
        account_name: str,
        create_update_parameters: Any,
    ) -> FakePoller:
        self.create_calls.append(
            {"account_name": account_name, "parameters": create_update_parameters},
        )
        data = create_update_parameters.as_dict()
        properties = create_update_parameters.properties
        account = FakeAccount(
            name=account_name,
            tags=dict(data.get("tags", {})),
            capabilities=list(getattr(properties, "capabilities", None) or []),
            enable_free_tier=bool(getattr(properties, "enable_free_tier", False)),
            api_properties=getattr(properties, "api_properties", None),
        )
        self.accounts[account_name] = account
        if getattr(properties, "create_mode", None) == "Restore" and self.on_restore is not None:
            self.on_restore(account_name)
        return FakePoller(account)

    def begin_update(
        self,
        *,
        resource_group_name: str,
        account_name: str,
        update_parameters: Any,
    ) -> FakePoller:
        self.update_calls.append(
            {"account_name": account_name, "parameters": update_parameters},
        )
        if self.update_error is not None:
            return FakePoller(error=self.update_error)
        account = self.accounts[account_name]
        if update_parameters.tags is not None:
            account.tags = dict(update_parameters.tags)
        return FakePoller(account)

    def begin_delete(self, *, resource_group_name: str, account_name: str) -> FakePoller:
        self.delete_calls.append(account_name)
        if self.delete_error is not None:
            return FakePoller(error=self.delete_error)
        self.accounts.pop(account_name, None)
        return FakePoller()

    def list_connection_strings(self, *, resource_group_name: str, account_name: str) -> Any:
        return SimpleNamespace(connection_strings=self.connection_strings)


@dataclass
class FakeResourceGroup:
    profile: Any
    resources: set[tuple[str, str]] = field(default_factory=set)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    throughput_error: Exception | None = None
    get_error: Exception | None = None

    def __getattr__(self, name: str) -> Any:
        allowed = {
            self.profile.get_method,
            self.profile.create_method,
            self.profile.update_throughput_method,
        }
        if name not in allowed:
            raise AttributeError(name)

        def invoke(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            key = (kwargs["account_name"], kwargs[self.profile.resource_name_parameter])
            if name == self.profile.get_method:
                if self.get_error is not None:
                    raise self.get_error
                if key not in self.resources:
                    raise ResourceNotFoundError(str(key))
                return SimpleNamespace(id=key[1])
            if name == self.profile.create_method:
                self.resources.add(key)
                return FakePoller(SimpleNamespace(id=key[1]))
            if self.throughput_error is not None:
                return FakePoller(error=self.throughput_error)
            return FakePoller()

        return invoke


class FakeMgmtClient:
    def __init__(self) -> None:
        self.database_accounts = FakeDatabaseAccounts()
        self.groups = {profile.operation_group: FakeResourceGroup(profile) for profile in PROFILES.values()}
        for name, group in self.groups.items():
            setattr(self, name, group)
        self.restore_resource_name = "astrolift"
        self.database_accounts.on_restore = self._restore_resources

    def _restore_resources(self, account_name: str) -> None:
        for group in self.groups.values():
            group.resources.add((account_name, self.restore_resource_name))


@dataclass
class FakeLock:
    name: str


@dataclass
class FakeManagementLocks:
    locks: list[FakeLock] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    list_error: Exception | None = None
    delete_error: Exception | None = None

    def list_at_resource_level(self, **kwargs: Any) -> list[FakeLock]:
        if self.list_error is not None:
            raise self.list_error
        return list(self.locks)

    def delete_at_resource_level(self, *, lock_name: str, **kwargs: Any) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(lock_name)
        self.locks = [lock for lock in self.locks if lock.name != lock_name]


@dataclass
class FakeLocksClient:
    management_locks: FakeManagementLocks = field(default_factory=FakeManagementLocks)


@dataclass
class FakeSecrets:
    values: dict[str, str] = field(default_factory=dict)
    delete_pollers: list[FakePoller] = field(default_factory=list)
    set_error: Exception | None = None
    delete_error: Exception | None = None

    def set_secret(self, name: str, value: str) -> None:
        if self.set_error is not None:
            raise self.set_error
        self.values[name] = value

    def begin_delete_secret(self, name: str) -> FakePoller:
        if self.delete_error is not None:
            raise self.delete_error
        if name not in self.values:
            raise ResourceNotFoundError(name)
        self.values.pop(name)
        poller = FakePoller()
        self.delete_pollers.append(poller)
        return poller


def _spec(**overrides: Any) -> ProvisionSpec:
    values = {
        "organization_id": "org-id",
        "organization_slug": "acme",
        "app_id": "app-id",
        "app_slug": "api",
        "environment_id": "env-id",
        "environment_name": "prod",
        "tenant_cluster_id": "azure-prod",
        "service_handle_hint": "data",
        "size": "small",
        "managed_service_id": "service-id",
        "binding_id": "binding-id",
        "config": {},
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _driver(
    variant: str,
    *,
    mgmt: FakeMgmtClient | None = None,
    locks: FakeLocksClient | None = None,
    secrets: FakeSecrets | None = None,
    **config: Any,
) -> tuple[AzureCosmosApiDriver, FakeMgmtClient, FakeLocksClient, FakeSecrets]:
    mgmt = mgmt or FakeMgmtClient()
    locks = locks or FakeLocksClient()
    secrets = secrets or FakeSecrets()
    driver = AzureCosmosApiDriver(
        config=AzureCosmosApiConfig(
            subscription_id="00000000-1111-2222-3333-444444444444",
            resource_group="rg-data",
            variant=variant,
            location="eastus2",
            keyvault_url="https://data.vault.azure.net",
            mgmt_client=mgmt,
            locks_client=locks,
            secret_client=secrets,
            **config,
        ),
    )
    return driver, mgmt, locks, secrets


@pytest.mark.parametrize(
    ("variant", "kind", "create_method", "capability"),
    [
        ("cosmos_nosql", "document_db", "begin_create_update_sql_database", ""),
        ("cosmos_mongodb", "document_db", "begin_create_update_mongo_db_database", "EnableMongo"),
        ("cosmos_gremlin", "graph_db", "begin_create_update_gremlin_database", "EnableGremlin"),
        ("cosmos_cassandra", "wide_column", "begin_create_update_cassandra_keyspace", "EnableCassandra"),
        ("cosmos_table", "kv_store", "begin_create_update_table", "EnableTable"),
    ],
)
def test_provision_uses_exact_api_operation_and_typed_models(
    variant: str,
    kind: str,
    create_method: str,
    capability: str,
) -> None:
    driver, mgmt, _, secrets = _driver(variant)

    result = driver.provision(_spec())

    assert result.ok and result.ready
    assert result.handle.startswith(f"{kind}/")
    account_call = mgmt.database_accounts.create_calls[0]
    payload = account_call["parameters"].as_dict()
    assert payload["properties"]["databaseAccountOfferType"] == "Standard"
    assert payload["properties"]["minimalTlsVersion"] == "Tls12"
    assert payload["properties"]["locations"] == [
        {"locationName": "eastus2", "failoverPriority": 0, "isZoneRedundant": False},
    ]
    assert payload["tags"]["astrolift.io/managed_service_id"] == "service-id"
    capabilities = {item["name"] for item in payload["properties"].get("capabilities", [])}
    assert capabilities == ({capability} if capability else set())
    group = mgmt.groups[PROFILES[variant].operation_group]
    call_name, call = next(item for item in group.calls if item[0] == create_method)
    assert call_name == create_method
    resource_payload = call[PROFILES[variant].create_parameter].as_dict()
    assert resource_payload["properties"]["options"]["throughput"] == 400
    assert len(secrets.values) == 2


@pytest.mark.parametrize(
    ("variant", "config", "expected"),
    [
        ("cosmos_nosql", {"database_name": "orders"}, "orders"),
        ("cosmos_mongodb", {"resource_name": "records"}, "records"),
        ("cosmos_gremlin", {"database_name": "graph"}, "graph"),
        ("cosmos_cassandra", {"keyspace_name": "events"}, "events"),
        ("cosmos_table", {"table_name": "sessions"}, "sessions"),
    ],
)
def test_variant_specific_resource_name_aliases(
    variant: str,
    config: dict[str, Any],
    expected: str,
) -> None:
    driver, _, _, _ = _driver(variant)
    result = driver.provision(_spec(config=config))
    assert result.ok
    assert result.handle.endswith(f"/{expected}")


def test_reconcile_is_idempotent_and_does_not_reset_account_defaults() -> None:
    driver, mgmt, _, _ = _driver("cosmos_nosql")
    first = driver.provision(_spec())
    second = driver.provision(_spec())

    assert first.ok and second.ok and first.handle == second.handle
    assert len(mgmt.database_accounts.create_calls) == 1
    update = mgmt.database_accounts.update_calls[-1]["parameters"].as_dict()
    assert update["tags"]["astrolift.io/managed_service_id"] == "service-id"
    assert update.get("properties", {}) == {}
    group = mgmt.groups[PROFILES["cosmos_nosql"].operation_group]
    assert sum(name == PROFILES["cosmos_nosql"].create_method for name, _ in group.calls) == 1


def test_reconcile_refuses_foreign_account_before_mutation() -> None:
    driver, mgmt, _, _ = _driver("cosmos_nosql")
    result = driver.provision(_spec())
    account_name = result.handle.split("/")[1]
    mgmt.database_accounts.accounts[account_name].tags["astrolift.io/managed_service_id"] = "other"

    rejected = driver.provision(_spec())

    assert not rejected.ok
    assert "refusing to adopt" in rejected.message
    assert not mgmt.database_accounts.update_calls


def test_reconcile_without_service_id_checks_ownership_tags() -> None:
    spec = _spec(managed_service_id="")
    driver, mgmt, _, _ = _driver("cosmos_nosql")
    result = driver.provision(spec)
    account_name = result.handle.split("/")[1]
    mgmt.database_accounts.accounts[account_name].tags["astrolift.io/app"] = "foreign"
    assert not driver.provision(spec).ok


def test_reconcile_rejects_create_only_drift() -> None:
    driver, mgmt, _, _ = _driver("cosmos_mongodb")
    result = driver.provision(
        _spec(config={"enable_free_tier": True, "mongo_server_version": "7.0"}),
    )
    assert result.ok
    account_name = result.handle.split("/")[1]
    mgmt.database_accounts.accounts[account_name].api_properties.server_version = "6.0"

    rejected = driver.provision(
        _spec(config={"enable_free_tier": True, "mongo_server_version": "7.0"}),
    )

    assert not rejected.ok
    assert "reprovision required" in rejected.message


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"throughput": 400, "autoscale_max_throughput": 1000}, "mutually exclusive"),
        ({"throughput": 450}, "increments of 100"),
        ({"autoscale_max_throughput": 1500}, "increments of 1000"),
        ({"public_network_access": "Disabled"}, "Private Endpoint"),
        ({"public_network_access": "SecuredByPerimeter"}, "Network Security Perimeter"),
        ({"disable_local_auth": True}, "Entra workload"),
        ({"consistency_level": "BoundedStaleness"}, "requires max_staleness"),
        ({"customer_managed_key_uri": "https://key.vault.azure.net/keys/data"}, "both key URI"),
        ({"customer_managed_identity_resource_id": "not-an-arm-id"}, "both key URI"),
        ({"regions": []}, "non-empty list"),
        (
            {
                "regions": [
                    {"location_name": "eastus", "failover_priority": 0},
                    {"location_name": "eastus", "failover_priority": 1},
                ],
            },
            "location names must be unique",
        ),
        (
            {"regions": [{"location_name": "eastus", "failover_priority": 2}]},
            "contiguous from zero",
        ),
        ({"virtual_network_rule_ids": ["vnet/subnet"]}, "ARM resource IDs"),
        ({"network_acl_bypass": "Everything"}, "ACL bypass"),
        ({"ip_rules": ["not-an-ip"]}, "not an IP address"),
        (
            {"enable_multiple_write_locations": True, "consistency_level": "Strong"},
            "do not support Strong",
        ),
        ({"total_throughput_limit": -2}, "must be -1 or greater"),
        ({"extra_capabilities": "EnableServerless"}, "list of non-empty strings"),
        ({"extra_capabilities": ["EnableTable"]}, "controlled by the selected variant"),
        ({"not_a_cosmos_control": True}, "unsupported Cosmos config fields"),
        ({"backup_policy_type": "Periodic", "periodic_backup_interval_minutes": 1}, "between 60"),
        ({"resource_name": "a", "database_name": "b"}, "aliases are mutually exclusive"),
    ],
)
def test_invalid_controls_fail_before_cloud_mutation(config: dict[str, Any], message: str) -> None:
    driver, mgmt, _, _ = _driver("cosmos_nosql")
    result = driver.provision(_spec(config=config))
    assert not result.ok
    assert message in result.message
    assert not mgmt.database_accounts.create_calls


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("variant", "not-cosmos"),
        ("backup_policy_default", "Unknown"),
        ("continuous_backup_tier_default", "Forever"),
        ("public_network_access_default", "Public"),
        ("consistency_level_default", "Linearizable"),
    ],
)
def test_config_rejects_invalid_install_defaults(field: str, value: str) -> None:
    values = {
        "subscription_id": "sub",
        "resource_group": "rg",
        "variant": "cosmos_nosql",
    }
    values[field] = value
    with pytest.raises(ValueError):
        AzureCosmosApiConfig(**values)


def test_provision_without_key_vault_fails_before_cloud_mutation() -> None:
    mgmt = FakeMgmtClient()
    driver = AzureCosmosApiDriver(
        config=AzureCosmosApiConfig(
            subscription_id="sub",
            resource_group="rg",
            variant="cosmos_nosql",
            mgmt_client=mgmt,
            locks_client=FakeLocksClient(),
        ),
    )
    result = driver.provision(_spec())
    assert not result.ok
    assert "Key Vault" in result.message
    assert not mgmt.database_accounts.create_calls


def test_update_and_restore_without_key_vault_fail_before_cloud_mutation() -> None:
    mgmt = FakeMgmtClient()
    driver = AzureCosmosApiDriver(
        config=AzureCosmosApiConfig(
            subscription_id="sub",
            resource_group="rg",
            variant="cosmos_nosql",
            mgmt_client=mgmt,
            locks_client=FakeLocksClient(),
        ),
    )

    updated = driver.update(UpdateSpec(handle="document_db/account/data", size="medium"))
    restored = driver.restore(
        SnapshotHandle(
            "document_db/source/astrolift",
            "instance|cosmos_nosql|astrolift",
            "2026-08-14T12:00:00+00:00",
        ),
        _spec(),
    )

    assert not updated.ok and "Key Vault" in updated.message
    assert not restored.ok and "Key Vault" in restored.message
    assert not mgmt.database_accounts.create_calls
    assert not mgmt.database_accounts.update_calls


def test_missing_connection_string_is_reported_not_silently_bound() -> None:
    driver, mgmt, _, secrets = _driver("cosmos_nosql")
    mgmt.database_accounts.connection_strings = []
    result = driver.provision(_spec())
    assert not result.ok
    assert "no read-write connection string" in result.message
    assert not secrets.values


@pytest.mark.parametrize("variant", list(PROFILES))
def test_update_uses_variant_specific_throughput_operation(variant: str) -> None:
    driver, mgmt, _, _ = _driver(variant)
    provisioned = driver.provision(_spec())
    group = mgmt.groups[PROFILES[variant].operation_group]

    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            size="medium",
            config={"consistency_level": "Eventual"},
        ),
    )

    assert result.ok
    throughput_name = PROFILES[variant].update_throughput_method
    _, throughput_call = next(item for item in group.calls if item[0] == throughput_name)
    throughput = throughput_call["update_throughput_parameters"].as_dict()
    assert throughput["properties"]["resource"]["throughput"] == 1000
    account_update = mgmt.database_accounts.update_calls[-1]["parameters"].as_dict()
    assert account_update["properties"] == {
        "consistencyPolicy": {"defaultConsistencyLevel": "Eventual"},
    }


def test_update_supports_autoscale_and_propagates_provider_failure() -> None:
    driver, mgmt, _, _ = _driver("cosmos_table")
    provisioned = driver.provision(_spec())
    group = mgmt.groups[PROFILES["cosmos_table"].operation_group]
    group.throughput_error = RuntimeError("throughput rejected")
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, config={"autoscale_max_throughput": 5000}),
    )
    assert not result.ok
    assert "throughput rejected" in result.message


@pytest.mark.parametrize(
    ("variant", "field", "value"),
    [
        ("cosmos_nosql", "resource_name", "changed"),
        ("cosmos_nosql", "database_name", "changed"),
        ("cosmos_cassandra", "keyspace_name", "changed"),
        ("cosmos_table", "table_name", "changed"),
        ("cosmos_nosql", "enable_free_tier", True),
        ("cosmos_nosql", "extra_capabilities", ["EnableServerless"]),
        ("cosmos_mongodb", "mongo_server_version", "6.0"),
    ],
)
def test_update_rejects_create_only_fields(variant: str, field: str, value: Any) -> None:
    driver, _, _, _ = _driver(variant)
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle, config={field: value}))
    assert not result.ok
    assert "reprovision" in result.message


def test_update_missing_resource_fails_closed() -> None:
    driver, _, _, _ = _driver("cosmos_nosql")
    result = driver.update(UpdateSpec(handle="document_db/missing/data", size="medium"))
    assert not result.ok
    assert "does not exist" in result.message


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        ("cosmos_nosql", {"DOCDB_URI", "DOCDB_DB", "DOCDB_AUTH_MODE"}),
        ("cosmos_mongodb", {"DOCDB_URI", "DOCDB_DB", "DOCDB_AUTH_MODE"}),
        ("cosmos_gremlin", {"GRAPH_DB_URL", "GRAPH_DB_ENDPOINT", "GRAPH_DB_REGION"}),
        ("cosmos_cassandra", {"WIDE_COLUMN_ENDPOINT", "WIDE_COLUMN_KEYSPACE", "WIDE_COLUMN_PORT"}),
        ("cosmos_table", {"KV_TABLE_NAME", "KV_REGION", "KV_ENDPOINT_OVERRIDE"}),
    ],
)
def test_binding_matches_portable_kind_and_keeps_credentials_secret(
    variant: str,
    expected: set[str],
) -> None:
    driver, _, _, _ = _driver(variant)
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(provisioned.handle))

    assert expected <= set(binding.env_vars)
    assert {
        "COSMOS_ENDPOINT",
        "COSMOS_DATABASE_NAME",
        "COSMOS_CONNECTION_STRING",
        "COSMOS_READONLY_CONNECTION_STRING",
    } <= set(binding.env_vars)
    assert binding.env_vars["COSMOS_CONNECTION_STRING"].secret_ref
    assert binding.env_vars["COSMOS_CONNECTION_STRING"].literal is None
    assert "accountkey=" not in repr(binding.env_vars).lower()
    assert set(driver.binding_schema().env_vars) == set(binding.env_vars)


def test_status_distinguishes_missing_account_resource_and_provider_state() -> None:
    driver, mgmt, _, _ = _driver("cosmos_nosql")
    assert driver.status(ServiceHandle("document_db/missing/data")).state == "deprovisioned"
    provisioned = driver.provision(_spec())
    account_name, resource_name = provisioned.handle.split("/")[1:]
    group = mgmt.groups[PROFILES["cosmos_nosql"].operation_group]
    group.resources.remove((account_name, resource_name))
    assert driver.status(ServiceHandle(provisioned.handle)).state == "error"
    group.resources.add((account_name, resource_name))
    mgmt.database_accounts.accounts[account_name].provisioning_state = "Deleting"
    assert driver.status(ServiceHandle(provisioned.handle)).state == "deprovisioning"


def test_non_404_describe_error_is_not_misreported_as_missing() -> None:
    driver, mgmt, _, _ = _driver("cosmos_nosql")
    mgmt.database_accounts.get_error = RuntimeError("control plane unavailable")
    with pytest.raises(RuntimeError, match="control plane unavailable"):
        driver.status(ServiceHandle("document_db/account/data"))


def test_snapshot_enables_continuous_backup_and_uses_instance_id() -> None:
    driver, mgmt, _, _ = _driver("cosmos_nosql")
    provisioned = driver.provision(_spec())
    snapshot = driver.snapshot(ServiceHandle(provisioned.handle))

    assert snapshot.snapshot_id == "restorable-instance-1|cosmos_nosql|astrolift"
    update = mgmt.database_accounts.update_calls[-1]["parameters"].as_dict()
    assert update["properties"]["backupPolicy"] == {
        "continuousModeProperties": {"tier": "Continuous30Days"},
        "type": "Continuous",
    }


def test_snapshot_refuses_when_provider_omits_instance_id() -> None:
    driver, mgmt, _, _ = _driver("cosmos_nosql")
    provisioned = driver.provision(_spec())
    mgmt.database_accounts.accounts[provisioned.handle.split("/")[1]].instance_id = ""
    with pytest.raises(AzureCosmosApiError, match="instance_id"):
        driver.snapshot(ServiceHandle(provisioned.handle))


def test_restore_uses_exact_pitr_source_and_is_retry_idempotent() -> None:
    driver, mgmt, _, secrets = _driver("cosmos_nosql")
    snapshot = SnapshotHandle(
        "document_db/source/astrolift",
        "restorable-source|cosmos_nosql|astrolift",
        "2026-08-14T12:00:00+00:00",
    )
    first = driver.restore(snapshot, _spec())
    second = driver.restore(snapshot, _spec())

    assert first.ok and second.ok and first.handle == second.handle
    assert len(mgmt.database_accounts.create_calls) == 1
    payload = mgmt.database_accounts.create_calls[0]["parameters"].as_dict()
    restore = payload["properties"]["restoreParameters"]
    assert restore["restoreMode"] == "PointInTime"
    assert restore["restoreSource"].endswith(
        "/locations/eastus2/restorableDatabaseAccounts/restorable-source",
    )
    assert len(secrets.values) == 2


@pytest.mark.parametrize(
    ("snapshot_id", "config", "error"),
    [
        ("invalid", {}, "invalid Cosmos"),
        ("instance|cosmos_table|astrolift", {}, "variant does not match"),
        ("instance|cosmos_nosql|other", {}, "name to match"),
        ("instance|cosmos_nosql|astrolift", {"public_network_access": "Disabled"}, "Private Endpoint"),
    ],
)
def test_restore_rejects_invalid_contract_before_cloud_mutation(
    snapshot_id: str,
    config: dict[str, Any],
    error: str,
) -> None:
    driver, mgmt, _, _ = _driver("cosmos_nosql")
    result = driver.restore(
        SnapshotHandle("document_db/source/astrolift", snapshot_id, "2026-08-14T12:00:00+00:00"),
        _spec(config=config),
    )
    assert not result.ok
    assert error.lower() in result.message.lower()
    assert not mgmt.database_accounts.create_calls


def test_restore_refuses_preexisting_partial_target() -> None:
    driver, mgmt, _, _ = _driver("cosmos_nosql")
    original = driver.provision(_spec())
    account_name, resource_name = original.handle.split("/")[1:]
    mgmt.groups[PROFILES["cosmos_nosql"].operation_group].resources.remove(
        (account_name, resource_name),
    )
    before = len(mgmt.database_accounts.create_calls)
    restored = driver.restore(
        SnapshotHandle(
            "document_db/source/astrolift",
            "instance|cosmos_nosql|astrolift",
            "2026-08-14T12:00:00+00:00",
        ),
        _spec(),
    )
    assert not restored.ok
    assert "already exists" in restored.message
    assert len(mgmt.database_accounts.create_calls) == before


def test_deprovision_refuses_locks_and_lock_listing_failures() -> None:
    driver, mgmt, locks, _ = _driver("cosmos_nosql")
    provisioned = driver.provision(_spec())
    locks.management_locks.locks = [FakeLock("protect-data")]
    refused = driver.deprovision(DeprovisionSpec(provisioned.handle), delete_data=True)
    assert not refused.ok and "protect-data" in refused.message
    assert not mgmt.database_accounts.delete_calls
    locks.management_locks.list_error = RuntimeError("locks API unavailable")
    failed = driver.deprovision(DeprovisionSpec(provisioned.handle), delete_data=True)
    assert not failed.ok and "locks API unavailable" in failed.message
    assert not mgmt.database_accounts.delete_calls


def test_force_deprovision_waits_for_secret_cleanup() -> None:
    driver, mgmt, locks, secrets = _driver("cosmos_nosql")
    provisioned = driver.provision(_spec())
    locks.management_locks.locks = [FakeLock("protect-data")]
    result = driver.deprovision(
        DeprovisionSpec(provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert locks.management_locks.deleted == ["protect-data"]
    assert mgmt.database_accounts.delete_calls
    assert secrets.delete_pollers and all(poller.waited for poller in secrets.delete_pollers)


def test_data_preserving_deprovision_records_pitr_and_backup_failure_is_safe() -> None:
    driver, _, _, _ = _driver("cosmos_nosql")
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(provisioned.handle))
    assert result.ok and "retained_pitr=restorable-instance-1" in result.message

    second_driver, second_mgmt, _, _ = _driver("cosmos_nosql")
    second = second_driver.provision(_spec())
    second_mgmt.database_accounts.update_error = RuntimeError("backup unavailable")
    failed = second_driver.deprovision(DeprovisionSpec(second.handle))
    assert not failed.ok and failed.retryable is False
    assert not second_mgmt.database_accounts.delete_calls


def test_delete_and_secret_cleanup_failures_are_independently_retryable() -> None:
    driver, mgmt, _, secrets = _driver("cosmos_nosql")
    provisioned = driver.provision(_spec())
    mgmt.database_accounts.delete_error = RuntimeError("delete unavailable")
    failed_delete = driver.deprovision(DeprovisionSpec(provisioned.handle), delete_data=True)
    assert not failed_delete.ok
    assert secrets.values

    mgmt.database_accounts.delete_error = None
    secrets.delete_error = RuntimeError("vault unavailable")
    failed_cleanup = driver.deprovision(DeprovisionSpec(provisioned.handle), delete_data=True)
    assert not failed_cleanup.ok
    assert "account deleted" in failed_cleanup.message

    secrets.delete_error = None
    retried = driver.deprovision(DeprovisionSpec(provisioned.handle), delete_data=True)
    assert retried.ok
    assert not secrets.values


def test_account_payload_exposes_ha_network_backup_cmk_and_capacity_controls() -> None:
    driver, mgmt, _, _ = _driver("cosmos_mongodb")
    identity = (
        "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-data/"
        "providers/Microsoft.ManagedIdentity/userAssignedIdentities/cosmos"
    )
    subnet = (
        "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-network/"
        "providers/Microsoft.Network/virtualNetworks/data/subnets/cosmos"
    )
    result = driver.provision(
        _spec(
            config={
                "autoscale_max_throughput": 5000,
                "backup_policy_type": "Periodic",
                "periodic_backup_interval_minutes": 120,
                "periodic_backup_retention_hours": 24,
                "backup_storage_redundancy": "Zone",
                "regions": [
                    {"location_name": "eastus2", "failover_priority": 0, "zone_redundant": True},
                    {"location_name": "westus2", "failover_priority": 1},
                ],
                "enable_automatic_failover": True,
                "enable_multiple_write_locations": True,
                "consistency_level": "Session",
                "virtual_network_rule_ids": [subnet],
                "ip_rules": ["203.0.113.10"],
                "network_acl_bypass": "AzureServices",
                "network_acl_bypass_resource_ids": [identity],
                "customer_managed_key_uri": "https://key.vault.azure.net/keys/cosmos/version",
                "customer_managed_identity_resource_id": identity,
                "enable_free_tier": True,
                "enable_analytical_storage": True,
                "enable_partition_merge": True,
                "enable_burst_capacity": True,
                "enable_priority_based_execution": True,
                "default_priority_level": "High",
                "enable_per_region_per_partition_autoscale": True,
                "total_throughput_limit": 10000,
                "mongo_server_version": "7.0",
                "extra_capabilities": ["EnableServerless"],
            },
        ),
    )
    assert result.ok
    payload = mgmt.database_accounts.create_calls[0]["parameters"].as_dict()
    properties = payload["properties"]
    assert properties["locations"][0]["isZoneRedundant"] is True
    assert properties["enableAutomaticFailover"] is True
    assert properties["enableMultipleWriteLocations"] is True
    assert properties["backupPolicy"]["type"] == "Periodic"
    assert properties["backupPolicy"]["periodicModeProperties"]["backupStorageRedundancy"] == "Zone"
    assert properties["keyVaultKeyUri"].startswith("https://")
    assert properties["defaultIdentity"] == f"UserAssignedIdentity={identity}"
    assert payload["identity"]["type"] == "UserAssigned"
    assert properties["capacity"]["totalThroughputLimit"] == 10000
    assert properties["apiProperties"]["serverVersion"] == "7.0"
    assert {capability["name"] for capability in properties["capabilities"]} == {
        "EnableMongo",
        "EnableServerless",
    }


def test_schema_exposes_all_runtime_controls_and_variant_resource_alias() -> None:
    driver, _, _, _ = _driver("cosmos_cassandra")
    properties = driver.config_schema()["properties"]
    assert driver.config_schema()["additionalProperties"] is False
    assert {
        "resource_name",
        "keyspace_name",
        "throughput",
        "autoscale_max_throughput",
        "regions",
        "backup_policy_type",
        "public_network_access",
        "virtual_network_rule_ids",
        "disable_local_auth",
        "customer_managed_key_uri",
        "enable_free_tier",
        "enable_analytical_storage",
        "enable_partition_merge",
        "enable_burst_capacity",
        "enable_priority_based_execution",
        "total_throughput_limit",
        "extra_capabilities",
    } <= set(properties)


def test_long_resource_and_secret_names_are_bounded_and_collision_resistant() -> None:
    first, _, _, _ = _driver(
        "cosmos_table",
        account_name_prefix="x" * 80,
        secret_name_prefix="secret" * 30,
    )
    second, _, _, _ = _driver(
        "cosmos_table",
        account_name_prefix="x" * 80,
        secret_name_prefix="secret" * 30,
    )
    one = first.provision(
        _spec(managed_service_id="one", config={"table_name": "table" * 100}),
    )
    two = second.provision(
        _spec(managed_service_id="two", config={"table_name": "table" * 100 + "other"}),
    )
    one_account, one_resource = one.handle.split("/")[1:]
    two_account, two_resource = two.handle.split("/")[1:]
    assert len(one_account) <= 44 and len(two_account) <= 44 and one_account != two_account
    assert len(one_resource) <= 255 and len(two_resource) <= 255 and one_resource != two_resource
    assert len(first._primary_secret(one_account)) <= 127


def test_invalid_handle_fails_explicitly() -> None:
    driver, _, _, _ = _driver("cosmos_nosql")
    with pytest.raises(AzureCosmosApiError, match="invalid cosmos_nosql handle"):
        driver.binding(ServiceHandle("kv_store/wrong"))
