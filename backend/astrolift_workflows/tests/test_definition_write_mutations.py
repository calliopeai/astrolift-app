"""Write-surface coverage for org-scoped WorkflowDefinition mutations (#970/#972).

Complements ``workflows/tests/test_org_scoping.py`` (clone agent-clear/keep,
update global/cross-org denial) and ``test_manifest_schema.py`` (import):
stage-order fidelity on clone, clone editability, createWorkflowStage write
gates, and delete's PROTECT/soft-delete semantics.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from graphql import GraphQLError

from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx
from workflows.models import Workflow, WorkflowDefinition, WorkflowInstance, WorkflowStage
from workflows.schema.mutations import Mutation

User = get_user_model()
pytestmark = pytest.mark.django_db


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Write Org A", slug="write-org-a")


@pytest.fixture
def other_org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Write Org B", slug="write-org-b")


@pytest.fixture
def member():
    return User.objects.create_user(username="wf_writer", email="w@t.com", password="pw")


def _make_def(slug, *, organization=None, pattern="chained"):
    return WorkflowDefinition.objects.create(
        name=f"Def {slug}",
        slug=slug,
        organization=organization,
        model_label="",
        pattern_kind=pattern,
        states=[],
        transitions=[],
        is_enabled=True,
    )


def _add_stage(definition, order, kind, **kwargs):
    return WorkflowStage.objects.create(
        definition=definition,
        slug=f"{definition.slug}-stage-{order}",
        order=order,
        kind=kind,
        **kwargs,
    )


# --------------------------------------------------------------------------
# cloneWorkflowDefinition
# --------------------------------------------------------------------------


def test_clone_copies_stages_in_order(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    src = _make_def("multi-stage-tmpl", organization=None)
    _add_stage(src, 0, WorkflowStage.StageKind.AGENT_DISPATCH, role="implementer")
    _add_stage(
        src,
        1,
        WorkflowStage.StageKind.HUMAN_GATE,
        prompt="Approve?",
        approvers=["team-leads"],
        on_failure=WorkflowStage.OnFailure.ESCALATE,
        timeout_seconds=120,
    )
    _add_stage(src, 2, WorkflowStage.StageKind.AGGREGATION)

    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.clone_workflow_definition(_info(member), slug="multi-stage-tmpl")
    assert res.ok, res.errors

    clone = WorkflowDefinition.objects.get(slug=res.slug, organization=org)
    stages = list(clone.stages.order_by("order"))
    assert [s.order for s in stages] == [0, 1, 2]
    assert [s.kind for s in stages] == [
        WorkflowStage.StageKind.AGENT_DISPATCH.value,
        WorkflowStage.StageKind.HUMAN_GATE.value,
        WorkflowStage.StageKind.AGGREGATION.value,
    ]
    # Gate config survives the copy (spec 40 §5.4 prompt/approvers).
    assert stages[1].prompt == "Approve?"
    assert stages[1].approvers == ["team-leads"]
    assert stages[1].on_failure == WorkflowStage.OnFailure.ESCALATE.value
    assert stages[1].timeout_seconds == 120


def test_clone_defaults_to_caller_org_when_org_id_omitted(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    _make_def("default-org-tmpl", organization=None)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.clone_workflow_definition(_info(member), slug="default-org-tmpl")
    assert res.ok, res.errors
    clone = WorkflowDefinition.objects.get(slug=res.slug, organization_id=org.id)
    assert clone.organization_id == org.id


def test_cloned_definition_is_editable(member, org, permission_resolver):
    """The whole point of clone-to-edit: the org copy passes the write guard
    even when it shares its slug with the read-only global it came from."""
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    template = _make_def("editable-tmpl", organization=None)

    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.clone_workflow_definition(_info(member), slug="editable-tmpl")
        assert res.ok, res.errors
        upd = m.update_workflow_definition(_info(member), slug=res.slug, name="Renamed")

    assert upd.ok, upd.errors
    clone = WorkflowDefinition.objects.get(slug=res.slug, organization=org)
    assert clone.name == "Renamed"
    template.refresh_from_db()
    assert template.name == "Def editable-tmpl"


def test_clone_other_org_source_not_visible(member, org, other_org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    _make_def("theirs-only", organization=other_org)
    before = WorkflowDefinition.objects.count()
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.clone_workflow_definition(_info(member), slug="theirs-only")
    assert not res.ok
    assert any("not visible" in msg for e in res.errors for msg in e.messages)
    assert WorkflowDefinition.objects.count() == before


def test_clone_denied_without_create(member, org):
    _make_def("no-perm-tmpl", organization=None)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(PermissionDenied):
            m.clone_workflow_definition(_info(member), slug="no-perm-tmpl")


# --------------------------------------------------------------------------
# createWorkflowStage
# --------------------------------------------------------------------------


def test_create_stage_rejected_on_global_definition(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    d = _make_def("g-stage-target", organization=None)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow_stage(_info(member), workflow_slug="g-stage-target", kind="agent_dispatch")
    assert not res.ok
    assert any("platform template" in msg for e in res.errors for msg in e.messages)
    assert d.stages.count() == 0


def test_create_stage_invalid_kind_rejected(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    d = _make_def("o-stage-kind", organization=org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow_stage(_info(member), workflow_slug="o-stage-kind", kind="teleport")
    assert not res.ok
    assert res.errors[0].field == "kind"
    assert d.stages.count() == 0


def test_create_stage_cross_org_definition_denied(member, org, other_org, permission_resolver):
    """A foreign org's slug resolves to not-found, not "not your organization"
    — cross-tenant existence must not be observable."""
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    d = _make_def("x-stage-target", organization=other_org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow_stage(_info(member), workflow_slug="x-stage-target", kind="agent_dispatch")
    assert not res.ok
    assert any("not found" in msg for e in res.errors for msg in e.messages)
    assert d.stages.count() == 0


def test_create_stage_append_skips_soft_deleted_orders(member, org, permission_resolver):
    """Soft-deleted stages still occupy the (definition, order) slot — an
    omitted order must append past them, not reuse order 0 and collide."""
    from django.utils import timezone

    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    d = _make_def("o-stage-append", organization=org)
    dead = _add_stage(d, 0, WorkflowStage.StageKind.AGENT_DISPATCH)
    # Legacy BaseCoreModel carries no soft_delete() helper — set the marker.
    dead.deleted_at = timezone.now()
    dead.save(update_fields=["deleted_at"])

    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow_stage(_info(member), workflow_slug="o-stage-append", kind="agent_dispatch")
    assert res.ok, res.errors
    live = d.stages.filter(deleted_at__isnull=True).get()
    assert live.order == 1


def test_create_stage_denied_without_update(member, org):
    _make_def("o-stage-noperm", organization=org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(PermissionDenied):
            m.create_workflow_stage(_info(member), workflow_slug="o-stage-noperm", kind="agent_dispatch")


# --------------------------------------------------------------------------
# update / deleteWorkflowDefinition
# --------------------------------------------------------------------------


def test_update_denied_without_permission(member, org):
    _make_def("o-upd-noperm", organization=org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(PermissionDenied):
            m.update_workflow_definition(_info(member), slug="o-upd-noperm", name="x")


def test_source_managed_definition_rejects_ui_writes(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    definition = _make_def("repo-owned", organization=org)
    definition.source_repo = "steadymd/smd-agents"
    definition.source_path = "workflows/triage.toml"
    definition.source_ref = "abc123"
    definition.save(update_fields=["source_repo", "source_path", "source_ref", "updated_at", "version"])

    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        result = m.update_workflow_definition(_info(member), slug="repo-owned", name="Drift")

    assert result.ok is False
    assert result.errors[0].field == "source"
    assert "steadymd/smd-agents/workflows/triage.toml" in result.errors[0].messages[0]
    definition.refresh_from_db()
    assert definition.name == "Def repo-owned"


def test_delete_rejected_on_global(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_DELETE)
    d = _make_def("g-del-target", organization=None)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.delete_workflow_definition(_info(member), slug="g-del-target")
    assert not res.ok
    assert any("platform template" in msg for e in res.errors for msg in e.messages)
    d.refresh_from_db()
    assert d.deleted_at is None


def test_delete_refused_while_configured_workflow_references(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_DELETE)
    d = _make_def("o-del-live", organization=org)
    Workflow.objects.create(organization=org, definition=d, name="Live", slug="live-wf")
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.delete_workflow_definition(_info(member), slug="o-del-live")
    assert not res.ok
    assert any("still use it" in msg for e in res.errors for msg in e.messages)
    d.refresh_from_db()
    assert d.deleted_at is None


def test_delete_soft_deletes(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_DELETE)
    d = _make_def("o-del-free", organization=org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.delete_workflow_definition(_info(member), slug="o-del-free")
    assert res.ok, res.errors
    d.refresh_from_db()
    assert d.deleted_at is not None
    assert d.deleted_by_id == member.id


def test_delete_cross_org_denied(member, org, other_org, permission_resolver):
    """Foreign slug → not found (oracle closed), and nothing is deleted."""
    permission_resolver.grant(Permission.WORKFLOW_DELETE)
    d = _make_def("x-del-target", organization=other_org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(GraphQLError, match="not found"):
            m.delete_workflow_definition(_info(member), slug="x-del-target")
    d.refresh_from_db()
    assert d.deleted_at is None


def test_delete_denied_without_permission(member, org):
    _make_def("o-del-noperm", organization=org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(PermissionDenied):
            m.delete_workflow_definition(_info(member), slug="o-del-noperm")


# --------------------------------------------------------------------------
# Cross-tenant existence oracle (slug resolution scope)
# --------------------------------------------------------------------------


def test_update_foreign_slug_indistinguishable_from_nonexistent(member, org, other_org, permission_resolver):
    """Oracle closed: probing a foreign org's slug yields the exact same
    error a nonexistent slug does."""
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    _make_def("x-oracle", organization=other_org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(GraphQLError) as foreign:
            m.update_workflow_definition(_info(member), slug="x-oracle", name="probe")
        with pytest.raises(GraphQLError) as missing:
            m.update_workflow_definition(_info(member), slug="x-missing", name="probe")
    assert str(foreign.value).replace("x-oracle", "x-missing") == str(missing.value)


# --------------------------------------------------------------------------
# createWorkflowStage — agent workload binding scope
# --------------------------------------------------------------------------


def _make_workload(org, suffix):
    """A concrete agent Workload in ``org`` (org lives via registered_app)."""
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{suffix}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug=f"app-{suffix}",
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app,
        name="Coder",
        slug=f"coder-{suffix}",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
    )


def test_create_stage_rejects_foreign_org_workload(member, org, other_org, permission_resolver):
    """Org A must not bind org B's agent workload into its stage."""
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    d = _make_def("o-stage-xagent", organization=org)
    theirs = _make_workload(other_org, "wxb")
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow_stage(
            _info(member),
            workflow_slug="o-stage-xagent",
            kind="agent_dispatch",
            agent_definition_guid=str(theirs.guid),
        )
    assert not res.ok
    assert res.errors[0].field == "agent_definition_guid"
    assert d.stages.count() == 0


