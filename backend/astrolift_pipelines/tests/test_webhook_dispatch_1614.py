"""A webhook actually starts the pipeline workflow (#1614).

`_dispatch_pipeline_run` had five independent bugs in one call, any one of
which was fatal, inside a `try/except Exception` that logged "dispatch
skipped". Since Temporal being disabled produces exactly that message on a
healthy install, there was no observable difference between "off" and
"broken", and no test could see it because the assertion everybody would
write -- the run reaches Temporal -- was the thing that did not happen.

So these assert the call as made, not the absence of an exception. The
`temporal_workflow_id` test matters most: it is what `cancellation.py` reads
to find the workflow, so an empty one silently disables cancellation too.
"""

from __future__ import annotations

import pytest

from astrolift_pipelines import webhook_views


class _Run:
    """Enough PipelineRun for the dispatch. A model instance would drag in
    the pipeline/org/cluster fixture chain for four fields."""

    def __init__(self):
        self.pk = 4321
        self.guid = "11111111-2222-3333-4444-555555555555"
        self.pipeline_id = 7
        self.run_number = 12
        self.temporal_workflow_id = ""
        self.saved_fields: list[str] = []

    def save(self, *, update_fields=None):
        self.saved_fields = list(update_fields or [])


@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []

    def _fake_start(workflow_name, args, *, workflow_id, task_queue=None):
        calls.append(
            {
                "workflow_name": workflow_name,
                "args": args,
                "workflow_id": workflow_id,
                "task_queue": task_queue,
            }
        )
        return object()

    import astrolift_workflows.client as client

    monkeypatch.setattr(client, "start_workflow", _fake_start)
    return calls


def test_the_dispatch_reaches_temporal(captured):
    run = _Run()

    webhook_views._dispatch_pipeline_run(run)

    assert len(captured) == 1, "the webhook did not start a workflow at all"
    call = captured[0]
    assert call["workflow_name"] == "PipelineRunWorkflow"


def test_the_workflow_gets_the_pk_in_a_list():
    """`PipelineRunWorkflow.run(self, pipeline_run_id: int)` -- the PK, and
    `start_workflow` takes `args` as a list. The old call passed a GUID
    string wrapped in a dataclass that does not exist."""
    import typing

    from astrolift_workflows.workflows.pipeline_run import PipelineRunWorkflow

    # get_type_hints rather than __annotations__: the module uses
    # `from __future__ import annotations`, so the raw dict holds the string
    # "int" and `is int` would fail on a correct signature.
    hints = typing.get_type_hints(PipelineRunWorkflow.run)
    assert hints["pipeline_run_id"] is int


def test_the_pk_is_what_is_actually_sent(captured):
    run = _Run()

    webhook_views._dispatch_pipeline_run(run)

    assert captured[0]["args"] == [4321], "the workflow must receive [pk], not a guid"


def test_the_default_task_queue_is_used(captured):
    """`task_queue="pipelines"` named a queue nobody registers. The workflow
    is in the single WORKFLOWS tuple, served on TEMPORAL_TASK_QUEUE, so the
    dispatch must not override it."""
    run = _Run()

    webhook_views._dispatch_pipeline_run(run)

    assert (
        captured[0]["task_queue"] is None
    ), "overriding the task queue sends the workflow to a queue no worker polls"


def test_the_workflow_id_is_recorded_so_cancellation_can_find_it(captured):
    """The half that made this worse than a dead dispatch.

    `cancellation._signal_temporal_cancel` returns early on an empty
    `temporal_workflow_id`, so a dispatch that does not record one disables
    cancellation as well.
    """
    run = _Run()

    webhook_views._dispatch_pipeline_run(run)

    assert run.temporal_workflow_id == "pipeline-run-7-12"
    assert "temporal_workflow_id" in run.saved_fields


def test_a_dispatch_failure_does_not_record_a_workflow_id(monkeypatch):
    """Recording an id for a workflow that never started would point
    cancellation at nothing."""
    import astrolift_workflows.client as client

    def _boom(*a, **kw):
        raise RuntimeError("temporal unreachable")

    monkeypatch.setattr(client, "start_workflow", _boom)
    run = _Run()

    webhook_views._dispatch_pipeline_run(run)

    assert run.temporal_workflow_id == ""
    assert run.saved_fields == []
