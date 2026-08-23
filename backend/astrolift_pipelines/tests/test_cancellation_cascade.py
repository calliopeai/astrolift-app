"""Cancelling a run settles the whole run, not just its top row (#93).

Three gaps, found while wiring `cancellation.py`:

**Step runs were never cancelled by anything.** The Temporal workflow's
cancel handler cascades to `JobRun` and stops there, and `_settle_step_runs`
only runs when a pod actually completed. So a cancelled run's steps read
`running` forever -- in every deployment, Temporal or not. `cancellation.py`
was the only code in the repo that cascaded to steps, and it had no caller.

**Job runs were not cancelled when the signal did not land.** The mutation
flipped `PipelineRun.status` locally and relied on the workflow to cascade,
which cannot happen if Temporal is disabled, the workflow is already gone,
or the run is PENDING and was never dispatched (so it has no workflow id).

**No audit event was emitted -- and could not have been.** The mutation's
local flip bypassed the state machine, and the state machine's emitters
called `core.events.emit_event`, which does not exist (the real entry point
is `Event.emit`). That ImportError went into a bare `except Exception:
pass`, so *no* pipeline audit event has ever been written, including from
the live self-hosted completion path in `runner_views`.

Plus a latent break in the module being wired: `_signal_temporal_cancel`
called `signal_workflow(id, signal_name=..., arg=..., task_queue=...)`
against a real signature of `(workflow_id, signal_name, *args)`. A
TypeError, inside a bare `except Exception: pass` -- so the cancel signal
would have silently never reached Temporal. Nothing called the function, so
no test could see it.
"""

from __future__ import annotations

import inspect
import itertools

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.cancellation import (
    _signal_temporal_cancel,
    cancel_pipeline_run,
)
from astrolift_pipelines.models import Job, JobRun, Pipeline, PipelineRun, Step, StepRun

pytestmark = pytest.mark.django_db

_n = itertools.count(1)


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-cancel-{next(_n)}")


def _run_with_work(org, *, run_status="running", job_status="running", step_status="running"):
    """A run with one job and two steps, all in the given statuses."""
    pipeline = Pipeline.objects.create(
        organization=org, name=f"ci-{next(_n)}", repo_url="https://github.com/a/b"
    )
    run = PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=next(_n),
        trigger_kind="push",
        trigger_ref="main",
        status=run_status,
    )
    job = Job.objects.create(pipeline=pipeline, pipeline_run=run, job_id="build", name="Build")
    job_run = JobRun.objects.create(pipeline_run=run, job=job, status=job_status)
    steps = [Step.objects.create(job=job, step_id=f"step-{i}", position=i, run="make") for i in range(2)]
    step_runs = [StepRun.objects.create(job_run=job_run, step=s, status=step_status) for s in steps]
    return run, job_run, step_runs


# ---------------------------------------------------------------------------
# The signature that was wrong for as long as nobody called it
# ---------------------------------------------------------------------------


def test_the_cancel_signal_matches_the_real_signal_workflow_signature():
    """The precise shape of the latent break, pinned both ways.

    Asserted against the live signature rather than a mock, because a mock
    accepts any keywords and would have passed happily on the broken call.
    """
    from astrolift_workflows.client import signal_workflow

    sig = inspect.signature(signal_workflow)

    # What the fixed code does.
    sig.bind("wf-1", "cancel")

    # What the code did before: `arg` and `task_queue` are not parameters.
    with pytest.raises(TypeError):
        sig.bind("wf-1", signal_name="cancel", arg={"reason": "x"}, task_queue="pipelines")


def test_the_signal_is_sent_with_the_workflow_id_and_cancel(monkeypatch, org):
    run, _, _ = _run_with_work(org)
    run.temporal_workflow_id = "pipeline-run-42"
    run.save(update_fields=["temporal_workflow_id"])

    seen: list = []
    monkeypatch.setattr(
        "astrolift_workflows.client.signal_workflow",
        lambda *args, **kwargs: seen.append((args, kwargs)) or True,
    )

    _signal_temporal_cancel(run)

    assert seen == [(("pipeline-run-42", "cancel"), {})]


def test_a_run_with_no_workflow_id_sends_nothing(monkeypatch, org):
    """A PENDING run that was never dispatched has no workflow to signal --
    and is exactly the case where the database cascade is all there is."""
    run, _, _ = _run_with_work(org, run_status="pending")

    called = []
    monkeypatch.setattr(
        "astrolift_workflows.client.signal_workflow",
        lambda *a, **k: called.append(1),
    )

    _signal_temporal_cancel(run)

    assert called == []


def test_a_failing_signal_does_not_stop_the_cascade(monkeypatch, org):
    """Best-effort, but no longer silent. The database is what the operator
    sees, so it must settle even when Temporal refuses."""
    run, job_run, step_runs = _run_with_work(org)
    run.temporal_workflow_id = "wf-1"
    run.save(update_fields=["temporal_workflow_id"])

    monkeypatch.setattr(
        "astrolift_workflows.client.signal_workflow",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("temporal down")),
    )

    cancel_pipeline_run(run, actor_display="tester")

    run.refresh_from_db()
    job_run.refresh_from_db()
    assert run.status == "cancelled"
    assert job_run.status == "cancelled"


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------


