"""Tier org-scoping for the Workflows domain (spec 40 §2, issue #966).

Covers the three-tier model evolution:
* WorkflowDefinition nullable org + read-only globals + (organization, slug)
* cloneWorkflowDefinition deep-copy / reparent / agent-clear
* read scope = org ∪ global; cross-org write denied
* tier-2 Workflow: required org + unbound agent_dispatch rejection
* the 0004 migration backfills pre-existing rows with organization=NULL
"""

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from graphql import GraphQLError

from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx
from workflows.models import (
    Workflow,
    WorkflowDefinition,
    WorkflowStage,
)
from workflows.schema.mutations import Mutation

User = get_user_model()
pytestmark = pytest.mark.django_db


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Org A", slug="org-a")


@pytest.fixture
def other_org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Org B", slug="org-b")


@pytest.fixture
def superuser():
    return User.objects.create_superuser(username="wf_super", email="s@t.com", password="pw")


@pytest.fixture
def member():
    return User.objects.create_user(username="wf_member", email="m@t.com", password="pw")


@pytest.fixture
def agent_workload(org):
    """A concrete agent Workload in ``org`` for stage agent_definition tests."""
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    team = Team.objects.create(organization=org, name="Eng", slug="eng-a")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-a")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="app-a",
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app,
        name="Coder",
        slug="coder-a",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
    )


def _make_def(slug, *, organization=None, pattern="chained", stage_agent=None, with_stage=True):
    d = WorkflowDefinition.objects.create(
        name=f"Def {slug}",
        slug=slug,
        organization=organization,
        model_label="",
        pattern_kind=pattern,
        states=[],
        transitions=[],
        is_enabled=True,
    )
    if with_stage:
        WorkflowStage.objects.create(
            definition=d,
            slug=f"{slug}-stage-0",
            order=0,
            kind=WorkflowStage.StageKind.AGENT_DISPATCH,
            role="implementer",
            agent_definition=stage_agent,
        )
    return d


# --------------------------------------------------------------------------
# Tier 1 — read-only globals + org scoping
# --------------------------------------------------------------------------