def test_create_stage_binds_caller_org_workload(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    _make_def("o-stage-ownagent", organization=org)
    mine = _make_workload(org, "wxa")
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow_stage(
            _info(member),
            workflow_slug="o-stage-ownagent",
            kind="agent_dispatch",
            agent_definition_guid=str(mine.guid),
        )
    assert res.ok, res.errors
    assert res.stage.agent_definition_id == mine.id


# --------------------------------------------------------------------------
# deleteWorkflowDefinition — webhook-trigger PROTECT guard
# --------------------------------------------------------------------------


def _make_webhook(definition, org, slug, *, enabled=True):
    from astrolift_agents.models import WorkflowWebhook

    return WorkflowWebhook.objects.create(
        workflow_definition=definition,
        organization=org,
        slug=slug,
        secret_hash="0" * 64,
        enabled=enabled,
    )


def test_delete_refused_while_enabled_webhook_trigger_references(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_DELETE)
    d = _make_def("o-del-hooked", organization=org)
    _make_webhook(d, org, "hooked-live")
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.delete_workflow_definition(_info(member), slug="o-del-hooked")
    assert not res.ok
    assert any("1 enabled webhook trigger" in msg for e in res.errors for msg in e.messages)
    d.refresh_from_db()
    assert d.deleted_at is None


def test_delete_allowed_once_webhook_triggers_disabled(member, org, permission_resolver):
    """WorkflowWebhook is not soft-deletable — ``enabled=False`` is how a
    trigger is retired, and a retired trigger must not block deletion."""
    permission_resolver.grant(Permission.WORKFLOW_DELETE)
    d = _make_def("o-del-unhooked", organization=org)
    _make_webhook(d, org, "hooked-dead", enabled=False)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.delete_workflow_definition(_info(member), slug="o-del-unhooked")
    assert res.ok, res.errors
    d.refresh_from_db()
    assert d.deleted_at is not None


# --------------------------------------------------------------------------
# Soft delete frees the (organization, slug) constraint
# --------------------------------------------------------------------------


def test_definition_slug_reusable_after_soft_delete(member, org, permission_resolver):
    """The org_slug constraint is conditioned on live rows — a deleted
    definition must not squat its slug forever."""
    permission_resolver.grant(Permission.WORKFLOW_DELETE)
    first = _make_def("o-reuse", organization=org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.delete_workflow_definition(_info(member), slug="o-reuse")
    assert res.ok, res.errors
    again = _make_def("o-reuse", organization=org)
    assert again.pk != first.pk
    assert again.deleted_at is None


def test_workflow_slug_reusable_after_soft_delete(org):
    from django.utils import timezone

    d = _make_def("wf-reuse-def", organization=org)
    wf = Workflow.objects.create(organization=org, definition=d, name="First", slug="wf-reuse")
    wf.deleted_at = timezone.now()
    wf.save(update_fields=["deleted_at", "deleted_by", "updated_at", "version"], skip_binding_validation=True)
    again = Workflow.objects.create(organization=org, definition=d, name="Second", slug="wf-reuse")
    assert again.pk != wf.pk


# --------------------------------------------------------------------------
# Legacy start/transition mutations — deny-by-default (spec 40 §8 dormant)
# --------------------------------------------------------------------------

_LEGACY_STATES = [
    {"name": "pending", "label": "Pending", "is_initial": True, "is_final": False},
    {"name": "done", "label": "Done", "is_initial": False, "is_final": True},
]
_LEGACY_TRANSITIONS = [{"from_state": "pending", "to_state": "done", "label": "Finish"}]


def _make_legacy_def(slug, *, organization):
    return WorkflowDefinition.objects.create(
        name=f"Legacy {slug}",
        slug=slug,
        organization=organization,
        model_label="workflows.workflowdefinition",
        states=_LEGACY_STATES,
        transitions=_LEGACY_TRANSITIONS,
        is_enabled=True,
    )


def test_start_workflow_denied_without_permission(member, org):
    _make_legacy_def("legacy-noperm", organization=org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(PermissionDenied):
            m.start_workflow(
                _info(member),
                workflow_slug="legacy-noperm",
                model_label="workflows.workflowdefinition",
                object_id=1,
            )


def test_start_workflow_foreign_definition_not_found(member, org, other_org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    d = _make_legacy_def("legacy-theirs", organization=other_org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(GraphQLError, match="not found or disabled"):
            m.start_workflow(
                _info(member),
                workflow_slug="legacy-theirs",
                model_label="workflows.workflowdefinition",
                object_id=d.pk,
            )


def test_start_workflow_runs_for_caller_org(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    d = _make_legacy_def("legacy-mine", organization=org)
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.start_workflow(
            _info(member),
            workflow_slug="legacy-mine",
            model_label="workflows.workflowdefinition",
            object_id=d.pk,
        )
    assert res.ok, res.errors
    assert WorkflowInstance.objects.filter(pk=res.instance_id, current_state="pending").exists()


def test_transition_workflow_denied_without_permission(member, org):
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(PermissionDenied):
            m.transition_workflow(_info(member), instance_id="1", to_state="done")


def test_transition_workflow_cross_org_instance_not_found(member, org, other_org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    d = _make_legacy_def("legacy-x-inst", organization=other_org)
    inst = WorkflowInstance.objects.create(workflow=d, organization=other_org, current_state="pending")
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        with pytest.raises(GraphQLError, match="not found"):
            m.transition_workflow(_info(member), instance_id=str(inst.pk), to_state="done")
    inst.refresh_from_db()
    assert inst.current_state == "pending"


def test_transition_workflow_caller_org_instance(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    d = _make_legacy_def("legacy-own-inst", organization=org)
    inst = WorkflowInstance.objects.create(workflow=d, organization=org, current_state="pending")
    m = Mutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.transition_workflow(_info(member), instance_id=str(inst.pk), to_state="done")
    assert res.ok, res.errors
    inst.refresh_from_db()
    assert inst.current_state == "done"
