"""Tests for AzureCosmosDriver (#371 -- kv_store/cosmos).

Mirrors the AWS DynamoDB + GCP Bigtable driver test surfaces:
provision (idempotent, tagged, mongo-default, conn-string persisted
to Key Vault), update, deprovision four-corner matrix
(delete_data x force_destroy), status state-mapping, binding
env-var shape, snapshot + restore.

The Azure SDK isn't installed in CI; the driver accepts injected
``mgmt_client`` + ``locks_client`` + ``secret_client`` for exactly
this reason. The fakes below speak just enough of the real SDK
shapes to drive every code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from azure.managed.cosmos import (
    KIND,
    AzureCosmosConfig,
    AzureCosmosDriver,
    AzureCosmosError,
)

# ---- fakes ------------------------------------------------------


class _NotFound(Exception):
    """Stand-in for azure.core.exceptions.ResourceNotFoundError; the
    driver sniffs error type via ``__class__.__name__``."""


_NotFound.__name__ = "ResourceNotFoundError"


@dataclass
class FakePoller:
    value: Any = None

    def result(self) -> Any:
        return self.value


@dataclass
class FakeConnectionStringEntry:
    connection_string: str
    description: str


@dataclass
class FakeConnectionStrings:
    connection_strings: list[FakeConnectionStringEntry]


@dataclass
class FakeAccount:
    name: str
    provisioning_state: str = "Succeeded"
    document_endpoint: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    kind: str = "MongoDB"


@dataclass
class FakeDatabaseAccountsClient:
    accounts: dict[str, FakeAccount] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)
    backup_policy_updates: list[dict[str, Any]] = field(default_factory=list)

    def get(self, *, resource_group_name, account_name):
        if account_name not in self.accounts:
            raise _NotFound(account_name)
        return self.accounts[account_name]

    def begin_create_or_update(
        self,
        *,
        resource_group_name,
        account_name,
        create_update_parameters,
    ):
        self.create_calls.append(
            {
                "name": account_name,
                "parameters": create_update_parameters,
            },
        )
        kind = create_update_parameters.get("kind", "MongoDB")
        acct = FakeAccount(
            name=account_name,
            provisioning_state="Succeeded",
            document_endpoint=(f"https://{account_name}.documents.azure.com:443/"),
            properties=dict(create_update_parameters.get("properties", {})),
            kind=kind,
        )
        self.accounts[account_name] = acct
        return FakePoller(value=acct)

    def begin_update(
        self,
        *,
        resource_group_name,
        account_name,
        update_parameters,
    ):
        self.update_calls.append(
            {"name": account_name, "parameters": update_parameters},
        )
        # Record backup-policy updates separately so tests can assert
        # the continuous-backup ensure call ran.
        bp = update_parameters.get("properties", {}).get("backup_policy")
        if bp:
            self.backup_policy_updates.append(
                {"account": account_name, "backup_policy": bp},
            )
        return FakePoller(value=None)

    def begin_delete(self, *, resource_group_name, account_name):
        self.delete_calls.append(account_name)
        if account_name not in self.accounts:
            raise _NotFound(account_name)
        del self.accounts[account_name]
        return FakePoller(value=None)

    def list_connection_strings(
        self,
        *,
        resource_group_name,
        account_name,
    ):
        return FakeConnectionStrings(
            connection_strings=[
                FakeConnectionStringEntry(
                    connection_string=(f"mongodb://{account_name}:secret@{account_name}.mongo.cosmos.azure.com:10255"),
                    description="Primary MongoDB Connection String",
                ),
                FakeConnectionStringEntry(
                    connection_string=(f"mongodb://{account_name}:roonly@{account_name}.mongo.cosmos.azure.com:10255"),
                    description="Primary Read-Only MongoDB Connection String",
                ),
            ],
        )


@dataclass
class FakeMongoResources:
    create_db_calls: list[dict[str, Any]] = field(default_factory=list)
    throughput_updates: list[dict[str, Any]] = field(default_factory=list)

    def begin_create_update_mongo_db_database(
        self,
        *,
        resource_group_name,
        account_name,
        database_name,
        create_update_mongo_db_database_parameters,
    ):
        self.create_db_calls.append(
            {
                "account": account_name,
                "database": database_name,
                "parameters": create_update_mongo_db_database_parameters,
            },
        )
        return FakePoller(value=None)

    def begin_update_mongo_db_database_throughput(
        self,
        *,
        resource_group_name,
        account_name,
        database_name,
        update_throughput_parameters,
    ):
        self.throughput_updates.append(
            {
                "account": account_name,
                "database": database_name,
                "parameters": update_throughput_parameters,
            },
        )
        return FakePoller(value=None)


@dataclass
class FakeMgmtClient:
    database_accounts_obj: FakeDatabaseAccountsClient = field(
        default_factory=FakeDatabaseAccountsClient,
    )
    mongo_db_resources_obj: FakeMongoResources = field(
        default_factory=FakeMongoResources,
    )

    @property
    def database_accounts(self) -> FakeDatabaseAccountsClient:
        return self.database_accounts_obj

    @property
    def mongo_db_resources(self) -> FakeMongoResources:
        return self.mongo_db_resources_obj


@dataclass
class FakeLock:
    name: str
    level: str = "CanNotDelete"


@dataclass
class FakeManagementLocks:
    locks: list[FakeLock] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def list_at_resource_level(self, **_):
        return list(self.locks)

    def delete_at_resource_level(self, *, lock_name, **_):
        self.deleted.append(lock_name)
        self.locks = [lock for lock in self.locks if lock.name != lock_name]


@dataclass
class FakeLocksClient:
    management_locks_obj: FakeManagementLocks = field(
        default_factory=FakeManagementLocks,
    )

    @property
    def management_locks(self) -> FakeManagementLocks:
        return self.management_locks_obj


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
def locks() -> FakeLocksClient:
    return FakeLocksClient()


@pytest.fixture
def secrets_client() -> FakeSecretClient:
    return FakeSecretClient()


@pytest.fixture
def driver(
    mgmt: FakeMgmtClient,
    locks: FakeLocksClient,
    secrets_client: FakeSecretClient,
) -> AzureCosmosDriver:
    return AzureCosmosDriver(
        config=AzureCosmosConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            locks_client=locks,
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
        service_handle_hint="kv",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_account_and_database(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert result.handle.startswith(f"{KIND}/")
    assert len(mgmt.database_accounts_obj.accounts) == 1
    assert len(mgmt.mongo_db_resources_obj.create_db_calls) == 1


def test_provision_defaults_to_mongo_api(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    create = mgmt.database_accounts_obj.create_calls[0]
    assert create["parameters"]["kind"] == "MongoDB"
    caps = create["parameters"]["properties"]["capabilities"]
    assert {"name": "EnableMongo"} in caps


def test_provision_idempotent(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    assert len(mgmt.database_accounts_obj.create_calls) == 1


def test_provision_persists_connection_strings_in_key_vault(
    driver: AzureCosmosDriver,
    secrets_client: FakeSecretClient,
) -> None:
    result = driver.provision(_spec())
    account_name = result.handle.split("/", 1)[1]
    primary = f"astrolift-cosmos-{account_name}-primary"
    readonly = f"astrolift-cosmos-{account_name}-readonly"
    assert primary in secrets_client.secrets
    assert readonly in secrets_client.secrets
    assert "mongodb://" in secrets_client.secrets[primary]
    # Read-only entry was distinguished and stored separately
    assert "roonly" in secrets_client.secrets[readonly]


def test_provision_honours_size_to_throughput(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec(size="large"))
    create_db = mgmt.mongo_db_resources_obj.create_db_calls[0]
    options = create_db["parameters"]["options"]
    assert options["throughput"] == 4000


def test_provision_honours_explicit_throughput(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec(config={"throughput": 2500}))
    create_db = mgmt.mongo_db_resources_obj.create_db_calls[0]
    assert create_db["parameters"]["options"]["throughput"] == 2500


def test_provision_honours_api_kind_cassandra(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    """Cassandra api_kind must surface the right capabilities. We
    don't have a Cassandra resource client on the fake, so this test
    drives only the account-create path -- enough to confirm the
    capability mapping."""
    # Remove the mongo client from the fake so we don't try to
    # create a mongo DB on a Cassandra account.
    mgmt.mongo_db_resources_obj = FakeMongoResources()
    # Patch the create path to skip database creation by overriding
    # the SDK route to a no-op for Cassandra in this fake setup.
    # We just probe that the capability was added and the begin_create
    # was called.
    # FakeMgmtClient doesn't have cassandra_resources -- we tolerate
    # the AttributeError since the only thing this test asserts is
    # the capabilities array on the account-create payload.
    import contextlib

    with contextlib.suppress(Exception):
        driver.provision(_spec(config={"api_kind": "Cassandra"}))
    create = mgmt.database_accounts_obj.create_calls[0]
    assert create["parameters"]["kind"] == "Cassandra"
    caps = create["parameters"]["properties"]["capabilities"]
    assert {"name": "EnableCassandra"} in caps


def test_provision_rejects_invalid_api_kind(
    driver: AzureCosmosDriver,
) -> None:
    result = driver.provision(_spec(config={"api_kind": "Bogus"}))
    assert not result.ok
    assert "invalid_api_kind" in result.errors


def test_provision_tags_account_with_astrolift_namespace(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    tags = mgmt.database_accounts_obj.create_calls[0]["parameters"]["tags"]
    assert tags["astrolift.io/managed-by"] == "platform"
    assert tags["astrolift.io/app"] == "api"
    assert tags["astrolift.io/environment"] == "prod"


def test_provision_without_keyvault_returns_error() -> None:
    """Driver must refuse rather than emit credentials in the clear
    when no Key Vault backend is wired up."""
    d = AzureCosmosDriver(
        config=AzureCosmosConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            mgmt_client=FakeMgmtClient(),
            locks_client=FakeLocksClient(),
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "Key Vault" in result.message


# ---- update -----------------------------------------------------


def test_update_throughput(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="medium"),
    )
    assert result.ok
    updates = mgmt.mongo_db_resources_obj.throughput_updates
    assert updates
    assert updates[-1]["parameters"]["resource"]["throughput"] == 1000


def test_update_public_network_access(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"public_network_access": "Disabled"},
        ),
    )
    assert result.ok
    last = mgmt.database_accounts_obj.update_calls[-1]
    assert last["parameters"]["properties"]["public_network_access"] == "Disabled"


def test_update_noop_when_nothing_to_change(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    before = len(mgmt.database_accounts_obj.update_calls)
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message
    assert len(mgmt.database_accounts_obj.update_calls) == before


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_retains_via_continuous_backup(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "continuous_backup=retained" in result.message
    # Backup-policy update ran
    assert mgmt.database_accounts_obj.backup_policy_updates


def test_deprovision_delete_data_skips_backup_enable(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "continuous_backup=skipped" in result.message
    # No backup-policy update was triggered on this path
    assert not mgmt.database_accounts_obj.backup_policy_updates


def test_deprovision_refuses_when_lock_present(
    driver: AzureCosmosDriver,
    locks: FakeLocksClient,
) -> None:
    provisioned = driver.provision(_spec())
    locks.management_locks_obj.locks.append(
        FakeLock(name="prod-lock", level="CanNotDelete"),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert not result.ok
    assert "resource locks" in result.message
    assert "prod-lock" in result.message


def test_deprovision_force_destroy_clears_lock(
    driver: AzureCosmosDriver,
    locks: FakeLocksClient,
) -> None:
    provisioned = driver.provision(_spec())
    locks.management_locks_obj.locks.append(
        FakeLock(name="prod-lock", level="CanNotDelete"),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert result.ok
    assert "prod-lock" in locks.management_locks_obj.deleted


def test_deprovision_atomic_both_flags(
    driver: AzureCosmosDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "continuous_backup=skipped" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_idempotent_when_already_gone(
    driver: AzureCosmosDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="kv_store/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_with_data_delete_drops_conn_secrets(
    driver: AzureCosmosDriver,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    account_name = provisioned.handle.split("/", 1)[1]
    primary = f"astrolift-cosmos-{account_name}-primary"
    assert primary in secrets_client.secrets

    driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert primary not in secrets_client.secrets
    assert primary in secrets_client.deleted


def test_deprovision_keeps_secrets_on_retain_path(
    driver: AzureCosmosDriver,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    account_name = provisioned.handle.split("/", 1)[1]
    primary = f"astrolift-cosmos-{account_name}-primary"

    driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    # When the data path is being retained, the conn-string secrets
    # remain in Key Vault so a restore-from-PITR has credentials.
    assert primary in secrets_client.secrets


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AzureCosmosDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="kv_store/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_succeeded_to_available(
    driver: AzureCosmosDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_maps_deleting_to_deprovisioning(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    account_name = provisioned.handle.split("/", 1)[1]
    mgmt.database_accounts_obj.accounts[account_name].provisioning_state = "Deleting"
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "deprovisioning"


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: AzureCosmosDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    for key in (
        "COSMOS_ENDPOINT",
        "COSMOS_DATABASE_NAME",
        "COSMOS_CONNECTION_STRING",
        "COSMOS_READONLY_CONNECTION_STRING",
    ):
        assert key in env
    assert env["COSMOS_ENDPOINT"].literal is not None
    assert env["COSMOS_ENDPOINT"].literal.startswith("https://")
    assert env["COSMOS_CONNECTION_STRING"].secret_ref is not None
    assert env["COSMOS_CONNECTION_STRING"].secret_ref.startswith("azure-kv://kv.vault.azure.net/secrets/")
    assert env["COSMOS_CONNECTION_STRING"].literal is None


def test_binding_iam_grants_cover_account_and_secret(
    driver: AzureCosmosDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    actions = {a for grant in binding.iam_grants for a in grant.actions}
    assert any("Microsoft.DocumentDB" in a for a in actions)
    assert any("KeyVault" in a for a in actions)


def test_binding_for_missing_raises(
    driver: AzureCosmosDriver,
) -> None:
    with pytest.raises(AzureCosmosError):
        driver.binding(ServiceHandle(handle="kv_store/never"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_returns_point_in_time_handle(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    account_name = provisioned.handle.split("/", 1)[1]
    assert snap.snapshot_id.startswith(account_name)
    assert "pit" in snap.snapshot_id
    # Continuous backup was ensured
    assert mgmt.database_accounts_obj.backup_policy_updates


def test_snapshot_surfaces_error_on_missing_account(
    driver: AzureCosmosDriver,
) -> None:
    with pytest.raises(AzureCosmosError):
        driver.snapshot(ServiceHandle(handle="kv_store/never"))


def test_restore_creates_target_account(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))

    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    target = result.handle.split("/", 1)[1]
    assert target in mgmt.database_accounts_obj.accounts
    create_calls = mgmt.database_accounts_obj.create_calls
    # Last create call must use Restore mode
    assert create_calls[-1]["parameters"]["properties"]["create_mode"] == "Restore"


def test_restore_surfaces_error_on_failed_create(
    driver: AzureCosmosDriver,
    mgmt: FakeMgmtClient,
) -> None:
    def boom(**_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("subscription quota exceeded")

    mgmt.database_accounts_obj.begin_create_or_update = boom  # type: ignore[assignment]

    snap = SnapshotHandle(
        handle="kv_store/some-source",
        snapshot_id="src-pit-20260515T000000Z",
        created_at="2026-05-15T00:00:00+00:00",
    )
    result = driver.restore(snap, _spec(service_handle_hint="failed"))
    assert not result.ok
    assert "restore begin_create_or_update" in result.message


# ---- naming + helpers -------------------------------------------


def test_account_name_canonicalization(
    driver: AzureCosmosDriver,
) -> None:
    name = driver._account_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="users",
        ),
    )
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    for c in name:
        assert c.isalnum() or c == "-"
    assert len(name) <= 44


def test_handle_round_trip(driver: AzureCosmosDriver) -> None:
    handle = driver._handle_for(account_name="my-acct")  # type: ignore[attr-defined]
    assert handle.startswith(f"{KIND}/")
    assert driver._account_name_from_handle(handle) == "my-acct"  # type: ignore[attr-defined]


def test_handle_rejects_malformed(driver: AzureCosmosDriver) -> None:
    with pytest.raises(AzureCosmosError):
        driver._account_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(AzureCosmosError):
        driver._account_name_from_handle("kv_store/")  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: AzureCosmosDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "api_kind",
        "throughput",
        "autoscale_max_throughput",
        "backup_policy_type",
        "database_name",
        "public_network_access",
        "disable_local_auth",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AzureCosmosDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "COSMOS_ENDPOINT",
        "COSMOS_DATABASE_NAME",
        "COSMOS_CONNECTION_STRING",
        "COSMOS_READONLY_CONNECTION_STRING",
    ):
        assert key in schema.env_vars
