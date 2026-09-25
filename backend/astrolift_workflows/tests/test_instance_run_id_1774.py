"""A configured run's instance mirror settles when its run does (#1774).

``runWorkflowDefinition`` created the ``WorkflowInstance`` without the
Temporal run id, while the run recorded it, and the completion sync matches
instances by run id, so the mirror stayed ``running`` with no completion
time after the run finished.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.contenttypes.models import ContentType

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities.workflow_stage_activities import _finalize_workflow_run_records
from workflows.models import WorkflowDefinition, WorkflowInstance

pytestmark = pytest.mark.django_db


@pytest.fixture
def run_and_mirror():
    org = Organization.objects.create(name="o", slug=f"o-{uuid.uuid4().hex[:6]}")
    definition = WorkflowDefinition.objects.create(
        name="d",
        slug=f"d-{uuid.uuid4().hex[:6]}",
        organization=org,
        model_label="",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        states=[],
        transitions=[],
        is_enabled=True,
    )
    workflow_id = f"WorkflowDefinitionRunWorkflow-{uuid.uuid4().hex[:6]}"
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id=workflow_id,
        run_id="temporal-run-1",
        status=WorkflowRun.Status.RUNNING,
    )
    # Exactly what runWorkflowDefinition wrote before the fix: no run id.
    mirror = WorkflowInstance.objects.create(
        workflow=definition,
        organization=org,
        content_type=ContentType.objects.get_for_model(WorkflowDefinition),
        object_id=definition.pk,
        current_state="running",
        temporal_workflow_id=workflow_id,
    )
    return run, mirror


def test_a_mirror_without_a_run_id_settles_and_is_stamped(run_and_mirror):
    run, mirror = run_and_mirror

    _finalize_workflow_run_records(str(run.pk), "completed", {"ok": True}, None)

    mirror.refresh_from_db()
    run.refresh_from_db()
    assert mirror.current_state == "completed"
    assert mirror.completed_at == run.ended_at is not None
    assert mirror.temporal_run_id == "temporal-run-1"


def test_another_runs_mirror_is_never_claimed(run_and_mirror):
    run, mirror = run_and_mirror
    mirror.temporal_run_id = "temporal-run-0"
    mirror.save(update_fields=["temporal_run_id"])

    _finalize_workflow_run_records(str(run.pk), "completed", {"ok": True}, None)

    mirror.refresh_from_db()
    assert mirror.current_state == "running" and mirror.completed_at is None
