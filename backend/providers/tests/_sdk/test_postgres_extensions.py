"""Tests for Postgres extension allow-listing (#14, #77)."""

from __future__ import annotations

from _sdk.postgres_extensions import (
    POLICIES,
    PG_CRON,
    PGVECTOR,
    ExtensionEntry,
    VariantExtensionPolicy,
    policy_for,
    validate_extensions,
)


def test_pgvector_in_every_variant() -> None:
    """pgvector is core for the agent stack — every variant
    must allow it."""
    for policy in POLICIES:
        assert policy.supports("vector"), (
            f"{policy.plugin_id}/{policy.variant} missing pgvector"
        )


def test_timescale_only_on_cnpg() -> None:
    """TimescaleDB requires shared_preload_libraries, which the
    cloud-managed variants don't expose. CNPG ships it."""
    cnpg = policy_for(plugin_id="k8s_native", variant="cnpg")
    assert cnpg is not None and cnpg.supports("timescaledb")
    rds = policy_for(plugin_id="aws", variant="rds")
    assert rds is not None and not rds.supports("timescaledb")


def test_pg_cron_on_azure_flexible_only() -> None:
    """Azure Flexible Server is the cloud-managed variant that
    surfaces pg_cron via parameter group; RDS doesn't."""
    azure = policy_for(plugin_id="azure", variant="flexible_server")
    assert azure is not None and azure.supports("pg_cron")
    rds = policy_for(plugin_id="aws", variant="rds")
    assert rds is not None and not rds.supports("pg_cron")


def test_validate_returns_rejected() -> None:
    result = validate_extensions(
        plugin_id="aws", variant="rds",
        requested=["pgcrypto", "pg_cron", "timescaledb"],
    )
    assert result.ok is False
    assert "pg_cron" in result.rejected
    assert "timescaledb" in result.rejected
    assert "pgcrypto" not in result.rejected


def test_validate_flags_restart_required() -> None:
    """pg_stat_statements is on every variant's allow list and
    requires a restart — the provisioner needs to know."""
    result = validate_extensions(
        plugin_id="aws", variant="rds",
        requested=["pgcrypto", "pg_stat_statements"],
    )
    assert result.ok is True
    assert "pg_stat_statements" in result.needs_restart
    assert "pgcrypto" not in result.needs_restart


def test_unknown_variant_fails_closed() -> None:
    """Missing policy → reject everything. Better to surface as
    a missing-policy bug than silently allow."""
    result = validate_extensions(
        plugin_id="azure", variant="not_yet_added",
        requested=["pgcrypto"],
    )
    assert result.ok is False
    assert result.rejected == ["pgcrypto"]


def test_deny_overrides_allow() -> None:
    """Operator can deny an extension that's on the allow list
    (e.g., security policy banning pgcrypto in regulated tenant)."""
    custom = VariantExtensionPolicy(
        plugin_id="aws", variant="rds",
        allow=(PGVECTOR,),
        deny=("vector",),
    )
    assert custom.supports("vector") is False


def test_pgvector_minimum_pg14() -> None:
    """pgvector needs Postgres 14+. Drivers must check before
    attempting CREATE EXTENSION."""
    assert PGVECTOR.minimum_postgres_version == 14


def test_pg_cron_requires_preload() -> None:
    assert PG_CRON.requires_shared_preload_libraries is True


def test_extension_entry_immutable() -> None:
    """Frozen dataclass → can't accidentally mutate the catalog."""
    entry = ExtensionEntry(name="x", description="y")
    try:
        entry.name = "z"  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("ExtensionEntry should be frozen")
