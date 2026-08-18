"""The seven steps, what a step result carries, and the grid it renders into.

Spec 41 §2 asked for a runner whose failure is "actionable output, not a
mystery". That is a shape constraint, so it lives in the types rather than in
the formatting.

Every red result carries three things: the **cell** it happened in, the **step**
it happened at, and the **assertion** that did not hold, stated as the thing
that was supposed to be true. A traceback is not one of the three. Tracebacks
are for the harness's own bugs; a platform defect wants a sentence somebody can
paste into a ticket.

``detail`` is the observed value that contradicted the assertion. Keeping it
separate from the assertion is what lets the grid print one line per cell and
still have the full reason available underneath.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class Step(Enum):
    """The lifecycle cycle of spec 43 §0.1, in order.

    REPRODUCE is a step like the others rather than a wrapper around them, so
    the grid has a column for the thing that has never run.
    """

    BUILDOUT = "BUILDOUT"
    VERIFY_UP = "VERIFY-UP"
    UPDATE = "UPDATE"
    VERIFY_UPD = "VERIFY-UPD"
    TEARDOWN = "TEARDOWN"
    VERIFY_CLEAN = "VERIFY-CLEAN"
    REPRODUCE = "REPRODUCE"

    def __str__(self) -> str:
        return self.value


#: Steps 1-6: one pass of the cycle. REPRODUCE is the second pass plus the
#: comparison, so it is not one of them.
CYCLE_STEPS: tuple[Step, ...] = (
    Step.BUILDOUT,
    Step.VERIFY_UP,
    Step.UPDATE,
    Step.VERIFY_UPD,
    Step.TEARDOWN,
    Step.VERIFY_CLEAN,
)


class Outcome(Enum):
    GREEN = "green"
    RED = "red"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return self.value


class AssertionFailed(Exception):
    """A step's assertion did not hold.

    Raised by the runner's own checks and caught by the step wrapper. Distinct
    from an unexpected exception: this one already knows how to say what was
    expected, so it is reported verbatim rather than as "the step raised".
    """

    def __init__(self, assertion: str, detail: str = "") -> None:
        super().__init__(f"{assertion}: {detail}" if detail else assertion)
        self.assertion = assertion
        self.detail = detail


@dataclass(frozen=True)
class StepResult:
    step: Step
    outcome: Outcome
    assertion: str = ""
    """What was supposed to be true. Empty on a green step, because there is
    nothing to act on."""
    detail: str = ""
    """What was observed instead."""
    duration_s: float = 0.0

    @property
    def is_green(self) -> bool:
        return self.outcome is Outcome.GREEN

    def describe(self, cell: str) -> str:
        """One actionable line. The cell comes from the caller because a step
        does not know which cell it is running for, and a report that omits the
        cell is unreadable the moment more than one runs."""
        head = f"{self.outcome.value.upper():<7} {cell:<28} {self.step}"
        if self.is_green or not self.assertion:
            return head
        line = f"{head}  --  {self.assertion}"
        return f"{line}\n{'':<8}{'':<28}   observed: {self.detail}" if self.detail else line


@dataclass(frozen=True)
class CycleResult:
    """One pass of steps 1-6."""

    cell: str
    steps: tuple[StepResult, ...] = ()

    @property
    def is_green(self) -> bool:
        return bool(self.steps) and all(step.is_green for step in self.steps)

    def result_for(self, step: Step) -> StepResult | None:
        return next((result for result in self.steps if result.step is step), None)

    @property
    def first_red(self) -> StepResult | None:
        return next((result for result in self.steps if result.outcome is Outcome.RED), None)


@dataclass(frozen=True)
class CellResult:
    """Both passes plus the comparison: everything the grid needs for one cell."""

    cell: str
    cloud: str
    first: CycleResult
    second: CycleResult | None = None
    reproduce: StepResult | None = None
    differences: tuple[str, ...] = ()

    @property
    def steps(self) -> tuple[StepResult, ...]:
        results = list(self.first.steps)
        if self.reproduce is not None:
            results.append(self.reproduce)
        return tuple(results)

    @property
    def is_green(self) -> bool:
        return bool(self.steps) and all(step.is_green for step in self.steps)

    def failures(self) -> list[str]:
        """Every red line, first pass then second, cell-qualified."""
        lines = [step.describe(self.cell) for step in self.first.steps if step.outcome is Outcome.RED]
        if self.second is not None:
            lines += [step.describe(f"{self.cell} (2nd)") for step in self.second.steps if step.outcome is Outcome.RED]
        if self.reproduce is not None and self.reproduce.outcome is Outcome.RED:
            lines.append(self.reproduce.describe(self.cell))
        return lines


@dataclass
class Grid:
    """The certification evidence: green/red per step per cell.

    Emitted rather than transcribed, which is the whole point of spec 43 §3.2's
    third bullet. ``as_dict`` is the machine-readable form; a run that is only
    ever read by a human ends up summarized in a ticket by hand, and the summary
    is what gets believed.
    """

    cells: list[CellResult] = field(default_factory=list)

    def add(self, result: CellResult) -> None:
        self.cells.append(result)

    @property
    def is_green(self) -> bool:
        return bool(self.cells) and all(cell.is_green for cell in self.cells)

    def render(self, steps: Sequence[Step] = (*CYCLE_STEPS, Step.REPRODUCE)) -> str:
        mark = {Outcome.GREEN: " ok ", Outcome.RED: "FAIL", Outcome.SKIPPED: "  - "}
        width = max([len(cell.cell) for cell in self.cells] + [4])
        header = "  ".join(f"{step!s:>13}" for step in steps)
        lines = [f"{'CELL':<{width}}  {header}"]
        for cell in self.cells:
            marks = []
            for step in steps:
                result = cell.reproduce if step is Step.REPRODUCE else cell.first.result_for(step)
                marks.append(f"{mark[result.outcome] if result else '  - ':>13}")
            lines.append(f"{cell.cell:<{width}}  " + "  ".join(marks))
        failures = [line for cell in self.cells for line in cell.failures()]
        if failures:
            lines.append("")
            lines += failures
        if self.cells:
            lines.append("")
            lines.append(f"{sum(1 for c in self.cells if c.is_green)}/{len(self.cells)} cells GREEN")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "green": self.is_green,
            "cells": [
                {
                    "cell": cell.cell,
                    "cloud": cell.cloud,
                    "green": cell.is_green,
                    "differences": list(cell.differences),
                    "steps": [
                        {
                            "step": str(step.step),
                            "outcome": str(step.outcome),
                            "assertion": step.assertion,
                            "detail": step.detail,
                            "duration_s": round(step.duration_s, 3),
                        }
                        for step in cell.steps
                    ],
                }
                for cell in self.cells
            ],
        }
