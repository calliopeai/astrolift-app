"""Tests for isolation mode resolution (#16, spec 11 §4)."""

from __future__ import annotations

import pytest

from astrolift_drivers.isolation import (
    SOURCE_MANIFEST,
    SOURCE_ORG_POLICY,
    SOURCE_VARIANT_DEFAULT,
    Isolation,
    IsolationError,
    VariantSupport,
    parse_mode,
    resolve,
)


def _both_default_shared() -> VariantSupport:
    return VariantSupport(
        plugin_slug="aws-rds",
        variant="postgres-15",
        allowed_modes=frozenset({Isolation.SHARED, Isolation.DEDICATED}),
        default=Isolation.SHARED,
    )


def _dedicated_only() -> VariantSupport:
    return VariantSupport(
        plugin_slug="aws-rds",
        variant="aurora-serverless-v2",
        allowed_modes=frozenset({Isolation.DEDICATED}),
        default=Isolation.DEDICATED,
    )


def _shared_only() -> VariantSupport:
    return VariantSupport(
        plugin_slug="acme-shared-pool",
        variant="pg-cheap",
        allowed_modes=frozenset({Isolation.SHARED}),
        default=Isolation.SHARED,
    )


# ---- variant declaration guards -------------------------------------


def test_variant_rejects_empty_allowed_modes():
    with pytest.raises(ValueError):
        VariantSupport(
            plugin_slug="x",
            variant="y",
            allowed_modes=frozenset(),
            default=Isolation.SHARED,
        )


def test_variant_rejects_default_not_in_allowed():
    with pytest.raises(ValueError, match="default"):
        VariantSupport(
            plugin_slug="x",
            variant="y",
            allowed_modes=frozenset({Isolation.SHARED}),
            default=Isolation.DEDICATED,
        )


# ---- parse_mode -----------------------------------------------------


def test_parse_mode_returns_none_when_unset():
    """Empty doesn't default to SHARED — variant default decides."""
    assert parse_mode(None) is None
    assert parse_mode("") is None


def test_parse_mode_known_values():
    assert parse_mode("shared") == Isolation.SHARED
    assert parse_mode("dedicated") == Isolation.DEDICATED


def test_parse_mode_rejects_unknown():
    with pytest.raises(IsolationError, match="not one of"):
        parse_mode("hybrid")


# ---- resolve ---------------------------------------------------------


def test_no_manifest_no_policy_uses_variant_default():
    out = resolve(
        manifest_choice=None,
        org_policy=None,
        org_policy_kind_key="postgres",
        variant=_both_default_shared(),
    )
    assert out.mode == Isolation.SHARED
    assert out.source == SOURCE_VARIANT_DEFAULT


def test_manifest_dedicated_overrides_default_when_supported():
    out = resolve(
        manifest_choice=Isolation.DEDICATED,
        org_policy=None,
        org_policy_kind_key="postgres",
        variant=_both_default_shared(),
    )
    assert out.mode == Isolation.DEDICATED
    assert out.source == SOURCE_MANIFEST


def test_manifest_choice_rejected_when_unsupported():
    """Manifest asked for shared but variant only supports dedicated."""
    with pytest.raises(IsolationError, match="only supports"):
        resolve(
            manifest_choice=Isolation.SHARED,
            org_policy=None,
            org_policy_kind_key="postgres",
            variant=_dedicated_only(),
        )


def test_org_policy_floors_manifest_choice():
    """Org policy = hard floor. Even when the manifest declares
    shared, a HIPAA org's dedicated policy wins."""
    out = resolve(
        manifest_choice=Isolation.SHARED,
        org_policy={"postgres": Isolation.DEDICATED},
        org_policy_kind_key="postgres",
        variant=_both_default_shared(),
    )
    assert out.mode == Isolation.DEDICATED
    assert out.source == SOURCE_ORG_POLICY


def test_org_policy_only_applies_to_matching_kind():
    out = resolve(
        manifest_choice=None,
        org_policy={"postgres": Isolation.DEDICATED},
        org_policy_kind_key="redis",  # different kind
        variant=_both_default_shared(),
    )
    # No org policy for redis → variant default
    assert out.source == SOURCE_VARIANT_DEFAULT


def test_org_policy_rejected_when_variant_cant_satisfy():
    """Compliance requires dedicated but the chosen variant only
    supports shared. Reject loudly so the operator picks a different
    variant or updates the policy."""
    with pytest.raises(IsolationError, match="org policy"):
        resolve(
            manifest_choice=None,
            org_policy={"postgres": Isolation.DEDICATED},
            org_policy_kind_key="postgres",
            variant=_shared_only(),
        )


def test_dedicated_only_variant_uses_dedicated_default():
    out = resolve(
        manifest_choice=None,
        org_policy=None,
        org_policy_kind_key="postgres",
        variant=_dedicated_only(),
    )
    assert out.mode == Isolation.DEDICATED
    assert out.source == SOURCE_VARIANT_DEFAULT


def test_decision_carries_originating_variant():
    """The decision wraps the variant so the caller can pass it
    straight to the driver."""
    variant = _both_default_shared()
    out = resolve(
        manifest_choice=None,
        org_policy=None,
        org_policy_kind_key="postgres",
        variant=variant,
    )
    assert out.variant is variant
