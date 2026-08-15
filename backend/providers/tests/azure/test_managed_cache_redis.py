"""Tests for AzureCacheRedisDriver (#364 -- redis/azure_cache_redis).

Mirrors the AzureMySQLFlexibleDriver / AzurePostgresFlexibleDriver test
surface: provision (idempotent, tagged, keys persisted to Key Vault),
update, deprovision four-corner matrix
(delete_data x force_destroy, plus the soft-delete-purge axis),
status state-mapping, binding env-var shape, snapshot + restore.
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
from azure.managed.cache_redis import (
    KIND,
    AzureCacheRedisConfig,
    AzureCacheRedisDriver,
    AzureCacheRedisError,
    _parse_sku,
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
class FakeCache:
    name: str
    provisioning_state: str = "Succeeded"
    host_name: str = ""
    ssl_port: int = 6380
    port: int = 0
    sku: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeKeys:
    primary_key: str = "primary-key-value-aaaaaaaaaaaaaaaa"
    secondary_key: str = "secondary-key-value-bbbbbbbbbbbbbbbb"


@dataclass
class FakeRedisOperations:
    caches: dict[str, FakeCache] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)
    purge_calls: list[str] = field(default_factory=list)
    export_calls: list[dict[str, Any]] = field(default_factory=list)
    import_calls: list[dict[str, Any]] = field(default_factory=list)
    purge_supported: bool = True
    export_supported: bool = True

    def get(self, *, resource_group_name: str, name: str) -> FakeCache:
        if name not in self.caches:
            raise _NotFound(name)
        return self.caches[name]

    def begin_create(
        self,
        *,
        resource_group_name: str,
        name: str,
        parameters: dict[str, Any],
    ) -> FakePoller:
        self.create_calls.append({"name": name, "parameters": parameters})
        cache = FakeCache(
            name=name,
            provisioning_state="Succeeded",
            host_name=f"{name}.redis.cache.windows.net",
            ssl_port=6380,
            port=6379 if parameters.get("enable_non_ssl_port") else 0,
            sku=dict(parameters.get("sku", {})),
            properties=dict(parameters),
        )
        self.caches[name] = cache
        return FakePoller(value=cache)

    def update(
        self,
        *,
        resource_group_name: str,
        name: str,
        parameters: dict[str, Any],
    ) -> FakeCache:
        self.update_calls.append({"name": name, "parameters": parameters})
        cache = self.caches.get(name)
        if cache is None:
            raise _NotFound(name)
        if "sku" in parameters:
            cache.sku = dict(parameters["sku"])
        if "enable_non_ssl_port" in parameters:
            cache.port = 6379 if parameters["enable_non_ssl_port"] else 0
        return cache

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        name: str,
    ) -> FakePoller:
        self.delete_calls.append(name)
        if name not in self.caches:
            raise _NotFound(name)
        del self.caches[name]
        return FakePoller(value=None)

    def begin_purge(
        self,
        *,
        resource_group_name: str,
        name: str,
    ) -> FakePoller:
        if not self.purge_supported:
            raise RuntimeError("purge not available in this region")
        self.purge_calls.append(name)
        return FakePoller(value=None)

    def list_keys(
        self,
        *,
        resource_group_name: str,
        name: str,
    ) -> FakeKeys:
        if name not in self.caches:
            raise _NotFound(name)
        return FakeKeys()

    def begin_export_data(
        self,
        *,
        resource_group_name: str,
        name: str,
        parameters: dict[str, Any],
    ) -> FakePoller:
        if not self.export_supported:
            raise RuntimeError("export not supported on this SKU")
        self.export_calls.append({"name": name, "parameters": parameters})
        return FakePoller(value=parameters.get("prefix"))

    def begin_import_data(
        self,
        *,
        resource_group_name: str,
        name: str,
        parameters: dict[str, Any],
    ) -> FakePoller:
        self.import_calls.append({"name": name, "parameters": parameters})
        if name not in self.caches:
            self.caches[name] = FakeCache(
                name=name,
                host_name=f"{name}.redis.cache.windows.net",
            )
        return FakePoller(value=None)


@dataclass
class FakeMgmtClient:
    redis_obj: FakeRedisOperations = field(default_factory=FakeRedisOperations)

    @property
    def redis(self) -> FakeRedisOperations:
        return self.redis_obj


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
) -> AzureCacheRedisDriver:
    return AzureCacheRedisDriver(
        config=AzureCacheRedisConfig(
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
        service_handle_hint="redis",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- parse_sku helper -------------------------------------------


def test_parse_sku_standard_c1() -> None:
    assert _parse_sku("Standard_C1") == ("Standard", "C", 1)


def test_parse_sku_premium_p3() -> None:
    assert _parse_sku("Premium_P3") == ("Premium", "P", 3)


def test_parse_sku_handles_lowercase() -> None:
    assert _parse_sku("standard_c2") == ("Standard", "C", 2)


# ---- provision --------------------------------------------------


def test_provision_creates_cache(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert result.handle.startswith(f"{KIND}/")
    assert len(mgmt.redis_obj.caches) == 1


def test_provision_defaults_to_standard_c1(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    create = mgmt.redis_obj.create_calls[0]
    sku = create["parameters"]["sku"]
    assert sku["name"] == "Standard"
    assert sku["family"] == "C"
    assert sku["capacity"] == 1


def test_provision_idempotent(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    assert len(mgmt.redis_obj.create_calls) == 1


def test_provision_persists_access_keys_in_key_vault(
    driver: AzureCacheRedisDriver,
    secrets_client: FakeSecretClient,
) -> None:
    result = driver.provision(_spec())
    cache_name = result.handle.split("/", 1)[1]
    primary = f"astrolift-redis-{cache_name}-primary"
    secondary = f"astrolift-redis-{cache_name}-secondary"
    assert primary in secrets_client.secrets
    assert secondary in secrets_client.secrets
    assert secrets_client.secrets[primary]
    assert secrets_client.secrets[secondary]


def test_provision_honours_size_to_sku(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec(size="xlarge"))
    create = mgmt.redis_obj.create_calls[0]
    assert create["parameters"]["sku"]["name"] == "Premium"
    assert create["parameters"]["sku"]["family"] == "P"


def test_provision_honours_spec_config_override(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(
        _spec(
            config={
                "sku_name": "Premium_P2",
                "minimum_tls_version": "1.2",
                "enable_non_ssl_port": True,
                "shard_count": 3,
            },
        ),
    )
    params = mgmt.redis_obj.create_calls[0]["parameters"]
    assert params["sku"]["name"] == "Premium"
    assert params["sku"]["family"] == "P"
    assert params["sku"]["capacity"] == 2
    assert params["minimum_tls_version"] == "1.2"
    assert params["enable_non_ssl_port"] is True
    assert params["shard_count"] == 3


def test_provision_tags_cache_with_astrolift_namespace(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    tags = mgmt.redis_obj.create_calls[0]["parameters"]["tags"]
    assert tags["astrolift.io/managed-by"] == "platform"
    assert tags["astrolift.io/app"] == "api"


def test_provision_without_keyvault_returns_error() -> None:
    d = AzureCacheRedisDriver(
        config=AzureCacheRedisConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            mgmt_client=FakeMgmtClient(),
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "Key Vault" in result.message


# ---- update -----------------------------------------------------


def test_update_resize(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="medium"),
    )
    assert result.ok
    last = mgmt.redis_obj.update_calls[-1]
    assert last["parameters"]["sku"]["capacity"] == 2


def test_update_minimum_tls_version(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"minimum_tls_version": "1.2"},
        ),
    )
    assert result.ok
    last = mgmt.redis_obj.update_calls[-1]
    assert last["parameters"]["minimum_tls_version"] == "1.2"


def test_update_noop_when_nothing_to_change(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    before = len(mgmt.redis_obj.update_calls)
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message
    assert len(mgmt.redis_obj.update_calls) == before


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_takes_export_keeps_soft_delete(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    cache_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok
    assert "export=taken" in result.message
    assert "purged=soft-delete" in result.message
    assert cache_name in mgmt.redis_obj.delete_calls
    assert cache_name not in mgmt.redis_obj.purge_calls


def test_deprovision_delete_data_only_skips_export(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    cache_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "export=skipped" in result.message
    # No export call for the cache when delete_data=True
    cache_exports = [c for c in mgmt.redis_obj.export_calls if c["name"] == cache_name]
    assert not cache_exports


def test_deprovision_force_destroy_only_purges(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    cache_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert result.ok
    assert "export=taken" in result.message
    assert "purged=yes" in result.message
    assert cache_name in mgmt.redis_obj.purge_calls


def test_deprovision_atomic_both_flags(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    cache_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "export=skipped" in result.message
    assert "purged=yes" in result.message
    assert cache_name in mgmt.redis_obj.purge_calls


def test_deprovision_idempotent_when_already_gone(
    driver: AzureCacheRedisDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="redis/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_with_data_delete_drops_access_key_secrets(
    driver: AzureCacheRedisDriver,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    cache_name = provisioned.handle.split("/", 1)[1]
    primary = f"astrolift-redis-{cache_name}-primary"
    secondary = f"astrolift-redis-{cache_name}-secondary"
    assert primary in secrets_client.secrets
    assert secondary in secrets_client.secrets

    driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert primary not in secrets_client.secrets
    assert secondary not in secrets_client.secrets


def test_deprovision_keeps_secrets_on_data_retained_path(
    driver: AzureCacheRedisDriver,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    cache_name = provisioned.handle.split("/", 1)[1]
    primary = f"astrolift-redis-{cache_name}-primary"

    driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert primary in secrets_client.secrets


def test_deprovision_handles_export_failure_gracefully(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    """Standard-tier caches don't support export; we should NOT block
    delete on the export failure (matches Memorystore + ElastiCache
    best-effort posture)."""
    mgmt.redis_obj.export_supported = False
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok
    assert "export=skipped" in result.message


def test_deprovision_treats_mid_modify_as_retryable_without_force(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())

    def boom(**_kwargs):
        raise RuntimeError("CacheNotInDesiredState: scaling")

    mgmt.redis_obj.begin_delete = boom  # type: ignore[assignment]
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert not result.ok
    assert "mid-modify" in result.message


def test_deprovision_force_destroy_bypasses_mid_modify(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())

    def boom(**_kwargs):
        raise RuntimeError("CacheNotInDesiredState: scaling")

    mgmt.redis_obj.begin_delete = boom  # type: ignore[assignment]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert not result.ok
    # Even with force_destroy, an unhandled SDK error still surfaces;
    # we just don't return the "mid-modify" hint message.
    assert "mid-modify" not in result.message


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AzureCacheRedisDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="redis/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_succeeded_to_available(
    driver: AzureCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_maps_deleting_to_deprovisioning(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    cache_name = provisioned.handle.split("/", 1)[1]
    mgmt.redis_obj.caches[cache_name].provisioning_state = "Deleting"
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "deprovisioning"


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: AzureCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    for key in (
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_TLS",
        "REDIS_AUTH_TOKEN",
        "REDIS_SECONDARY_AUTH_TOKEN",
        "REDIS_URL",
    ):
        assert key in env
    assert env["REDIS_AUTH_TOKEN"].secret_ref is not None
    assert env["REDIS_AUTH_TOKEN"].secret_ref.startswith("azure-kv://kv.vault.azure.net/secrets/")
    assert env["REDIS_URL"].secret_ref.startswith("azure-kv://kv.vault.azure.net/secrets/")
    assert env["REDIS_AUTH_TOKEN"].literal is None
    assert env["REDIS_PORT"].literal == "6380"
    assert env["REDIS_TLS"].literal == "1"
    assert env["REDIS_HOST"].literal.endswith(".redis.cache.windows.net")


def test_binding_includes_non_ssl_port_when_enabled(
    driver: AzureCacheRedisDriver,
) -> None:
    provisioned = driver.provision(
        _spec(config={"enable_non_ssl_port": True}),
    )
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    assert "REDIS_PORT_NON_SSL" in binding.env_vars
    assert binding.env_vars["REDIS_PORT_NON_SSL"].literal == "6379"


def test_binding_omits_non_ssl_port_by_default(
    driver: AzureCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    assert "REDIS_PORT_NON_SSL" not in binding.env_vars


def test_binding_iam_grants_cover_cache_and_secret(
    driver: AzureCacheRedisDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    actions = {a for grant in binding.iam_grants for a in grant.actions}
    assert any("Microsoft.Cache" in a for a in actions)
    assert any("KeyVault" in a for a in actions)


def test_binding_for_missing_raises(
    driver: AzureCacheRedisDriver,
) -> None:
    with pytest.raises(AzureCacheRedisError):
        driver.binding(ServiceHandle(handle="redis/never"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_creates_handle(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    cache_name = provisioned.handle.split("/", 1)[1]
    assert snap.snapshot_id.startswith(cache_name)
    assert mgmt.redis_obj.export_calls


def test_snapshot_surfaces_driver_error(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    mgmt.redis_obj.export_supported = False
    with pytest.raises(AzureCacheRedisError):
        driver.snapshot(ServiceHandle(handle=provisioned.handle))


def test_restore_from_snapshot_creates_new_cache(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))

    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    target_name = result.handle.split("/", 1)[1]
    assert target_name in mgmt.redis_obj.caches


def test_restore_surfaces_error_on_failure(
    driver: AzureCacheRedisDriver,
    mgmt: FakeMgmtClient,
) -> None:
    def boom(**_kwargs):
        raise RuntimeError("import quota exceeded")

    mgmt.redis_obj.begin_import_data = boom  # type: ignore[assignment]
    snap = SnapshotHandle(
        handle="redis/some-source",
        snapshot_id="snap-1",
        created_at="2026-05-15T00:00:00+00:00",
    )
    result = driver.restore(snap, _spec(service_handle_hint="failed"))
    assert not result.ok
    assert "begin_import_data" in result.message


# ---- naming + helpers -------------------------------------------


def test_cache_name_canonicalization(
    driver: AzureCacheRedisDriver,
) -> None:
    name = driver._cache_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="cache",
        ),
    )
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    for c in name:
        assert c.isalnum() or c == "-"
    assert len(name) <= 63


def test_handle_round_trip(driver: AzureCacheRedisDriver) -> None:
    handle = driver._handle_for(cache_name="my-cache")  # type: ignore[attr-defined]
    assert handle.startswith(f"{KIND}/")
    assert (
        driver._cache_name_from_handle(handle) == "my-cache"  # type: ignore[attr-defined]
    )


def test_handle_rejects_malformed(
    driver: AzureCacheRedisDriver,
) -> None:
    with pytest.raises(AzureCacheRedisError):
        driver._cache_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(AzureCacheRedisError):
        driver._cache_name_from_handle("redis/")  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: AzureCacheRedisDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "sku_name",
        "minimum_tls_version",
        "enable_non_ssl_port",
        "shard_count",
        "subnet_id",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AzureCacheRedisDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_TLS",
        "REDIS_AUTH_TOKEN",
        "REDIS_SECONDARY_AUTH_TOKEN",
        "REDIS_URL",
    ):
        assert key in schema.env_vars
