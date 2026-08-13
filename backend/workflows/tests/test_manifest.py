"""Workflow manifest serializer round-trip + grammar coverage (spec 40 §5.4)."""

from __future__ import annotations

import pytest

from astrolift_manifest.parser import ManifestError
from workflows.manifest import (
    ParsedWorkflowManifest,
    WorkflowDefSpec,
    WorkflowStageSpec,
    definition_to_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
)
from workflows.models import WorkflowDefinition, WorkflowStage

# The spec 40 §5.4 reference manifest: a chained workflow with an
# agent_dispatch stage (role + agent + all three skill-ref forms +
# on_failure + timeout) followed by a human_gate (prompt + approvers).
FIXTURE = """\
[workflow]
slug = "feature-dev"
name = "Feature Dev"
pattern = "chained"
description = "Implement, then gate."

[[stage]]
kind = "agent_dispatch"
role = "implementer"
agent = "my-coder"
environment_spec_slug = "coder-large"
skills = ["write-tests", "./skills/foo", "acme/dev-skills/lint@v2"]
prompt = "Implement the accepted specification."
output_key = "implementation"
on_failure = "retry"
timeout = 600
fan_out = 3

[[stage]]
kind = "human_gate"
prompt = "Approve the implementation?"
approvers = ["team:reviewers"]
timeout = 86400
"""


# --------------------------------------------------------------------------- #
# Parse correctness
# --------------------------------------------------------------------------- #


def test_parse_definition_fields():
    parsed = parse_workflow_manifest(FIXTURE)
    assert parsed.definition == WorkflowDefSpec(
        slug="feature-dev",
        name="Feature Dev",
        pattern="chained",
        description="Implement, then gate.",
    )


def test_parse_stages_map_to_order():
    parsed = parse_workflow_manifest(FIXTURE)
    assert [s.order for s in parsed.stages] == [0, 1]

    agent_stage = parsed.stages[0]
    assert agent_stage.kind == "agent_dispatch"
    assert agent_stage.role == "implementer"
    assert agent_stage.agent == "my-coder"
    assert agent_stage.environment_spec_slug == "coder-large"
    assert agent_stage.prompt == "Implement the accepted specification."
    assert agent_stage.output_key == "implementation"
    assert agent_stage.on_failure == "retry"
    assert agent_stage.timeout == 600
    assert agent_stage.fan_out == 3

    gate = parsed.stages[1]
    assert gate.kind == "human_gate"
    assert gate.prompt == "Approve the implementation?"
    assert gate.approvers == ["team:reviewers"]
    assert gate.agent is None
    assert gate.timeout == 86400


def test_skillref_grammar_passthrough():
    """All three spec 39 reference forms round-trip to their canonical string."""
    parsed = parse_workflow_manifest(FIXTURE)
    assert parsed.stages[0].skills == [
        "write-tests",  # catalogue
        "./skills/foo",  # local
        "acme/dev-skills/lint@v2",  # org-repo + @pin
    ]


def test_role_only_global_stage_omits_agent():
    toml = """\
[workflow]
slug = "review"
name = "Review"
pattern = "review_loop"

[[stage]]
kind = "agent_dispatch"
role = "reviewer"
"""
    parsed = parse_workflow_manifest(toml)
    assert parsed.stages[0].agent is None
    assert parsed.stages[0].role == "reviewer"


@pytest.mark.parametrize("pattern", [c.value for c in WorkflowDefinition.PatternKind])
def test_every_pattern_kind_parses(pattern):
    toml = f'[workflow]\nslug = "w"\nname = "W"\npattern = "{pattern}"\n'
    parsed = parse_workflow_manifest(toml)
    assert parsed.definition.pattern == pattern


@pytest.mark.parametrize("kind", [c.value for c in WorkflowStage.StageKind])
def test_every_stage_kind_parses(kind):
    child = '\nworkflow = "child"' if kind == WorkflowStage.StageKind.WORKFLOW else ""
    toml = f'[workflow]\nslug = "w"\nname = "W"\npattern = "single"\n\n[[stage]]\nkind = "{kind}"{child}\n'
    parsed = parse_workflow_manifest(toml)
    assert parsed.stages[0].kind == kind


def test_nested_workflow_stage_round_trips():
    toml = """\
[workflow]
slug = "outer"
name = "Outer"
pattern = "chained"

[[stage]]
kind = "workflow"
workflow = "inner"
output_key = "inner_result"
timeout = 900
"""
    parsed = parse_workflow_manifest(toml)
    assert parsed.stages[0].workflow == "inner"
    assert parse_workflow_manifest(emit_workflow_manifest(parsed)) == parsed


def test_nested_workflow_stage_requires_child_slug():
    toml = '[workflow]\nslug="outer"\nname="Outer"\n\n[[stage]]\nkind="workflow"\n'
    with pytest.raises(ManifestError) as exc:
        parse_workflow_manifest(toml)
    assert exc.value.path == "stage[0].workflow"


def test_workflow_child_slug_rejected_on_other_stage_kinds():
    toml = '[workflow]\nslug="outer"\nname="Outer"\n\n[[stage]]\nkind="checkpoint"\nworkflow="inner"\n'
    with pytest.raises(ManifestError) as exc:
        parse_workflow_manifest(toml)
    assert exc.value.path == "stage[0].workflow"


def test_fan_out_dynamic():
    toml = (
        '[workflow]\nslug = "w"\nname = "W"\npattern = "fan_out"\n\n'
        '[[stage]]\nkind = "agent_dispatch"\nfan_out = "dynamic"\n'
    )
    parsed = parse_workflow_manifest(toml)
    assert parsed.stages[0].fan_out == "dynamic"


