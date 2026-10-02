"""Actual UI documents and real PostgreSQL GraphQL metadata, without RBAC substitutes."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from graphql import build_schema, parse, validate

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities.workflow_stage_activities import (
    _get_workflow_stages_sync,
)
from config.schema import schema
from core.run_input_contract import digest
from core.tenancy import TenantContext, tenant_context
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution
from workflows.reviewed_starts import definition_revision, reserve_start


def test_every_actual_tiered_ui_document_validates_against_published_sdl():
    root = Path(__file__).resolve().parents[3]
    graph = build_schema((root / "backend/schema.graphql").read_text())
    for filename in ("tiered.queries.ts", "tiered.mutations.ts"):
        text = (root / "frontend/graphql/workflows" / filename).read_text()
        fields = dict(re.findall(r"const (\w+) = `([\s\S]*?)`;", text))
        documents = re.findall(r"export const (\w+) = gql`([\s\S]*?)`;", text)
        assert documents
        for name, document in documents:
            for field, value in fields.items():
                document = document.replace("${" + field + "}", value)
            assert "${" not in document
            assert not validate(graph, parse(document)), name


@pytest.mark.django_db(transaction=True)
def test_real_review_freezes_return_target_and_round_budget_after_live_edit():
    org = Organization.objects.create(name="Frozen return", slug="frozen-return")
    user = get_user_model().objects.create_superuser(
        username="frozen-return", email="return@example.test", password="test"
    )
    definition = WorkflowDefinition.objects.create(
        organization=org,
        name="Review",
        slug="frozen-return",
        model_label="",
        pattern_kind="review_loop",
    )
    WorkflowStage.objects.create(
        definition=definition, order=0, kind="checkpoint", output_key="draft"
    )
    gate = WorkflowStage.objects.create(
        definition=definition,
        order=1,
        kind="human_gate",
        output_key="review",
        back_edge={
            "to": "draft",
            "when": "gate_rejected",
            "max_rounds": 2,
            "on_exhausted": "fail",
        },
    )
    before = definition_revision(definition)
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        row = reserve_start(
            definition_id=definition.guid,
            expected_revision=before,
            expected_input_schema_digest=digest(definition.input_schema),
            request_id="bounded-reviewed-return",
            inputs={},
            user=user,
        )
        gate.back_edge = {**gate.back_edge, "max_rounds": 7}
        gate.save()
        plan = _get_workflow_stages_sync(
            definition.slug,
            str(row.execution.pk),
            workflow_definition_id=str(definition.pk),
            review_bounded_loops=True,
        )
        assert plan["stages"][1]["back_edge"]["max_rounds"] == 2
        assert definition_revision(definition) != before


@pytest.mark.django_db
def test_graphql_publishes_real_round_and_owned_branch_identity_and_refuses_other_org():
    org = Organization.objects.create(name="Round identity", slug="round-identity")
    other = Organization.objects.create(
        name="Other round identity", slug="other-round-identity"
    )
    user = get_user_model().objects.create_superuser(
        username="round-reader", email="reader@example.test", password="test"
    )
    definition = WorkflowDefinition.objects.create(
        organization=org, name="Round identity", slug="round-identity", model_label=""
    )
    stage = WorkflowStage.objects.create(
        definition=definition, order=0, kind="agent_dispatch"
    )
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        status="running",
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="round-target",
        run_id="round-run",
    )
    parent = WorkflowStageExecution.objects.create(
        slug="round-parent", workflow_run=run, stage=stage, round_number=7
    )
    cause = {
        "edge": "review->draft",
        "reason": "gate_rejected",
        "max_rounds": 5,
        "edge_round": 3,
    }
    branch = WorkflowStageExecution.objects.create(
        slug="round-branch",
        workflow_run=run,
        stage=stage,
        round_number=7,
        attempt_number=2,
        caused_by=cause,
        fanout_parent_execution=parent,
        fanout_index=1,
    )
    query = 'query{workflowStageExecutions(workflowId:"round-target",runId:"round-run"){guid roundNumber attemptNumber causedBy{edge reason maxRounds edgeRound} fanoutStageId fanoutParentExecutionGuid fanoutIndex}}'

    def execute(org_id):
        with tenant_context(
            TenantContext(organization_id=org_id, actor_user_id=user.pk)
        ):
            return schema.execute_sync(
                query, context_value=SimpleNamespace(user=user, request=None)
            )

    own = execute(org.pk)
    assert not own.errors
    row = next(
        row
        for row in own.data["workflowStageExecutions"]
        if row["guid"] == str(branch.guid)
    )
    assert row == {
        "guid": str(branch.guid),
        "roundNumber": 7,
        "attemptNumber": 2,
        "causedBy": {
            "edge": "review->draft",
            "reason": "gate_rejected",
            "maxRounds": 5,
            "edgeRound": 3,
        },
        "fanoutStageId": str(stage.guid),
        "fanoutParentExecutionGuid": str(parent.guid),
        "fanoutIndex": 1,
    }
    foreign = execute(other.pk)
    assert not foreign.errors and foreign.data["workflowStageExecutions"] == []


@pytest.mark.django_db
@pytest.mark.parametrize("pattern", ["supervisor_worker", "advisor"])
def test_inert_patterns_are_not_new_native_or_configured_authoring_choices(pattern):
    from workflows.manifest import (
        create_definition_from_manifest,
        parse_workflow_manifest,
    )
    from workflows.models import Workflow

    org = Organization.objects.create(
        name="Supported patterns", slug="supported-patterns"
    )
    user = get_user_model().objects.create_superuser(
        username="pattern-operator", email="operator@example.test", password="test"
    )
    parsed = parse_workflow_manifest(
        f'[workflow]\nname="Unsupported"\nslug="unsupported"\npattern="{pattern}"'
    )
    with pytest.raises(ValueError, match="no supported executor"):
        create_definition_from_manifest(parsed, organization=org)
    # Keep old records readable without allowing another inert configured copy.
    definition = WorkflowDefinition.objects.create(
        organization=org,
        name="Legacy",
        slug="legacy-pattern",
        model_label="",
        pattern_kind=pattern,
    )
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        context = SimpleNamespace(user=user, request=None)
        configured = schema.execute_sync(
            'mutation{createWorkflow(name:"No new inert run",definitionSlug:"legacy-pattern"){ok errors{field messages}}}',
            context_value=context,
        )
        cloned = schema.execute_sync(
            'mutation{cloneWorkflowDefinition(slug:"legacy-pattern"){ok errors{field messages}}}',
            context_value=context,
        )
    assert not configured.errors and not cloned.errors
    assert (
        not configured.data["createWorkflow"]["ok"]
        and not cloned.data["cloneWorkflowDefinition"]["ok"]
    )
    assert not Workflow.objects.filter(organization=org).exists()
    assert WorkflowDefinition.objects.filter(organization=org).count() == 1
