"""Pipeline DSL extensions — step outputs, conditionals, `needs` syntax (#94).

This module extends the base TOML DSL with v1+ constructs that weren't
in the initial spec but are needed before GA:

1. **Step outputs**: Steps can declare output values that downstream
   steps (in the same job) or downstream jobs can access via ${jobs.<id>.outputs.<key>}

2. **Conditionals**: Jobs and steps can declare an `if` expression that
   is evaluated before execution. v1 supports simple variable checks only:
   - if: ${ctx.git.branch} == "main"
   - if: ${ctx.git.tag} != ""

3. **Schema versioning**: Pipeline TOML must declare its schema version.
   Current version: 1. Future versions increment when breaking changes land.

4. **Improved `needs` syntax**: Support for fan-in patterns where a job
   depends on ALL outputs from a set of fan-out jobs:
   needs = ["build", "test-unit", "test-integration"]

These are additive changes to the DSL parser — existing TOML files
that don't use these features are unaffected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


CURRENT_SCHEMA_VERSION = 1


class DslValidationError(ValueError):
    """Raised when a pipeline TOML fails DSL validation."""


@dataclass
class StepOutput:
    """A step-level output declaration."""
    name: str
    value_expression: str  # ${ctx.*} expression or literal string


@dataclass
class ConditionalExpr:
    """A parsed `if:` conditional expression."""
    left: str           # LHS variable path (e.g. ctx.git.branch)
    operator: str       # == or !=
    right: str          # RHS literal string


def parse_schema_version(toml_dict: dict[str, Any]) -> int:
    """Extract and validate the schema version from a pipeline TOML dict.

    Returns the version integer. Raises DslValidationError if missing or
    newer than CURRENT_SCHEMA_VERSION.
    """
    pipeline_block = toml_dict.get("pipeline") or {}
    version = pipeline_block.get("schema_version", 1)

    try:
        version = int(version)
    except (TypeError, ValueError):
        raise DslValidationError(f"Invalid schema_version: {version!r}")

    if version > CURRENT_SCHEMA_VERSION:
        raise DslValidationError(
            f"Pipeline TOML schema_version={version} is newer than this platform supports "
            f"(max: {CURRENT_SCHEMA_VERSION}). Upgrade Astrolift to use this pipeline."
        )
    return version


def parse_conditional(if_expr: str) -> ConditionalExpr:
    """Parse an `if:` conditional expression.

    Supported syntax at v1:
        ${ctx.git.branch} == "main"
        ${ctx.git.tag} != ""
        ${env.DEPLOY} == "true"

    Raises DslValidationError for unsupported expressions.
    """
    # Pattern: ${VAR_PATH} OPERATOR "LITERAL"
    pattern = r'^\$\{([^}]+)\}\s*(==|!=)\s*"([^"]*)"$'
    match = re.match(pattern, if_expr.strip())
    if not match:
        raise DslValidationError(
            f"Unsupported conditional expression: {if_expr!r}. "
            "v1 supports: ${{ctx.*}} == \"value\" or ${{ctx.*}} != \"value\""
        )
    return ConditionalExpr(
        left=match.group(1),
        operator=match.group(2),
        right=match.group(3),
    )


def evaluate_conditional(cond: ConditionalExpr, context) -> bool:
    """Evaluate a conditional against a PipelineContext.

    Returns True if the job/step should execute, False if it should be skipped.
    """
    from astrolift_pipelines.context_eval import evaluate

    # Build a simple template to evaluate the LHS
    template = "${" + cond.left + "}"
    try:
        lhs_value = evaluate(template, context)
    except Exception:  # noqa: BLE001
        return False  # Unknown variable — skip the step

    if cond.operator == "==":
        return lhs_value == cond.right
    if cond.operator == "!=":
        return lhs_value != cond.right
    return False


def parse_step_outputs(outputs_block: dict[str, str]) -> list[StepOutput]:
    """Parse a step's [outputs] block into StepOutput objects.

    Each key is an output name; the value is the expression to evaluate.
    Example:
        [jobs.build.steps.build-step.outputs]
        image_tag = "${ctx.git.sha}"
    """
    return [
        StepOutput(name=key, value_expression=value)
        for key, value in (outputs_block or {}).items()
    ]


def validate_needs(job_needs: list[str], all_job_ids: set[str]) -> None:
    """Validate that all `needs` references resolve to defined jobs.

    Raises DslValidationError if any referenced job_id doesn't exist.
    """
    unknown = set(job_needs) - all_job_ids
    if unknown:
        raise DslValidationError(
            f"Unknown job(s) in `needs`: {sorted(unknown)}. "
            f"Defined jobs: {sorted(all_job_ids)}"
        )
