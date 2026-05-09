"""Tests for preview teardown policy (#87, spec 18 §7)."""

from __future__ import annotations

import pytest

from astrolift_workflows.preview_teardown import (
    PREVIEW_TEARDOWN_DEADLINE_SECONDS,
    STEP_DEADLINES_SECONDS,
    TEARDOWN_ORDER,
    CleanupAction,
    CleanupMode,
    ManagedServiceKind,
    PreviewTeardownState,
    TeardownError,
    TeardownStep,
    already_torn_down,
    cleanup_action_for,
    deadline_for_step,
    plan_managed_service_cleanup,
    pr_comment_for_teardown,
    teardown_steps_to_run,
    total_step_budget,
)

# ---- step ordering -------------------------------------------------


def test_teardown_order_locked():
    """Spec §7: namespace → DNS → managed services → bookkeeping.
    Lock so reordering requires deliberate review (out-of-order
    teardown can leak resources or race in-flight requests)."""
    assert TEARDOWN_ORDER == (
        TeardownStep.DELETE_NAMESPACE,
        TeardownStep.DELETE_DNS,
        TeardownStep.CLEANUP_MANAGED_SERVICES,
        TeardownStep.MARK_TORN_DOWN,
        TeardownStep.EMIT_EVENT,
        TeardownStep.COMMENT_PR,
    )


# ---- step deadlines ------------------------------------------------


def test_step_deadlines_sum_within_total():
    """Spec §7: total teardown <= 15 minutes."""
    assert total_step_budget() <= PREVIEW_TEARDOWN_DEADLINE_SECONDS


def test_step_deadlines_complete_for_every_step():
    """Every step in TEARDOWN_ORDER must have a deadline mapping."""
    for step in TEARDOWN_ORDER:
        deadline = deadline_for_step(step=step)
        assert deadline > 0


def test_namespace_step_gets_largest_deadline():
    """Namespace deletion + finalizers is the slow path; should
    be the biggest budget."""
    namespace_deadline = STEP_DEADLINES_SECONDS[TeardownStep.DELETE_NAMESPACE]
    other_max = max(
        v for k, v in STEP_DEADLINES_SECONDS.items()
        if k != TeardownStep.DELETE_NAMESPACE
    )
    assert namespace_deadline >= other_max


def test_deadline_for_unknown_step():
    """Defensive — caller can't pass a step not in the map."""
    class Fake:
        pass
    with pytest.raises(TeardownError):
        deadline_for_step(step=Fake())  # type: ignore[arg-type]


# ---- managed-service cleanup ordering ------------------------------


def test_cleanup_orders_postgres_last():
    """Source of truth (postgres) drops last so writes from cache
    flush etc. don't outlive the DB."""
    actions = (
        CleanupAction(
            binding_id=1, kind=ManagedServiceKind.POSTGRES,
            mode=CleanupMode.SHARED_WITH_MAIN, target="preview_db",
        ),
        CleanupAction(
            binding_id=2, kind=ManagedServiceKind.QUEUE,
            mode=CleanupMode.DEDICATED, target="queue-id",
        ),
    )
    ordered = plan_managed_service_cleanup(bindings=actions)
    assert ordered[0].kind == ManagedServiceKind.QUEUE
    assert ordered[-1].kind == ManagedServiceKind.POSTGRES


