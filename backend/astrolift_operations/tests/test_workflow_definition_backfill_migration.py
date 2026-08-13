from importlib import import_module

import pytest
from django.apps import apps

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from workflows.models import WorkflowDefinition, WorkflowInstance

pytestmark = pytest.mark.django_db


def _backfill():
    migration = import_module("astrolift_operations.migrations.0019_workflowrun_workflow_definition")
    migration.backfill_workflow_definitions(apps, None)


def test_backfill_links_pre_scope_instance_by_definition_owner():
    organization = Organization.objects.create(name="Backfill", slug="run-backfill")
    definition = WorkflowDefinition.objects.create(
        organization=organization,
        name="Triage",
        slug="run-backfill-triage",
        model_label="",
        states=[],
        transitions=[],
    )
    run = WorkflowRun.objects.create(
        organization=organization,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="WorkflowDefinitionRunWorkflow-old",
        run_id="old-run",
    )
    WorkflowInstance.objects.create(
        workflow=definition,
        organization=None,
        current_state="running",
        temporal_workflow_id=run.workflow_id,
    )

    _backfill()

    run.refresh_from_db()
    assert run.workflow_definition_id == definition.pk


def test_backfill_leaves_ambiguous_instances_unlinked():
    organization = Organization.objects.create(name="Ambiguous", slug="run-ambiguous")
    run = WorkflowRun.objects.create(
        organization=organization,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="WorkflowDefinitionRunWorkflow-ambiguous",
        run_id="ambiguous-run",
    )
    for suffix in ("a", "b"):
        definition = WorkflowDefinition.objects.create(
            organization=organization,
            name=f"Triage {suffix}",
            slug=f"run-ambiguous-{suffix}",
            model_label="",
            states=[],
            transitions=[],
        )
        WorkflowInstance.objects.create(
            workflow=definition,
            organization=None,
            current_state="running",
            temporal_workflow_id=run.workflow_id,
        )

    _backfill()

    run.refresh_from_db()
    assert run.workflow_definition_id is None
