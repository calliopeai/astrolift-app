"""Tests for capability negotiation + binding validation (#18)."""

from __future__ import annotations

from _sdk.availability import MATRIX
from _sdk.base import ProviderPlugin
from _sdk.capabilities import (
    ServiceDependency,
    negotiate_variant,
    validate_cluster_binding,
)
from _sdk.composition import CompositionRegistry


class _StubDriver:
    pass


def _full_plugin(plugin_id: str = "aws") -> ProviderPlugin:
    return ProviderPlugin(
        id=plugin_id,
        display_name=plugin_id,
        drivers={
            "cluster": _StubDriver,
            "ingress": _StubDriver,
            "dns": _StubDriver,
            "tls": _StubDriver,
            "secrets": _StubDriver,
            "identity": _StubDriver,
            "registry": _StubDriver,
        },
        managed_service_drivers={
            ("object_store", "s3"): _StubDriver,
        },
    )


def test_full_plugin_binds_with_no_deps() -> None:
    plugin = _full_plugin("aws")
    result = validate_cluster_binding(plugin=plugin, matrix=MATRIX)
    assert result.ok is True
    assert result.issues == []


def test_missing_required_role_flagged() -> None:
    plugin = ProviderPlugin(
        id="bare", display_name="bare",
        drivers={"cluster": _StubDriver},
    )
    result = validate_cluster_binding(plugin=plugin, matrix=MATRIX)
    assert result.ok is False
    codes = {iss.code for iss in result.issues}
    assert "missing_required_role" in codes


def test_required_dep_satisfied_by_plugin() -> None:
    plugin = _full_plugin("aws")
    result = validate_cluster_binding(
        plugin=plugin, matrix=MATRIX,
        service_deps=[ServiceDependency(kind="object_store", variant="s3")],
    )
    assert result.ok is True


def test_required_dep_missing_flagged() -> None:
    plugin = _full_plugin("aws")
    result = validate_cluster_binding(
        plugin=plugin, matrix=MATRIX,
        service_deps=[
            ServiceDependency(kind="postgres", variant="rds"),
        ],
    )
    assert result.ok is False
    issue = result.issues[0]
    assert issue.code == "missing_managed_service"
    assert issue.kind == "postgres"
    assert issue.variant == "rds"


def test_optional_dep_missing_does_not_fail() -> None:
    plugin = _full_plugin("aws")
    result = validate_cluster_binding(
        plugin=plugin, matrix=MATRIX,
        service_deps=[
            ServiceDependency(
                kind="postgres", variant="rds", required=False,
            ),
        ],
    )
    assert result.ok is True


def test_composition_satisfies_missing_role() -> None:
    composition = CompositionRegistry()
    composition.delegate_driver(
        target_plugin_id="bare", role="dns",
        source_plugin_id="external",
    )
    plugin = ProviderPlugin(
        id="bare", display_name="bare",
        drivers={
            "cluster": _StubDriver, "ingress": _StubDriver,
            "tls": _StubDriver, "secrets": _StubDriver,
            "identity": _StubDriver, "registry": _StubDriver,
        },
    )
    result = validate_cluster_binding(
        plugin=plugin, matrix=MATRIX, composition=composition,
    )
    # dns is missing on bare but composition delegates → ok
    assert result.ok is True


def test_composition_satisfies_missing_managed_service() -> None:
    composition = CompositionRegistry()
    composition.delegate_managed_service(
        target_plugin_id="aws", kind="postgres", variant="cnpg",
        source_plugin_id="k8s_native",
    )
    plugin = _full_plugin("aws")
    result = validate_cluster_binding(
        plugin=plugin, matrix=MATRIX, composition=composition,
        service_deps=[
            ServiceDependency(kind="postgres", variant="cnpg"),
        ],
    )
    assert result.ok is True


def test_negotiate_variant_picks_first_match() -> None:
    plugin = _full_plugin("aws")
    chosen = negotiate_variant(
        plugin=plugin, matrix=MATRIX, kind="object_store",
        preferred_variants=["gcs", "blob", "s3"],
    )
    # gcs/blob aren't in the aws plugin → s3
    assert chosen == "s3"


def test_negotiate_variant_returns_none_when_no_match() -> None:
    plugin = _full_plugin("aws")
    chosen = negotiate_variant(
        plugin=plugin, matrix=MATRIX, kind="object_store",
        preferred_variants=["gcs", "blob"],
    )
    assert chosen is None
