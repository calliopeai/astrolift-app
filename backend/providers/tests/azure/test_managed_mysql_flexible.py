"""Tests for AzureMySQLFlexibleDriver (#370 -- mysql/azure_mysql_flex).

Mirrors the AWS RDS-MySQL driver's test surface (#370 sibling):
provision (idempotent, tagged-via-the-Azure-tag-dict, password
persisted to Key Vault), update, deprovision four-corner matrix
(delete_data x force_destroy), status state-mapping, binding
env-var shape, snapshot + restore.

The Azure SDK isn't installed in CI; the driver accepts an
injected ``mgmt_client`` + ``secret_client`` for exactly this
reason. The fakes below speak just enough of the real SDKs
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
from azure.managed.mysql_flexible import (
    KIND,
    AzureMySQLConfig,
    AzureMySQLError,
    AzureMySQLFlexibleDriver,
    _final_backup_name,
    _generate_master_password,
)

# ---- fakes ------------------------------------------------------


class _NotFound(Exception):
    """Stand-in for azure.core.exceptions.ResourceNotFoundError; the
    driver sniffs error type via ``__class__.__name__`` so we just
    have to make the name match."""


_NotFound.__name__ = "ResourceNotFoundError"


@dataclass
class FakePoller:
    value: Any = None

    def result(self) -> Any:
        return self.value


@dataclass
class FakeServer:
    name: str
    state: str = "Ready"
    deletion_protection: bool = False
    fully_qualified_domain_name: str = ""
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeServersClient:
    servers: dict[str, FakeServer] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def get(self, *, resource_group_name: str, server_name: str) -> FakeServer:
        if server_name not in self.servers:
            raise _NotFound(server_name)
        return self.servers[server_name]

    def begin_create(
        self,
        *,
        resource_group_name: str,
        server_name: str,
        parameters: dict[str, Any],
    ) -> FakePoller:
        self.create_calls.append(
            {"name": server_name, "parameters": parameters},
        )
        props = parameters.get("properties", {})
        srv = FakeServer(
            name=server_name,
            state="Ready",
            deletion_protection=bool(
                props.get("deletion_protection", False),
            ),
            fully_qualified_domain_name=(f"{server_name}.mysql.database.azure.com"),
            properties=dict(props),
        )
        self.servers[server_name] = srv
        return FakePoller(value=srv)

    def begin_update(
        self,
        *,
        resource_group_name: str,
        server_name: str,
        parameters: dict[str, Any],
    ) -> FakePoller:
        self.update_calls.append(
            {"name": server_name, "parameters": parameters},
        )
        srv = self.servers.get(server_name)
        if srv is None:
            raise _NotFound(server_name)
        # Apply known fields from the partial update body.
        props = parameters.get("properties", {})
        if "deletion_protection" in props:
            srv.deletion_protection = bool(props["deletion_protection"])
        return FakePoller(value=srv)

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        server_name: str,
    ) -> FakePoller:
        self.delete_calls.append(server_name)
        srv = self.servers.get(server_name)
        if srv is None:
            raise _NotFound(server_name)
        if srv.deletion_protection:
            # Mirror the real Azure behaviour: delete refuses while
            # deletion_protection is on. The driver should clear
            # the flag before reaching here when force_destroy=True.
            raise RuntimeError(
                f"deletion_protection enabled on {server_name}",
            )
        del self.servers[server_name]
        return FakePoller(value=None)


@dataclass
class FakeBackupsClient:
    backups: dict[str, list[str]] = field(default_factory=dict)
    fail_next: bool = False

    def put(
        self,
        *,
        resource_group_name: str,
        server_name: str,
        backup_name: str,
    ) -> FakePoller:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("backup quota exceeded")
        self.backups.setdefault(server_name, []).append(backup_name)
        return FakePoller(value=backup_name)


@dataclass
class FakeMgmtClient:
    servers_obj: FakeServersClient = field(default_factory=FakeServersClient)
    backups_obj: FakeBackupsClient = field(default_factory=FakeBackupsClient)

    @property
    def servers(self) -> FakeServersClient:
        return self.servers_obj

    @property
    def backups(self) -> FakeBackupsClient:
        return self.backups_obj


@dataclass
class FakeSecretClient:
    """Mimics azure.keyvault.secrets.SecretClient just enough."""

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
) -> AzureMySQLFlexibleDriver:
    return AzureMySQLFlexibleDriver(
        config=AzureMySQLConfig(
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
        service_handle_hint="mysql",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_server(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert result.handle.startswith(f"{KIND}/")
    assert len(mgmt.servers_obj.servers) == 1


def test_provision_defaults_to_mysql_8_0(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    create = mgmt.servers_obj.create_calls[0]
    assert create["parameters"]["properties"]["version"].startswith("8.0")


def test_provision_idempotent(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    # Second provision must not have called begin_create again.
    assert len(mgmt.servers_obj.create_calls) == 1


def test_provision_persists_master_password_in_key_vault(
    driver: AzureMySQLFlexibleDriver,
    secrets_client: FakeSecretClient,
) -> None:
    result = driver.provision(_spec())
    server_name = result.handle.split("/", 1)[1]
    secret_name = f"astrolift-mysql-{server_name}-master"
    assert secret_name in secrets_client.secrets
    assert len(secrets_client.secrets[secret_name]) >= 16


def test_provision_honours_size_to_sku(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec(size="large"))
    create = mgmt.servers_obj.create_calls[0]
    assert create["parameters"]["sku"]["name"] == "Standard_D2ds_v4"
    assert create["parameters"]["sku"]["tier"] == "GeneralPurpose"


def test_provision_honours_spec_config_override(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(
        _spec(
            config={
                "sku_name": "Standard_E4ds_v4",
                "sku_tier": "BusinessCritical",
                "storage_gb": 256,
                "high_availability": "ZoneRedundant",
                "deletion_protection": False,
            },
        ),
    )
    params = mgmt.servers_obj.create_calls[0]["parameters"]
    assert params["sku"]["name"] == "Standard_E4ds_v4"
    assert params["sku"]["tier"] == "BusinessCritical"
    assert params["properties"]["storage"]["storage_size_gb"] == 256
    assert params["properties"]["high_availability"]["mode"] == ("ZoneRedundant")
    assert params["properties"]["deletion_protection"] is False


def test_provision_tags_server_with_astrolift_namespace(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    tags = mgmt.servers_obj.create_calls[0]["parameters"]["tags"]
    assert tags["astrolift-managed-by"] == "platform"
    assert tags["astrolift-app"] == "api"
    assert tags["astrolift-env"] == "prod"


def test_provision_without_keyvault_returns_error() -> None:
    """Driver must refuse rather than emit credentials in the clear
    when no Key Vault backend is wired up."""
    d = AzureMySQLFlexibleDriver(
        config=AzureMySQLConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            mgmt_client=FakeMgmtClient(),
            # no keyvault_url, no secret_client
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "Key Vault" in result.message


# ---- update -----------------------------------------------------


def test_update_resize(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="medium"),
    )
    assert result.ok
    assert mgmt.servers_obj.update_calls
    last = mgmt.servers_obj.update_calls[-1]
    assert last["parameters"]["sku"]["name"] == "Standard_B2ms"


def test_update_engine_version(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"engine_version": "8.0.32"},
        ),
    )
    assert result.ok
    last = mgmt.servers_obj.update_calls[-1]
    assert last["parameters"]["properties"]["version"] == "8.0.32"


def test_update_noop_when_nothing_to_change(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    before = len(mgmt.servers_obj.update_calls)
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message
    assert len(mgmt.servers_obj.update_calls) == before


def test_update_high_availability(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"high_availability": "ZoneRedundant"},
        ),
    )
    assert result.ok
    last = mgmt.servers_obj.update_calls[-1]
    assert last["parameters"]["properties"]["high_availability"]["mode"] == ("ZoneRedundant")


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_takes_snapshot_respects_protection(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    provisioned = driver.provision(_spec())  # deletion_protection on
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert not result.ok
    assert "deletion_protection" in result.message


def test_deprovision_delete_data_only_skips_snapshot(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    server_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "snapshot=skipped" in result.message
    # No backup taken on delete_data=True
    assert server_name not in mgmt.backups_obj.backups


def test_deprovision_default_with_protection_off_takes_snapshot(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    server_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "snapshot=taken" in result.message
    assert mgmt.backups_obj.backups.get(server_name)


def test_deprovision_force_destroy_disables_protection_and_keeps_snapshot(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())  # protection on
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert result.ok
    assert "snapshot=taken" in result.message
    assert "force_destroy=True" in result.message
    # The driver must have flipped deletion_protection off via update.
    update_with_flag_off = [
        c
        for c in mgmt.servers_obj.update_calls
        if c["parameters"]
        .get("properties", {})
        .get(
            "deletion_protection",
        )
        is False
    ]
    assert update_with_flag_off


def test_deprovision_atomic_both_flags(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "snapshot=skipped" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_idempotent_when_already_gone(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="mysql/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_with_data_delete_drops_master_password_secret(
    driver: AzureMySQLFlexibleDriver,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    server_name = provisioned.handle.split("/", 1)[1]
    secret_name = f"astrolift-mysql-{server_name}-master"
    assert secret_name in secrets_client.secrets

    driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert secret_name not in secrets_client.secrets
    assert secret_name in secrets_client.deleted


def test_deprovision_keeps_secret_on_data_retained_path(
    driver: AzureMySQLFlexibleDriver,
    secrets_client: FakeSecretClient,
) -> None:
    """When the data path is being retained (snapshot taken), the
    master password must stay in Key Vault so a restore from the
    final backup still has the original credentials."""
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    server_name = provisioned.handle.split("/", 1)[1]
    secret_name = f"astrolift-mysql-{server_name}-master"

    driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert secret_name in secrets_client.secrets


def test_deprovision_surfaces_backup_failure(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    """A failing pre-delete backup must abort the deprovision so the
    operator doesn't silently lose the data path."""
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    mgmt.backups_obj.fail_next = True
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert not result.ok
    assert "final backup" in result.message
    # Server must still exist.
    assert provisioned.handle.split("/", 1)[1] in mgmt.servers_obj.servers


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="mysql/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_ready_to_available(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_maps_dropping_to_deprovisioning(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    server_name = provisioned.handle.split("/", 1)[1]
    mgmt.servers_obj.servers[server_name].state = "Dropping"
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "deprovisioning"


