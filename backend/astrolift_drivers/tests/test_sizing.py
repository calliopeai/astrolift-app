"""Tests for managed-service sizing + cost estimation (#28)."""

from __future__ import annotations

import pytest

from astrolift_drivers.sizing import (
    CostEstimate,
    ManagedServiceSpec,
    Size,
    SizeError,
    clear_cost_estimators,
    clear_registry,
    estimate_cost,
    lookup_size_mapping,
    parse_size,
    register_cost_estimator,
    register_size_mapping,
    resolve_size,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    clear_cost_estimators()
    yield
    clear_registry()
    clear_cost_estimators()


# ---- size parsing ---------------------------------------------------


def test_parse_size_default_when_missing():
    assert parse_size(None) == Size.MEDIUM
    assert parse_size("") == Size.MEDIUM


def test_parse_size_known_values():
    for s in Size:
        assert parse_size(s.value) == s


def test_parse_size_rejects_unknown():
    with pytest.raises(SizeError, match="not one of"):
        parse_size("massive")


def test_size_vocabulary_locked():
    """Lock the closed enum so adding a new size requires updating
    every plugin's mapping table — caught in code review."""
    assert {s.value for s in Size} == {"small", "medium", "large", "xlarge", "custom"}


# ---- mapping registry -----------------------------------------------


def test_register_and_lookup_round_trip():
    spec = ManagedServiceSpec(
        plugin_slug="aws-rds", kind="postgres", size=Size.MEDIUM,
        native_class="db.r6g.large",
        parameters={"storage_gb": 100, "replicas": 1},
    )
    register_size_mapping(spec)
    out = lookup_size_mapping(plugin_slug="aws-rds", kind="postgres", size=Size.MEDIUM)
    assert out is spec


def test_lookup_unregistered_returns_none():
    out = lookup_size_mapping(plugin_slug="missing", kind="postgres", size=Size.MEDIUM)
    assert out is None


# ---- resolve_size ---------------------------------------------------


def test_resolve_uses_registered_mapping():
    spec = ManagedServiceSpec(
        plugin_slug="aws-rds", kind="postgres", size=Size.MEDIUM,
        native_class="db.r6g.large",
        parameters={"storage_gb": 100},
    )
    register_size_mapping(spec)
    out = resolve_size(plugin_slug="aws-rds", kind="postgres", size=Size.MEDIUM)
    assert out is spec


def test_resolve_org_override_wins_over_registry():
    """Operators may downgrade from medium to a cheaper class for
    a particular org without changing the plugin."""
    register_size_mapping(ManagedServiceSpec(
        plugin_slug="aws-rds", kind="postgres", size=Size.MEDIUM,
        native_class="db.r6g.large", parameters={},
    ))
    overridden = ManagedServiceSpec(
        plugin_slug="aws-rds", kind="postgres", size=Size.MEDIUM,
        native_class="db.t3.medium", parameters={},
    )
    out = resolve_size(
        plugin_slug="aws-rds", kind="postgres", size=Size.MEDIUM,
        org_overrides={("aws-rds", "postgres", Size.MEDIUM): overridden},
    )
    assert out.native_class == "db.t3.medium"


def test_resolve_unmapped_pair_raises():
    with pytest.raises(SizeError, match="no size mapping"):
        resolve_size(plugin_slug="unloaded", kind="postgres", size=Size.MEDIUM)


def test_resolve_custom_requires_parameters():
    """size='custom' without explicit parameters is a config bug."""
    with pytest.raises(SizeError, match="custom"):
        resolve_size(plugin_slug="aws-rds", kind="postgres", size=Size.CUSTOM)


def test_resolve_custom_emits_spec_with_empty_native_class():
    out = resolve_size(
        plugin_slug="aws-rds", kind="postgres", size=Size.CUSTOM,
        custom_parameters={"instance_class": "db.r6g.16xlarge", "storage_gb": 4000},
    )
    assert out.size == Size.CUSTOM
    assert out.native_class == ""
    assert out.parameters["instance_class"] == "db.r6g.16xlarge"


def test_managed_service_spec_rejects_empty_native_for_non_custom():
    """non-CUSTOM specs must carry a native_class — a missing one
    means a plugin author forgot to fill it in."""
    with pytest.raises(ValueError):
        ManagedServiceSpec(
            plugin_slug="aws-rds", kind="postgres", size=Size.MEDIUM,
            native_class="", parameters={},
        )


# ---- cost estimates -------------------------------------------------


def test_register_and_call_cost_estimator():
    spec = ManagedServiceSpec(
        plugin_slug="aws-rds", kind="postgres", size=Size.MEDIUM,
        native_class="db.r6g.large", parameters={"storage_gb": 100},
    )

    def estimator(s: ManagedServiceSpec) -> CostEstimate:
        return CostEstimate(
            monthly_usd_low=180.0, monthly_usd_high=210.0,
            notes="us-east-1, on-demand",
        )

    register_cost_estimator(plugin_slug="aws-rds", kind="postgres", fn=estimator)
    out = estimate_cost(spec)
    assert out is not None
    assert out.monthly_usd_low == 180.0
    assert out.monthly_usd_high == 210.0
    assert "us-east-1" in out.notes


def test_estimate_returns_none_when_unimplemented():
    """UI falls back to static catalog when no estimator registered."""
    spec = ManagedServiceSpec(
        plugin_slug="no-estimator", kind="postgres", size=Size.MEDIUM,
        native_class="x.large", parameters={},
    )
    assert estimate_cost(spec) is None


def test_cost_estimate_rejects_inverted_band():
    with pytest.raises(ValueError, match="monthly_usd_high"):
        CostEstimate(monthly_usd_low=200, monthly_usd_high=100)


def test_cost_estimate_rejects_negative():
    with pytest.raises(ValueError):
        CostEstimate(monthly_usd_low=-10, monthly_usd_high=10)


def test_cost_estimate_zero_band_allowed():
    """Free tier or sandbox (e.g. shared dev cluster) — both bounds 0."""
    out = CostEstimate(monthly_usd_low=0, monthly_usd_high=0, notes="sandbox")
    assert out.monthly_usd_low == 0