def test_cancelling_a_run_cancels_its_step_runs(monkeypatch, org):
    """The gap no live path covered. Before this, a cancelled run's steps
    stayed `running` in the UI forever."""
    run, _, step_runs = _run_with_work(org)
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *a, **k: True)

    cancel_pipeline_run(run)

    for step_run in step_runs:
        step_run.refresh_from_db()
        assert step_run.status == "cancelled"


def test_cascaded_step_runs_get_a_finished_timestamp(monkeypatch, org):
    """`.update()` does not fire `auto_now`, so the timestamps have to be
    set explicitly or the rows look settled-but-never-finished."""
    run, _, step_runs = _run_with_work(org)
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *a, **k: True)

    cancel_pipeline_run(run)

    for step_run in step_runs:
        step_run.refresh_from_db()
        assert step_run.finished_at is not None


def test_cancelling_a_run_cancels_its_job_runs(monkeypatch, org):
    run, job_run, _ = _run_with_work(org)
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *a, **k: True)

    cancel_pipeline_run(run)

    job_run.refresh_from_db()
    assert job_run.status == "cancelled"
    assert job_run.finished_at is not None


def test_a_pending_job_run_is_cancelled_too(monkeypatch, org):
    """A queued job never got a pod; it still must not sit `pending` after
    the run it belongs to is gone."""
    run, job_run, _ = _run_with_work(org, job_status="pending")
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *a, **k: True)

    cancel_pipeline_run(run)

    job_run.refresh_from_db()
    assert job_run.status == "cancelled"


@pytest.mark.parametrize("terminal", ["success", "failure"])
def test_an_already_finished_job_run_is_left_alone(monkeypatch, org, terminal):
    """The state machine is append-only. A job that already succeeded must
    not be rewritten to cancelled just because the run was cancelled after
    it finished."""
    run, job_run, step_runs = _run_with_work(org, job_status=terminal, step_status=terminal)
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *a, **k: True)

    cancel_pipeline_run(run)

    job_run.refresh_from_db()
    assert job_run.status == terminal
    for step_run in step_runs:
        step_run.refresh_from_db()
        assert step_run.status == terminal


def test_the_run_itself_reaches_cancelled(monkeypatch, org):
    run, _, _ = _run_with_work(org)
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *a, **k: True)

    cancel_pipeline_run(run)

    run.refresh_from_db()
    assert run.status == "cancelled"
    assert run.finished_at is not None


def test_an_already_terminal_run_is_not_transitioned_again(monkeypatch, org, caplog):
    """`transition_pipeline_run` refuses it and the service logs rather than
    raising, so a double-cancel is not an error."""
    run, _, _ = _run_with_work(org, run_status="success", job_status="success")
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *a, **k: True)

    cancel_pipeline_run(run)

    run.refresh_from_db()
    assert run.status == "success"


# ---------------------------------------------------------------------------
# The audit event only the state machine emits
# ---------------------------------------------------------------------------


def test_cancelling_emits_an_audit_event_naming_the_actor(monkeypatch, org):
    """The local flip the mutation used to do bypassed the state machine, so
    an operator cancel left no audit trail at all."""
    run, _, _ = _run_with_work(org)
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *a, **k: True)

    events: list = []
    monkeypatch.setattr(
        "core.events.Event.emit",
        staticmethod(lambda event_type, **kwargs: events.append((event_type, kwargs))),
    )

    cancel_pipeline_run(run, actor_display="leo@calliope.ai")

    types = [t for t, _ in events]
    assert "pipeline_run.cancelled" in types
    _, kwargs = next(e for e in events if e[0] == "pipeline_run.cancelled")
    assert kwargs["payload"]["actor_display"] == "leo@calliope.ai"
    assert kwargs["resource_kind"] == "pipeline_run"


def test_the_audit_event_reaches_the_real_event_pipeline(monkeypatch, org):
    """The strong version: subscribe to the actual fan-out rather than
    asserting a mock was called.

    A mock accepts any keywords, so it would have passed just as happily
    against the broken `emit_event(action=...)` call. Reading the real
    envelope is the same reason the metrics tests read the live Prometheus
    registry instead of the module's private globals.
    """
    from core.events import register_event_subscriber, unregister_event_subscriber

    run, _, _ = _run_with_work(org)
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *a, **k: True)

    seen: list = []
    register_event_subscriber(seen.append)
    try:
        cancel_pipeline_run(run, actor_display="tester")
    finally:
        unregister_event_subscriber(seen.append)

    types = [e.event_type for e in seen]
    assert "pipeline_run.cancelled" in types, f"got {types}"
    assert "pipeline_job_run.cancelled" in types, f"got {types}"


def test_the_emit_call_matches_the_real_event_api():
    """Pins the signature the emitters were wrong about. `emit_event` never
    existed; the shape below is what does."""
    from core.events import Event

    sig = inspect.signature(Event.emit)

    sig.bind(
        "pipeline_run.cancelled",
        payload={},
        resource_kind="pipeline_run",
        resource_id="x",
        organization_id=1,
    )

    # The call the emitters actually made for this module's entire life.
    with pytest.raises(TypeError):
        sig.bind(action="pipeline_run.cancelled", org_id=1, metadata={}, actor_display="x")
