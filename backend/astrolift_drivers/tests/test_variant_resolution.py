"""Tests for the variant resolution engine (#55).

The resolver is pure so tests just feed in plain dicts. Each test
pins one branch of the priority chain — manifest pin > org policy
> cluster default > plugin default > unresolved.
"""

from __future__ import annotations

import pytest

from astrolift_drivers.variant_resolution import (
    SOURCE_CLUSTER_DEFAULT,
    SOURCE_MANIFEST_PIN,
    SOURCE_ORG_POLICY,
    SOURCE_PLUGIN_DEFAULT,
    PluginVariant,
    UnresolvedVariant,
    resolve_variant,
    variants_from_plugin_manifest,
)


def _aws_postgres_catalogue():
    """Synthetic plugin variant catalogue for tests."""
    return [
        PluginVariant(
            plugin_slug="aws-rds",
            variant="aurora-15",
            kind="postgres",
            is_default_for_kind=True,
        ),
        PluginVariant(
            plugin_slug="aws-rds",
            variant="rds-postgres-15",
            kind="postgres",
        ),
        PluginVariant(
            plugin_slug="aws-rds",
            variant="elasticache-redis-7",
            kind="redis",
            is_default_for_kind=True,
        ),
    ]


# ---- priority chain ---------------------------------------------------


def test_manifest_pin_wins_when_present():
    out = resolve_variant(
        kind="postgres",
        manifest_pin="aws-rds/rds-postgres-15",
        org_service_defaults=None,
        cluster_service_defaults=None,
        cluster_slug="prod",
        plugin_slug="aws-rds",
        plugin_variants=_aws_postgres_catalogue(),
    )
    assert out.fqn == "aws-rds/rds-postgres-15"
    assert out.source == SOURCE_MANIFEST_PIN


def test_org_policy_overrides_cluster_default_and_plugin_default():
    out = resolve_variant(
        kind="postgres",
        manifest_pin=None,
        org_service_defaults={"postgres": "aws-rds/rds-postgres-15"},
        cluster_service_defaults={"postgres": "aws-rds/aurora-15"},
        cluster_slug="prod",
        plugin_slug="aws-rds",
        plugin_variants=_aws_postgres_catalogue(),
    )
    assert out.fqn == "aws-rds/rds-postgres-15"
    assert out.source == SOURCE_ORG_POLICY


def test_cluster_default_used_when_org_policy_silent():
    out = resolve_variant(
        kind="postgres",
        manifest_pin=None,
        org_service_defaults={},
        cluster_service_defaults={"postgres": "aws-rds/rds-postgres-15"},
        cluster_slug="prod",
        plugin_slug="aws-rds",
        plugin_variants=_aws_postgres_catalogue(),
    )
    assert out.fqn == "aws-rds/rds-postgres-15"
    assert out.source == SOURCE_CLUSTER_DEFAULT


def test_plugin_default_is_last_resort():
    """No pin / no org policy / no cluster default → fall back to the
    plugin's is_default_for_kind variant."""
    out = resolve_variant(
        kind="postgres",
        manifest_pin=None,
        org_service_defaults=None,
        cluster_service_defaults=None,
        cluster_slug="prod",
        plugin_slug="aws-rds",
        plugin_variants=_aws_postgres_catalogue(),
    )
    assert out.fqn == "aws-rds/aurora-15"
    assert out.source == SOURCE_PLUGIN_DEFAULT


# ---- rejection paths --------------------------------------------------


def test_no_resolution_raises_with_kind_and_cluster():
    """A kind the plugin doesn't expose at all → UnresolvedVariant
    carrying enough context to render a friendly UI error."""
    with pytest.raises(UnresolvedVariant) as exc:
        resolve_variant(
            kind="kafka",
            manifest_pin=None,
            org_service_defaults=None,
            cluster_service_defaults=None,
            cluster_slug="prod-east",
            plugin_slug="aws-rds",
            plugin_variants=_aws_postgres_catalogue(),
        )
    assert exc.value.kind == "kafka"
    assert exc.value.cluster_slug == "prod-east"
    assert "kafka" in str(exc.value)
    assert "prod-east" in str(exc.value)


