"""Tests for the cross-cloud coverage view (#21).

Rule tests build their own matrix around a kind that exists nowhere in the
catalogue, so they keep proving the rule after the real catalogue moves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from _sdk.availability import AvailabilityMatrix, ManagedServiceEntry
from _sdk.coverage import CLOUDS, COLUMNS, IN_CLUSTER, OPT_IN_TIER, coverage, gaps
from scripts.coverage_matrix import OUTPUT, render

if TYPE_CHECKING:
    import pytest


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
        ManagedServiceEntry(kind="ledger_db", variant="qldb", plugin_id="aws"),
        ManagedServiceEntry(kind="ledger_db", variant="spanner_ledger", plugin_id="gcp", status="preview"),
        ManagedServiceEntry(kind="ledger_db", variant="confidential_ledger", plugin_id="azure", status="planned"),
        ManagedServiceEntry(kind="ledger_db", variant="old_immudb", plugin_id="k8s_native", status="deprecated"),
    )
    row = coverage(matrix)[0]
    assert row.covered_clouds == ("aws", "gcp")
    assert row.cell("azure").planned == ("confidential_ledger",)
    assert not row.cell("azure").is_executable
    assert not row.cell(IN_CLUSTER).is_executable
    assert gaps(matrix) == (("ledger_db", "azure"),)


def test_experimental_counts_as_executable() -> None:
    matrix = _matrix(
        ManagedServiceEntry(kind="ledger_db", variant="qldb", plugin_id="aws"),
        ManagedServiceEntry(kind="ledger_db", variant="confidential_ledger", plugin_id="azure", status="experimental"),
    )
    assert gaps(matrix) == (("ledger_db", "gcp"),)


def test_a_kind_only_k8s_native_ships_is_not_a_cloud_gap() -> None:
    """An in-cluster-only kind is a portable answer, not a hole: no cloud
    proves it is a cloud-managed shape, so there is nothing to declare."""
    matrix = _matrix(ManagedServiceEntry(kind="vault_secrets", variant="vault", plugin_id=IN_CLUSTER))
    row = coverage(matrix)[0]
    assert row.cell(IN_CLUSTER).is_executable
    assert row.covered_clouds == ()
    assert gaps(matrix) == ()


def test_an_in_cluster_variant_covers_the_clouds_the_kind_misses() -> None:
    """We run the cluster, so one in-cluster variant is parity everywhere.
    Pair this with the next test: the k8s_native row is the only difference
    between them, and it is what turns two gaps into none."""
    matrix = _matrix(
        ManagedServiceEntry(kind="ledger_db", variant="qldb", plugin_id="aws"),
        ManagedServiceEntry(kind="ledger_db", variant="immudb", plugin_id=IN_CLUSTER),
    )
    row = coverage(matrix)[0]
    assert row.missing_clouds == ("gcp", "azure")
    assert not row.is_cloud_portable, "no managed variant on GCP or Azure"
    assert row.is_portable, "the in-cluster variant reaches both"
    assert gaps(matrix) == ()


def test_the_same_kind_without_an_in_cluster_variant_is_two_gaps() -> None:
    matrix = _matrix(ManagedServiceEntry(kind="ledger_db", variant="qldb", plugin_id="aws"))
    row = coverage(matrix)[0]
    assert row.missing_clouds == ("gcp", "azure")
    assert not row.is_portable
    assert gaps(matrix) == (("ledger_db", "gcp"), ("ledger_db", "azure"))


def test_a_planned_in_cluster_variant_does_not_make_a_kind_portable() -> None:
    """`planned` cannot be provisioned, so it cannot be the thing that closes
    a hole -- otherwise writing a roadmap entry would erase two real gaps."""
    matrix = _matrix(
        ManagedServiceEntry(kind="ledger_db", variant="qldb", plugin_id="aws"),
        ManagedServiceEntry(kind="ledger_db", variant="immudb", plugin_id=IN_CLUSTER, status="planned"),
    )
    assert not coverage(matrix)[0].is_portable
    assert gaps(matrix) == (("ledger_db", "gcp"), ("ledger_db", "azure"))


def test_an_opt_in_kind_with_holes_on_two_clouds_is_not_a_gap() -> None:
    """Opt-in kinds are outside the guaranteed cross-cloud surface. The driver
    works; the hole is a catalogue choice, so it is not tracked as work."""
    assert "sms" in OPT_IN_TIER
    matrix = _matrix(ManagedServiceEntry(kind="sms", variant="sns_sms", plugin_id="aws"))
    row = coverage(matrix)[0]
    assert row.missing_clouds == ("gcp", "azure")
    assert not row.is_portable, "the exclusion is about the tier, not about reachability"
    assert gaps(matrix) == ()


def test_dropping_a_kind_from_the_opt_in_tier_surfaces_its_holes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror of the test above, on the same matrix: nothing about the
    kind changed, only the tier it sits in, and the holes come back."""
    matrix = _matrix(ManagedServiceEntry(kind="sms", variant="sns_sms", plugin_id="aws"))
    monkeypatch.setattr("_sdk.coverage.OPT_IN_TIER", OPT_IN_TIER - {"sms"})
    assert gaps(matrix) == (("sms", "gcp"), ("sms", "azure"))


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
        assert f"| `{row.kind}`" in text
    for kind, cloud in gaps():
        assert f"| `{kind}` | {cloud} |" in text


def test_generated_doc_marks_the_opt_in_rows() -> None:
    """A reader scanning the table has to be able to tell which rows the gap
    ledger deliberately ignores, or their empty cells look like unowned work."""
    text = render()
    for kind in OPT_IN_TIER:
        assert f"| `{kind}` (opt-in) |" in text
    for row in coverage():
        if row.kind not in OPT_IN_TIER:
            assert f"| `{row.kind}` |" in text, f"{row.kind} is not opt-in and must not be marked"
