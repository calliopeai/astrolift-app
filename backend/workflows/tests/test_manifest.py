"""Workflow manifest serializer round-trip + grammar coverage (spec 40 §5.4)."""

from __future__ import annotations

import pytest
from astrolift_manifest.parser import ManifestError

from workflows.manifest import (
    ParsedWorkflowManifest,
    WorkflowDefSpec,
    WorkflowStageSpec,
    create_definition_from_manifest,
    definition_to_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
    replace_definition_from_manifest,
    shape_compatible,
)
from workflows.models import Workflow, WorkflowDefinition, WorkflowStage

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
    if kind == WorkflowStage.StageKind.COLLECTION:
        child = '\noutput_key = "each"\niteration_json = \'{"max_items":1,"items":[],"body_end":"body"}\'\n\n[[stage]]\nkind = "checkpoint"\noutput_key = "body"'
    elif kind == WorkflowStage.StageKind.FORMAT_RECORD:
        child = '\niteration_json = \'{"source_format":"langflow_parser","pattern":"{text}","separator":""}\''
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


# --------------------------------------------------------------------------- #
# replace: in place / versioned / blocked (#1822)
# --------------------------------------------------------------------------- #


def _org(slug):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name=slug, slug=slug)


TWO_STAGE_TOML = """\
[workflow]
slug = "demand-scout"
name = "Demand Scout"
pattern = "chained"

[[stage]]
kind = "agent_dispatch"
role = "scout"

[[stage]]
kind = "human_gate"
"""

TWO_STAGE_TOML_EDITED = """\
[workflow]
slug = "demand-scout"
name = "Demand Scout v2"
pattern = "chained"

[[stage]]
kind = "agent_dispatch"
role = "scout"
prompt = "Look for accounts matching the new persona."

[[stage]]
kind = "human_gate"
prompt = "Approve the leads?"
"""

ONE_STAGE_TOML = """\
[workflow]
slug = "demand-scout"
name = "Demand Scout"
pattern = "chained"

[[stage]]
kind = "agent_dispatch"
"""

# No agent_dispatch stage in this pair: a configured Workflow needs no
# binding to save cleanly, so the tests below can isolate replace's own
# in-place/versioned/blocked behavior from stage_bindings validation.
TWO_GATE_TOML = """\
[workflow]
slug = "demand-scout"
name = "Demand Scout"
pattern = "chained"

[[stage]]
kind = "human_gate"
prompt = "Approve stage one?"

[[stage]]
kind = "checkpoint"
"""

TWO_GATE_TOML_EDITED = """\
[workflow]
slug = "demand-scout"
name = "Demand Scout v2"
pattern = "chained"

[[stage]]
kind = "human_gate"
prompt = "Approve the updated persona targeting?"

[[stage]]
kind = "checkpoint"
"""

ONE_GATE_TOML = """\
[workflow]
slug = "demand-scout"
name = "Demand Scout"
pattern = "chained"

[[stage]]
kind = "human_gate"
prompt = "Approve?"
"""


@pytest.mark.django_db
def test_shape_compatible_true_for_matching_kind_sequence():
    org = _org("shape-match")
    definition = create_definition_from_manifest(parse_workflow_manifest(TWO_STAGE_TOML), organization=org)
    assert shape_compatible(definition, parse_workflow_manifest(TWO_STAGE_TOML_EDITED))


@pytest.mark.django_db
def test_shape_compatible_false_for_stage_count_change():
    org = _org("shape-count")
    definition = create_definition_from_manifest(parse_workflow_manifest(TWO_STAGE_TOML), organization=org)
    assert not shape_compatible(definition, parse_workflow_manifest(ONE_STAGE_TOML))


@pytest.mark.django_db
def test_shape_compatible_false_for_kind_change():
    org = _org("shape-kind")
    definition = create_definition_from_manifest(parse_workflow_manifest(TWO_STAGE_TOML), organization=org)
    swapped = TWO_STAGE_TOML.replace('kind = "human_gate"', 'kind = "checkpoint"')
    assert not shape_compatible(definition, parse_workflow_manifest(swapped))


