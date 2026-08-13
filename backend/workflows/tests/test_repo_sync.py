from __future__ import annotations

import pytest

from workflows.repo_sync import (
    discover_repository_workflows,
    reconcile_repository_workflows,
)

WORKFLOW = """\
[workflow]
slug = "triage-chain"
name = "Triage Chain"
pattern = "chained"

[[stage]]
kind = "agent_dispatch"
agent = "triage-agent"
environment_spec_slug = "triage-prod"
skills = ["evidence"]
prompt = "Collect evidence."
output_key = "evidence"

[[stage]]
kind = "agent_dispatch"
agent = "triage-agent"
prompt = "Classify the prior evidence."
output_key = "classification"
"""


@pytest.fixture
def org(db):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Workflow Source Org", slug="workflow-source-org")


@pytest.fixture
def agent(db, org):
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    team = Team.objects.create(organization=org, name="Team", slug="source-team")
    project = Project.objects.create(organization=org, team=team, name="Project", slug="source-project")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Triage Agent",
        slug="triage-agent",
    )
    return Workload.objects.create(
        registered_app=app,
        name="Triage Agent",
        slug="triage-agent",
        kind=Workload.Kind.AGENT,
    )


def test_discovery_is_confined_to_workflows_directory():
    found = discover_repository_workflows(
        {
            "workflows/triage.toml": WORKFLOW,
            "workflows/nested/second.toml": WORKFLOW.replace("triage-chain", "triage-chain-2", 1),
            "agents/triage/astrolift.toml": "[app]\nname='not-a-workflow'\n",
            ".github/workflows/deploy.yml": "ignored",
            "workflows/../outside.toml": WORKFLOW,
        }
    )
    assert [item.path for item in found] == [
        "workflows/nested/second.toml",
        "workflows/triage.toml",
    ]


@pytest.mark.django_db
def test_reconcile_creates_runnable_bound_definition(org, agent):
    from workflows.models import WorkflowDefinition

    manifests = discover_repository_workflows({"workflows/triage.toml": WORKFLOW})
    result = reconcile_repository_workflows(
        organization=org,
        source_repo="steadymd/smd-agents",
        source_ref="abc123",
        manifests=manifests,
    )

    assert [(item.slug, item.created) for item in result] == [("triage-chain", True)]
    definition = WorkflowDefinition.objects.get(source_path="workflows/triage.toml")
    assert definition.source_repo == "steadymd/smd-agents"
    assert definition.source_ref == "abc123"
    assert definition.model_label == ""
    assert definition.is_enabled is True
    stages = list(definition.stages.order_by("order"))
    assert [stage.agent_definition_id for stage in stages] == [agent.pk, agent.pk]
    assert stages[0].environment_spec_slug == "triage-prod"
    assert stages[0].skill_refs == ["evidence"]
    assert stages[0].output_key == "evidence"


@pytest.mark.django_db
def test_reconcile_updates_in_place_and_retires_removed_stages(org, agent):
    from workflows.models import WorkflowDefinition, WorkflowStage

    first = discover_repository_workflows({"workflows/triage.toml": WORKFLOW})
    reconcile_repository_workflows(
        organization=org,
        source_repo="steadymd/smd-agents",
        source_ref="one",
        manifests=first,
    )
    definition = WorkflowDefinition.objects.get(source_path="workflows/triage.toml")
    stage_zero_pk = definition.stages.get(order=0).pk

    changed = WORKFLOW.replace('name = "Triage Chain"', 'name = "Triage Chain v2"').split("\n[[stage]]", 2)
    one_stage = changed[0] + "\n[[stage]]" + changed[1]
    second = discover_repository_workflows({"workflows/triage.toml": one_stage})
    result = reconcile_repository_workflows(
        organization=org,
        source_repo="steadymd/smd-agents",
        source_ref="two",
        manifests=second,
    )

    definition.refresh_from_db()
    assert result[0].created is False
    assert definition.name == "Triage Chain v2"
    assert definition.source_ref == "two"
    assert definition.stages.get(order=0).pk == stage_zero_pk
    assert WorkflowStage.objects.get(definition=definition, order=1).deleted_at is not None


@pytest.mark.django_db
def test_reconcile_retires_definition_removed_from_source_tree(org, agent):
    from workflows.models import WorkflowDefinition

    manifests = discover_repository_workflows({"workflows/triage.toml": WORKFLOW})
    reconcile_repository_workflows(
        organization=org,
        source_repo="steadymd/smd-agents",
        source_ref="one",
        manifests=manifests,
    )

    reconcile_repository_workflows(
        organization=org,
        source_repo="steadymd/smd-agents",
        source_ref="two",
        manifests=[],
    )

    definition = WorkflowDefinition.objects.get(source_path="workflows/triage.toml")
    assert definition.deleted_at is not None
    assert definition.is_enabled is False
    assert definition.source_ref == "two"
