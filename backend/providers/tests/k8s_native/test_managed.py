"""Tests for in-cluster managed-service drivers (#53)."""

from __future__ import annotations

from unittest.mock import MagicMock

from _sdk.cluster import ApplyResult
from _sdk.managed_service import (
    ProvisionSpec,
    ServiceHandle,
)
from k8s_native.managed.postgres_cnpg import (
    SIZE_TO_SPEC,
    CNPGConfig,
    CNPGPostgresDriver,
)
from k8s_native.managed.redis_operator import (
    RedisOperatorConfig,
    RedisOperatorDriver,
)


def _spec(**overrides) -> ProvisionSpec:
    base = dict(
        managed_service_id="8b7e2c6b-0b93-4126-a121-abc123456789",
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="local-k8s",
        service_handle_hint="db",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- CNPG Postgres -----------------------------------------------


def test_cnpg_provision_renders_cluster_crd() -> None:
    """Render-only mode (no cluster_driver). Confirms shape."""
    driver = CNPGPostgresDriver()
    result = driver.provision(_spec())
    assert result.ok is True
    assert result.handle.startswith("postgres/")


def test_cnpg_size_translates_to_instances() -> None:
    cluster_driver = MagicMock()
    cluster_driver.get_manifest.return_value = None
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=["Cluster/api-prod-db"],
        updated=[],
        unchanged=[],
        errors=[],
    )
    driver = CNPGPostgresDriver(
        config=CNPGConfig(
            cluster_driver=cluster_driver,
        )
    )
    driver.provision(_spec(size="large"))
    args, _ = cluster_driver.apply_manifests.call_args
    manifest = args[2][0]
    assert manifest["kind"] == "Cluster"
    assert manifest["spec"]["instances"] == SIZE_TO_SPEC["large"]["instances"]


def test_cnpg_extensions_in_post_init_sql() -> None:
    cluster_driver = MagicMock()
    cluster_driver.get_manifest.return_value = None
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=[],
        updated=[],
        unchanged=[],
        errors=[],
    )
    driver = CNPGPostgresDriver(
        config=CNPGConfig(
            cluster_driver=cluster_driver,
        )
    )
    driver.provision(_spec(config={"extensions": ["pg_trgm", "vector"]}))
    args, _ = cluster_driver.apply_manifests.call_args
    manifest = args[2][0]
    sql_steps = manifest["spec"]["bootstrap"]["initdb"]["postInitSQL"]
    assert any("pg_trgm" in s for s in sql_steps)
    assert any("vector" in s for s in sql_steps)


def test_cnpg_storage_class_config_threaded() -> None:
    cluster_driver = MagicMock()
    cluster_driver.get_manifest.return_value = None
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=[],
        updated=[],
        unchanged=[],
        errors=[],
    )
    driver = CNPGPostgresDriver(
        config=CNPGConfig(
            cluster_driver=cluster_driver,
            storage_class="fast-ssd",
        )
    )
    driver.provision(_spec())
    args, _ = cluster_driver.apply_manifests.call_args
    manifest = args[2][0]
    assert manifest["spec"]["storage"]["storageClass"] == "fast-ssd"


def test_cnpg_binding_emits_db_env_vars() -> None:
    driver = CNPGPostgresDriver()
    handle = ServiceHandle(handle="postgres/cluster-1/acme-api/api-prod-db")
    binding = driver.binding(handle)
    assert "DATABASE_HOST" in binding.env_vars
    assert "DATABASE_URL" in binding.env_vars
    assert "DATABASE_PASSWORD" in binding.env_vars