@pytest.mark.django_db
def test_shape_compatible_ignores_order_gap_from_soft_deleted_stage():
    """A soft-deleted middle stage leaves a gap in ``order``: comparison is
    positional (live-stage index), not the raw ``order`` value (#1822)."""
    from django.utils import timezone

    org = _org("shape-gap")
    definition = WorkflowDefinition.objects.create(
        name="Gapped",
        slug="gapped",
        organization=org,
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        model_label="",
    )
    WorkflowStage.objects.create(definition=definition, order=0, kind=WorkflowStage.StageKind.AGENT_DISPATCH)
    WorkflowStage.objects.create(
        definition=definition,
        order=1,
        kind=WorkflowStage.StageKind.HUMAN_GATE,
        deleted_at=timezone.now(),
    )
    WorkflowStage.objects.create(definition=definition, order=2, kind=WorkflowStage.StageKind.CHECKPOINT)

    parsed = parse_workflow_manifest(
        '[workflow]\nslug = "gapped"\nname = "Gapped"\npattern = "chained"\n'
        '\n[[stage]]\nkind = "agent_dispatch"\n\n[[stage]]\nkind = "checkpoint"\n'
    )
    assert shape_compatible(definition, parsed)


@pytest.mark.django_db
def test_replace_creates_when_no_org_owned_definition_exists():
    org = _org("replace-create")
    outcome = replace_definition_from_manifest(parse_workflow_manifest(TWO_STAGE_TOML), organization=org)
    assert outcome.mode == "created"
    assert outcome.definition.slug == "demand-scout"
    assert outcome.definition.organization_id == org.pk


@pytest.mark.django_db
def test_replace_leaves_a_global_template_untouched_and_creates_org_copy():
    """A platform-global (organization=None) sharing the slug is not "the
    org's own": replace creates an org-scoped copy instead of touching it,
    identical to a plain import (spec 40 §2.1)."""
    global_def = create_definition_from_manifest(parse_workflow_manifest(TWO_STAGE_TOML), organization=None)
    org = _org("replace-global")

    outcome = replace_definition_from_manifest(
        parse_workflow_manifest(TWO_STAGE_TOML_EDITED), organization=org
    )

    assert outcome.mode == "created"
    assert outcome.definition.organization_id == org.pk
    global_def.refresh_from_db()
    assert global_def.name == "Demand Scout"  # untouched: still the original


@pytest.mark.django_db
def test_replace_updates_in_place_preserves_stage_identity_and_bindings():
    """Proves running-instance + binding safety (#1822): matching shape means
    the same ``WorkflowDefinition``/``WorkflowStage`` rows (same pks) are
    edited in place, so a configured Workflow's ``stage_bindings`` (keyed by
    ``order``) and any ``WorkflowStageExecution``/in-flight Temporal run
    (keyed by stage pk) are unaffected: only content changed."""
    org = _org("replace-inplace")
    definition = create_definition_from_manifest(
        parse_workflow_manifest(TWO_GATE_TOML), organization=org, is_enabled=True
    )
    stage0 = definition.stages.get(order=0)
    stage1 = definition.stages.get(order=1)
    wf = Workflow.objects.create(
        organization=org,
        definition=definition,
        name="Nightly Scout",
        slug="nightly-scout",
        stage_bindings={"1": {"params": {"prompt": "Approve?"}}},
        trigger_kind=Workflow.TriggerKind.SCHEDULE,
        schedule_cron="0 9 * * *",
    )

    outcome = replace_definition_from_manifest(
        parse_workflow_manifest(TWO_GATE_TOML_EDITED), organization=org
    )

    assert outcome.mode == "updated_in_place"
    assert outcome.definition.pk == definition.pk

    definition.refresh_from_db()
    assert definition.name == "Demand Scout v2"

    stage0.refresh_from_db()
    stage1.refresh_from_db()
    assert stage0.prompt == "Approve the updated persona targeting?"
    # Same rows: the WorkflowStage pks a past WorkflowStageExecution or an
    # in-flight run's already-fetched stage plan key on did not move.
    assert {s.pk for s in definition.stages.filter(deleted_at__isnull=True)} == {stage0.pk, stage1.pk}

    wf.refresh_from_db()
    assert wf.definition_id == definition.pk  # never repointed: same row
    assert wf.stage_bindings == {"1": {"params": {"prompt": "Approve?"}}}
    assert wf.schedule_cron == "0 9 * * *"


