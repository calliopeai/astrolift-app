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
def project(db, org):
    from astrolift_identity.models import Project, Team

    team = Team.objects.create(organization=org, name="Team", slug="source-team")
    return Project.objects.create(
        organization=org,
        team=team,
        name="Project",
        slug="source-project",
    )


@pytest.fixture
def agent(db, org, project):
    from astrolift_registry.models import RegisteredApp, Workload

    app = RegisteredApp.objects.create(
        organization=org,
        team=project.team,
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
def test_reconcile_creates_runnable_bound_definition(org, project, agent):
    from workflows.models import WorkflowDefinition

    manifests = discover_repository_workflows({"workflows/triage.toml": WORKFLOW})
    result = reconcile_repository_workflows(
        organization=org,
        project=project,
        source_repo="steadymd/smd-agents",
        source_ref="abc123",
        manifests=manifests,
    )

    assert [(item.slug, item.created) for item in result] == [("triage-chain", True)]
    definition = WorkflowDefinition.objects.get(source_path="workflows/triage.toml")
    assert definition.source_repo == "steadymd/smd-agents"
    assert definition.project == project
    assert definition.source_ref == "abc123"
    assert definition.model_label == ""
    assert definition.is_enabled is True
    stages = list(definition.stages.order_by("order"))
    assert [stage.agent_definition_id for stage in stages] == [agent.pk, agent.pk]
    assert stages[0].environment_spec_slug == "triage-prod"
    assert stages[0].skill_refs == ["evidence"]
    assert stages[0].output_key == "evidence"


@pytest.mark.django_db
def test_reconcile_updates_in_place_and_retires_removed_stages(org, project, agent):
    from workflows.models import WorkflowDefinition, WorkflowStage

    first = discover_repository_workflows({"workflows/triage.toml": WORKFLOW})
    reconcile_repository_workflows(
        organization=org,
        project=project,
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
        project=project,
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
def test_reconcile_retires_definition_removed_from_source_tree(org, project, agent):
    from workflows.models import WorkflowDefinition

    manifests = discover_repository_workflows({"workflows/triage.toml": WORKFLOW})
    reconcile_repository_workflows(
        organization=org,
        project=project,
        source_repo="steadymd/smd-agents",
        source_ref="one",
        manifests=manifests,
    )

    reconcile_repository_workflows(
        organization=org,
        project=project,
        source_repo="steadymd/smd-agents",
        source_ref="two",
        manifests=[],
    )

    definition = WorkflowDefinition.objects.get(source_path="workflows/triage.toml")
    assert definition.deleted_at is not None
    assert definition.is_enabled is False
    assert definition.source_ref == "two"


@pytest.mark.django_db
def test_reconcile_keeps_the_definition_when_its_file_moves_in_the_repo(org, project, agent):
    """A workflow moved to another file under ``workflows/`` keeps its
    definition: same row, same stages, new path (#2610)."""
    from workflows.models import WorkflowDefinition

    reconcile_repository_workflows(
        organization=org,
        project=project,
        source_repo="steadymd/smd-agents",
        source_ref="one",
        manifests=discover_repository_workflows({"workflows/triage.toml": WORKFLOW}),
    )
    definition = WorkflowDefinition.objects.get(source_path="workflows/triage.toml")
    stage_pks = list(definition.stages.order_by("order").values_list("pk", flat=True))

    result = reconcile_repository_workflows(
        organization=org,
        project=project,
        source_repo="steadymd/smd-agents",
        source_ref="two",
        manifests=discover_repository_workflows({"workflows/triage/chain.toml": WORKFLOW}),
    )

    assert [(row.path, row.slug, row.created) for row in result] == [
        ("workflows/triage/chain.toml", "triage-chain", False)
    ]
    definition.refresh_from_db()
    assert definition.source_path == "workflows/triage/chain.toml"
    assert definition.source_ref == "two"
    assert definition.deleted_at is None
    assert list(definition.stages.order_by("order").values_list("pk", flat=True)) == stage_pks
    assert WorkflowDefinition.objects.filter(source_repo="steadymd/smd-agents").count() == 1


@pytest.mark.django_db
def test_reconcile_refuses_one_slug_declared_by_two_files(org, project, agent):
    """Two files of one tree declaring a slug are not a move."""
    from workflows.models import WorkflowDefinition

    reconcile_repository_workflows(
        organization=org,
        project=project,
        source_repo="steadymd/smd-agents",
        source_ref="one",
        manifests=discover_repository_workflows({"workflows/triage.toml": WORKFLOW}),
    )

    with pytest.raises(ValueError, match="already owned by another definition"):
        reconcile_repository_workflows(
            organization=org,
            project=project,
            source_repo="steadymd/smd-agents",
            source_ref="two",
            manifests=discover_repository_workflows(
                {"workflows/triage.toml": WORKFLOW, "workflows/triage-copy.toml": WORKFLOW}
            ),
        )

    definition = WorkflowDefinition.objects.get(slug="triage-chain")
    assert (definition.source_path, definition.source_ref) == ("workflows/triage.toml", "one")


def _imported_definition(org, project):
    from workflows.manifest import create_definition_from_manifest, parse_workflow_manifest

    return create_definition_from_manifest(parse_workflow_manifest(WORKFLOW), organization=org)


def _other_repos_definition(org, project):
    from workflows.models import WorkflowDefinition

    reconcile_repository_workflows(
        organization=org,
        project=project,
        source_repo="steadymd/other-agents",
        source_ref="one",
        manifests=discover_repository_workflows({"workflows/triage.toml": WORKFLOW}),
    )
    return WorkflowDefinition.objects.get(source_repo="steadymd/other-agents")


@pytest.mark.django_db
@pytest.mark.parametrize("make_holder", [_imported_definition, _other_repos_definition])
def test_reconcile_refuses_a_slug_held_outside_the_repo(org, project, agent, make_holder):
    """Only the repo's own definition follows its file. One imported by hand
    (no source repo) or owned by another repo keeps the slug (#2610)."""
    from workflows.models import WorkflowDefinition

    holder = make_holder(org, project)
    before = (holder.source_repo, holder.source_path, holder.source_ref)

    with pytest.raises(ValueError, match="already owned by another definition"):
        reconcile_repository_workflows(
            organization=org,
            project=project,
            source_repo="steadymd/smd-agents",
            source_ref="two",
            manifests=discover_repository_workflows({"workflows/moved.toml": WORKFLOW}),
        )

    holder.refresh_from_db()
    assert (holder.source_repo, holder.source_path, holder.source_ref) == before
    assert holder.deleted_at is None
    assert not WorkflowDefinition.objects.filter(source_repo="steadymd/smd-agents").exists()


@pytest.mark.django_db
def test_reconcile_rejects_project_outside_repository_org(org, project):
    from astrolift_identity.models import Organization, Project, Team

    other_org = Organization.objects.create(name="Other", slug="workflow-source-other")
    other_team = Team.objects.create(organization=other_org, name="Other", slug="source-other-team")
    other_project = Project.objects.create(
        organization=other_org,
        team=other_team,
        name="Other",
        slug="source-other-project",
    )

    with pytest.raises(ValueError, match="must belong"):
        reconcile_repository_workflows(
            organization=org,
            project=other_project,
            source_repo="steadymd/smd-agents",
            source_ref="abc123",
            manifests=discover_repository_workflows({"workflows/triage.toml": WORKFLOW}),
        )


@pytest.mark.django_db
def test_reconcile_supports_forward_nested_workflow_references(org, project):
    parent = """\
[workflow]
slug = "outer"
name = "Outer"
pattern = "chained"
[[stage]]
kind = "workflow"
workflow = "inner"
output_key = "inner_result"
"""
    child = """\
[workflow]
slug = "inner"
name = "Inner"
pattern = "single"
[[stage]]
kind = "checkpoint"
"""
    # Parent sorts first: validation must wait until every manifest converges.
    result = reconcile_repository_workflows(
        organization=org,
        project=project,
        source_repo="steadymd/nested",
        source_ref="abc123",
        manifests=discover_repository_workflows(
            {
                "workflows/a-parent.toml": parent,
                "workflows/z-child.toml": child,
            }
        ),
    )
    assert [row.slug for row in result] == ["outer", "inner"]
    from workflows.models import WorkflowDefinition

    assert WorkflowDefinition.objects.get(slug="outer").stages.get(order=0).workflow_ref == "inner"


@pytest.mark.django_db
def test_reconcile_rejects_cycle_and_rolls_back_every_definition(org, project):
    def manifest(slug: str, child: str) -> str:
        return f"""\
[workflow]
slug = "{slug}"
name = "{slug}"
pattern = "chained"
[[stage]]
kind = "workflow"
workflow = "{child}"
"""

    with pytest.raises(ValueError, match="nested workflow cycle"):
        reconcile_repository_workflows(
            organization=org,
            project=project,
            source_repo="steadymd/cycle",
            source_ref="abc123",
            manifests=discover_repository_workflows(
                {
                    "workflows/a.toml": manifest("a", "b"),
                    "workflows/b.toml": manifest("b", "a"),
                }
            ),
        )

    from workflows.models import WorkflowDefinition

    assert not WorkflowDefinition.objects.filter(source_repo="steadymd/cycle").exists()
