"""Tests for the CI matrix-consistency check (#26)."""

from __future__ import annotations

from _sdk.availability import (
    AvailabilityMatrix,
    DriverEntry,
    ManagedServiceEntry,
)
from _sdk.base import ProviderPlugin
from _sdk.matrix_check import check_matrix


class _Stub:
    pass


def _aws_plugin() -> ProviderPlugin:
    return ProviderPlugin(
        id="aws", display_name="aws",
        drivers={
            "cluster": _Stub, "ingress": _Stub, "dns": _Stub,
            "tls": _Stub, "secrets": _Stub, "identity": _Stub,
            "registry": _Stub,
        },
        managed_service_drivers={("object_store", "s3"): _Stub},
    )


def test_in_sync_plugin_and_matrix_passes() -> None:
    plugin = _aws_plugin()
    matrix = AvailabilityMatrix(
        drivers=tuple(
            DriverEntry(role=role, plugin_id="aws")
            for role in (
                "cluster", "ingress", "dns", "tls",
                "secrets", "identity", "registry",
            )
        ),
        managed_services=(
            ManagedServiceEntry(
                kind="object_store", variant="s3", plugin_id="aws",
            ),
        ),
    )
    report = check_matrix(plugins=[plugin], matrix=matrix)
    assert report.ok is True
    assert report.issues == []


def test_manifest_missing_from_matrix_drift() -> None:
    plugin = _aws_plugin()
    matrix = AvailabilityMatrix()  # empty
    report = check_matrix(plugins=[plugin], matrix=matrix)
    assert report.ok is False
    codes = {iss.code for iss in report.issues}
    assert "manifest_not_in_matrix" in codes


def test_matrix_listed_role_not_in_manifest() -> None:
    plugin = ProviderPlugin(
        id="aws", display_name="aws",
        drivers={"cluster": _Stub},
    )
    matrix = AvailabilityMatrix(
        drivers=(
            DriverEntry(role="cluster", plugin_id="aws"),
            DriverEntry(role="dns", plugin_id="aws"),
        ),
    )
    report = check_matrix(plugins=[plugin], matrix=matrix)
    codes = {iss.code for iss in report.issues}
    assert "matrix_not_in_manifest" in codes


def test_matrix_with_unknown_plugin() -> None:
    matrix = AvailabilityMatrix(
        drivers=(DriverEntry(role="cluster", plugin_id="ghost"),),
    )
    report = check_matrix(plugins=[], matrix=matrix)
    codes = {iss.code for iss in report.issues}
    assert "matrix_unknown_plugin" in codes


def test_live_canonical_matrix_in_sync() -> None:
    """The canonical MATRIX must stay in sync with all real
    PLUGIN manifests. Future driver additions must touch both."""
    from _sdk.availability import MATRIX
    from _sdk.matrix_check import load_plugins

    plugins = load_plugins()
    if not plugins:
        # The entry-point group can be empty in some test
        # contexts (no installed dist); skip in that case.
        return
    report = check_matrix(plugins=plugins, matrix=MATRIX)
    assert report.ok is True, [iss.detail for iss in report.issues]