@pytest.mark.django_db
def test_replace_versions_and_repoints_configured_workflows():
    org = _org("replace-version")
    old = create_definition_from_manifest(
        parse_workflow_manifest(TWO_GATE_TOML), organization=org, is_enabled=True
    )
    wf = Workflow.objects.create(
        organization=org,
        definition=old,
        name="Nightly Scout",
        slug="nightly-scout",
        stage_bindings={},
        trigger_kind=Workflow.TriggerKind.SCHEDULE,
        schedule_cron="0 9 * * *",
    )

    # Drops to one stage: the shape changed, so this must version rather
    # than edit `old` in place.
    outcome = replace_definition_from_manifest(parse_workflow_manifest(ONE_GATE_TOML), organization=org)

    assert outcome.mode == "versioned"
    assert outcome.definition.pk != old.pk
    assert outcome.definition.slug == "demand-scout-1"
    # Inherits the superseded definition's enabled state: a version
    # replacing an in-use, enabled definition must not land disabled under a
    # schedule that is about to fire against it.
    assert outcome.definition.is_enabled is True
    assert outcome.repointed_slugs == ["nightly-scout"]

    wf.refresh_from_db()
    assert wf.definition_id == outcome.definition.pk
    assert wf.schedule_cron == "0 9 * * *"  # the schedule row itself is untouched

    # The old definition and its stages are untouched: any run already
    # pinned to it (WorkflowRun.workflow_definition_id, frozen at dispatch)
    # keeps resolving exactly what it started with.
    old.refresh_from_db()
    assert old.is_enabled is True
    assert old.stages.filter(deleted_at__isnull=True).count() == 2


@pytest.mark.django_db
def test_replace_blocks_and_writes_nothing_when_repoint_breaks_bindings():
    org = _org("replace-blocked")
    old = create_definition_from_manifest(
        parse_workflow_manifest(TWO_GATE_TOML), organization=org, is_enabled=True
    )
    wf = Workflow.objects.create(
        organization=org,
        definition=old,
        name="Nightly Scout",
        slug="nightly-scout",
        stage_bindings={},
        trigger_kind=Workflow.TriggerKind.MANUAL,
    )
    before_definitions = set(WorkflowDefinition.objects.values_list("pk", flat=True))

    # The new shape's agent_dispatch stage has no default agent and no
    # binding covers it: validate_bindings() must refuse the repoint.
    outcome = replace_definition_from_manifest(parse_workflow_manifest(ONE_STAGE_TOML), organization=org)

    assert outcome.mode == "blocked"
    assert outcome.definition is None
    assert "nightly-scout" in outcome.blocked_errors
    assert any("stage 0" in msg for msg in outcome.blocked_errors["nightly-scout"])

    # Nothing was written: no orphaned new version, Workflow untouched.
    assert set(WorkflowDefinition.objects.values_list("pk", flat=True)) == before_definitions
    wf.refresh_from_db()
    assert wf.definition_id == old.pk


@pytest.mark.django_db
def test_replace_in_place_rebinds_agent_by_slug():
    """The manifest's ``agent`` ref is re-resolved on every in-place update,
    just like a create: a newly-registered agent can pick up an existing
    stage's slot without a shape change."""
    org = _org("replace-agent")
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    create_definition_from_manifest(parse_workflow_manifest(ONE_STAGE_TOML), organization=org)
    team = Team.objects.create(organization=org, name="Eng", slug="replace-agent-eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="replace-agent-demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="replace-agent-app",
        provisioning_status="ready",
    )
    workload = Workload.objects.create(
        registered_app=app,
        name="Scout",
        slug="scout-agent",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
    )

    toml_with_agent = ONE_STAGE_TOML + 'agent = "scout-agent"\n'
    outcome = replace_definition_from_manifest(parse_workflow_manifest(toml_with_agent), organization=org)

    assert outcome.mode == "updated_in_place"
    stage = outcome.definition.stages.get(order=0)
    assert stage.agent_definition_id == workload.pk
    assert stage.agent_ref == "scout-agent"
