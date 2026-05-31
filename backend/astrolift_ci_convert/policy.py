"""Converter policy — one-shot import, strict-by-default (#101).

The CI converter is a migration tool, not a sync adapter. Once a pipeline
is imported as Astrolift TOML the source file (GHA / GitLab CI) is no
longer the source of truth.

ConversionPolicy controls:

* ``mode`` — "one_shot" (default) or "incremental" (reserved for future
  round-trip sync tooling; one_shot is the only supported mode at v1).
* ``strict_by_default`` — when True, any unsupported construct aborts
  conversion and raises ConversionError with a full list of issues.
* ``unsupported_handling`` — what to do with individual unsupported steps:
  - "error": raise ConversionError (only reached when strict=False; strict
    mode raises at pipeline level before reaching per-step handling)
  - "warn": print a warning to stderr and replace the step with a
    ``run: echo "UNSUPPORTED: {construct}"`` placeholder
  - "skip": drop the step entirely, silently

DEFAULT_POLICY is strict — unsupported constructs abort with a list.
"""

from __future__ import annotations

import copy
import warnings
from dataclasses import dataclass

from astrolift_ci_convert.common.types import PipelineDef, StepDef


class ConversionError(Exception):
    """Raised when strict policy detects unsupported constructs."""

    def __init__(self, message: str, issues: list[str] | None = None):
        super().__init__(message)
        self.issues = issues or []


@dataclass
class ConversionPolicy:
    """Policy controlling how the CI converter handles unsupported constructs.

    Attributes
    ----------
    mode:
        "one_shot" — single-pass migration (default, only supported mode at v1).
        "incremental" — reserved for future round-trip sync support.
    strict_by_default:
        When True, abort conversion if *any* unsupported construct is found.
        The full list of issues is included in the ConversionError so the
        operator knows everything to fix before re-running.
    unsupported_handling:
        Per-construct fallback used when ``strict_by_default=False``:
        - "error" — raise ConversionError on the first unsupported construct.
        - "warn"  — print a warning and replace the step with a placeholder.
        - "skip"  — drop the step silently.
    """

    mode: str = "one_shot"
    strict_by_default: bool = True
    unsupported_handling: str = "error"

    def __post_init__(self) -> None:
        if self.mode not in ("one_shot", "incremental"):
            raise ValueError(f"Unknown mode: {self.mode!r}. Expected 'one_shot' or 'incremental'.")
        if self.unsupported_handling not in ("error", "warn", "skip"):
            raise ValueError(
                f"Unknown unsupported_handling: {self.unsupported_handling!r}. "
                "Expected 'error', 'warn', or 'skip'."
            )


DEFAULT_POLICY = ConversionPolicy(
    mode="one_shot",
    strict_by_default=True,
    unsupported_handling="error",
)


def apply_policy(pipeline_def: PipelineDef, policy: ConversionPolicy | None = None) -> PipelineDef:
    """Validate and transform ``pipeline_def`` according to ``policy``.

    Returns a new ``PipelineDef`` with unsupported steps handled per the
    policy.  The input is never mutated.

    Strict mode (``policy.strict_by_default=True``):
        If any step in any job has a non-null ``uses`` field that looks like
        a third-party action (not an Astrolift built-in), *or* if any
        ``todos`` list is non-empty, collect all issues and raise
        ``ConversionError`` with the full list.

    Non-strict mode (``policy.strict_by_default=False``):
        Dispatch per-step to ``unsupported_handling``:
        - "error": raise ``ConversionError`` at the first unsupported step.
        - "warn":  emit a ``warnings.warn`` and replace the step with a
          ``run: echo "UNSUPPORTED: {construct}"`` placeholder.
        - "skip":  remove the step from the job.

    Parameters
    ----------
    pipeline_def:
        The ``PipelineDef`` produced by a converter (GHA or GitLab).
    policy:
        Policy to apply. Defaults to ``DEFAULT_POLICY``.

    Returns
    -------
    PipelineDef
        A (possibly transformed) copy of the pipeline definition.

    Raises
    ------
    ConversionError
        In strict mode when any unsupported construct is detected, or in
        non-strict mode with ``unsupported_handling="error"``.
    """
    if policy is None:
        policy = DEFAULT_POLICY

    # Collect all issues across the pipeline for strict mode.
    all_issues: list[str] = list(pipeline_def.todos)

    for job in pipeline_def.jobs:
        all_issues.extend(job.todos)
        for step in job.steps:
            all_issues.extend(_step_issues(step))

    if policy.strict_by_default and all_issues:
        raise ConversionError(
            f"Conversion aborted: {len(all_issues)} unsupported construct(s) found. "
            "Fix or remove them and retry, or use a permissive policy.",
            issues=all_issues,
        )

    # Non-strict: deep-copy and apply per-step handling.
    result = _deep_copy_pipeline(pipeline_def)
    for job in result.jobs:
        job.steps = _apply_steps(job.steps, policy, job.job_id)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _step_issues(step: StepDef) -> list[str]:
    """Collect issues for a single step."""
    issues: list[str] = list(step.todos)

    # A step that has a ``uses`` field but also todos is already captured
    # above.  Emit an additional issue if the uses string looks like a
    # third-party action (contains "/" but does NOT start with "astrolift/").
    if step.uses and "/" in step.uses and not step.uses.startswith("astrolift/"):
        issues.append(f"uses: {step.uses!r} — third-party action; no Astrolift equivalent")

    return issues


def _apply_steps(steps: list[StepDef], policy: ConversionPolicy, job_id: str) -> list[StepDef]:
    """Apply ``policy.unsupported_handling`` to each step that has issues."""
    result: list[StepDef] = []
    for step in steps:
        issues = _step_issues(step)
        if not issues:
            result.append(step)
            continue

        construct_label = issues[0] if issues else step.uses or step.name

        if policy.unsupported_handling == "error":
            raise ConversionError(
                f"Unsupported construct in job {job_id!r}: {construct_label}",
                issues=issues,
            )
        elif policy.unsupported_handling == "warn":
            for issue in issues:
                warnings.warn(
                    f"Unsupported construct in job {job_id!r}: {issue}",
                    stacklevel=4,
                )
            placeholder = StepDef(
                name=f"UNSUPPORTED: {step.name}",
                run=f'echo "UNSUPPORTED: {construct_label}"',
            )
            result.append(placeholder)
        elif policy.unsupported_handling == "skip":
            # Drop the step entirely.
            pass

    return result


def _deep_copy_pipeline(pipeline_def: PipelineDef) -> PipelineDef:
    """Return a deep copy of ``pipeline_def``."""
    return copy.deepcopy(pipeline_def)
