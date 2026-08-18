"""The declared-gap guard (#21).

The first test is the guard itself. The rest prove the guard can fail: a
guard that only ever confirms today's state would pass forever while
portability quietly narrowed.

``DECLARED_GAPS`` is empty since #1453 closed the last default-tier gap, so
the failure-mode tests build the ledger row they need instead of borrowing a
real one. That is the stronger form anyway: they now prove the guard's
behaviour rather than the current contents of the ledger, and closing or
opening a gap no longer edits the guard's own tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from _sdk.availability import MATRIX, AvailabilityMatrix, ManagedServiceEntry
from _sdk.coverage import CLOUDS, OPT_IN_TIER, coverage, gaps
from _sdk.coverage_gaps import (
    CLASSIFICATIONS,
    DECLARED_GAPS,
    DeclaredGap,
    describe,
    reconcile,
)

_FIXTURE_KIND = "ledger_db"
_FIXTURE_CLOUD = "gcp"


def _matrix_with(*entries: ManagedServiceEntry) -> AvailabilityMatrix:
    return AvailabilityMatrix(
        drivers=MATRIX.drivers,
        managed_services=(*MATRIX.managed_services, *entries),
    )


def _one_cloud_kind() -> AvailabilityMatrix:
    """A kind executable on AWS only, so it owns exactly two open gaps."""
    return _matrix_with(
        ManagedServiceEntry(
            kind=_FIXTURE_KIND,
            variant="qldb",
            plugin_id="aws",
            description="fixture-only kind",
        ),
    )


if TYPE_CHECKING:
    import pytest


def test_declared_gaps_match_the_live_matrix() -> None:
    """THE GUARD. Every hole in cross-cloud coverage is declared, and every
    declared hole still exists."""
    report = reconcile()
    assert report.ok, describe(report)


def test_the_default_tier_surface_has_no_open_gaps() -> None:
    """The #1447 exit state, asserted rather than described. Every kind in the
    guaranteed cross-cloud surface is now reachable on all three clouds, which
    is what makes an empty ledger the right answer instead of a missing one."""
    assert gaps() == ()
    assert DECLARED_GAPS == ()


def test_guard_catches_a_gap_dropped_from_the_ledger() -> None:
    """Deleting a row that still describes a real hole must fail: that is how
    an unexplained gap sneaks in as a ledger edit rather than a code change."""
    matrix = _one_cloud_kind()
    declared = (
        DeclaredGap(kind=_FIXTURE_KIND, cloud="azure", classification="buildable"),
        DeclaredGap(kind=_FIXTURE_KIND, cloud=_FIXTURE_CLOUD, classification="buildable"),
    )
    assert reconcile(matrix=matrix, declared=declared).ok, "both fixture holes start declared"

    thinned = tuple(g for g in declared if g.cloud != _FIXTURE_CLOUD)
    report = reconcile(matrix=matrix, declared=thinned)

    assert not report.ok
    assert report.undeclared == ((_FIXTURE_KIND, _FIXTURE_CLOUD),)
    assert report.stale == ()
    message = describe(report)
    assert f"UNDECLARED GAP {_FIXTURE_KIND}/{_FIXTURE_CLOUD}" in message
    assert repr(_FIXTURE_CLOUD) in message
    assert "DECLARED_GAPS" in message
    assert "reference=<issue url>" in message


def test_guard_catches_a_new_kind_shipped_on_one_cloud_only() -> None:
    """The regression this exists to stop: a kind lands on AWS, the other two
    clouds are never covered, nobody declares why."""
    report = reconcile(matrix=_one_cloud_kind())

    assert not report.ok
    assert report.undeclared == (("ledger_db", "azure"), ("ledger_db", "gcp"))
    assert report.stale == ()
    message = describe(report)
    assert "UNDECLARED GAP ledger_db/gcp" in message
    assert "UNDECLARED GAP ledger_db/azure" in message


def test_guard_catches_a_stale_row_after_a_driver_lands() -> None:
    """Shipping the missing variant must force the ledger row out; otherwise
    the doc keeps telling people a closed gap is open.

    The whole of #1453: ``email``/``gcp`` was declared, the SMTP relay landed,
    and the guard is what refuses to let the explanation outlive the hole."""
    declared = (DeclaredGap(kind="email", cloud="gcp", classification="buildable"),)

    report = reconcile(declared=declared)

    assert not report.ok
    assert report.stale == (("email", "gcp"),)
    assert report.undeclared == (), "no other hole is open in the live matrix"
    message = describe(report)
    assert "STALE LEDGER ROW email/gcp" in message
    assert "Delete the row" in message


def test_guard_reports_both_directions_at_once() -> None:
    """One run can be wrong both ways. The report must carry both, or fixing
    the first failure hides the second until the next push."""
    report = reconcile(
        matrix=_one_cloud_kind(),
        declared=(DeclaredGap(kind="email", cloud="gcp", classification="buildable"),),
    )

    assert set(report.undeclared) == {("ledger_db", "azure"), ("ledger_db", "gcp")}
    assert report.stale == (("email", "gcp"),)
    message = describe(report)
    assert "UNDECLARED GAP ledger_db/gcp" in message
    assert "STALE LEDGER ROW email/gcp" in message


def test_the_opt_in_tier_is_load_bearing_and_not_a_hiding_place() -> None:
    """A kind belongs to the tier or to the ledger, never both. The tier also
    has to be doing real work: if nothing in it had a hole, the exclusion
    would be proving nothing and the tests below would be theatre."""
    rows = {row.kind: row for row in coverage()}
    assert {kind for kind in OPT_IN_TIER if not rows[kind].is_portable}, (
        "every opt-in kind is reachable everywhere; the tier excludes nothing"
    )
    assert not {g.kind for g in DECLARED_GAPS} & OPT_IN_TIER, "opt-in kinds must not carry ledger rows"
    assert not {kind for kind, _ in gaps()} & OPT_IN_TIER


def test_pulling_a_kind_out_of_the_opt_in_tier_reopens_its_holes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Promoting a kind into the guaranteed surface is a real decision with a
    real bill: the guard immediately demands its missing clouds be explained.
    `sms` ships on AWS only, so promoting it owes two rows."""
    monkeypatch.setattr("_sdk.coverage.OPT_IN_TIER", OPT_IN_TIER - {"sms"})

    report = reconcile()

    assert not report.ok
    assert report.undeclared == (("sms", "azure"), ("sms", "gcp"))
    assert report.stale == ()
    assert "UNDECLARED GAP sms/gcp" in describe(report)


