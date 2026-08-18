"""Declared-gap ledger for cross-cloud managed-service coverage (#21).

Every hole in the coverage view is either known and explained, or it is a
regression. This ledger holds the known ones; ``reconcile()`` compares it
against what the live matrix actually computes and the guard test in
``tests/_sdk/test_coverage_ledger.py`` fails on any disagreement, in either
direction:

* an **undeclared** gap means someone shipped a kind on one cloud without
  covering the others and without saying why -- the portability claim
  silently narrowed;
* a **stale** row means the gap was closed and the explanation is now a lie.

A row is not a to-do list item, it is an answer to "why can I not use this
kind on that cloud?". Keep the classification honest; ``no_cloud_equivalent``
is a statement about the cloud, not about our backlog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from _sdk.availability import MATRIX, AvailabilityMatrix
from _sdk.coverage import gaps

Classification = Literal[
    "buildable",
    "planned_variant",
    "no_cloud_equivalent",
    "taxonomy",
]

CLASSIFICATIONS: dict[Classification, str] = {
    "buildable": "the cloud has a suitable managed service, nobody has written the driver",
    "planned_variant": "a variant is already declared with status planned; implement it",
    "no_cloud_equivalent": "that cloud offers nothing equivalent; the portable answer is another kind",
    "taxonomy": "a driver exists but is registered under a different kind; classification question",
}


@dataclass(frozen=True)
class DeclaredGap:
    kind: str
    cloud: str
    classification: Classification
    reference: str = ""
    """Tracking issue URL. Empty until the ticket is filed."""


# Every row here is a gap that exists right now and is understood. Adding a
# row is how you take responsibility for narrowing portability; deleting one
# is mandatory the moment the driver lands.
DECLARED_GAPS: tuple[DeclaredGap, ...] = (
    DeclaredGap(kind="cache", cloud="gcp", classification="buildable"),
    DeclaredGap(kind="cache", cloud="azure", classification="no_cloud_equivalent"),
    DeclaredGap(kind="cdn", cloud="azure", classification="planned_variant"),
    DeclaredGap(kind="database_proxy", cloud="gcp", classification="no_cloud_equivalent"),
    DeclaredGap(kind="database_proxy", cloud="azure", classification="no_cloud_equivalent"),
    DeclaredGap(kind="email", cloud="gcp", classification="planned_variant"),
    DeclaredGap(kind="encryption_key", cloud="azure", classification="planned_variant"),
    DeclaredGap(kind="mq", cloud="gcp", classification="no_cloud_equivalent"),
    DeclaredGap(kind="mq", cloud="azure", classification="no_cloud_equivalent"),
    DeclaredGap(kind="observability", cloud="azure", classification="planned_variant"),
    DeclaredGap(kind="search", cloud="gcp", classification="planned_variant"),
    DeclaredGap(kind="sms", cloud="gcp", classification="no_cloud_equivalent"),
    DeclaredGap(kind="sms", cloud="azure", classification="planned_variant"),
    DeclaredGap(kind="stream", cloud="gcp", classification="no_cloud_equivalent"),
    DeclaredGap(kind="warehouse", cloud="azure", classification="planned_variant"),
    DeclaredGap(kind="wide_column", cloud="gcp", classification="taxonomy"),
    DeclaredGap(kind="workflow_engine", cloud="azure", classification="planned_variant"),
)


@dataclass(frozen=True)
class LedgerReport:
    undeclared: tuple[tuple[str, str], ...]
    """Computed gaps with no ledger row."""

    stale: tuple[tuple[str, str], ...]
    """Ledger rows whose gap no longer exists."""

    @property
    def ok(self) -> bool:
        return not self.undeclared and not self.stale


def reconcile(
    *,
    matrix: AvailabilityMatrix = MATRIX,
    declared: tuple[DeclaredGap, ...] = DECLARED_GAPS,
) -> LedgerReport:
    computed = set(gaps(matrix))
    known = {(row.kind, row.cloud) for row in declared}
    return LedgerReport(
        undeclared=tuple(sorted(computed - known)),
        stale=tuple(sorted(known - computed)),
    )


def describe(report: LedgerReport) -> str:
    """Failure text for the guard, written for whoever broke it."""
    lines: list[str] = []
    for kind, cloud in report.undeclared:
        lines.append(
            f"UNDECLARED GAP {kind}/{cloud}: kind {kind!r} is executable on another cloud "
            f"but has no executable variant on {cloud!r}. Ship the {cloud} variant, or "
            f"add DeclaredGap(kind={kind!r}, cloud={cloud!r}, classification=..., "
            f"reference=<issue url>) to DECLARED_GAPS in _sdk/coverage_gaps.py. "
            f"Classifications: {', '.join(CLASSIFICATIONS)}."
        )
    for kind, cloud in report.stale:
        lines.append(
            f"STALE LEDGER ROW {kind}/{cloud}: {kind!r} is now executable on {cloud!r} "
            f"(or no longer executable on any cloud). Delete the row from DECLARED_GAPS "
            f"in _sdk/coverage_gaps.py."
        )
    return "\n".join(lines)
