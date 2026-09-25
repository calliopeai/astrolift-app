"""Workflow routes check the permission at the object's own scope (#1965).

Cancel, terminate and signal checked ``workflow.trigger`` with no target
and then matched only the run's organization, so a selected team or
project (a team token, an ``X-Astrolift-Team`` header) authorized every
run in the org. The same shape sat behind the run readers, the
configured-workflow and definition gates whose scope factories returned
``None`` for anything without a project, the collection resolvers, and the
legacy readers that checked no permission at all. And the fleet-wide path
admitted anyone holding ``audit_log.read`` + ``admin.elevate``, which every
stock org owner and admin does, so one org's admin reached every other
org's runs.

These run against real RoleBindings on the stock roles: one org with two
teams (MedOps owns the Intake and Billing projects and an app in Intake,
Platform owns Core and an app in Core), plus a second org.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.utils import timezone
from graphql import GraphQLError

from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_operations.models import WorkflowRun
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.schema import mutations as temporal_mutations
from astrolift_workflows.schema.mutations import TemporalWorkflowsMutation, WorkflowsMutation
from astrolift_workflows.schema.queries import TemporalWorkflowsQuery, WorkflowsQuery
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info
from workflows.models import (
    Workflow,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowStage,
    WorkflowStageExecution,
)
from workflows.schema.mutations import Mutation as LegacyMutation
from workflows.schema.queries import Query as LegacyQuery
from workflows.scopes import definition_scope_by_slug, visible_runs, workflow_run_scope

pytestmark = pytest.mark.django_db

User = get_user_model()
RESYNC = importlib.import_module("astrolift_identity.migrations.0034_resync_system_roles_stock_catalogue")

MEDOPS_RUNS = ["wf-billing", "wf-deploy-medops", "wf-intake"]
ORG_RUNS = sorted([*MEDOPS_RUNS, "wf-core", "wf-deploy-platform", "wf-org"])


def _definition(org, slug, project):
    definition = WorkflowDefinition.objects.create(
        organization=org, project=project, name=slug, slug=slug, model_label="", is_enabled=True
    )
    WorkflowStage.objects.create(
        slug=f"{slug}-{definition.pk}-gate",
        definition=definition,
        order=0,
        kind=WorkflowStage.StageKind.HUMAN_GATE,
    )
    return definition


def _definition_run(org, definition, wid):
    run = WorkflowRun.objects.create(
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id=wid,
        run_id=f"{wid}-r1",
        status=WorkflowRun.Status.RUNNING,
        organization=org,
        workflow_definition=definition,
    )
    instance = WorkflowInstance.objects.create(
        organization=org,
        workflow=definition,
        temporal_workflow_id=wid,
        temporal_run_id=run.run_id,
        current_state="running",
        object_id=4242,
    )
    execution = WorkflowStageExecution.objects.create(
        slug=f"{wid}-exec",
        workflow_run=run,
        stage=definition.stages.get(),
        status=WorkflowStageExecution.Status.RUNNING,
    )
    return SimpleNamespace(run=run, instance=instance, execution=execution)


def _app_run(org, app, wid):
    return WorkflowRun.objects.create(
        workflow_kind="DeployAppWorkflow",
        workflow_id=wid,
        run_id=f"{wid}-r1",
        status=WorkflowRun.Status.RUNNING,
        organization=org,
        registered_app=app,
    )


@pytest.fixture
def world():
    w = ScopeWorld("wf1965")
    w.billing_project = Project.objects.create(
        organization=w.org, team=w.medops, name="Billing", slug="billing-wf1965"
    )
    w.beta = Organization.objects.create(name="Beta", slug="beta-wf1965")
    w.template = _definition(None, "template-flow", None)
    w.intake_def = _definition(w.org, "intake-flow", w.medops_project)
    w.billing_def = _definition(w.org, "billing-flow", w.billing_project)
    w.core_def = _definition(w.org, "core-flow", w.platform_project)
    w.org_def = _definition(w.org, "org-flow", None)
    w.beta_def = _definition(w.beta, "beta-flow", None)
    w.intake = _definition_run(w.org, w.intake_def, "wf-intake")
    w.billing = _definition_run(w.org, w.billing_def, "wf-billing")
    w.core = _definition_run(w.org, w.core_def, "wf-core")
    w.org_level = _definition_run(w.org, w.org_def, "wf-org")
    w.beta_run = _definition_run(w.beta, w.beta_def, "wf-beta")
    _app_run(w.org, w.medops_app, "wf-deploy-medops")
    _app_run(w.org, w.platform_app, "wf-deploy-platform")
    RESYNC.upsert_system_roles(django_apps, None)
    return w


def _holder(slug, kind, scope_id, name):
    user = User.objects.create(username=f"{name}-wf1965", email=f"{name}-wf1965@acme.test")
    role = Role.objects.get(slug=slug, is_system=True, organization=None)
    RoleBinding.objects.create(user=user, role=role, scope_kind=kind, scope_id=scope_id)
    return user


def _as(world, user, *, org=None, team=None, project=None):
    return tenant_context(
        TenantContext(
            organization_id=(org or world.org).pk,
            team_id=team.pk if team else None,
            project_id=project.pk if project else None,
            actor_user_id=user.pk,
        )
    )


@pytest.fixture
def delivered(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(temporal_mutations, "cancel_workflow", lambda wid: sent.append(wid) or True)
    monkeypatch.setattr(
        temporal_mutations, "terminate_workflow", lambda wid, reason: sent.append(wid) or True
    )
    monkeypatch.setattr(
        temporal_mutations, "signal_workflow", lambda wid, name, *args: sent.append(wid) or True
    )
    return sent


@pytest.fixture
def temporal_reads(monkeypatch):
    row = {
        "workflow_type": "DeployAppWorkflow",
        "status": "RUNNING",
        "started_at": "2026-09-24T00:00:00+00:00",
        "closed_at": "",
        "run_id": "r1",
        "duration_seconds": None,
        "task_queue": "astrolift-main",
    }
    monkeypatch.setattr(
        "astrolift_workflows.schema.queries.describe_workflow_instance",
        lambda wid: {**row, "workflow_id": wid},
    )
    monkeypatch.setattr("astrolift_workflows.schema.queries.workflow_history", lambda wid: [])
    monkeypatch.setattr(
        "astrolift_workflows.schema.queries.list_workflow_instances",
        lambda **kw: ([{**row, "workflow_id": wid} for wid in [*ORG_RUNS, "wf-beta"]], None),
    )
    monkeypatch.setattr(
        "astrolift_workflows.execution_controls.describe_workflow_instance", lambda *a, **k: None
    )


OPS = {
    "cancel": lambda info, wid: TemporalWorkflowsMutation().cancel_workflow_instance(info, wid),
    "terminate": lambda info, wid: TemporalWorkflowsMutation().terminate_workflow_instance(
        info, wid, "wedged"
    ),
    "signal": lambda info, wid: TemporalWorkflowsMutation().signal_workflow_instance(info, wid, "abort"),
}

SUB_ORG_HOLDERS = [
    # (stock role, binding kind, world attribute, selected context, runs it covers)
    ("team_developer", "TEAM", "medops", "team", MEDOPS_RUNS),
    ("team_operator", "TEAM", "medops", "team", MEDOPS_RUNS),
    ("project_developer", "PROJECT", "medops_project", "project", ["wf-deploy-medops", "wf-intake"]),
    ("project_operator", "PROJECT", "medops_project", "project", ["wf-deploy-medops", "wf-intake"]),
]


# ---------------------------------------------------------------------------
# cancel / terminate / signal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("selected", [False, True], ids=["org-context", "selected-context"])
@pytest.mark.parametrize("op", sorted(OPS))
@pytest.mark.parametrize(("slug", "kind", "scope", "pick", "own"), SUB_ORG_HOLDERS)
def test_a_sub_org_holder_acts_on_its_own_runs_and_no_sibling(
    world, delivered, selected, op, slug, kind, scope, pick, own
):
    holder = _holder(slug, kind, getattr(world, scope).pk, slug)
    info = make_info(holder)
    context = {pick: getattr(world, scope)} if selected else {}

    with _as(world, holder, **context):
        for wid in own:
            assert OPS[op](info, wid).ok, wid
        for wid in [*sorted(set(ORG_RUNS) - set(own)), "wf-beta", "wf-missing"]:
            with pytest.raises(PermissionDenied):
                OPS[op](info, wid)

    assert delivered == own


@pytest.mark.parametrize("op", sorted(OPS))
def test_an_org_grant_acts_on_every_run_in_its_org_and_no_other(world, delivered, op):
    admin = _holder("org_admin", "ORG", world.org.pk, "admin")
    info = make_info(admin)

    with _as(world, admin):
        for wid in ORG_RUNS:
            assert OPS[op](info, wid).ok, wid
        foreign = OPS[op](info, "wf-beta")

    assert foreign.ok is False
    assert "not found" in foreign.errors[0].messages[0]
    assert delivered == ORG_RUNS


def test_another_orgs_admin_reaches_none_of_these_runs(world, delivered):
    beta_admin = _holder("org_admin", "ORG", world.beta.pk, "beta-admin")

    with _as(world, beta_admin, org=world.beta):
        result = TemporalWorkflowsMutation().cancel_workflow_instance(make_info(beta_admin), "wf-intake")

    assert result.ok is False
    assert delivered == []


def test_the_platform_operator_still_acts_fleet_wide(world, delivered):
    operator = User.objects.create(username="operator-wf1965", is_superuser=True, is_active=True)

    with _as(world, operator):
        assert TemporalWorkflowsMutation().cancel_workflow_instance(make_info(operator), "wf-beta").ok

    assert delivered == ["wf-beta"]


def test_a_gate_decision_names_an_execution_of_the_signalled_run(world, delivered):
    developer = _holder("team_developer", "TEAM", world.medops.pk, "gate-dev")
    info = make_info(developer)

    with _as(world, developer):
        foreign = TemporalWorkflowsMutation().signal_workflow_instance(
            info,
            "wf-intake",
            "human_gate_decision",
            {"execution_id": str(world.core.execution.guid), "decision": "approved"},
        )
        own = TemporalWorkflowsMutation().signal_workflow_instance(
            info,
            "wf-intake",
            "human_gate_decision",
            {"execution_id": str(world.intake.execution.guid), "decision": "approved"},
        )

    assert foreign.ok is False
    assert foreign.errors[0].field == "payload"
    assert own.ok is True
    assert delivered == ["wf-intake"]


# ---------------------------------------------------------------------------
# exact execution reads and controls
# ---------------------------------------------------------------------------


def test_execution_routes_check_the_run_scope_not_the_selected_team(world, temporal_reads):
    developer = _holder("team_developer", "TEAM", world.medops.pk, "exec-dev")
    info = make_info(developer)
    queries, mutations = WorkflowsQuery(), WorkflowsMutation()

    def control(run):
        return mutations.control_workflow_execution(
            info, str(run.guid), run.workflow_id, run.run_id, "cancel"
        )

    with _as(world, developer, team=world.medops):
        assert queries.workflow_execution(info, str(world.intake.run.guid)) is not None
        assert queries.workflow_execution_stages(info, str(world.intake.run.guid)) is not None
        assert "unavailable" in control(world.intake.run).errors[0].messages[0]
        for run in (world.org_level.run, world.core.run):
            with pytest.raises(PermissionDenied):
                queries.workflow_execution(info, str(run.guid))
            with pytest.raises(PermissionDenied):
                queries.workflow_execution_stages(info, str(run.guid))
            with pytest.raises(PermissionDenied):
                control(run)


def test_execution_routes_keep_org_grants(world, temporal_reads):
    admin = _holder("org_admin", "ORG", world.org.pk, "exec-admin")

    with _as(world, admin):
        assert (
            WorkflowsQuery().workflow_execution(make_info(admin), str(world.org_level.run.guid)) is not None
        )
        assert WorkflowsQuery().workflow_execution(make_info(admin), str(world.beta_run.run.guid)) is None


# ---------------------------------------------------------------------------
# the Temporal viewer and the operations run list
# ---------------------------------------------------------------------------


def test_viewer_reads_follow_the_run_scope(world, temporal_reads):
    auditor = User.objects.create(username="team-auditor-wf1965")
    bind_role(
        auditor,
        permissions=[Permission.AUDIT_LOG_READ],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="auditor-wf1965",
    )
    info = make_info(auditor)
    viewer = TemporalWorkflowsQuery()

    for context in ({}, {"team": world.medops}):
        with _as(world, auditor, **context):
            assert viewer.astrolift_workflow_instance_detail(info, "wf-intake") is not None
            assert viewer.astrolift_workflow_instance(info, "wf-deploy-medops") is not None
            for wid in ("wf-core", "wf-org", "wf-deploy-platform", "wf-beta"):
                with pytest.raises(PermissionDenied):
                    viewer.astrolift_workflow_instance_detail(info, wid)
                with pytest.raises(PermissionDenied):
                    viewer.astrolift_workflow_instance(info, wid)
            listed = sorted(item.workflow_id for item in viewer.astrolift_workflow_instances(info).items)
            runs = sorted(row.workflow_id for row in OperationsQuery().astrolift_workflow_runs(info))
        assert listed == MEDOPS_RUNS
        assert runs == MEDOPS_RUNS


def test_viewer_org_grants_read_their_own_org_only(world, temporal_reads):
    admin = _holder("org_admin", "ORG", world.org.pk, "viewer-admin")
    info = make_info(admin)
    viewer = TemporalWorkflowsQuery()

    with _as(world, admin):
        assert viewer.astrolift_workflow_instance_detail(info, "wf-core") is not None
        assert viewer.astrolift_workflow_instance_detail(info, "wf-beta") is None
        assert viewer.astrolift_workflow_instance(info, "wf-beta") is None
        listed = sorted(item.workflow_id for item in viewer.astrolift_workflow_instances(info).items)
        runs = sorted(row.workflow_id for row in OperationsQuery().astrolift_workflow_runs(info))

    assert listed == ORG_RUNS
    assert runs == ORG_RUNS


# ---------------------------------------------------------------------------
# configured workflows, definitions and their collections
# ---------------------------------------------------------------------------


def test_collections_list_only_what_a_sub_org_grant_covers(world):
    for slug, definition in (
        ("intake-wf", world.intake_def),
        ("core-wf", world.core_def),
        ("org-wf", world.org_def),
    ):
        Workflow.objects.create(organization=world.org, definition=definition, name=slug, slug=slug)
    developer = _holder("team_developer", "TEAM", world.medops.pk, "list-dev")
    info = make_info(developer)
    queries = WorkflowsQuery()

    for context in ({}, {"team": world.medops}):
        with _as(world, developer, **context):
            workflows = sorted(w.slug for w in queries.workflows(info))
            workflows_page = sorted(w.slug for w in queries.workflows_page(info).items)
            definitions = sorted(d.slug for d in queries.workflow_definitions(info))
            definitions_page = sorted(d.slug for d in queries.workflow_definitions_page(info).items)
            runs = sorted(r.temporal_workflow_id for r in queries.workflow_definition_runs(info))
            core_runs = queries.workflow_definition_runs(info, project_id=str(world.platform_project.guid))
        assert workflows == workflows_page == ["intake-wf"]
        assert definitions == definitions_page == ["billing-flow", "intake-flow"]
        assert runs == ["wf-billing", "wf-intake"]
        assert core_runs == []


def test_collections_keep_the_whole_org_for_an_org_grant(world):
    for slug, definition in (
        ("intake-wf", world.intake_def),
        ("core-wf", world.core_def),
        ("org-wf", world.org_def),
    ):
        Workflow.objects.create(organization=world.org, definition=definition, name=slug, slug=slug)
    admin = _holder("org_admin", "ORG", world.org.pk, "list-admin")
    info = make_info(admin)
    queries = WorkflowsQuery()

    with _as(world, admin):
        workflows = sorted(w.slug for w in queries.workflows(info))
        definitions = sorted(d.slug for d in queries.workflow_definitions(info))
        runs = sorted(r.temporal_workflow_id for r in queries.workflow_definition_runs(info))

    assert workflows == ["core-wf", "intake-wf", "org-wf"]
    # The seeded platform catalogue adds more templates; every one is org-level.
    assert {"billing-flow", "core-flow", "intake-flow", "org-flow", "template-flow"} <= set(definitions)
    assert "beta-flow" not in definitions
    assert runs == ["wf-billing", "wf-core", "wf-intake", "wf-org"]


def test_org_level_workflow_objects_need_an_org_grant(world):
    Workflow.objects.create(
        organization=world.org, definition=world.intake_def, name="Intake", slug="intake-wf"
    )
    org_workflow = Workflow.objects.create(
        organization=world.org, definition=world.org_def, name="Org", slug="org-wf"
    )
    team_admin = _holder("team_admin", "TEAM", world.medops.pk, "team-admin")
    info = make_info(team_admin)

    with _as(world, team_admin, team=world.medops):
        assert WorkflowsQuery().workflow(info, "intake-wf") is not None
        assert LegacyMutation().update_workflow_definition(info, "intake-flow", description="ours").ok
        with pytest.raises(PermissionDenied):
            WorkflowsQuery().workflow(info, "org-wf")
        with pytest.raises(PermissionDenied):
            WorkflowsMutation().run_workflow(info, workflow_id=str(org_workflow.guid))
        with pytest.raises(PermissionDenied):
            LegacyMutation().update_workflow_definition(info, "org-flow", description="theirs")
        with pytest.raises(PermissionDenied):
            LegacyMutation().update_workflow_definition(info, "core-flow", description="theirs")

    world.org_def.refresh_from_db()
    assert world.org_def.description != "theirs"


def test_create_workflow_checks_the_definition_it_binds(world):
    developer = _holder("team_developer", "TEAM", world.medops.pk, "create-dev")
    info = make_info(developer)

    with _as(world, developer, team=world.medops):
        assert WorkflowsMutation().create_workflow(info, name="Intake", definition_slug="intake-flow").ok
        with pytest.raises(PermissionDenied):
            WorkflowsMutation().create_workflow(info, name="Core", definition_slug="core-flow")
        with pytest.raises(PermissionDenied):
            WorkflowsMutation().create_workflow(info, name="Org", definition_slug="org-flow")

    assert list(Workflow.objects.values_list("definition__slug", flat=True)) == ["intake-flow"]


def test_create_workflow_binds_the_orgs_own_definition_over_a_template_of_the_same_slug(world):
    """The gate checks the org's own definition, so that is the row to bind.

    The template is older (lower pk), which is the row a bare ``.first()``
    over the visible set picked."""
    _definition(None, "shared-flow", None)
    own = _definition(world.org, "shared-flow", world.medops_project)
    developer = _holder("team_developer", "TEAM", world.medops.pk, "shared-dev")

    with _as(world, developer, team=world.medops):
        result = WorkflowsMutation().create_workflow(
            make_info(developer), name="Shared", definition_slug="shared-flow"
        )

    assert result.ok, result.errors
    assert Workflow.objects.get(slug="shared").definition == own


def test_a_deleted_definition_does_not_lend_its_project_to_a_live_one(world):
    moved = _definition(world.org, "moved-flow", world.medops_project)
    moved.deleted_at = timezone.now()
    moved.save(update_fields=["deleted_at"])
    live = _definition(world.org, "moved-flow", world.platform_project)

    with tenant_context(TenantContext(organization_id=world.org.pk)):
        scope = definition_scope_by_slug("slug")({"slug": "moved-flow"})

    assert scope == PermissionScope(kind=ScopeKind.PROJECT, id=live.project_id)


# ---------------------------------------------------------------------------
# legacy workflows.schema readers and triggers
# ---------------------------------------------------------------------------


def test_legacy_readers_check_the_object_scope(world):
    developer = _holder("team_developer", "TEAM", world.medops.pk, "legacy-dev")
    info = make_info(developer)
    legacy = LegacyQuery()

    with _as(world, developer):
        assert list(legacy.workflow_stage_executions(info, "wf-intake", "wf-intake-r1")) == [
            world.intake.execution
        ]
        assert [s.definition_id for s in legacy.workflow_stages(info, "intake-flow")] == [world.intake_def.pk]
        assert legacy.workflow_instance(info, str(world.intake.instance.pk)) == world.intake.instance
        assert sorted(i.pk for i in legacy.workflow_instances(info, 4242)) == sorted(
            [world.intake.instance.pk, world.billing.instance.pk]
        )
        with pytest.raises(PermissionDenied):
            legacy.workflow_stage_executions(info, "wf-core", "wf-core-r1")
        with pytest.raises(PermissionDenied):
            legacy.workflow_stages(info, "core-flow")
        with pytest.raises(PermissionDenied):
            legacy.workflow_instance(info, str(world.core.instance.pk))


def test_the_legacy_stage_reader_leaves_org_less_runs_out(world):
    orphan = WorkflowRun.objects.create(
        workflow_kind="WorkflowDefinitionRunWorkflow", workflow_id="wf-orphan", run_id="r1", organization=None
    )
    WorkflowStageExecution.objects.create(
        slug="wf-orphan-exec", workflow_run=orphan, stage=world.template.stages.get(), status="running"
    )
    admin = _holder("org_admin", "ORG", world.org.pk, "orphan-admin")

    with _as(world, admin):
        assert list(LegacyQuery().workflow_stage_executions(make_info(admin), "wf-orphan", "r1")) == []


def test_legacy_triggers_check_the_definition_they_run(world, monkeypatch):
    started: list[str] = []

    def _start(definition, **_kwargs):
        started.append(definition.slug)
        return SimpleNamespace(pk=1), f"WorkflowDefinitionRunWorkflow-{definition.slug}"

    monkeypatch.setattr("workflows.run_service.start_workflow_definition_run", _start)
    developer = _holder("team_developer", "TEAM", world.medops.pk, "trigger-dev")
    info = make_info(developer)
    legacy = LegacyMutation()

    with _as(world, developer, team=world.medops):
        assert legacy.run_workflow_definition(info, "intake-flow").ok
        for slug in ("core-flow", "org-flow", "template-flow"):
            with pytest.raises(PermissionDenied):
                legacy.run_workflow_definition(info, slug)
        with pytest.raises(PermissionDenied):
            legacy.start_workflow(
                info, "core-flow", "astrolift_registry.RegisteredApp", world.platform_app.pk
            )

    assert started == ["intake-flow"]


def _forms_definition(org, slug):
    return WorkflowDefinition.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        model_label="astrolift_registry.RegisteredApp",
        states=[{"name": "draft", "is_initial": True}, {"name": "done", "is_final": True}],
        transitions=[{"from_state": "draft", "to_state": "done", "label": "Finish"}],
        is_enabled=True,
    )


def test_legacy_instances_are_read_and_moved_by_their_own_org_only(world):
    """A legacy instance with no org (the 0004 backfill, and every one the
    legacy start path created) belonged to every org's readers and
    transitioners. It now belongs to none, and the start path records the
    caller's org so a tenant still reaches the instances it starts."""
    forms = _forms_definition(world.org, "intake-form")
    orphan = WorkflowInstance.objects.create(
        organization=None, workflow=forms, current_state="draft", object_id=world.medops_app.pk
    )
    admin = _holder("org_admin", "ORG", world.org.pk, "forms-admin")
    beta_admin = _holder("org_admin", "ORG", world.beta.pk, "forms-beta")
    info, beta_info = make_info(admin), make_info(beta_admin)

    with _as(world, admin):
        started = LegacyMutation().start_workflow(
            info, "intake-form", "astrolift_registry.RegisteredApp", world.medops_app.pk
        )
    own = WorkflowInstance.objects.get(pk=int(started.instance_id))
    assert own.organization_id == world.org.pk

    with _as(world, beta_admin, org=world.beta):
        assert LegacyQuery().workflow_instance(beta_info, str(own.pk)) is None
        with pytest.raises(GraphQLError, match="not found"):
            LegacyMutation().transition_workflow(beta_info, str(own.pk), "done")

    with _as(world, admin):
        assert LegacyQuery().workflow_instance(info, str(own.pk)) == own
        assert LegacyQuery().workflow_instance(info, str(orphan.pk)) is None
        assert [i.pk for i in LegacyQuery().workflow_instances(info, world.medops_app.pk)] == [own.pk]
        with pytest.raises(GraphQLError, match="not found"):
            LegacyMutation().transition_workflow(info, str(orphan.pk), "done")
        assert LegacyMutation().transition_workflow(info, str(own.pk), "done").ok

    orphan.refresh_from_db()
    assert orphan.current_state == "draft"


