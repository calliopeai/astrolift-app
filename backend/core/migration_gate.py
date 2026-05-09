"""
Destructive-migration safety gate (#169 part 1, spec 04 §12).

Walks Django migration operation lists to spot destructive ops
(column drops, table drops, type changes that lose data) and
returns a structured assessment. CI runs the check on every PR;
the deploy workflow runs it again before applying.

Why parse operations instead of diffing schemas:

- Django's migration ``Operation`` classes carry intent. ``RemoveField``
  is unambiguous; a schema diff would have to guess.
- Migrations get checked at *plan time* (CI / preview mode) before
  any DDL runs. By the time a schema diff would catch it, the
  destructive change has already executed.

Decisions:

- ``DESTRUCTIVE`` — drops or shrinks data. Blocked in CI unless
  the operator passes ``--allow-destructive`` (workflow flag).
- ``RISKY`` — changes that may lose data depending on existing
  values (e.g. type narrowing). Warned, not blocked.
- ``SAFE`` — additive changes, index changes, RunPython without
  destructive side effects (we can't introspect arbitrary Python,
  so RunPython is conservatively reported as RISKY).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from enum import Enum


class MigrationRisk(str, Enum):
    SAFE = "safe"
    RISKY = "risky"
    DESTRUCTIVE = "destructive"


# Operation class names that are unambiguously destructive — drops
# rows or columns, schemas can't be reconstructed without a backup.
DESTRUCTIVE_OPS: frozenset[str] = frozenset({
    "DeleteModel",
    "RemoveField",
    "RemoveConstraint",   # only sometimes destructive — see exception
    "RemoveIndex",
})

# These touch shape but don't drop data per se; flagged RISKY.
RISKY_OPS: frozenset[str] = frozenset({
    "AlterField",         # may narrow a type
    "RenameField",        # data preserved, but ORM-level breaking
    "RenameModel",
    "RunPython",          # opaque to static analysis
    "RunSQL",             # opaque
})


@dataclasses.dataclass(frozen=True, slots=True)
class OperationAssessment:
    op_kind: str
    risk: MigrationRisk
    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class MigrationAssessment:
    """Summary of one migration's risk profile."""

    overall: MigrationRisk
    operations: tuple[OperationAssessment, ...]
    destructive_ops: tuple[OperationAssessment, ...]
    risky_ops: tuple[OperationAssessment, ...]


def assess_operations(operations: Iterable[object]) -> MigrationAssessment:
    """Assess a list of Django ``Operation`` instances (or anything
    with the same ``__class__.__name__``-based API).

    The function is duck-typed — pass instances or stand-in objects
    with a ``.__class__.__name__`` attribute. Used by tests without
    needing the full Django app boot.
    """
    assessments: list[OperationAssessment] = []
    for op in operations:
        kind = type(op).__name__
        risk, detail = _classify(op, kind)
        assessments.append(OperationAssessment(op_kind=kind, risk=risk, detail=detail))

    destructive = tuple(a for a in assessments if a.risk == MigrationRisk.DESTRUCTIVE)
    risky = tuple(a for a in assessments if a.risk == MigrationRisk.RISKY)
    overall = (
        MigrationRisk.DESTRUCTIVE if destructive
        else MigrationRisk.RISKY if risky
        else MigrationRisk.SAFE
    )
    return MigrationAssessment(
        overall=overall,
        operations=tuple(assessments),
        destructive_ops=destructive,
        risky_ops=risky,
    )


def _classify(op: object, kind: str) -> tuple[MigrationRisk, str]:
    if kind in DESTRUCTIVE_OPS:
        # ``RemoveConstraint`` is only destructive for unique
        # constraints (loses uniqueness invariant) — but introspecting
        # the constraint model field requires the ORM. We err on the
        # side of flagging it; safe migrations can list them in an
        # allow-list per migration file.
        target = (
            getattr(op, "name", None)
            or getattr(op, "model_name", None)
            or ""
        )
        return MigrationRisk.DESTRUCTIVE, f"{kind}({target})"
    if kind in RISKY_OPS:
        target = (
            getattr(op, "name", None)
            or getattr(op, "old_name", None)
            or ""
        )
        return MigrationRisk.RISKY, f"{kind}({target})"
    return MigrationRisk.SAFE, kind


class DestructiveMigrationBlocked(Exception):
    """Raised by :func:`gate` when a destructive migration tries to
    apply without the operator override."""

    def __init__(self, *, assessment: MigrationAssessment, migration: str = ""):
        self.assessment = assessment
        self.migration = migration
        ops = ", ".join(a.detail for a in assessment.destructive_ops)
        super().__init__(
            f"destructive migration{f' {migration!r}' if migration else ''} "
            f"blocked: {ops}; pass allow_destructive=True with operator approval"
        )


def gate(
    *,
    assessment: MigrationAssessment,
    allow_destructive: bool,
    migration_label: str = "",
) -> None:
    """Block destructive migrations unless the operator override is
    set. RISKY migrations always pass this gate (warning only —
    operators see the warning in the CI log)."""
    if assessment.overall == MigrationRisk.DESTRUCTIVE and not allow_destructive:
        raise DestructiveMigrationBlocked(
            assessment=assessment, migration=migration_label
        )


def render_preview(assessment: MigrationAssessment) -> str:
    """Human-readable preview the CLI prints before applying.

    Shape::

        DESTRUCTIVE migration — 2 destructive op(s), 1 risky op(s):
          [DESTRUCTIVE] RemoveField(legacy_email)
          [DESTRUCTIVE] DeleteModel(LegacyApp)
          [RISKY]       AlterField(notes)
    """
    lines = [
        f"{assessment.overall.value.upper()} migration — "
        f"{len(assessment.destructive_ops)} destructive op(s), "
        f"{len(assessment.risky_ops)} risky op(s):"
    ]
    for op in assessment.operations:
        if op.risk == MigrationRisk.SAFE:
            continue
        tag = f"[{op.risk.value.upper()}]".ljust(15)
        lines.append(f"  {tag} {op.detail}")
    return "\n".join(lines)