# --------------------------------------------------------------------------- #
# Round-trip: emit ∘ parse is lossless + stable
# --------------------------------------------------------------------------- #


def test_emit_parse_is_lossless():
    once = parse_workflow_manifest(FIXTURE)
    emitted = emit_workflow_manifest(once)
    twice = parse_workflow_manifest(emitted)
    # No information lost crossing the emit boundary.
    assert once == twice


def test_emit_is_stable():
    parsed = parse_workflow_manifest(FIXTURE)
    first = emit_workflow_manifest(parsed)
    second = emit_workflow_manifest(parse_workflow_manifest(first))
    # Emit is a fixed point — re-emitting yields byte-identical TOML.
    assert first == second


def test_emit_dynamic_fan_out_round_trips():
    spec = ParsedWorkflowManifest(
        definition=WorkflowDefSpec(slug="w", name="W", pattern="fan_out"),
        stages=[WorkflowStageSpec(order=0, kind="agent_dispatch", fan_out="dynamic")],
    )
    assert parse_workflow_manifest(emit_workflow_manifest(spec)).stages[0].fan_out == "dynamic"


# --------------------------------------------------------------------------- #
# Structured errors (never raw exceptions)
# --------------------------------------------------------------------------- #


def test_malformed_toml_raises_structured_error():
    with pytest.raises(ManifestError) as exc:
        parse_workflow_manifest('[workflow\nslug = "x"')
    assert "invalid TOML" in str(exc.value)
    assert exc.value.line is not None


def test_missing_workflow_table():
    with pytest.raises(ManifestError) as exc:
        parse_workflow_manifest('[[stage]]\nkind = "checkpoint"\n')
    assert exc.value.path == "workflow"


def test_bad_pattern_reports_path():
    with pytest.raises(ManifestError) as exc:
        parse_workflow_manifest('[workflow]\nslug = "w"\nname = "W"\npattern = "nope"\n')
    assert exc.value.path == "workflow.pattern"


def test_bad_stage_kind_reports_indexed_path():
    toml = '[workflow]\nslug = "w"\nname = "W"\npattern = "single"\n\n[[stage]]\nkind = "bogus"\n'
    with pytest.raises(ManifestError) as exc:
        parse_workflow_manifest(toml)
    assert exc.value.path == "stage[0].kind"


def test_bad_on_failure_reports_path():
    toml = (
        '[workflow]\nslug = "w"\nname = "W"\npattern = "single"\n\n'
        '[[stage]]\nkind = "agent_dispatch"\non_failure = "explode"\n'
    )
    with pytest.raises(ManifestError) as exc:
        parse_workflow_manifest(toml)
    assert exc.value.path == "stage[0].on_failure"


def test_malformed_skill_ref_reports_path():
    toml = (
        '[workflow]\nslug = "w"\nname = "W"\npattern = "single"\n\n'
        '[[stage]]\nkind = "agent_dispatch"\nskills = ["a@b@c"]\n'
    )
    with pytest.raises(ManifestError) as exc:
        parse_workflow_manifest(toml)
    assert exc.value.path == "stage[0].skills[0]"


def test_duplicate_effective_output_key_reports_path():
    toml = """\
[workflow]
slug = "duplicate-output"
name = "Duplicate output"

[[stage]]
kind = "checkpoint"

[[stage]]
kind = "checkpoint"
output_key = "stage_0"
"""
    with pytest.raises(ManifestError, match="output_key values must be unique") as exc:
        parse_workflow_manifest(toml)
    assert exc.value.path == "stage.output_key"


# --------------------------------------------------------------------------- #
# Model → emit → parse (real Postgres)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_definition_emit_parse_equivalent():
    definition = WorkflowDefinition.objects.create(
        name="Triage Bot",
        slug="triage-bot",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        model_label="agents.Agent",
        description="A persisted definition.",
    )
    WorkflowStage.objects.create(
        definition=definition,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        role="triager",
        skill_refs=["write-tests", "acme/dev-skills/lint@v2"],
        environment_spec_slug="triage-runtime",
        prompt="Triage the report.",
        output_key="triage",
        on_failure=WorkflowStage.OnFailure.RETRY,
        timeout_seconds=600,
        fan_out_count=3,
    )
    WorkflowStage.objects.create(
        definition=definition,
        order=1,
        kind=WorkflowStage.StageKind.HUMAN_GATE,
        timeout_seconds=86400,
    )

    toml = emit_workflow_manifest(definition_to_manifest(definition))
    parsed = parse_workflow_manifest(toml)

    assert parsed.definition.slug == "triage-bot"
    assert parsed.definition.name == "Triage Bot"
    assert parsed.definition.pattern == "chained"
    assert parsed.definition.description == "A persisted definition."

    assert [s.order for s in parsed.stages] == [0, 1]
    s0 = parsed.stages[0]
    assert s0.kind == "agent_dispatch"
    assert s0.role == "triager"
    assert s0.skills == ["write-tests", "acme/dev-skills/lint@v2"]
    assert s0.environment_spec_slug == "triage-runtime"
    assert s0.prompt == "Triage the report."
    assert s0.output_key == "triage"
    assert s0.on_failure == "retry"
    assert s0.timeout == 600
    assert s0.fan_out == 3

    s1 = parsed.stages[1]
    assert s1.kind == "human_gate"
    assert s1.timeout == 86400
