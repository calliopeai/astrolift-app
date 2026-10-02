import pytest

from workflows.back_edges import (
    LoopContractError,
    edge_matches,
    validate_back_edge,
    validate_loop_plan,
)
from workflows.manifest import emit_workflow_manifest, parse_workflow_manifest


def edge(**changes):
    return {"to": "draft", "when": "gate_rejected", "max_rounds": 3, **changes}


@pytest.mark.parametrize(
    "value",
    [
        {"to": "draft", "when": "gate_rejected"},
        None,
        True,
        [],
        edge(max_rounds=None),
        edge(max_rounds=True),
        edge(max_rounds=0),
        edge(max_rounds=21),
        edge(max_rounds=2.5),
        edge(when="unknown"),
        edge(on_exhausted="skip"),
        edge(typo=True),
    ],
)
def test_back_edge_refuses_unbounded_or_ambiguous_contracts(value):
    with pytest.raises(LoopContractError):
        validate_back_edge(value, kind="human_gate")


@pytest.mark.parametrize("target", ["review", "later", "missing"])
def test_return_target_must_be_an_earlier_live_stage(target):
    with pytest.raises(LoopContractError, match="earlier"):
        validate_loop_plan(
            [
                {"order": 0, "kind": "checkpoint", "output_key": "draft"},
                {
                    "order": 1,
                    "kind": "human_gate",
                    "output_key": "review",
                    "back_edge": edge(to=target),
                },
                {"order": 2, "kind": "checkpoint", "output_key": "later"},
            ],
            pattern_kind="review_loop",
        )


def test_review_loop_label_alone_is_not_an_executable_loop():
    with pytest.raises(LoopContractError, match="requires an explicit bounded"):
        validate_loop_plan(
            [{"order": 0, "kind": "human_gate"}], pattern_kind="review_loop"
        )


def test_overlapping_edges_have_a_finite_whole_run_budget():
    plan = [
        {"order": index, "kind": "checkpoint", "output_key": f"s{index}"}
        for index in range(100)
    ]
    for index in range(1, 100):
        plan[index]["back_edge"] = {
            "to": "s0",
            "when": "output_equals",
            "path": "again",
            "value": True,
            "max_rounds": 20,
        }
    with pytest.raises(LoopContractError, match="maximum stage visits"):
        validate_loop_plan(plan, pattern_kind="chained")


@pytest.mark.parametrize(
    "output",
    [
        {},
        {"tests": {}},
        {"tests": {"passed": "false"}},
        {"tests": {"passed": 0}},
        {"tests": {"passed": float("nan")}},
    ],
)
def test_missing_or_incompatible_condition_data_is_unavailable(output):
    condition = validate_back_edge(
        edge(when="output_equals", path="tests.passed", value=False), kind="checkpoint"
    )
    with pytest.raises(LoopContractError):
        edge_matches(condition, output)
    assert edge_matches(condition, {"tests": {"passed": False}})
    assert not edge_matches(condition, {"tests": {"passed": True}})


def test_bounded_back_edge_toml_round_trip():
    parsed = parse_workflow_manifest("""[workflow]
slug = "review"
name = "Review"
pattern = "review_loop"
[[stage]]
kind = "checkpoint"
output_key = "draft"
[[stage]]
kind = "human_gate"
output_key = "review"
[stage.back_edge]
to = "draft"
when = "gate_rejected"
max_rounds = 5
on_exhausted = "escalate"
""")
    assert parsed.stages[1].back_edge == edge(max_rounds=5, on_exhausted="escalate")
    assert parse_workflow_manifest(emit_workflow_manifest(parsed)) == parsed


def test_execution_budget_includes_attempts_and_parallel_branches():
    stages = [
        {
            "order": 0,
            "kind": "agent_dispatch",
            "output_key": "draft",
            "max_attempts": 20,
            "fan_out_count": 50,
        },
        {
            "order": 1,
            "kind": "human_gate",
            "output_key": "review",
            "back_edge": edge(max_rounds=2),
        },
    ]
    with pytest.raises(LoopContractError, match="maximum execution units"):
        validate_loop_plan(stages, pattern_kind="review_loop")


def test_return_targets_cannot_depend_on_reusable_positional_defaults():
    with pytest.raises(LoopContractError, match="explicit stable"):
        validate_loop_plan(
            [
                {"order": 0, "kind": "checkpoint"},
                {
                    "order": 1,
                    "kind": "human_gate",
                    "output_key": "review",
                    "back_edge": edge(to="stage_0"),
                },
            ],
            pattern_kind="review_loop",
        )