def test_cnpg_binding_emits_the_canonical_postgres_envelope() -> None:
    """#1402 -- this driver only had the pre-#1003 DATABASE_* names, so an app
    that moved from RDS to CNPG lost every POSTGRES_* variable it read."""
    binding = CNPGPostgresDriver().binding(ServiceHandle(handle="postgres/cluster-1/acme-api/api-prod-db"))

    assert {
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_SSL_MODE",
        "DATABASE_URL",
    } <= set(binding.env_vars)
    # Each canonical key reads the same field of the operator-generated Secret
    # as its legacy alias, so the two never drift apart.
    for canonical, legacy in (
        ("POSTGRES_HOST", "DATABASE_HOST"),
        ("POSTGRES_PORT", "DATABASE_PORT"),
        ("POSTGRES_DB", "DATABASE_NAME"),
        ("POSTGRES_USER", "DATABASE_USER"),
        ("POSTGRES_PASSWORD", "DATABASE_PASSWORD"),
    ):
        assert binding.env_vars[canonical] == binding.env_vars[legacy]
    assert binding.env_vars["POSTGRES_SSL_MODE"].literal == "require"


def test_cnpg_provision_failure_propagates() -> None:
    cluster_driver = MagicMock()
    cluster_driver.get_manifest.return_value = None
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=[],
        updated=[],
        unchanged=[],
        errors=["webhook denied"],
    )
    driver = CNPGPostgresDriver(
        config=CNPGConfig(
            cluster_driver=cluster_driver,
        )
    )
    result = driver.provision(_spec())
    assert result.ok is False


# ---- Redis Operator ----------------------------------------------


def test_redis_provision_single_instance_for_small() -> None:
    cluster_driver = MagicMock()
    cluster_driver.get_manifest.return_value = None
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=[],
        updated=[],
        unchanged=[],
        errors=[],
    )
    driver = RedisOperatorDriver(
        config=RedisOperatorConfig(
            cluster_driver=cluster_driver,
        )
    )
    driver.provision(_spec(size="small"))
    args, _ = cluster_driver.apply_manifests.call_args
    manifest = args[2][0]
    # small = 1 instance → standalone Redis kind
    assert manifest["kind"] == "Redis"


def test_redis_provision_replicated_for_medium() -> None:
    cluster_driver = MagicMock()
    cluster_driver.get_manifest.return_value = None
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=[],
        updated=[],
        unchanged=[],
        errors=[],
    )
    driver = RedisOperatorDriver(
        config=RedisOperatorConfig(
            cluster_driver=cluster_driver,
        )
    )
    driver.provision(_spec(size="medium"))
    args, _ = cluster_driver.apply_manifests.call_args
    manifest = args[2][0]
    # medium+ uses RedisReplication
    assert manifest["kind"] == "RedisReplication"


def test_redis_persistent_storage_block() -> None:
    cluster_driver = MagicMock()
    cluster_driver.get_manifest.return_value = None
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=[],
        updated=[],
        unchanged=[],
        errors=[],
    )
    driver = RedisOperatorDriver(
        config=RedisOperatorConfig(
            cluster_driver=cluster_driver,
            persistent=True,
        )
    )
    driver.provision(_spec())
    args, _ = cluster_driver.apply_manifests.call_args
    manifest = args[2][0]
    assert "storage" in manifest["spec"]


def test_redis_cache_mode_no_storage() -> None:
    cluster_driver = MagicMock()
    cluster_driver.get_manifest.return_value = None
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=[],
        updated=[],
        unchanged=[],
        errors=[],
    )
    driver = RedisOperatorDriver(
        config=RedisOperatorConfig(
            cluster_driver=cluster_driver,
            persistent=False,
        )
    )
    driver.provision(_spec(config={"persistent": False}))
    args, _ = cluster_driver.apply_manifests.call_args
    manifest = args[2][0]
    assert "storage" not in manifest["spec"]


def test_redis_binding_emits_url() -> None:
    driver = RedisOperatorDriver()
    binding = driver.binding(ServiceHandle(handle="redis/cluster-1/acme-api/api-prod-cache"))
    assert "REDIS_URL" in binding.env_vars
    assert "REDIS_HOST" in binding.env_vars
    assert "REDIS_PASSWORD" in binding.env_vars
