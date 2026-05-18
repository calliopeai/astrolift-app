"""Tests for TenantClusterRegistry + connectivity probe (#17)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from _sdk.base import ProviderPlugin
from _sdk.cluster_registry import (
    TenantClusterRegistry,
    probe_connectivity,
)
from _sdk.composition import CompositionRegistry


class _Stub:
    pass


def _full_plugin() -> ProviderPlugin:
    return ProviderPlugin(
        id="aws", display_name="aws",
        drivers={
            r: _Stub for r in (
                "cluster", "ingress", "dns", "tls",
                "secrets", "identity", "registry",
            )
        },
    )


def _bare_plugin() -> ProviderPlugin:
    return ProviderPlugin(
        id="bare", display_name="bare",
        drivers={"cluster": _Stub},
    )


def test_register_full_plugin_succeeds() -> None:
    registry = TenantClusterRegistry()
    record = registry.register(
        cluster_id="aws-prod-east",
        plugin=_full_plugin(),
        display_name="AWS Prod (us-east-1)",
        location="us-east-1",
        config={"region": "us-east-1", "account_id": "123456789012"},
    )
    assert record.cluster_id == "aws-prod-east"
    assert record.plugin_id == "aws"


def test_register_missing_required_role_raises() -> None:
    registry = TenantClusterRegistry()
    with pytest.raises(ValueError, match="missing required roles"):
        registry.register(
            cluster_id="bare", plugin=_bare_plugin(),
            display_name="bare",
            location="local",
        )


def test_register_with_composition_satisfies_missing() -> None:
    composition = CompositionRegistry()
    for role in (
        "ingress", "dns", "tls", "secrets", "identity", "registry",
    ):
        composition.delegate_driver(
            target_plugin_id="bare", role=role,
            source_plugin_id="k8s_native",
        )
    registry = TenantClusterRegistry()
    record = registry.register(
        cluster_id="bare", plugin=_bare_plugin(),
        display_name="bare", location="local",
        composition=composition,
    )
    assert record.composition is composition


def test_get_and_list() -> None:
    registry = TenantClusterRegistry()
    registry.register(
        cluster_id="aws-1", plugin=_full_plugin(),
        display_name="AWS-1", location="us-east-1",
    )
    registry.register(
        cluster_id="aws-2", plugin=_full_plugin(),
        display_name="AWS-2", location="us-west-2",
    )
    assert registry.get("aws-1").display_name == "AWS-1"
    ids = {r.cluster_id for r in registry.list()}
    assert ids == {"aws-1", "aws-2"}


def test_deregister_removes() -> None:
    registry = TenantClusterRegistry()
    registry.register(
        cluster_id="x", plugin=_full_plugin(),
        display_name="X", location="us-east-1",
    )
    registry.deregister("x")
    assert registry.get("x") is None


def test_probe_succeeds_when_get_namespace_works() -> None:
    registry = TenantClusterRegistry()
    record = registry.register(
        cluster_id="aws-1", plugin=_full_plugin(),
        display_name="AWS-1", location="us-east-1",
    )
    cluster_driver = MagicMock()
    cluster_driver.get_namespace.return_value = MagicMock()
    result = probe_connectivity(
        record=record, cluster_driver=cluster_driver,
        now_iso="2026-05-09T00:00:00Z",
    )
    assert result.ok is True
    assert "reachable" in result.message
    cluster_driver.get_namespace.assert_called_once_with(
        "aws-1", "astrolift-system",
    )


def test_probe_fails_on_apiserver_error() -> None:
    registry = TenantClusterRegistry()
    record = registry.register(
        cluster_id="aws-1", plugin=_full_plugin(),
        display_name="AWS-1", location="us-east-1",
    )
    cluster_driver = MagicMock()
    cluster_driver.get_namespace.side_effect = RuntimeError(
        "401 unauthorized",
    )
    result = probe_connectivity(
        record=record, cluster_driver=cluster_driver,
        now_iso="2026-05-09T00:00:00Z",
    )
    assert result.ok is False
    assert "401" in result.message