def test_dsl_preserves_return_edge_targets_attempts_and_agent_instructions():
    from workflows.services.dsl_parser import (
        emit_workflows_dsl,
        parse_workflows_dsl,
        validate_workflow_dsl,
    )

    definitions = parse_workflows_dsl("""workflows:
  - slug: review
    name: Review
    pattern_kind: review_loop
    stages:
      - kind: agent_dispatch
        agent_ref: writer
        output_key: draft
        environment_spec_slug: default
        prompt: Revise using feedback
        role: writer
        max_attempts: 4
      - kind: human_gate
        output_key: review
        approvers: [team:reviewers]
        back_edge:
          to: draft
          when: gate_rejected
          max_rounds: 5
          on_exhausted: escalate
""")
    assert validate_workflow_dsl(definitions[0]) == []
    assert parse_workflows_dsl(emit_workflows_dsl(definitions)) == definitions


@pytest.mark.django_db(transaction=True)
def test_real_pg_repeated_activity_has_one_round_attempt_and_refuses_foreign_stage():
    from astrolift_identity.models import Organization
    from astrolift_operations.models import WorkflowRun
    from astrolift_workflows.activities.workflow_stage_activities import (
        _create_stage_execution_sync,
    )
    from workflows.models import (
        WorkflowDefinition,
        WorkflowStage,
        WorkflowStageExecution,
    )

    org = Organization.objects.create(name="Bounded identity", slug="bounded-identity")
    definition = WorkflowDefinition.objects.create(
        organization=org, name="Identity", slug="identity", model_label=""
    )
    foreign = WorkflowDefinition.objects.create(
        organization=org, name="Foreign", slug="foreign", model_label=""
    )
    stage = WorkflowStage.objects.create(
        definition=definition, order=0, kind="checkpoint", slug="identity-stage"
    )
    foreign_stage = WorkflowStage.objects.create(
        definition=foreign, order=0, kind="checkpoint", slug="foreign-stage"
    )
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        status="running",
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="bounded-identity",
        run_id="",
    )
    context = {
        "round_number": 2,
        "caused_by": {
            "edge": "review->draft",
            "reason": "gate_rejected",
            "max_rounds": 3,
            "edge_round": 2,
        },
    }
    first = _create_stage_execution_sync(str(run.pk), str(stage.pk), 1, context)
    assert _create_stage_execution_sync(str(run.pk), str(stage.pk), 1, context) == first
    assert WorkflowStageExecution.objects.filter(workflow_run=run).count() == 1
    with pytest.raises(ValueError, match="does not belong"):
        _create_stage_execution_sync(str(run.pk), str(foreign_stage.pk), 1, context)
    with pytest.raises(ValueError):
        _create_stage_execution_sync(str(run.pk), str(stage.pk), 21, context)


@pytest.mark.parametrize("value", [None, False, True, 0, 1.5, "", "null"])
def test_toml_preserves_typed_scalar_conditions_including_json_null(value):
    from workflows.manifest import (
        ParsedWorkflowManifest,
        WorkflowDefSpec,
        WorkflowStageSpec,
    )

    parsed = ParsedWorkflowManifest(
        definition=WorkflowDefSpec(
            slug="typed-condition", name="Typed condition", pattern="chained"
        ),
        stages=[
            WorkflowStageSpec(order=0, kind="checkpoint", output_key="draft"),
            WorkflowStageSpec(
                order=1,
                kind="checkpoint",
                output_key="check",
                back_edge={
                    "to": "draft",
                    "when": "output_equals",
                    "max_rounds": 2,
                    "on_exhausted": "fail",
                    "path": "value",
                    "value": value,
                },
            ),
        ],
    )
    encoded = emit_workflow_manifest(parsed)
    assert parse_workflow_manifest(encoded) == parsed
    if value is None:
        assert "back_edge_json" in encoded


@pytest.mark.django_db(transaction=True)
def test_new_bounded_execution_refuses_an_unbound_run_mirror():
    from astrolift_identity.models import Organization
    from astrolift_operations.models import WorkflowRun
    from astrolift_workflows.activities.workflow_stage_activities import (
        _create_stage_execution_sync,
    )
    from workflows.models import (
        WorkflowDefinition,
        WorkflowStage,
        WorkflowStageExecution,
    )

    org = Organization.objects.create(name="Unbound run", slug="unbound-run")
    definition = WorkflowDefinition.objects.create(
        organization=org, name="Unbound", slug="unbound", model_label=""
    )
    stage = WorkflowStage.objects.create(
        definition=definition, kind="checkpoint", order=0
    )
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="unbound-run",
        run_id="",
        status="running",
    )
    with pytest.raises(ValueError, match="does not belong"):
        _create_stage_execution_sync(str(run.pk), str(stage.pk), 1, {"round_number": 1})
    assert not WorkflowStageExecution.objects.filter(workflow_run=run).exists()