def test_global_definition_write_denied_to_member(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    _make_def("g-readonly", organization=None)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.update_workflow_definition(_info(member), slug="g-readonly", name="hacked")
    assert not res.ok
    assert any("platform template" in msg for e in res.errors for msg in e.messages)


def test_superuser_can_seed_global(superuser):
    m = Mutation()
    res = m.create_workflow_definition(
        _info(superuser),
        name="Seed",
        slug="seed-global",
        model_label="",
        states=[],
        transitions=[],
        pattern_kind="chained",
    )
    assert res.ok, res.errors
    assert WorkflowDefinition.objects.get(slug="seed-global").organization_id is None


def test_cross_org_definition_write_denied(member, org, other_org, permission_resolver):
    """A foreign org's slug resolves to not-found — no cross-tenant
    existence oracle, and no write."""
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    d = _make_def("x-other", organization=other_org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(GraphQLError, match="not found"):
            m.update_workflow_definition(_info(member), slug="x-other", name="hax")
    d.refresh_from_db()
    assert d.name == "Def x-other"


def test_read_scope_org_union_global(org, other_org):
    _make_def("rs-global", organization=None, with_stage=False)
    _make_def("rs-mine", organization=org, with_stage=False)
    _make_def("rs-theirs", organization=other_org, with_stage=False)
    visible = set(WorkflowDefinition.visible_to_org(org.id).values_list("slug", flat=True))
    assert "rs-global" in visible
    assert "rs-mine" in visible
    assert "rs-theirs" not in visible


# --------------------------------------------------------------------------
# Tier 1 — cloneWorkflowDefinition
# --------------------------------------------------------------------------


def test_clone_reparents_and_clears_global_agent(member, org, agent_workload, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    src = _make_def("g-clone", organization=None, stage_agent=agent_workload)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.clone_workflow_definition(_info(member), slug="g-clone", org_id=str(org.guid))
    assert res.ok, res.errors
    clone = WorkflowDefinition.objects.get(slug=res.slug, organization=org)
    assert clone.pk != src.pk
    assert clone.organization_id == org.id
    stages = list(clone.stages.order_by("order"))
    assert len(stages) == 1
    # Global source stage → agent cleared, role preserved (spec §2.1/§2.4).
    assert stages[0].agent_definition_id is None
    assert stages[0].role == "implementer"


def test_clone_same_org_keeps_agent(member, org, agent_workload, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    _make_def("o-clone", organization=org, stage_agent=agent_workload)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.clone_workflow_definition(_info(member), slug="o-clone", org_id=str(org.guid))
    assert res.ok, res.errors
    clone = WorkflowDefinition.objects.get(slug=res.slug, organization=org)
    assert clone.stages.first().agent_definition_id == agent_workload.id


def test_clone_slug_collision_appends_copy(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    _make_def("dup", organization=None, with_stage=False)
    _make_def("dup", organization=org, with_stage=False)  # collides on (org, slug)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.clone_workflow_definition(_info(member), slug="dup", org_id=str(org.guid))
    assert res.ok, res.errors
    assert res.slug == "dup-copy"


def test_clone_foreign_org_id_rejected(member, org, other_org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    _make_def("g-foreign", organization=None, with_stage=False)
    m = Mutation()
    # Caller's tenant is org, but they pass other_org's id.
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.clone_workflow_definition(_info(member), slug="g-foreign", org_id=str(other_org.guid))
    assert not res.ok
    assert any("mismatch" in msg for e in res.errors for msg in e.messages)


# --------------------------------------------------------------------------
# Tier 2 — Workflow
# --------------------------------------------------------------------------


def test_workflow_requires_org(org):
    d = _make_def("wf-noorg-def", organization=org, with_stage=False)
    wf = Workflow(organization=None, definition=d, name="NoOrg", slug="no-org")
    with pytest.raises(ValidationError):
        wf.save()


def test_workflow_rejects_unbound_agent_dispatch(org, agent_workload):
    d = _make_def("wf-unbound-def", organization=org, stage_agent=None)
    wf = Workflow(organization=org, definition=d, name="My WF", slug="my-wf")
    with pytest.raises(ValidationError) as exc:
        wf.save()
    assert "stage 0" in str(exc.value)

    # Bind the stage via stage_bindings to a real agent workload → now valid.
    wf.stage_bindings = {"0": {"agent_workload_id": str(agent_workload.guid)}}
    wf.save()
    assert wf.pk is not None


def test_workflow_rejects_garbage_binding(org):
    """#1094: an agent_workload_id that resolves to no live kind=agent
    Workload in the org — a random guid, or plain garbage — is rejected at
    save time, naming the stage order."""
    d = _make_def("wf-garbage-def", organization=org, stage_agent=None)
    wf = Workflow(
        organization=org,
        definition=d,
        name="Garbage WF",
        slug="garbage-wf",
        stage_bindings={"0": {"agent_workload_id": str(uuid.uuid4())}},
    )
    with pytest.raises(ValidationError) as exc:
        wf.save()
    assert "stage 0" in str(exc.value)

    # A non-guid string must not blow up the guid lookup — same rejection.
    wf.stage_bindings = {"0": {"agent_workload_id": "not-a-guid"}}
    with pytest.raises(ValidationError) as exc:
        wf.save()
    assert "stage 0" in str(exc.value)


def test_workflow_rejects_foreign_org_binding(org, other_org, agent_workload):
    """#1094: org B cannot bind org A's agent workload — the resolution is
    scoped via registered_app.organization, so a foreign workload is simply
    unresolved."""
    d = _make_def("wf-xorg-def", organization=other_org, stage_agent=None)
    wf = Workflow(
        organization=other_org,
        definition=d,
        name="Foreign bind",
        slug="foreign-bind",
        # agent_workload lives in ``org``, not ``other_org``.
        stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
    )
    with pytest.raises(ValidationError) as exc:
        wf.save()
    assert "stage 0" in str(exc.value)


def test_workflow_rejects_non_agent_workload_binding(org, agent_workload):
    """#1094: a live Workload of the wrong kind (deployment) does not satisfy
    an agent binding."""
    from astrolift_registry.models import Workload

    web = Workload.objects.create(
        registered_app=agent_workload.registered_app,
        name="Web",
        slug="web-a",
        kind=Workload.Kind.DEPLOYMENT.value,
    )
    d = _make_def("wf-nonagent-def", organization=org, stage_agent=None)
    wf = Workflow(
        organization=org,
        definition=d,
        name="Non-agent bind",
        slug="non-agent-bind",
        stage_bindings={"0": {"agent_workload_id": str(web.guid)}},
    )
    with pytest.raises(ValidationError) as exc:
        wf.save()
    assert "stage 0" in str(exc.value)


def test_workflow_bound_via_stage_agent_is_valid(org, agent_workload):
    d = _make_def("wf-bound-def", organization=org, stage_agent=agent_workload)
    wf = Workflow(organization=org, definition=d, name="Bound", slug="bound-wf")
    wf.save()
    assert wf.pk is not None


# --------------------------------------------------------------------------
# Tier 3 — run repoint
# --------------------------------------------------------------------------


def test_instance_start_configured_denormalizes_org(org, agent_workload):
    from workflows.models import WorkflowInstance

    d = _make_def("wf-run-def", organization=org, stage_agent=agent_workload)
    wf = Workflow.objects.create(organization=org, definition=d, name="Run WF", slug="run-wf")
    inst = WorkflowInstance.start(configured_workflow=wf, temporal_workflow_id="wf-xyz")
    assert inst.configured_workflow_id == wf.id
    assert inst.organization_id == org.id
    assert inst.workflow_id == d.id  # legacy mirror still points at the shape
    assert inst.object_id is None
    assert inst.temporal_workflow_id == "wf-xyz"


# --------------------------------------------------------------------------
# Migration — backfill org=NULL on pre-existing rows
# --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_migration_0004_backfills_existing_rows_org_null(request):
    from django.contrib.contenttypes.models import ContentType
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    app = "workflows"
    migrate_from = [(app, "0003_historicalworkflowdefinition_pattern_kind_and_more")]
    migrate_to = [(app, "0004_historicalworkflow_workflow_and_more")]

    # Rewind to the pre-tenancy state and seed legacy rows.
    executor = MigrationExecutor(connection)
    current_targets = executor.loader.graph.leaf_nodes()
    # Later transaction tests need the current schema, even if this test fails.
    request.addfinalizer(lambda: MigrationExecutor(connection).migrate(current_targets))
    executor.migrate(migrate_from)
    old_apps = executor.loader.project_state(migrate_from).apps
    OldDef = old_apps.get_model(app, "WorkflowDefinition")
    OldInst = old_apps.get_model(app, "WorkflowInstance")
    # The contenttypes table is shared; use the raw id so the FK accepts it
    # regardless of which apps-state ContentType class owns the relation.
    ct_pk = ContentType.objects.first().pk
    d = OldDef.objects.create(
        name="Legacy",
        slug="legacy-mig-row",
        model_label="x.y",
        states=[],
        transitions=[],
        is_enabled=True,
        version=1,
        guid=uuid.uuid4(),
    )
    inst = OldInst.objects.create(
        workflow=d,
        content_type_id=ct_pk,
        object_id=1,
        current_state="running",
        version=1,
    )

    # Roll forward — the field/constraint swap must apply on the existing rows.
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(migrate_to)
    new_apps = executor.loader.project_state(migrate_to).apps
    NewDef = new_apps.get_model(app, "WorkflowDefinition")
    NewInst = new_apps.get_model(app, "WorkflowInstance")

    assert NewDef.objects.get(slug="legacy-mig-row").organization_id is None
    assert NewInst.objects.get(pk=inst.pk).organization_id is None


# --------------------------------------------------------------------------
# workflows.schema.queries read scoping (#968 follow-up)
# --------------------------------------------------------------------------


def _query():
    from workflows.schema.queries import Query

    return Query()


def test_workflow_stages_cross_org_returns_empty(member, org, other_org):
    """A foreign org's definition slug must be byte-identical to a
    nonexistent one — stages carry prompt/approvers/role, so an unscoped
    read leaks gate config cross-tenant."""
    _make_def("foreign-stages", organization=other_org)
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        stages = _query().workflow_stages(_info(member), workflow_slug="foreign-stages")
    assert list(stages) == []


def test_workflow_stages_own_org_and_global_visible(member, org, other_org):
    own = _make_def("own-stages", organization=org)
    glob = _make_def("global-stages", organization=None)
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        own_stages = list(_query().workflow_stages(_info(member), workflow_slug="own-stages"))
        glob_stages = list(_query().workflow_stages(_info(member), workflow_slug="global-stages"))
    assert [s.definition_id for s in own_stages] == [own.id]
    assert [s.definition_id for s in glob_stages] == [glob.id]


def test_workflow_stages_org_row_preferred_on_slug_collision(member, org):
    """On an (org, global) slug collision the org-owned definition wins —
    same preference as _definition_for_write."""
    _make_def("collide-stages", organization=None)
    own = _make_def("collide-stages", organization=org)
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        stages = list(_query().workflow_stages(_info(member), workflow_slug="collide-stages"))
    assert {s.definition_id for s in stages} == {own.id}


def test_workflow_instance_cross_org_returns_none(member, org, other_org):
    from django.contrib.contenttypes.models import ContentType

    from workflows.models import WorkflowInstance

    d = _make_def("foreign-inst-def", organization=other_org, with_stage=False)
    inst = WorkflowInstance.objects.create(
        workflow=d,
        organization=other_org,
        current_state="running",
        content_type=ContentType.objects.first(),
        object_id=424242,
    )
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        assert _query().workflow_instance(_info(member), id=inst.pk) is None
        # Same closure on the list reader.
        listed = list(_query().workflow_instances(_info(member), object_id=424242))
    assert inst.pk not in {i.pk for i in listed}


def test_workflow_stage_executions_cross_org_returns_empty(member, org, other_org):
    from astrolift_operations.models import WorkflowRun

    WorkflowRun.objects.create(
        workflow_kind="workflow_definition_run",
        workflow_id="wfid-foreign",
        run_id="rid-foreign",
        organization=other_org,
    )
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        rows = _query().workflow_stage_executions(
            _info(member), workflow_id="wfid-foreign", run_id="rid-foreign"
        )
    assert list(rows) == []


def _seed_stage_execution(definition, *, workflow_id, run_id, organization):
    """A foreign/org-less run carrying one real stage execution — the row an
    unscoped reader would hand back."""
    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStageExecution

    run = WorkflowRun.objects.create(
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_definition=definition,
        workflow_id=workflow_id,
        run_id=run_id,
        organization=organization,
    )
    return WorkflowStageExecution.objects.create(
        workflow_run=run,
        stage=definition.stages.get(order=0),
        status=WorkflowStageExecution.Status.RUNNING,
    )


def test_workflow_stage_executions_cross_org_hides_a_seeded_row(member, org, other_org):
    """The empty-run assertion above passes with or without scoping. Seed the
    foreign run with a stage execution that an unscoped read WOULD return, and
    prove the owning org still sees it (astrolift-cli#69: the rows now carry
    stage role + declared approvers)."""
    definition = _make_def("foreign-exec-def", organization=other_org)
    execution = _seed_stage_execution(
        definition, workflow_id="wfid-foreign-exec", run_id="rid-foreign-exec", organization=other_org
    )

    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        outsider = list(
            _query().workflow_stage_executions(
                _info(member), workflow_id="wfid-foreign-exec", run_id="rid-foreign-exec"
            )
        )
    with _tenant_ctx(TenantContext(organization_id=other_org.id, actor_user_id=member.id)):
        owner = list(
            _query().workflow_stage_executions(
                _info(member), workflow_id="wfid-foreign-exec", run_id="rid-foreign-exec"
            )
        )

    assert outsider == []
    assert [e.pk for e in owner] == [execution.pk]


def test_workflow_stage_executions_without_tenant_returns_empty(member, org):
    """Fail closed with no tenant. The read scope is org ∪ platform-global, so
    a null org compiles to "IS NULL OR IS NULL" and would return every org-less
    run's stage detail in the install."""
    definition = _make_def("orgless-exec-def", organization=None)
    _seed_stage_execution(
        definition, workflow_id="wfid-orgless-exec", run_id="rid-orgless-exec", organization=None
    )

    with _tenant_ctx(None):
        rows = list(
            _query().workflow_stage_executions(
                _info(member), workflow_id="wfid-orgless-exec", run_id="rid-orgless-exec"
            )
        )
    assert rows == []

    # Same org-less run stays readable to a real tenant (the platform-global
    # carve-out this guard must not break).
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        scoped = list(
            _query().workflow_stage_executions(
                _info(member), workflow_id="wfid-orgless-exec", run_id="rid-orgless-exec"
            )
        )
    assert len(scoped) == 1