def test_ledger_rows_are_unique_and_well_formed() -> None:
    seen = {(g.kind, g.cloud) for g in DECLARED_GAPS}
    assert len(seen) == len(DECLARED_GAPS), "duplicate ledger rows"
    for gap in DECLARED_GAPS:
        assert gap.cloud in CLOUDS, f"{gap.kind}/{gap.cloud}: not a public cloud column"
        assert gap.classification in CLASSIFICATIONS, f"{gap.kind}/{gap.cloud}: unknown classification"


def test_planned_variant_rows_agree_with_the_matrix_status() -> None:
    """`planned_variant` is not an opinion: it means a `planned` entry is
    sitting in the matrix. Keeping the two in step stops the ledger from
    calling a roadmap item unbuildable, or vice versa."""
    planned = {
        (m.kind, m.plugin_id) for m in MATRIX.managed_services if m.status == "planned" and m.plugin_id in CLOUDS
    }
    for gap in DECLARED_GAPS:
        declared_planned = gap.classification == "planned_variant"
        has_planned_entry = (gap.kind, gap.cloud) in planned
        assert declared_planned == has_planned_entry, (
            f"{gap.kind}/{gap.cloud}: classification {gap.classification!r} disagrees with the "
            f"matrix ({'a' if has_planned_entry else 'no'} planned variant is declared there)"
        )
