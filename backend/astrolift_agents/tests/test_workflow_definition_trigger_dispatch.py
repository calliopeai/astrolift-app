"""Trigger-path dispatch shape for the WorkflowDefinition stage executor (#1030).

``trigger_workflow_instance`` (the inner entry point shared by SCM-push /
inbound-webhook / manual launches) must start the executor
``WorkflowDefinitionRunWorkflow`` with a ``WorkflowDefinitionRunInput``
dataclass — the same argument shape the workflow's ``run`` method declares.

Regression guarded: the dispatch previously passed a plain ``dict``
(``{"workflow_instance_id": ..., "input": ..., "trigger_kind": ...}``), which
the workflow's ``run(self, input: WorkflowDefinitionRunInput)`` could not
consume, so a triggered run never started. These tests capture the actual
argument handed to ``start_workflow`` and assert both its concrete type and
that its fields are threaded through correctly, plus a guard that the type
matches the workflow ``run`` annotation (so the two can't silently drift).
"""

from __future__ import annotations

import typing

import pytest

from astrolift_operations.models import WorkflowRun
from astrolift_workflows.client import WorkflowHandle
from astrolift_workflows.inputs import WorkflowDefinitionRunInput
from workflows.models import WorkflowDefinition

pytestmark = pytest.mark.django_db


_STATES = [
    {"name": "pending", "label": "Pending", "is_initial": True, "is_final": False},
    {"name": "done", "label": "Done", "is_initial": False, "is_final": True},
]


def _definition(slug: str = "deploy-on-push") -> WorkflowDefinition:
    return WorkflowDefinition.objects.create(
        name=f"Def {slug}",
        slug=slug,
        model_label="workflows.workflowdefinition",
        states=_STATES,
        transitions=[{"from_state": "pending", "to_state": "done", "label": "Complete"}],
        is_enabled=True,
    )


@pytest.fixture
def captured_start(monkeypatch):
    """Replace ``start_workflow`` (resolved inside ``run_service``) with a spy
    that records every dispatch and returns an enqueued handle."""
    calls: list[dict] = []

    def _spy(workflow_name, args, *, workflow_id, task_queue=None):
        calls.append(
            {
                "workflow_name": workflow_name,
                "args": args,
                "workflow_id": workflow_id,
            }
        )
        return WorkflowHandle(workflow_id=workflow_id, run_id="run-xyz", enqueued=True)

    # run_service does ``from astrolift_workflows.client import start_workflow``
    # at call time, so patching the attribute on the client module is what the
    # local import resolves.
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _spy)
    return calls


def test_trigger_dispatches_workflow_definition_run_input(captured_start):
    from astrolift_agents.services.workflow_triggers import trigger_workflow_instance

    definition = _definition("spy-def")
    payload = {"ref": "refs/heads/release", "repository": {"full_name": "acme/x"}}

    trigger_workflow_instance(definition, payload, trigger_kind="scm_push")

    assert len(captured_start) == 1
    call = captured_start[0]
    assert call["workflow_name"] == "WorkflowDefinitionRunWorkflow"
    assert len(call["args"]) == 1

    arg = call["args"][0]
    # The bug: a plain dict was passed. Assert the concrete dataclass type.
    assert isinstance(arg, WorkflowDefinitionRunInput)
    assert not isinstance(arg, dict)

    # Fields threaded through.
    assert arg.workflow_definition_slug == definition.slug
    assert arg.trigger_payload == payload
    assert arg.actor.kind == "system"
    assert arg.actor.display == "scm_push"

    # workflow_run_id must be a real WorkflowRun pk (the executor activities do
    # ``WorkflowRun.objects.get(pk=int(workflow_run_id))``). The old dict carried
    # no such key at all — this is the load-bearing falsifiable guard.
    assert arg.workflow_run_id.isdigit()
    run = WorkflowRun.objects.get(pk=int(arg.workflow_run_id))
    assert run.workflow_kind == "WorkflowDefinitionRunWorkflow"
    # The workflow id mirrors the run pk and is echoed in the dispatch id.
    assert call["workflow_id"] == f"WorkflowDefinitionRunWorkflow-{run.pk}"


def test_dispatched_arg_type_matches_workflow_run_annotation(captured_start):
    """The dispatched argument's type is exactly what ``run`` declares.

    Resolves the ``WorkflowDefinitionRunWorkflow.run`` annotation (string form
    under ``from __future__ import annotations``) and asserts the captured
    dispatch argument is an instance of that very type — so a future signature
    change or a revert to an untyped dict can't pass silently.
    """
    from astrolift_agents.services.workflow_triggers import trigger_workflow_instance
    from astrolift_workflows.workflows.workflow_definition_run import (
        WorkflowDefinitionRunWorkflow,
    )

    hints = typing.get_type_hints(WorkflowDefinitionRunWorkflow.run)
    annotated_input_type = hints["input"]
    # Sanity: the annotation really is the dataclass we expect.
    assert annotated_input_type is WorkflowDefinitionRunInput

    trigger_workflow_instance(_definition("ann-def"), {"k": "v"}, trigger_kind="manual")

    assert len(captured_start) == 1
    arg = captured_start[0]["args"][0]
    assert isinstance(arg, annotated_input_type)
