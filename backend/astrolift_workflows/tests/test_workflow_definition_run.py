"""Pure-policy tests for the WorkflowDefinition stage executor.

The orchestration decisions (what to do after an agent run finishes, how
many fan-out children to spawn, which stage fans out) live in module-level
helpers with no Temporal / Django dependency — exactly so they can be
exercised here without a workflow environment, mirroring
``test_pipeline_run_workflow`` for ``topological_sort``.
"""

from __future__ import annotations

from astrolift_workflows.workflows.workflow_definition_run import (
    KIND_AGENT_DISPATCH,
    KIND_HUMAN_GATE,
    MAX_STAGE_ATTEMPTS,
    ON_FAILURE_ESCALATE,
    ON_FAILURE_FAIL,
    ON_FAILURE_RETRY,
    ON_FAILURE_SKIP,
    PATTERN_FAN_OUT,
    STATUS_COMPLETED,
    STATUS_ESCALATED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    decide_after_agent_run,
    is_fan_out_stage,
    resolve_fan_out_count,
)

# ---------------------------------------------------------------------------
# decide_after_agent_run
# ---------------------------------------------------------------------------


def test_succeeded_run_proceeds_regardless_of_on_failure():
    """A succeeded agent run always proceeds, whatever on_failure says."""
    for on_failure in (
        ON_FAILURE_FAIL,
        ON_FAILURE_RETRY,
        ON_FAILURE_SKIP,
        ON_FAILURE_ESCALATE,
    ):
        decision = decide_after_agent_run("succeeded", on_failure, attempt_number=1)
        assert decision.proceed is True
        assert decision.abort is False
        assert decision.terminal_status == STATUS_COMPLETED


def test_failed_run_with_on_failure_fail_aborts():
    decision = decide_after_agent_run("failed", ON_FAILURE_FAIL, attempt_number=1)
    assert decision.abort is True
    assert decision.proceed is False
    assert decision.terminal_status == STATUS_FAILED


def test_failed_run_with_on_failure_skip_proceeds_as_skipped():
    """skip turns a failure into a non-fatal SKIPPED — the chain continues."""
    decision = decide_after_agent_run("failed", ON_FAILURE_SKIP, attempt_number=1)
    assert decision.proceed is True
    assert decision.abort is False
    assert decision.terminal_status == STATUS_SKIPPED


def test_timed_out_run_with_on_failure_skip_proceeds():
    """A timeout is a failure for policy purposes — skip still proceeds."""
    decision = decide_after_agent_run("timed_out", ON_FAILURE_SKIP, attempt_number=1)
    assert decision.proceed is True
    assert decision.terminal_status == STATUS_SKIPPED


def test_failed_run_with_on_failure_escalate_escalates():
    decision = decide_after_agent_run("failed", ON_FAILURE_ESCALATE, attempt_number=1)
    assert decision.escalate is True
    assert decision.proceed is False
    assert decision.abort is False
    assert decision.terminal_status == STATUS_ESCALATED


def test_retry_under_budget_requests_retry():
    """on_failure=retry asks for another attempt while attempts remain."""
    decision = decide_after_agent_run("failed", ON_FAILURE_RETRY, attempt_number=1)
    assert decision.retry is True
    assert decision.abort is False
    assert decision.proceed is False


def test_retry_at_last_allowed_attempt_still_retries():
    """Attempt N-1 (< max) still gets one final retry."""
    decision = decide_after_agent_run(
        "failed", ON_FAILURE_RETRY, attempt_number=MAX_STAGE_ATTEMPTS - 1
    )
    assert decision.retry is True


def test_retry_exhausted_degrades_to_abort():
    """Once attempts hit the cap, retry degrades to abort — no infinite loop."""
    decision = decide_after_agent_run(
        "failed", ON_FAILURE_RETRY, attempt_number=MAX_STAGE_ATTEMPTS
    )
    assert decision.abort is True
    assert decision.retry is False
    assert decision.terminal_status == STATUS_FAILED


def test_retry_budget_is_overridable():
    """A caller can widen/narrow the retry budget."""
    # attempt 2 with max=2 → exhausted → abort
    assert decide_after_agent_run(
        "failed", ON_FAILURE_RETRY, attempt_number=2, max_attempts=2
    ).abort
    # attempt 2 with max=5 → still retries
    assert decide_after_agent_run(
        "failed", ON_FAILURE_RETRY, attempt_number=2, max_attempts=5
    ).retry


def test_cancelled_run_treated_as_failure():
    """A cancelled run is not a success — on_failure governs the outcome."""
    assert decide_after_agent_run("cancelled", ON_FAILURE_FAIL, attempt_number=1).abort
    assert decide_after_agent_run("cancelled", ON_FAILURE_SKIP, attempt_number=1).proceed


# ---------------------------------------------------------------------------
# resolve_fan_out_count
# ---------------------------------------------------------------------------


def test_static_fan_out_count_wins():
    assert resolve_fan_out_count(5, previous_output=None) == 5
    # Even when prior output has items, an explicit static count wins.
    assert resolve_fan_out_count(3, {"items": [1, 2, 3, 4, 5, 6]}) == 3


def test_dynamic_fan_out_from_previous_items():
    """fan_out_count=None derives the count from prior output['items']."""
    assert resolve_fan_out_count(None, {"items": ["a", "b", "c"]}) == 3
    assert resolve_fan_out_count(None, {"items": []}) == 0


def test_dynamic_fan_out_missing_items_is_zero():
    assert resolve_fan_out_count(None, {}) == 0
    assert resolve_fan_out_count(None, None) == 0
    assert resolve_fan_out_count(None, {"items": "not-a-list"}) == 0


def test_static_count_is_clamped_to_cap():
    assert resolve_fan_out_count(10_000, None, cap=50) == 50


def test_dynamic_count_is_clamped_to_cap():
    big = {"items": list(range(1000))}
    assert resolve_fan_out_count(None, big, cap=50) == 50


def test_zero_or_negative_static_count_falls_through_to_dynamic():
    """A non-positive static count is ignored in favour of the dynamic path."""
    assert resolve_fan_out_count(0, {"items": [1, 2]}) == 2
    assert resolve_fan_out_count(-3, {"items": [1, 2, 3, 4]}) == 4
    assert resolve_fan_out_count(0, None) == 0


# ---------------------------------------------------------------------------
# is_fan_out_stage
# ---------------------------------------------------------------------------


def _stage(kind: str) -> dict:
    return {"kind": kind, "order": 0}


def test_fan_out_only_for_fan_out_pattern():
    assert is_fan_out_stage(
        PATTERN_FAN_OUT, _stage(KIND_AGENT_DISPATCH), already_fanned=False
    )
    # Non-fan-out patterns never fan out.
    assert not is_fan_out_stage(
        "chained", _stage(KIND_AGENT_DISPATCH), already_fanned=False
    )
    assert not is_fan_out_stage(
        "single", _stage(KIND_AGENT_DISPATCH), already_fanned=False
    )


def test_fan_out_only_for_agent_dispatch_kind():
    assert not is_fan_out_stage(
        PATTERN_FAN_OUT, _stage(KIND_HUMAN_GATE), already_fanned=False
    )
    assert not is_fan_out_stage(
        PATTERN_FAN_OUT, _stage("checkpoint"), already_fanned=False
    )


def test_fan_out_only_first_agent_dispatch_stage():
    """Once a fan-out has happened, later agent stages run inline."""
    assert not is_fan_out_stage(
        PATTERN_FAN_OUT, _stage(KIND_AGENT_DISPATCH), already_fanned=True
    )
