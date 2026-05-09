"""Tests for hybrid-delegation registry (#54)."""

from __future__ import annotations

import pytest

from _sdk.composition import CompositionRegistry


def test_delegate_driver_rejects_self() -> None:
    registry = CompositionRegistry()
    with pytest.raises(ValueError):
        registry.delegate_driver(
            target_plugin_id="aws",
            role="cluster",
            source_plugin_id="aws",
        )


def test_delegate_managed_service_rejects_self() -> None:
    registry = CompositionRegistry()
    with pytest.raises(ValueError):
        registry.delegate_managed_service(
            target_plugin_id="aws",
            kind="postgres", variant="rds",
            source_plugin_id="aws",
        )


def test_resolve_driver_returns_none_when_not_delegated() -> None:
    registry = CompositionRegistry()
    assert registry.resolve_driver(
        target_plugin_id="aws", role="cluster",
    ) is None


def test_resolve_driver_returns_source_when_delegated() -> None:
    registry = CompositionRegistry()
    registry.delegate_driver(
        target_plugin_id="aws",
        role="cluster",
        source_plugin_id="k8s_native",
    )
    assert registry.resolve_driver(
        target_plugin_id="aws", role="cluster",
    ) == "k8s_native"


def test_resolve_managed_service_postgres_cnpg_on_aws() -> None:
    """The motivating case: operator runs CNPG (in-cluster) on
    EKS instead of RDS."""
    registry = CompositionRegistry()
    registry.delegate_managed_service(
        target_plugin_id="aws",
        kind="postgres", variant="cnpg",
        source_plugin_id="k8s_native",
    )
    assert registry.resolve_managed_service(
        target_plugin_id="aws",
        kind="postgres", variant="cnpg",
    ) == "k8s_native"
    # AWS-native variant unaffected
    assert registry.resolve_managed_service(
        target_plugin_id="aws",
        kind="postgres", variant="rds",
    ) is None


def test_multiple_delegations_per_target() -> None:
    registry = CompositionRegistry()
    registry.delegate_driver(
        target_plugin_id="aws", role="cluster",
        source_plugin_id="k8s_native",
    )
    registry.delegate_driver(
        target_plugin_id="aws", role="ingress",
        source_plugin_id="k8s_native",
    )
    assert registry.resolve_driver(
        target_plugin_id="aws", role="cluster",
    ) == "k8s_native"
    assert registry.resolve_driver(
        target_plugin_id="aws", role="ingress",
    ) == "k8s_native"