# ---- binding ----------------------------------------------------


def test_binding_emits_the_canonical_mysql_envelope(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    """#1402 -- this driver only had the pre-#1003 DATABASE_* names, so an app
    that moved from Aurora MySQL to Azure Flexible Server lost every MYSQL_*."""
    provisioned = driver.provision(_spec())
    env = driver.binding(ServiceHandle(handle=provisioned.handle)).env_vars

    assert {"MYSQL_HOST", "MYSQL_PORT", "MYSQL_DB", "MYSQL_USER", "MYSQL_PASSWORD", "DATABASE_URL"} <= set(env)
    for canonical, legacy in (
        ("MYSQL_HOST", "DATABASE_HOST"),
        ("MYSQL_PORT", "DATABASE_PORT"),
        ("MYSQL_DB", "DATABASE_NAME"),
        ("MYSQL_USER", "DATABASE_USER"),
    ):
        assert env[canonical].literal == env[legacy].literal
    assert env["MYSQL_PASSWORD"].secret_ref == env["DATABASE_PASSWORD"].secret_ref
    assert env["MYSQL_PASSWORD"].literal is None


def test_binding_returns_connection_envelope(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    for key in (
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_NAME",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "DATABASE_URL",
    ):
        assert key in env
    assert env["DATABASE_PASSWORD"].secret_ref is not None
    assert env["DATABASE_PASSWORD"].secret_ref.startswith("azure-kv://kv.vault.azure.net/secrets/")
    assert env["DATABASE_URL"].secret_ref.startswith("azure-kv://kv.vault.azure.net/secrets/")
    assert env["DATABASE_PASSWORD"].literal is None
    assert env["DATABASE_HOST"].literal is not None
    assert env["DATABASE_HOST"].literal.endswith(
        ".mysql.database.azure.com",
    )
    assert env["DATABASE_USER"].literal == "astrolift"
    assert env["DATABASE_PORT"].literal == "3306"


def test_binding_iam_grants_cover_server_and_secret(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    actions = {a for grant in binding.iam_grants for a in grant.actions}
    assert any("Microsoft.DBforMySQL" in a for a in actions)
    assert any("KeyVault" in a for a in actions)


def test_binding_for_missing_raises(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    with pytest.raises(AzureMySQLError):
        driver.binding(ServiceHandle(handle="mysql/never"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_creates_handle(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    server_name = provisioned.handle.split("/", 1)[1]
    assert snap.snapshot_id.startswith(server_name)
    assert snap.snapshot_id in mgmt.backups_obj.backups[server_name]


def test_snapshot_surfaces_driver_error(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    mgmt.backups_obj.fail_next = True
    with pytest.raises(AzureMySQLError):
        driver.snapshot(ServiceHandle(handle=provisioned.handle))


def test_restore_from_snapshot_creates_new_server(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))

    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    target_name = result.handle.split("/", 1)[1]
    assert target_name in mgmt.servers_obj.servers
    # The create call for the restore must use PointInTimeRestore
    # mode so it's a fork from the source rather than a fresh server.
    restore_call = mgmt.servers_obj.create_calls[-1]
    assert restore_call["parameters"]["properties"]["create_mode"] == ("PointInTimeRestore")


def test_restore_surfaces_error_on_failed_create(
    driver: AzureMySQLFlexibleDriver,
    mgmt: FakeMgmtClient,
) -> None:
    """Wiring: when the underlying SDK call raises, the driver must
    surface ok=False with an errors list, not crash the workflow."""

    # Replace the begin_create on the fake to force a failure.
    def boom(**_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("subscription quota exceeded")

    mgmt.servers_obj.begin_create = boom  # type: ignore[assignment]

    snap = SnapshotHandle(
        handle="mysql/some-source",
        snapshot_id="snap-1",
        created_at="2026-05-15T00:00:00+00:00",
    )
    result = driver.restore(snap, _spec(service_handle_hint="failed"))
    assert not result.ok
    assert "restore begin_create" in result.message


# ---- naming + helpers -------------------------------------------


def test_server_name_canonicalization(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    name = driver._server_name_for(  # type: ignore[attr-defined]
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
    assert len(name) <= 63


def test_secret_name_for_url_distinct_from_master(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    server = "astrolift-acme-api-prod-mysql"
    assert driver._secret_name_for(server_name=server) != (  # type: ignore[attr-defined]
        driver._secret_name_for_url(server_name=server)  # type: ignore[attr-defined]
    )


def test_handle_round_trip(driver: AzureMySQLFlexibleDriver) -> None:
    handle = driver._handle_for(server_name="my-server")  # type: ignore[attr-defined]
    assert handle.startswith(f"{KIND}/")
    assert driver._server_name_from_handle(handle) == "my-server"  # type: ignore[attr-defined]


def test_handle_rejects_malformed(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    with pytest.raises(AzureMySQLError):
        driver._server_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(AzureMySQLError):
        driver._server_name_from_handle("mysql/")  # type: ignore[attr-defined]


def test_generated_password_uses_safe_charset() -> None:
    pw = _generate_master_password(length=64)
    assert len(pw) == 64
    forbidden = set('/@"\\ ')
    assert not (set(pw) & forbidden)


def test_final_backup_name_is_bounded() -> None:
    name = _final_backup_name(server_name="x" * 100)
    assert len(name) <= 64


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: AzureMySQLFlexibleDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "engine_version",
        "sku_name",
        "sku_tier",
        "storage_gb",
        "high_availability",
        "deletion_protection",
        "backup_retention_days",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AzureMySQLFlexibleDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_NAME",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "DATABASE_URL",
    ):
        assert key in schema.env_vars
