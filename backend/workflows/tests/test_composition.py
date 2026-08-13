from __future__ import annotations

import pytest

from workflows.composition import WorkflowCompositionError, validate_workflow_composition
from workflows.models import WorkflowDefinition, WorkflowStage


@pytest.fixture
def project(db):
    from astrolift_identity.models import Organization, Project, Team

    org = Organization.objects.create(name="Composition Org", slug="composition-org")
    team = Team.objects.create(organization=org, name="Team", slug="composition-team")
    return Project.objects.create(
        organization=org,
        team=team,
        name="Project",
        slug="composition-project",
    )


def _definition(project, slug: str) -> WorkflowDefinition:
    return WorkflowDefinition.objects.create(
        organization=project.organization,
        project=project,
        name=slug,
        slug=slug,
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        is_enabled=True,
    )


def _nested(parent: WorkflowDefinition, child_slug: str) -> None:
    WorkflowStage.objects.create(
        slug=f"{parent.slug}-nested",
        definition=parent,
        order=0,
        kind=WorkflowStage.StageKind.WORKFLOW,
        workflow_ref=child_slug,
    )


@pytest.mark.django_db
def test_project_workflow_cannot_invoke_another_projects_definition(project):
    from astrolift_identity.models import Project

    other = Project.objects.create(
        organization=project.organization,
        team=project.team,
        name="Other",
        slug="composition-other",
    )
    parent = _definition(project, "parent")
    _definition(other, "foreign-child")
    _nested(parent, "foreign-child")

    with pytest.raises(WorkflowCompositionError, match="cannot resolve visible child"):
        validate_workflow_composition(parent)


@pytest.mark.django_db
def test_nested_workflow_depth_is_bounded(project):
    definitions = [_definition(project, f"depth-{index}") for index in range(10)]
    for parent, child in zip(definitions[:-1], definitions[1:], strict=True):
        _nested(parent, child.slug)

    with pytest.raises(WorkflowCompositionError, match="depth exceeds 8"):
        validate_workflow_composition(definitions[0])
