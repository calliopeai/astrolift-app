"""Tests for managed-service kind catalog + binding-env validator (#16)."""

from __future__ import annotations

from _sdk.managed_service_kinds import (
    KINDS,
    KindCatalog,
    ManagedServiceKind,
    validate_binding_envs,
)


def test_canonical_catalog_has_core_kinds() -> None:
    names = {k.name for k in KINDS.kinds}
    assert {"postgres", "redis", "object_store", "queue"} <= names


def test_postgres_required_envs() -> None:
    pg = KINDS.get("postgres")
    assert pg is not None
    assert "POSTGRES_HOST" in pg.binding_envs_required
    assert "POSTGRES_PORT" in pg.binding_envs_required
    assert "POSTGRES_PASSWORD" in pg.binding_envs_required


def test_validate_binding_envs_returns_missing() -> None:
    missing = validate_binding_envs(
        kind="postgres",
        emitted_envs=["POSTGRES_HOST", "POSTGRES_PORT"],
    )
    assert "POSTGRES_DB" in missing
    assert "POSTGRES_USER" in missing
    assert "POSTGRES_PASSWORD" in missing


def test_validate_binding_envs_complete_returns_empty() -> None:
    missing = validate_binding_envs(
        kind="postgres",
        emitted_envs=[
            "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
            "POSTGRES_USER", "POSTGRES_PASSWORD",
        ],
    )
    assert missing == []


def test_validate_unknown_kind_returns_empty() -> None:
    """Unknown kinds skip validation (operator-defined kinds may
    not yet be in the canonical catalog)."""
    missing = validate_binding_envs(
        kind="not_a_real_kind", emitted_envs=[],
    )
    assert missing == []


def test_kind_catalog_get_returns_none_for_missing() -> None:
    catalog = KindCatalog()
    assert catalog.get("nonexistent") is None


def test_custom_catalog_extension() -> None:
    """Plugin authors can extend the catalog at import time."""
    custom = KindCatalog(kinds=(
        ManagedServiceKind(
            name="vector_db",
            description="Vector similarity search store",
            binding_envs_required=("VECTOR_DB_URL", "VECTOR_DB_TOKEN"),
        ),
    ))
    missing = validate_binding_envs(
        kind="vector_db",
        emitted_envs=["VECTOR_DB_URL"],
        catalog=custom,
    )
    assert missing == ["VECTOR_DB_TOKEN"]