def test_cleanup_orders_queue_first():
    """Workload writes to queue/object-store; drain those first."""
    actions = (
        CleanupAction(
            binding_id=1, kind=ManagedServiceKind.REDIS,
            mode=CleanupMode.SHARED_WITH_MAIN, target="prefix:",
        ),
        CleanupAction(
            binding_id=2, kind=ManagedServiceKind.OBJECT_STORE,
            mode=CleanupMode.SHARED_WITH_MAIN, target="prefix/",
        ),
        CleanupAction(
            binding_id=3, kind=ManagedServiceKind.QUEUE,
            mode=CleanupMode.DEDICATED, target="q-id",
        ),
        CleanupAction(
            binding_id=4, kind=ManagedServiceKind.POSTGRES,
            mode=CleanupMode.SHARED_WITH_MAIN, target="db",
        ),
    )
    ordered = plan_managed_service_cleanup(bindings=actions)
    kinds_in_order = [a.kind for a in ordered]
    assert kinds_in_order == [
        ManagedServiceKind.QUEUE,
        ManagedServiceKind.OBJECT_STORE,
        ManagedServiceKind.REDIS,
        ManagedServiceKind.POSTGRES,
    ]


def test_cleanup_within_kind_orders_by_binding_id():
    """Stable ordering for repeatability."""
    actions = (
        CleanupAction(
            binding_id=5, kind=ManagedServiceKind.POSTGRES,
            mode=CleanupMode.SHARED_WITH_MAIN, target="db5",
        ),
        CleanupAction(
            binding_id=2, kind=ManagedServiceKind.POSTGRES,
            mode=CleanupMode.SHARED_WITH_MAIN, target="db2",
        ),
        CleanupAction(
            binding_id=8, kind=ManagedServiceKind.POSTGRES,
            mode=CleanupMode.SHARED_WITH_MAIN, target="db8",
        ),
    )
    ordered = plan_managed_service_cleanup(bindings=actions)
    assert [a.binding_id for a in ordered] == [2, 5, 8]


def test_cleanup_action_for_validates_target():
    """Empty target = potentially dropping the wrong thing.
    Refuse loudly — spec doesn't say 'best effort'."""
    with pytest.raises(TeardownError, match="target"):
        cleanup_action_for(
            binding_id=1, kind=ManagedServiceKind.POSTGRES,
            mode=CleanupMode.SHARED_WITH_MAIN, target="",
        )


# ---- idempotency ---------------------------------------------------


def test_already_torn_down_skips_destructive_steps():
    state = PreviewTeardownState(
        preview_id=1,
        status="torn_down",
        torn_down_at_unix=1_700_000_000,
    )
    assert already_torn_down(state=state) is True
    steps = teardown_steps_to_run(state=state)
    assert steps == ()


def test_running_preview_runs_full_flow():
    state = PreviewTeardownState(
        preview_id=1,
        status="running",
        torn_down_at_unix=None,
    )
    steps = teardown_steps_to_run(state=state)
    assert steps == TEARDOWN_ORDER


def test_inconsistent_state_runs_bookkeeping_tail():
    """status=torn_down but no timestamp → the destructive work
    happened, but the bookkeeping didn't. Idempotent re-run should
    fix the inconsistency without re-running destructive steps."""
    state = PreviewTeardownState(
        preview_id=1,
        status="torn_down",
        torn_down_at_unix=None,
    )
    steps = teardown_steps_to_run(state=state)
    assert TeardownStep.DELETE_NAMESPACE not in steps
    assert TeardownStep.MARK_TORN_DOWN in steps
    assert TeardownStep.EMIT_EVENT in steps
    assert TeardownStep.COMMENT_PR in steps


def test_failed_preview_runs_full_flow():
    """A 'failed' preview never finished provisioning, but cleanup
    still runs (best-effort tear-down of partial resources)."""
    state = PreviewTeardownState(
        preview_id=1,
        status="failed",
        torn_down_at_unix=None,
    )
    steps = teardown_steps_to_run(state=state)
    assert steps == TEARDOWN_ORDER


# ---- PR comment ----------------------------------------------------


def test_pr_comment_merge():
    body = pr_comment_for_teardown(pr_number=42, is_merge=True)
    assert "merged" in body
    assert "#42" in body
    assert "torn down" in body.lower()


def test_pr_comment_close():
    body = pr_comment_for_teardown(pr_number=42, is_merge=False)
    assert "closed" in body
    assert "merged" not in body
    assert "#42" in body