def test_manifest_pin_rejected_when_plugin_doesnt_expose_it():
    """A pinned variant that doesn't exist on the plugin → reject.
    Catches typos and 'wrong cluster' bugs at deploy time."""
    with pytest.raises(UnresolvedVariant):
        resolve_variant(
            kind="postgres",
            manifest_pin="aws-rds/totally-fake",
            org_service_defaults=None,
            cluster_service_defaults=None,
            cluster_slug="prod",
            plugin_slug="aws-rds",
            plugin_variants=_aws_postgres_catalogue(),
        )


def test_manifest_pin_rejected_for_kind_mismatch():
    """Pinning a redis variant for a postgres kind is wrong even
    when the variant exists. Different kinds aren't substitutable."""
    with pytest.raises(UnresolvedVariant):
        resolve_variant(
            kind="postgres",
            manifest_pin="aws-rds/elasticache-redis-7",
            org_service_defaults=None,
            cluster_service_defaults=None,
            cluster_slug="prod",
            plugin_slug="aws-rds",
            plugin_variants=_aws_postgres_catalogue(),
        )


def test_malformed_manifest_pin_rejected():
    """Pin without slash → reject. Empty pin path falls through to
    the lower priority chain (treated like no pin)."""
    with pytest.raises(UnresolvedVariant):
        resolve_variant(
            kind="postgres",
            manifest_pin="not-a-pin",
            org_service_defaults=None,
            cluster_service_defaults=None,
            cluster_slug="prod",
            plugin_slug="aws-rds",
            plugin_variants=_aws_postgres_catalogue(),
        )


def test_org_policy_with_unknown_variant_falls_through_to_cluster_default():
    """Bad org policy entries don't fail the resolution — they fall
    through to the next source. Otherwise a typo in org policy bricks
    every deploy in the org until someone notices."""
    out = resolve_variant(
        kind="postgres",
        manifest_pin=None,
        org_service_defaults={"postgres": "aws-rds/typo"},
        cluster_service_defaults={"postgres": "aws-rds/rds-postgres-15"},
        cluster_slug="prod",
        plugin_slug="aws-rds",
        plugin_variants=_aws_postgres_catalogue(),
    )
    assert out.source == SOURCE_CLUSTER_DEFAULT
    assert out.fqn == "aws-rds/rds-postgres-15"


# ---- determinism ------------------------------------------------------


def test_resolution_is_deterministic_across_calls():
    inputs = dict(
        kind="postgres",
        manifest_pin=None,
        org_service_defaults=None,
        cluster_service_defaults=None,
        cluster_slug="prod",
        plugin_slug="aws-rds",
        plugin_variants=_aws_postgres_catalogue(),
    )
    a = resolve_variant(**inputs)
    b = resolve_variant(**inputs)
    assert (a.fqn, a.source) == (b.fqn, b.source)


# ---- catalogue loader -------------------------------------------------


def test_variants_from_plugin_manifest_round_trip():
    manifest = {
        "variants": [
            {"kind": "postgres", "variant": "aurora-15", "is_default_for_kind": True},
            {"kind": "postgres", "variant": "rds-15"},
            {"kind": "redis", "variant": "elasticache-7", "is_default_for_kind": True},
        ]
    }
    variants = variants_from_plugin_manifest("aws-rds", manifest)
    assert {v.fqn for v in variants} == {
        "aws-rds/aurora-15",
        "aws-rds/rds-15",
        "aws-rds/elasticache-7",
    }
    defaults = [v for v in variants if v.is_default_for_kind]
    assert {v.kind for v in defaults} == {"postgres", "redis"}


def test_variants_loader_skips_malformed_entries():
    manifest = {
        "variants": [
            {"kind": "postgres", "variant": "aurora-15"},
            {"kind": "postgres"},  # missing variant
            "not a dict",
            {"variant": "x"},  # missing kind
        ]
    }
    variants = variants_from_plugin_manifest("aws-rds", manifest)
    assert len(variants) == 1
    assert variants[0].variant == "aurora-15"


def test_variants_loader_handles_missing_variants_key():
    """A plugin without a ``variants`` array (e.g. capability-only
    plugin) shouldn't blow up — return empty so the resolver
    rejects with the proper error."""
    assert variants_from_plugin_manifest("p", {}) == []
    assert variants_from_plugin_manifest("p", {"variants": None}) == []
