"""Tests for astrolift_ci_convert.policy (#101).

Covers:
- ConversionPolicy dataclass validation (bad mode / unsupported_handling).
- DEFAULT_POLICY is strict by default.
- apply_policy strict mode: raises ConversionError listing all issues.
- apply_policy non-strict + unsupported_handling="error": raises on first.
- apply_policy non-strict + unsupported_handling="warn": emits warnings,
  replaces step with placeholder.
- apply_policy non-strict + unsupported_handling="skip": drops steps.
- Clean pipeline (no todos / third-party actions) passes all modes.
- Input is never mutated.
"""

from __future__ import annotations

import warnings

import pytest

from astrolift_ci_convert.common.types import JobDef, PipelineDef, StepDef
from astrolift_ci_convert.policy import (
    DEFAULT_POLICY,
    ConversionError,
    ConversionPolicy,
    apply_policy,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _clean_pipeline() -> PipelineDef:
    """A pipeline with no unsupported constructs."""
    return PipelineDef(
        name="clean-ci",
        jobs=[
            JobDef(
                job_id="test",
                name="test",
                steps=[
                    StepDef(name="checkout", uses="astrolift/checkout@v1"),
                    StepDef(name="run tests", run="pytest"),
                ],
            )
        ],
    )


def _pipeline_with_third_party_action() -> PipelineDef:
    """A pipeline containing a third-party GitHub Action."""
    return PipelineDef(
        name="gha-import",
        jobs=[
            JobDef(
                job_id="build",
                name="build",
                steps=[
                    StepDef(name="checkout", uses="astrolift/checkout@v1"),
                    StepDef(name="setup node", uses="actions/setup-node@v4"),
                    StepDef(name="build", run="npm run build"),
                ],
            )
        ],
    )


def _pipeline_with_todos() -> PipelineDef:
    """A pipeline where the converter left todo annotations."""
    step_with_todo = StepDef(
        name="matrix-step",
        run="echo hello",
        todos=["matrix is not supported in Astrolift Pipelines v1"],
    )
    job_with_todo = JobDef(
        job_id="test",
        name="test",
        steps=[step_with_todo],
        todos=["complex expression conditional not supported"],
    )
    return PipelineDef(
        name="complex-pipeline",
        jobs=[job_with_todo],
        todos=["workflow-level todo example"],
    )


def _pipeline_multiple_issues() -> PipelineDef:
    """A pipeline with multiple unsupported steps."""
    return PipelineDef(
        name="multi-issue",
        jobs=[
            JobDef(
                job_id="deploy",
                name="deploy",
                steps=[
                    StepDef(name="step-a", uses="actions/checkout@v3"),
                    StepDef(name="step-b", uses="docker/setup-buildx-action@v3"),
                    StepDef(name="step-c", run="make build"),
                ],
            )
        ],
    )


# ---------------------------------------------------------------------------
# ConversionPolicy dataclass
# ---------------------------------------------------------------------------


def test_default_policy_is_strict():
    assert DEFAULT_POLICY.strict_by_default is True
    assert DEFAULT_POLICY.mode == "one_shot"
    assert DEFAULT_POLICY.unsupported_handling == "error"


def test_policy_bad_mode_raises():
    with pytest.raises(ValueError, match="Unknown mode"):
        ConversionPolicy(mode="round_trip")


def test_policy_bad_unsupported_handling_raises():
    with pytest.raises(ValueError, match="Unknown unsupported_handling"):
        ConversionPolicy(strict_by_default=False, unsupported_handling="ignore")


def test_policy_valid_combinations():
    ConversionPolicy(mode="one_shot", strict_by_default=True, unsupported_handling="error")
    ConversionPolicy(mode="incremental", strict_by_default=False, unsupported_handling="warn")
    ConversionPolicy(mode="one_shot", strict_by_default=False, unsupported_handling="skip")


# ---------------------------------------------------------------------------
# apply_policy — clean pipeline passes all modes
# ---------------------------------------------------------------------------


def test_clean_pipeline_strict_passes():
    result = apply_policy(_clean_pipeline(), DEFAULT_POLICY)
    assert len(result.jobs) == 1
    assert len(result.jobs[0].steps) == 2


def test_clean_pipeline_warn_passes():
    policy = ConversionPolicy(strict_by_default=False, unsupported_handling="warn")
    result = apply_policy(_clean_pipeline(), policy)
    assert len(result.jobs[0].steps) == 2


def test_clean_pipeline_skip_passes():
    policy = ConversionPolicy(strict_by_default=False, unsupported_handling="skip")
    result = apply_policy(_clean_pipeline(), policy)
    assert len(result.jobs[0].steps) == 2


# ---------------------------------------------------------------------------
# apply_policy — strict mode
# ---------------------------------------------------------------------------


def test_strict_raises_on_third_party_action():
    pipeline = _pipeline_with_third_party_action()
    with pytest.raises(ConversionError) as exc_info:
        apply_policy(pipeline, DEFAULT_POLICY)

    err = exc_info.value
    assert err.issues  # at least one issue recorded
    # The third-party action must appear in the issues list.
    assert any("actions/setup-node@v4" in issue for issue in err.issues)


def test_strict_raises_on_todos():
    pipeline = _pipeline_with_todos()
    with pytest.raises(ConversionError) as exc_info:
        apply_policy(pipeline, DEFAULT_POLICY)

    err = exc_info.value
    assert len(err.issues) >= 3  # pipeline-level + job-level + step-level todos
    assert any("matrix" in issue for issue in err.issues)


def test_strict_raises_lists_all_issues_not_just_first():
    """strict mode must collect all issues, not abort on the first one."""
    pipeline = _pipeline_multiple_issues()
    with pytest.raises(ConversionError) as exc_info:
        apply_policy(pipeline, DEFAULT_POLICY)

    err = exc_info.value
    # Both third-party actions must appear.
    text = " ".join(err.issues)
    assert "actions/checkout" in text
    assert "docker/setup-buildx-action" in text


# ---------------------------------------------------------------------------
# apply_policy — non-strict + unsupported_handling="error"
# ---------------------------------------------------------------------------


def test_non_strict_error_raises_on_first_unsupported():
    policy = ConversionPolicy(strict_by_default=False, unsupported_handling="error")
    pipeline = _pipeline_with_third_party_action()
    with pytest.raises(ConversionError):
        apply_policy(pipeline, policy)


# ---------------------------------------------------------------------------
# apply_policy — non-strict + unsupported_handling="warn"
# ---------------------------------------------------------------------------


def test_warn_emits_warning_and_replaces_step():
    policy = ConversionPolicy(strict_by_default=False, unsupported_handling="warn")
    pipeline = _pipeline_with_third_party_action()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = apply_policy(pipeline, policy)

    # A warning must have been emitted.
    assert any("actions/setup-node" in str(w.message) for w in caught)

    # The unsupported step is replaced with a placeholder, not dropped.
    steps = result.jobs[0].steps
    assert len(steps) == 3  # same count: checkout, placeholder, build
    placeholder = steps[1]
    assert placeholder.run is not None
    assert "UNSUPPORTED" in placeholder.run


def test_warn_preserves_clean_steps():
    policy = ConversionPolicy(strict_by_default=False, unsupported_handling="warn")
    pipeline = _pipeline_with_third_party_action()

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        result = apply_policy(pipeline, policy)

    steps = result.jobs[0].steps
    # checkout (astrolift built-in) and build (run:) are untouched.
    assert steps[0].uses == "astrolift/checkout@v1"
    assert steps[2].run == "npm run build"


# ---------------------------------------------------------------------------
# apply_policy — non-strict + unsupported_handling="skip"
# ---------------------------------------------------------------------------


def test_skip_drops_unsupported_steps():
    policy = ConversionPolicy(strict_by_default=False, unsupported_handling="skip")
    pipeline = _pipeline_with_third_party_action()
    result = apply_policy(pipeline, policy)

    steps = result.jobs[0].steps
    # actions/setup-node step is dropped; only checkout + build remain.
    assert len(steps) == 2
    assert steps[0].uses == "astrolift/checkout@v1"
    assert steps[1].run == "npm run build"


def test_skip_all_unsupported_leaves_empty_steps():
    policy = ConversionPolicy(strict_by_default=False, unsupported_handling="skip")
    pipeline = PipelineDef(
        name="all-bad",
        jobs=[
            JobDef(
                job_id="j",
                name="j",
                steps=[
                    StepDef(name="s1", uses="actions/setup-python@v5"),
                    StepDef(name="s2", uses="actions/cache@v4"),
                ],
            )
        ],
    )
    result = apply_policy(pipeline, policy)
    assert result.jobs[0].steps == []


# ---------------------------------------------------------------------------
# Input immutability
# ---------------------------------------------------------------------------


def test_apply_policy_does_not_mutate_input():
    """apply_policy must never modify the original PipelineDef."""
    policy = ConversionPolicy(strict_by_default=False, unsupported_handling="skip")
    pipeline = _pipeline_with_third_party_action()
    original_step_count = len(pipeline.jobs[0].steps)

    apply_policy(pipeline, policy)

    # Original is unchanged.
    assert len(pipeline.jobs[0].steps) == original_step_count


# ---------------------------------------------------------------------------
# Astrolift built-in actions are not flagged as unsupported
# ---------------------------------------------------------------------------


def test_astrolift_builtin_action_is_not_flagged():
    policy = ConversionPolicy(strict_by_default=False, unsupported_handling="error")
    pipeline = PipelineDef(
        name="built-in-only",
        jobs=[
            JobDef(
                job_id="j",
                name="j",
                steps=[
                    StepDef(name="checkout", uses="astrolift/checkout@v1"),
                    StepDef(name="deploy", uses="astrolift/deploy@v1"),
                ],
            )
        ],
    )
    # Should not raise — all uses: start with "astrolift/"
    result = apply_policy(pipeline, policy)
    assert len(result.jobs[0].steps) == 2
