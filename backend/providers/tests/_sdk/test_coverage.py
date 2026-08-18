"""Tests for the cross-cloud coverage view (#21)."""

from __future__ import annotations

from _sdk.availability import AvailabilityMatrix, ManagedServiceEntry
from _sdk.coverage import CLOUDS, COLUMNS, IN_CLUSTER, coverage, gaps
from scripts.coverage_matrix import OUTPUT, render


def _matrix(*entries: ManagedServiceEntry) -> AvailabilityMatrix:
    return AvailabilityMatrix(managed_services=entries)


def test_every_column_gets_a_cell_even_when_the_plugin_ships_nothing() -> None:
    row = coverage(_matrix(ManagedServiceEntry(kind="queue", variant="sqs", plugin_id="aws")))[0]
    assert tuple(cell.plugin_id for cell in row.cells) == COLUMNS
    assert row.cell("gcp").executable == ()
    assert row.covered_clouds == ("aws",)
    assert row.missing_clouds == ("gcp", "azure")
    assert not row.is_cloud_portable


def test_planned_and_deprecated_are_not_coverage() -> None:
    matrix = _matrix(
        ManagedServiceEntry(kind="cdn", variant="cloudfront", plugin_id="aws"),
        ManagedServiceEntry(kind="cdn", variant="cloud_cdn", plugin_id="gcp", status="preview"),
        ManagedServiceEntry(kind="cdn", variant="front_door", plugin_id="azure", status="planned"),
        ManagedServiceEntry(kind="cdn", variant="old_edge", plugin_id="k8s_native", status="deprecated"),
    )
    row = coverage(matrix)[0]
    assert row.covered_clouds == ("aws", "gcp")
    assert row.cell("azure").planned == ("front_door",)
    assert not row.cell("azure").is_executable
    assert not row.cell(IN_CLUSTER).is_executable
    assert gaps(matrix) == (("cdn", "azure"),)


def test_experimental_counts_as_executable() -> None:
    matrix = _matrix(
        ManagedServiceEntry(kind="stream", variant="kinesis", plugin_id="aws"),
        ManagedServiceEntry(kind="stream", variant="event_hubs", plugin_id="azure", status="experimental"),
    )
    assert gaps(matrix) == (("stream", "gcp"),)


def test_a_kind_only_k8s_native_ships_is_not_a_cloud_gap() -> None:
    """An in-cluster-only kind is a portable answer, not a hole: no cloud
    proves it is a cloud-managed shape, so there is nothing to declare."""
    matrix = _matrix(ManagedServiceEntry(kind="vault_secrets", variant="vault", plugin_id=IN_CLUSTER))
    row = coverage(matrix)[0]
    assert row.cell(IN_CLUSTER).is_executable
    assert row.covered_clouds == ()
    assert gaps(matrix) == ()


def test_gaps_are_sorted_by_kind_then_cloud_order() -> None:
    matrix = _matrix(
        ManagedServiceEntry(kind="zeta", variant="z", plugin_id="gcp"),
        ManagedServiceEntry(kind="alpha", variant="a", plugin_id="gcp"),
    )
    assert gaps(matrix) == (("alpha", "aws"), ("alpha", "azure"), ("zeta", "aws"), ("zeta", "azure"))
    assert CLOUDS == ("aws", "gcp", "azure")


def test_generated_doc_is_current() -> None:
    """The committed table is a generated artifact. Regenerate it with
    `make coverage-matrix` rather than hand-editing."""
    assert OUTPUT.read_text(encoding="utf-8") == render(), (
        "docs/managed_service_coverage.md is stale; run `make coverage-matrix`"
    )


def test_generated_doc_names_every_kind_and_gap() -> None:
    text = render()
    for row in coverage():
        assert f"| `{row.kind}` |" in text
    for kind, cloud in gaps():
        assert f"| `{kind}` | {cloud} |" in text
