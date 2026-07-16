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


def test_workflow_rejects_unbound_agent_dispatch(org):
    d = _make_def("wf-unbound-def", organization=org, stage_agent=None)
    wf = Workflow(organization=org, definition=d, name="My WF", slug="my-wf")
    with pytest.raises(ValidationError) as exc:
        wf.save()
    assert "stage 0" in str(exc.value)

    # Bind the stage via stage_bindings → now valid.
    wf.stage_bindings = {"0": {"agent_workload_id": str(uuid.uuid4())}}
    wf.save()
    assert wf.pk is not None


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
def test_migration_0004_backfills_existing_rows_org_null():
    from django.contrib.contenttypes.models import ContentType
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    app = "workflows"
    migrate_from = [(app, "0003_historicalworkflowdefinition_pattern_kind_and_more")]
    migrate_to = [(app, "0004_historicalworkflow_workflow_and_more")]

    # Rewind to the pre-tenancy state and seed legacy rows.
    executor = MigrationExecutor(connection)
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
