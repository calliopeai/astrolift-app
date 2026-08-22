"""TearDownPreviewWorkflow consults the teardown policy (#87, spec 18 §7).

``astrolift_workflows.preview_teardown`` declared the ordered teardown
sequence, the idempotency short-circuit and the per-step time budget,
and nothing outside its own unit tests ever called it. The workflow ran
two activities under a flat 10-minute timeout, so:

  * a re-fired teardown re-deleted the namespace and re-stamped
    ``torn_down_at``, losing the original teardown time, and
  * no ``preview_env.torn_down`` event was ever emitted, so webhook and
    activity-feed consumers never saw a teardown at all.

These tests drive the workflow body against a fake ``workflow`` module,
the same way ``test_deregister_app`` does, so the step sequence is
observable without a Temporal server.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from astrolift_workflows.inputs import Actor, TearDownPreviewInput
from astrolift_workflows.preview_teardown import TeardownStep, deadline_for_step
from astrolift_workflows.workflows.tear_down_preview import TearDownPreviewWorkflow


class _FakeWorkflowAPI:
    """Records ``execute_activity`` calls and returns canned results."""

    def __init__(self, *, results: dict[str, object]):
        self._results = results
        self.calls: list[tuple[str, dict]] = []

    async def execute_activity(self, activity_fn, *args, **kwargs):
        name = getattr(activity_fn, "__name__", str(activity_fn))
        self.calls.append((name, kwargs))
        result = self._results.get(name)
        if isinstance(result, Exception):
            raise result
        return result

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def timeout_for(self, name: str) -> timedelta:
        return next(kw["start_to_close_timeout"] for n, kw in self.calls if n == name)


def _run(state: dict, *, extra_results: dict[str, object] | None = None):
    results: dict[str, object] = {
        "load_preview_teardown_state": state,
        "delete_preview_namespace": "preview-pr-7",
    }
    results.update(extra_results or {})
    fake = _FakeWorkflowAPI(results=results)
    with patch(
        "astrolift_workflows.workflows.tear_down_preview.workflow",
        SimpleNamespace(execute_activity=fake.execute_activity),
    ):
        result = asyncio.run(
            TearDownPreviewWorkflow().run(
                TearDownPreviewInput(
                    preview_environment_id=7,
                    actor=Actor(kind="system", display="test"),
                )
            ),
        )
    return fake, result


def test_running_preview_walks_the_policy_sequence():
    """A live preview runs the implemented steps in the policy's order,
    including the event emission that never happened before."""
    fake, result = _run({"preview_id": 7, "status": "running", "torn_down_at_unix": None})

    assert result.ok is True
    assert fake.names() == [
        "load_preview_teardown_state",
        "delete_preview_namespace",
        "mark_preview_torn_down",
        "emit_preview_torn_down_event",
    ]


def test_already_torn_down_preview_does_no_destructive_work():
    """The policy's short-circuit: a re-fire against a finished teardown
    must not re-delete the namespace or re-stamp ``torn_down_at``."""
    fake, result = _run(
        {"preview_id": 7, "status": "torn_down", "torn_down_at_unix": 1_700_000_000},
    )

    assert result.ok is True
    assert fake.names() == ["load_preview_teardown_state"]


def test_inconsistent_row_runs_the_bookkeeping_tail_only():
    """``torn_down`` status with no timestamp is the converge branch:
    fix the row and re-emit, but never re-run the destructive steps."""
    fake, result = _run({"preview_id": 7, "status": "torn_down", "torn_down_at_unix": None})

    assert result.ok is True
    assert fake.names() == [
        "load_preview_teardown_state",
        "mark_preview_torn_down",
        "emit_preview_torn_down_event",
    ]


def test_step_timeouts_come_from_the_policy_budget():
    """Each step carries its own budgeted deadline rather than one flat
    timeout that can overrun the spec's 15-minute total."""
    fake, _ = _run({"preview_id": 7, "status": "running", "torn_down_at_unix": None})

    assert fake.timeout_for("delete_preview_namespace") == timedelta(
        seconds=deadline_for_step(step=TeardownStep.DELETE_NAMESPACE),
    )
    assert fake.timeout_for("mark_preview_torn_down") == timedelta(
        seconds=deadline_for_step(step=TeardownStep.MARK_TORN_DOWN),
    )
    assert fake.timeout_for("delete_preview_namespace") != fake.timeout_for(
        "mark_preview_torn_down",
    )


def test_namespace_delete_failure_stops_before_marking_torn_down():
    """A preview whose workloads are still running must not be recorded
    as torn down."""
    fake, result = _run(
        {"preview_id": 7, "status": "running", "torn_down_at_unix": None},
        extra_results={"delete_preview_namespace": RuntimeError("cluster unreachable")},
    )

    assert result.ok is False
    assert "cluster unreachable" in result.message
    assert "mark_preview_torn_down" not in fake.names()
