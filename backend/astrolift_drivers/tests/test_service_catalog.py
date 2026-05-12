"""Tests for the abstract managed service catalog (#11, spec 11 §3)."""

from __future__ import annotations

import pytest

from astrolift_drivers.service_catalog import (
    ALL_KINDS,
    KIND_DESCRIPTIONS,
    CatalogVariant,
    clear_catalog,
    find_variant,
    get_entry,
    list_entries,
    register_variant,
    register_variants,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_catalog()
    yield
    clear_catalog()


# ---- catalog meta ---------------------------------------------------


def test_all_kinds_match_spec_11_section_6():
    """Lock-test: any new kind must update both the catalog and the
    env_injection envelope. Drift surfaces here."""
    expected = {
        "postgres",
        "mysql",
        "redis",
        "mq",
        "queue",
        "topic",
        "kv_store",
        "document_db",
        "search",
        "vector_index",
        "time_series",
        "object_store",
        "nfs",
        "cdn",
        "email",
        "sms",
    }
    assert set(ALL_KINDS) == expected
    # Every kind must have a description
    for kind in ALL_KINDS:
        assert KIND_DESCRIPTIONS[kind]


def test_get_entry_includes_envelope_keys():
    """Catalog entries pull envelope keys from env_injection so the
    two never drift."""
    entry = get_entry("postgres")
    assert "DATABASE_URL" in entry.envelope_keys


def test_get_entry_unknown_kind_raises():
    with pytest.raises(KeyError, match="unknown service kind"):
        get_entry("not-a-real-kind")


# ---- variant registration -------------------------------------------


def test_register_and_list_variant():
    register_variant(
        CatalogVariant(
            plugin_slug="aws-rds",
            variant="aurora-15",
            kind="postgres",
            is_default_for_kind=True,
        )
    )
    entry = get_entry("postgres")
    assert len(entry.variants) == 1
    assert entry.variants[0].fqn == "aws-rds/aurora-15"


def test_register_rejects_unknown_kind():
    with pytest.raises(ValueError, match="not in the abstract catalog"):
        register_variant(
            CatalogVariant(
                plugin_slug="weird",
                variant="v1",
                kind="never-heard-of",
            )
        )


def test_register_variants_bulk():
    register_variants(
        [
            CatalogVariant(plugin_slug="aws-rds", variant="aurora-15", kind="postgres"),
            CatalogVariant(plugin_slug="aws-rds", variant="postgres-15", kind="postgres"),
            CatalogVariant(plugin_slug="aws-elasticache", variant="redis-7", kind="redis"),
        ]
    )
    pg = get_entry("postgres")
    rd = get_entry("redis")
    assert len(pg.variants) == 2
    assert len(rd.variants) == 1


def test_list_entries_covers_every_kind_in_order():
    """Empty kinds still show up — UI displays 'no variants registered'
    so operators see what's missing."""
    entries = list_entries()
    assert tuple(e.kind for e in entries) == ALL_KINDS


def test_find_variant_lookup():
    register_variant(
        CatalogVariant(
            plugin_slug="gcp-cloudsql",
            variant="postgres-15",
            kind="postgres",
        )
    )
    out = find_variant(plugin_slug="gcp-cloudsql", variant="postgres-15")
    assert out is not None
    assert out.fqn == "gcp-cloudsql/postgres-15"


def test_find_variant_returns_none_when_unregistered():
    assert find_variant(plugin_slug="missing", variant="x") is None


def test_default_for_kind_flag_preserved():
    register_variant(
        CatalogVariant(
            plugin_slug="aws-rds",
            variant="aurora-15",
            kind="postgres",
            is_default_for_kind=True,
        )
    )
    register_variant(
        CatalogVariant(
            plugin_slug="aws-rds",
            variant="postgres-15",
            kind="postgres",
            is_default_for_kind=False,
        )
    )
    pg = get_entry("postgres")
    defaults = [v for v in pg.variants if v.is_default_for_kind]
    assert len(defaults) == 1
    assert defaults[0].variant == "aurora-15"
