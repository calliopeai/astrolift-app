from importlib import import_module

import pytest
from django.apps import apps

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from workflows.models import WorkflowDefinition, WorkflowStage

pytestmark = pytest.mark.django_db


def _backfill():
    migration = import_module("workflows.migrations.0011_repair_workflow_definition_projects")
    migration.repair_repository_workflow_projects(apps, None)


def _project(organization, slug):
    team = Team.objects.create(organization=organization, name=slug, slug=slug)
    return Project.objects.create(organization=organization, team=team, name=slug, slug=slug)


def _agent(project, slug):
    app = RegisteredApp.objects.create(
        organization=project.organization,
        team=project.team,
        project=project,
        name=slug,
        slug=slug,
    )
    return Workload.objects.create(
        registered_app=app,
        name=slug,
        slug=slug,
        kind=Workload.Kind.AGENT,
    )


def _definition(organization, slug):
    return WorkflowDefinition.objects.create(
        organization=organization,
        name=slug,
        slug=slug,
        source_repo="steadymd/smd-agents",
        model_label="",
        states=[],
        transitions=[],
    )


def test_backfill_deduplicates_multiple_stage_agents_in_one_project():
    organization = Organization.objects.create(name="Backfill", slug="project-backfill")
    project = _project(organization, "triage")
    definition = _definition(organization, "triage-chain")

    for order in range(2):
        WorkflowStage.objects.create(
            definition=definition,
            order=order,
            agent_definition=_agent(project, f"triage-{order}"),
        )

    _backfill()

    definition.refresh_from_db()
    assert definition.project_id == project.pk


def test_backfill_leaves_cross_project_definition_unassigned():
    organization = Organization.objects.create(name="Ambiguous", slug="project-ambiguous")
    definition = _definition(organization, "ambiguous-chain")

    for order, slug in enumerate(("alpha", "beta")):
        project = _project(organization, slug)
        WorkflowStage.objects.create(
            definition=definition,
            order=order,
            agent_definition=_agent(project, f"{slug}-agent"),
        )

    _backfill()

    definition.refresh_from_db()
    assert definition.project_id is None
