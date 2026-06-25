"""run_workflow_definition GraphQL mutation (#976).

This mutation had ZERO test coverage. It gates on staff, validates the
WorkflowDefinition (exists / enabled / has stages), then starts the shared
stage executor (WorkflowDefinitionRunWorkflow) via
``workflows.run_service.start_workflow_definition_run`` — creating the
WorkflowRun mirror + WorkflowInstance. These tests pin that contract without
a real Temporal worker: ``start_workflow`` is patched at its canonical module
(``astrolift_workflows.client.start_workflow``) because run_service imports it
locally inside the function, so a stale re-export would let a real call leak.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from graphql import GraphQLError

from astrolift_operations.models import WorkflowRun
from workflows.models import WorkflowDefinition, WorkflowInstance, WorkflowStage
from workflows.schema.mutations import Mutation

pytestmark = pytest.mark.django_db

MIN_STATES = [
    {"name": "pending", "label": "Pending", "is_initial": True, "is_final": False},
    {"name": "done", "label": "Done", "is_initial": False, "is_final": True},
]
MIN_TRANSITIONS = [{"from_state": "pending", "to_state": "done", "label": "Complete"}]


def _staff_info():
    User = get_user_model()
    user = User.objects.create(username="staff@test", email="staff@test", is_staff=True)
    return SimpleNamespace(context=SimpleNamespace(user=user)), user


def _definition(*, slug="wf-run-test", enabled=True, with_stage=True):
    wd = WorkflowDefinition.objects.create(
        name="Run test",
        slug=slug,
        model_label="workflows.workflowdefinition",
        pattern_kind=WorkflowDefinition.PatternKind.SINGLE,
        states=MIN_STATES,
        transitions=MIN_TRANSITIONS,
        is_enabled=enabled,
    )
    if with_stage:
        WorkflowStage.objects.create(
            definition=wd, order=0, kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        )
    return wd


@pytest.fixture
def patched_start(monkeypatch):
    """Patch the canonical Temporal entry point run_service imports locally."""
    calls = []

    def _fake(name, *, args=None, workflow_id=None, **kw):
        calls.append({"name": name, "args": args, "workflow_id": workflow_id})
        return SimpleNamespace(enqueued=True, run_id="test-run-id")

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _fake)
    return calls


def test_happy_path_starts_executor_and_creates_rows(patched_start):
    wd = _definition()
    info, _user = _staff_info()

    result = Mutation().run_workflow_definition(info, workflow_slug=wd.slug)

    assert result.ok is True
    # WorkflowRun mirror created, kind + derived workflow id correct.
    run = WorkflowRun.objects.get(pk=int(result.workflow_run_id))
    assert run.workflow_kind == "WorkflowDefinitionRunWorkflow"
    assert result.temporal_workflow_id == f"WorkflowDefinitionRunWorkflow-{run.pk}"
    assert run.workflow_id == result.temporal_workflow_id
    # The executor was actually enqueued with the right id + definition slug.
    assert len(patched_start) == 1
    assert patched_start[0]["name"] == "WorkflowDefinitionRunWorkflow"
    assert patched_start[0]["workflow_id"] == result.temporal_workflow_id
    assert patched_start[0]["args"][0].workflow_definition_slug == wd.slug
    # UI mirror instance created and pointed at the temporal id.
    inst = WorkflowInstance.objects.get(workflow=wd)
    assert inst.temporal_workflow_id == result.temporal_workflow_id


def test_non_staff_is_denied_and_starts_nothing(patched_start):
    wd = _definition()
    User = get_user_model()
    user = User.objects.create(username="plain@test", email="plain@test", is_staff=False)
    info = SimpleNamespace(context=SimpleNamespace(user=user))

    with pytest.raises(GraphQLError):
        Mutation().run_workflow_definition(info, workflow_slug=wd.slug)

    assert patched_start == []
    assert not WorkflowRun.objects.exists()


def test_unknown_slug_returns_error_no_start(patched_start):
    info, _ = _staff_info()
    result = Mutation().run_workflow_definition(info, workflow_slug="does-not-exist")
    assert result.ok is False
    assert result.errors[0].field == "workflow_slug"
    assert patched_start == []


def test_disabled_definition_returns_error(patched_start):
    wd = _definition(slug="wf-disabled", enabled=False)
    info, _ = _staff_info()
    result = Mutation().run_workflow_definition(info, workflow_slug=wd.slug)
    assert result.ok is False
    assert result.errors[0].field == "workflow_slug"
    assert patched_start == []


def test_definition_with_no_stages_returns_error(patched_start):
    wd = _definition(slug="wf-no-stages", with_stage=False)
    info, _ = _staff_info()
    result = Mutation().run_workflow_definition(info, workflow_slug=wd.slug)
    assert result.ok is False
    assert "no stages" in result.errors[0].messages[0].lower()
    assert patched_start == []
    assert not WorkflowRun.objects.exists()