def test_lists_and_the_run_gate_agree_on_deleted_owners(world):
    """``visible_runs`` and the single-run gate resolve a soft-deleted
    project, app or team the same way: it covers nothing. A project binding
    still covers a live project whose team was deleted, on both sides."""
    now = timezone.now()
    gone_project = Project.objects.create(
        organization=world.org, team=world.medops, name="Gone", slug="gone-wf1965"
    )
    _definition_run(world.org, _definition(world.org, "gone-flow", gone_project), "wf-gone-project")
    gone_app = RegisteredApp.objects.create(
        organization=world.org,
        team=world.medops,
        project=world.medops_project,
        name="Gone app",
        slug="gone-app-wf1965",
        k8s_namespace="acme-gone-app-wf1965",
        provisioning_status="ready",
    )
    _app_run(world.org, gone_app, "wf-gone-app")
    gone_team = Team.objects.create(organization=world.org, name="Gone team", slug="gone-team-wf1965")
    stranded = Project.objects.create(
        organization=world.org, team=gone_team, name="Stranded", slug="stranded-wf1965"
    )
    _definition_run(world.org, _definition(world.org, "stranded-flow", stranded), "wf-stranded")
    Project.all_objects.filter(pk=gone_project.pk).update(deleted_at=now)
    RegisteredApp.all_objects.filter(pk=gone_app.pk).update(deleted_at=now)
    Team.all_objects.filter(pk=gone_team.pk).update(deleted_at=now)

    holders = {
        "team": (_holder("team_developer", "TEAM", world.medops.pk, "alive-team"), MEDOPS_RUNS),
        "stranded-project": (
            _holder("project_developer", "PROJECT", stranded.pk, "stranded"),
            ["wf-stranded"],
        ),
        "deleted-team": (_holder("team_developer", "TEAM", gone_team.pk, "gone-team"), []),
    }
    wids = sorted(WorkflowRun.objects.filter(organization=world.org).values_list("workflow_id", flat=True))

    for name, (holder, expected) in holders.items():
        with _as(world, holder):
            listed = set(
                visible_runs(
                    WorkflowRun.objects.filter(organization=world.org), world.org.pk, Permission.WORKFLOW_READ
                ).values_list("workflow_id", flat=True)
            )
            gated = set()
            for wid in wids:
                try:
                    check_permission(Permission.WORKFLOW_READ, scope=workflow_run_scope(wid, world.org.pk))
                except PermissionDenied:
                    continue
                gated.add(wid)
        assert listed == gated == set(expected), name
