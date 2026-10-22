"""Committed PostgreSQL snapshots, real scopes and concurrent content edits."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import close_old_connections

from astrolift_identity.models import Member
from config.schema import schema
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user
from workflows import atomic_review
from workflows.models import Workflow, WorkflowDefinition, WorkflowStage
from workflows.reviewed_starts import definition_revision

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def no_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


@pytest.fixture
def world():
    w = ScopeWorld("review2459")
    user = make_user("review2459")
    Member.objects.create(user=user, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(
        user,
        permissions=[
            Permission.WORKFLOW_READ,
            Permission.WORKFLOW_UPDATE,
            Permission.WORKFLOW_TRIGGER,
            Permission.AGENT_READ,
            Permission.AGENT_DISPATCH,
        ],
        kind="PROJECT",
        scope_id=w.medops_project.pk,
        slug=f"review-{uuid4().hex}",
    )
    definition = WorkflowDefinition.objects.create(
        organization=w.org,
        project=w.medops_project,
        name="Exact reviewed definition",
        slug="exact-review",
        model_label="",
        is_enabled=True,
    )
    stage = WorkflowStage.objects.create(
        definition=definition,
        slug="review-gate",
        name="Review gate",
        order=0,
        kind="human_gate",
        role="Reviewer",
        prompt="Prompt A",
        approvers=["team:reviewers"],
    )
    workflow = Workflow.objects.create(
        organization=w.org,
        definition=definition,
        name="Reviewed schedule",
        slug="review-schedule",
        is_enabled=False,
        trigger_kind="schedule",
        schedule_cron="0 * * * *",
    )
    return SimpleNamespace(
        w=w,
        user=user,
        info=make_info(user),
        definition=definition,
        stage=stage,
        workflow=workflow,
        tenant=TenantContext(organization_id=w.org.pk, actor_user_id=user.pk),
    )


def test_public_full_review_binds_exact_bytes_and_revision(world):
    with tenant_context(world.tenant):
        result = schema.execute_sync(
            """query($id: GUID!) {
          workflowDefinitionFullReview(id: $id) {
            definitionId revision capturedAt consistency canonicalization reviewDigest
            canonicalReview rootManifest inputSchemaDigest
          }
        }""",
            variable_values={"id": str(world.definition.guid)},
            context_value=world.info.context,
        )
    assert result.errors is None, result.errors
    review = result.data["workflowDefinitionFullReview"]
    assert review["definitionId"] == str(world.definition.guid)
    assert review["reviewDigest"] == hashlib.sha256(review["canonicalReview"].encode()).hexdigest()
    assert review["consistency"] == "POSTGRES_REPEATABLE_READ"
    assert json.loads(review["canonicalReview"])["revision"] == review["revision"]
    assert "Prompt A" in review["rootManifest"]
    assert "team:reviewers" in review["canonicalReview"]


def test_schedule_review_compiles_captured_bindings_and_configuration(world):
    world.workflow.stage_bindings = {"0": {"params": {"prompt": "Bound gate prompt"}}}
    world.workflow.save()
    with tenant_context(world.tenant):
        review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
    assert review.configuration["stageBindings"] == world.workflow.stage_bindings
    assert review.plans[str(world.definition.pk)]["stages"][0]["prompt"] == "Bound gate prompt"
    assert review.plans[str(world.definition.pk)]["stages"][0]["reviewed_stage"]["approvers"] == [
        "team:reviewers"
    ]
    assert review.schedule_review_digest


def test_concurrent_prompt_edit_and_aba_cannot_pair_new_manifest_with_old_revision(world, monkeypatch):
    entered, changed = Event(), Event()
    original = atomic_review._definition_graph
    before = definition_revision(world.definition)

    def paused(definition, **kwargs):
        entered.set()
        assert changed.wait(15)
        return original(definition, **kwargs)

    monkeypatch.setattr(atomic_review, "_definition_graph", paused)

    def capture():
        close_old_connections()
        try:
            with tenant_context(world.tenant):
                return atomic_review.capture_review(definition_id=str(world.definition.guid))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(capture)
        assert entered.wait(15)
        WorkflowStage.objects.filter(pk=world.stage.pk).update(prompt="Prompt B", approvers=["team:other"])
        changed.set()
        review = future.result(timeout=15)
    assert review.revision == before
    assert "Prompt A" in review.root_manifest and "Prompt B" not in review.root_manifest
    assert "team:reviewers" in review.canonical_review and "team:other" not in review.canonical_review
    # An actual A -> B -> A without version-counter help defeats R/export/R,
    # but cannot change the bytes held by this single repeatable-read capture.
    WorkflowStage.objects.filter(pk=world.stage.pk).update(prompt="Prompt A", approvers=["team:reviewers"])
    assert definition_revision(world.definition) == before
    assert review.review_digest == hashlib.sha256(review.canonical_review.encode()).hexdigest()


def test_child_raw_content_requires_its_own_read_permission(world):
    child = WorkflowDefinition.objects.create(
        organization=None,
        project=None,
        name="Private child",
        slug="private-child",
        model_label="",
    )
    WorkflowStage.objects.create(
        definition=child, slug="private-gate", order=0, kind="human_gate", prompt="Private child prompt"
    )
    WorkflowStage.objects.create(
        definition=world.definition,
        slug="child-body",
        order=1,
        kind="workflow",
        workflow_ref=f"guid:{child.guid}",
    )
    with tenant_context(world.tenant), pytest.raises(PermissionDenied):
        atomic_review.capture_review(definition_id=str(world.definition.guid))
    bind_role(
        world.user,
        permissions=[Permission.WORKFLOW_READ],
        kind="ORG",
        scope_id=world.w.org.pk,
        slug="child-read",
    )
    with tenant_context(world.tenant):
        capture = atomic_review.capture_review(definition_id=str(world.definition.guid))
    assert "Private child prompt" in capture.canonical_review


def test_capture_connection_is_read_only_and_is_removed_after_review(world):
    from django.db import DatabaseError, connections

    with atomic_review.capture_connection() as (alias, _):
        with connections[alias].cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            assert cursor.fetchone()[0] == "repeatable read"
            with pytest.raises(DatabaseError, match="read-only"):
                cursor.execute("UPDATE workflows_workflowstage SET prompt = 'not allowed' WHERE false")
    with pytest.raises(AttributeError):
        getattr(connections._connections, alias)


def approve(world, request_id="approval-one"):
    from workflows.reviewed_schedules import activate_reviewed_schedule

    review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
    return activate_reviewed_schedule(
        workflow_id=str(world.workflow.guid),
        expected_digest=review.schedule_review_digest,
        request_id=request_id,
        confirmed=True,
    )


def fire(world):
    from astrolift_workflows.activities.configured_workflow_schedule_fire import _create_sync

    return _create_sync({"workflow_guid": str(world.workflow.guid), "organization_id": world.w.org.pk})


def test_explicit_approval_is_saved_not_engine_activation_and_idempotent(world):
    from workflows.reviewed_schedules import activate_reviewed_schedule, approved_payload

    with tenant_context(world.tenant):
        review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
        kwargs = {
            "workflow_id": str(world.workflow.guid),
            "expected_digest": review.schedule_review_digest,
            "request_id": "person-confirmation",
            "confirmed": True,
        }
        approval = activate_reviewed_schedule(**kwargs)
        again = activate_reviewed_schedule(**kwargs)
    world.workflow.refresh_from_db()
    assert approval.pk == again.pk == world.workflow.reviewed_schedule_approval_id
    assert world.workflow.is_enabled
    assert approval.approver_id == world.user.pk
    payload = approved_payload(approval)
    assert payload["configuration"]["isEnabled"] is True
    assert payload["plans"][str(world.definition.pk)]["stages"][0]["reviewed_stage"]["role"] == "Reviewer"
    assert b"Prompt A" not in bytes(approval.payload_ciphertext)


def test_fire_uses_system_actor_and_sealed_plan_after_reservation(world, monkeypatch):
    from astrolift_operations.models import WorkflowRun
    from astrolift_workflows.activities import workflow_stage_activities as activities
    from workflows.models import WorkflowStageExecution
    from workflows.reviewed_starts import frozen_plan

    with tenant_context(world.tenant):
        approval = approve(world)
    result = fire(world)
    assert not result.skipped
    assert result.run_input.actor.kind == "system"
    assert result.run_input.actor.user_id is None
    assert result.run_input.stage_bindings is None
    run = WorkflowRun.objects.get(pk=int(result.run_input.workflow_run_id))
    assert run.trigger_actor_user_id is None
    assert run.reviewed_schedule_execution.approval_id == approval.pk
    WorkflowStage.objects.filter(pk=world.stage.pk).update(kind="checkpoint", role="Changed", approvers=[])
    captured = []
    monkeypatch.setattr(activities, "_capture_gate_interaction", lambda *a, **k: None)
    monkeypatch.setattr(activities, "_notify_gate_reviewers", lambda run, stage: captured.append(stage))
    execution_id = activities._create_stage_execution_sync(str(run.pk), str(world.stage.pk), 1)
    execution = WorkflowStageExecution.objects.select_related("stage").get(pk=execution_id)
    assert execution.stage.kind == "checkpoint"
    assert execution.effective_stage.kind == "human_gate"
    assert execution.effective_stage.approvers == ["team:reviewers"]
    assert captured[0].role == "Reviewer"
    assert frozen_plan(run, str(world.definition.pk))["stages"][0]["kind"] == "human_gate"


@pytest.mark.parametrize("change", ["prompt", "approvers", "bindings", "inputs", "cron", "enabled"])
def test_fire_fails_closed_after_reviewed_content_change(world, change):
    from astrolift_operations.models import WorkflowRun

    with tenant_context(world.tenant):
        approve(world)
    if change in ("prompt", "approvers"):
        WorkflowStage.objects.filter(pk=world.stage.pk).update(
            **{change: "changed" if change == "prompt" else []}
        )
    else:
        fields = {
            "bindings": {"stage_bindings": {"0": {"params": {"prompt": "different"}}}},
            "inputs": {"inputs": {"changed": True}},
            "cron": {"schedule_cron": "1 * * * *"},
            "enabled": {"is_enabled": False},
        }[change]
        Workflow.objects.filter(pk=world.workflow.pk).update(**fields)
    before = WorkflowRun.objects.count()
    assert fire(world).skipped
    assert WorkflowRun.objects.count() == before


def test_each_definition_abac_check_with_broad_role(world):
    from astrolift_identity.models import Policy

    bind_role(
        world.user,
        permissions=[Permission.WORKFLOW_READ],
        kind="ORG",
        scope_id=world.w.org.pk,
        slug=f"broad-{uuid4().hex}",
    )
    child = WorkflowDefinition.objects.create(
        organization=world.w.org,
        project=world.w.platform_project,
        name="Nested",
        slug="nested-review",
        model_label="",
    )
    Policy.objects.create(
        organization=world.w.org,
        name="Deny child project",
        slug="deny-child",
        scope_level="PROJECT",
        scope_id=world.w.platform_project.pk,
        action_pattern="workflow.read",
    )
    # Composition independently refuses cross-project edges; isolate the actual
    # per-definition authority check with two real owned targets and broad RBAC.
    with tenant_context(world.tenant), atomic_review.current_review_authority():
        atomic_review.authorize_definitions(
            {"definitions": {world.definition.pk: world.definition}}, Permission.WORKFLOW_READ
        )
        with pytest.raises(PermissionDenied):
            atomic_review.authorize_definitions(
                {"definitions": {world.definition.pk: world.definition, child.pk: child}},
                Permission.WORKFLOW_READ,
            )


def test_fresh_grant_checks_ignore_outer_scope_memo(world):
    from astrolift_identity.models import RoleBinding
    from core.permissions import _scopes_memo, granted_scopes

    with tenant_context(world.tenant):
        marker = _scopes_memo.set({})
        try:
            assert granted_scopes(world.tenant, Permission.WORKFLOW_READ)
            RoleBinding.objects.filter(user=world.user).delete()
            with pytest.raises(PermissionDenied):
                atomic_review.capture_review(definition_id=str(world.definition.guid))
        finally:
            _scopes_memo.reset(marker)


@pytest.mark.parametrize("bearer", [False, True])
@pytest.mark.parametrize("withdrawal", ["membership", "grant", "policy", "idp_group"])
def test_timer_rechecks_current_recorded_approval_authority(world, bearer, withdrawal):
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken, Policy, RoleBinding

    token = ApiToken.objects.create(
        user=world.user,
        organization=world.w.org,
        team=world.w.medops,
        name="Approval credential",
        token_hash="approval-hash",
        scopes=["read:apps", "write:apps"],
    )
    bindings = RoleBinding.objects.filter(user=world.user)
    member = Member.objects.filter(user=world.user, scope_kind="ORG", scope_id=world.w.org.pk)
    if withdrawal == "idp_group":
        bindings.update(user=None, group_external_id="reviewers")
        member.update(idp_groups=["reviewers"])
    marker = set_current_api_token(token if bearer else None)
    try:
        with tenant_context(world.tenant):
            approve(world)
    finally:
        reset_current_api_token(marker)
    if withdrawal == "membership":
        member.delete()
    elif withdrawal == "grant":
        bindings.delete()
    elif withdrawal == "policy":
        Policy.objects.create(
            organization=world.w.org,
            name="Withdraw trigger",
            slug="withdraw-trigger",
            scope_level="ORG",
            action_pattern="workflow.trigger",
        )
    else:
        member.update(idp_groups=[])
    assert fire(world).skipped


@pytest.mark.parametrize("change", ["revoked", "expired", "scopes", "team", "hash", "deleted"])
def test_timer_cannot_outlive_or_expand_the_approving_bearer(world, change):
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    token = ApiToken.objects.create(
        user=world.user,
        organization=world.w.org,
        team=world.w.medops,
        name="Approval credential",
        token_hash="approval-hash",
        scopes=["read:apps", "write:apps"],
    )
    marker = set_current_api_token(token)
    try:
        with tenant_context(world.tenant):
            approve(world)
    finally:
        reset_current_api_token(marker)
    changes = {
        "revoked": {"is_revoked": True},
        "expired": {"expires_at": timezone.now() - timedelta(seconds=1)},
        "scopes": {"scopes": ["workflow.read"]},
        "team": {"team_id": world.w.platform.pk},
        "hash": {"token_hash": "rotated"},
        "deleted": {"deleted_at": timezone.now()},
    }
    ApiToken.all_objects.filter(pk=token.pk).update(**changes[change])
    assert fire(world).skipped


def test_runtime_policy_cannot_borrow_a_persons_ip_from_approval(world):
    from astrolift_identity import abac
    from astrolift_identity.models import Policy

    Policy.objects.create(
        organization=world.w.org,
        name="Person IP",
        slug="person-ip",
        scope_level="ORG",
        action_pattern="workflow.trigger",
        effect="ALLOW",
        conditions=[{"kind": "ip_allowlist", "cidrs": ["10.0.0.0/8"]}],
    )
    attrs = abac.RequestAttributes(actor_user_id=world.user.pk, client_ip="10.1.2.3")
    with tenant_context(world.tenant), abac.request_attributes(attrs):
        approve(world)
    assert fire(world).skipped


def test_native_receipt_and_stage_snapshots_are_immutable(world, monkeypatch):
    from django.db import DatabaseError, transaction

    from astrolift_operations.models import WorkflowRun
    from astrolift_workflows.activities import workflow_stage_activities as activities
    from workflows.models import (
        ReviewedWorkflowScheduleApproval,
        ReviewedWorkflowScheduleExecution,
        WorkflowStageExecution,
    )

    with tenant_context(world.tenant):
        approval = approve(world)
    result = fire(world)
    run = WorkflowRun.objects.get(pk=int(result.run_input.workflow_run_id))
    monkeypatch.setattr(activities, "_capture_gate_interaction", lambda *a, **k: None)
    monkeypatch.setattr(activities, "_notify_gate_reviewers", lambda *a, **k: None)
    execution_id = activities._create_stage_execution_sync(str(run.pk), str(world.stage.pk), 1)
    for rows, update in [
        (
            ReviewedWorkflowScheduleApproval.objects.filter(pk=approval.pk),
            {"compiled_plans_digest": "0" * 64},
        ),
        (
            ReviewedWorkflowScheduleExecution.objects.filter(workflow_run=run),
            {"organization_id": world.w.org.pk + 1},
        ),
        (WorkflowStageExecution.objects.filter(pk=execution_id), {"reviewed_stage_snapshot": None}),
    ]:
        with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
            rows.update(**update)


def test_activation_refreshes_grants_after_waiting_for_the_workflow_lock(world, monkeypatch):
    from django.db import transaction

    from astrolift_identity.models import RoleBinding
    from workflows import reviewed_schedules

    entered = Event()
    original = reviewed_schedules._locked_review
    with tenant_context(world.tenant):
        review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))

    def waiting(*args):
        entered.set()
        return original(*args)

    def activate():
        close_old_connections()
        try:
            with tenant_context(world.tenant):
                return reviewed_schedules.activate_reviewed_schedule(
                    workflow_id=str(world.workflow.guid),
                    expected_digest=review.schedule_review_digest,
                    request_id="waited-approval",
                    confirmed=True,
                )
        finally:
            close_old_connections()

    monkeypatch.setattr(reviewed_schedules, "_locked_review", waiting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Workflow.objects.select_for_update().get(pk=world.workflow.pk)
            pending = pool.submit(activate)
            assert entered.wait(15)
            RoleBinding.objects.filter(user=world.user).delete()
        with pytest.raises(PermissionDenied):
            pending.result(timeout=15)


def test_public_activation_reports_saved_receipt_and_unconfirmed_engine(world):
    with tenant_context(world.tenant):
        review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
        result = schema.execute_sync(
            """mutation($input: ActivateReviewedWorkflowScheduleInput!) {
              activateReviewedWorkflowSchedule(input: $input) {
                ok errors { code message } data {
                  id workflowId definitionId approvedByUserId scheduleReviewDigest
                  isCurrent databaseStatus engineState credentialKind credentialExpiresAt
                }
              }
            }""",
            variable_values={
                "input": {
                    "workflowId": str(world.workflow.guid),
                    "expectedScheduleReviewDigest": review.schedule_review_digest,
                    "requestId": "public-activation",
                    "confirmed": True,
                }
            },
            context_value=world.info.context,
        )
    assert result.errors is None, result.errors
    envelope = result.data["activateReviewedWorkflowSchedule"]
    assert envelope["ok"], envelope["errors"]
    assert envelope["data"]["scheduleReviewDigest"] == review.schedule_review_digest
    assert envelope["data"]["approvedByUserId"] == str(world.user.pk)
    assert envelope["data"]["databaseStatus"] == "SAVED"
    assert envelope["data"]["engineState"] == "UNCONFIRMED"


@pytest.mark.parametrize("permission", [Permission.AGENT_READ, Permission.AGENT_DISPATCH])
def test_agent_policy_uses_actual_current_target_and_dispatch_region(world, permission):
    from astrolift_identity.models import Policy
    from astrolift_registry.models import Workload
    from core.tests.utils.scope_world import make_cluster
    from workflows.reviewed_schedules import activate_reviewed_schedule

    bind_role(
        world.user,
        permissions=[Permission.AGENT_READ, Permission.AGENT_DISPATCH],
        kind="ORG",
        scope_id=world.w.org.pk,
        slug=f"broad-agent-{uuid4().hex}",
    )
    agent = Workload.objects.create(
        registered_app=world.w.medops_app, name="Reviewed agent", slug="review-agent", kind="agent"
    )
    WorkflowStage.objects.filter(pk=world.stage.pk).update(kind="agent_dispatch", agent_definition=agent)
    cluster = make_cluster(world.w, "review-agent")
    type(cluster).objects.filter(pk=cluster.pk).update(region="us-east-1", lifecycle="managed")
    Policy.objects.create(
        organization=world.w.org,
        name="Deny east agent",
        slug="deny-east-agent",
        scope_level="APP",
        scope_id=world.w.medops_app.pk,
        action_pattern=permission.value,
        resource_pattern={"region": ["us-east-1"]},
    )
    with tenant_context(world.tenant):
        if permission == Permission.AGENT_READ:
            with pytest.raises(PermissionDenied):
                atomic_review.capture_review(definition_id=str(world.definition.guid))
        else:
            review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
            with pytest.raises(PermissionDenied):
                activate_reviewed_schedule(
                    workflow_id=str(world.workflow.guid),
                    expected_digest=review.schedule_review_digest,
                    request_id="denied-agent",
                    confirmed=True,
                )
        type(cluster).objects.filter(pk=cluster.pk).update(region="us-west-2")
        review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
        approval = activate_reviewed_schedule(
            workflow_id=str(world.workflow.guid),
            expected_digest=review.schedule_review_digest,
            request_id="allowed-agent",
            confirmed=True,
        )
    assert approval.pk


def test_foreign_source_agent_fk_is_refused_before_manifest_projection(world):
    from astrolift_registry.models import Workload

    foreign = ScopeWorld("foreign-review-agent")
    agent = Workload.objects.create(
        registered_app=foreign.medops_app,
        name="Private foreign agent",
        slug="private-foreign-agent",
        kind="agent",
    )
    WorkflowStage.objects.filter(pk=world.stage.pk).update(agent_definition=agent)
    with tenant_context(world.tenant), pytest.raises(PermissionDenied):
        atomic_review.capture_review(definition_id=str(world.definition.guid))


def test_named_gate_authority_and_public_projection_cannot_follow_later_edits(world, monkeypatch):
    from astrolift_operations.models import WorkflowRun
    from astrolift_workflows.activities import workflow_stage_activities as activities
    from astrolift_workflows.human_gates import find_gate
    from workflows.models import WorkflowStageExecution

    WorkflowStage.objects.filter(pk=world.stage.pk).update(approvers=[world.user.email])
    with tenant_context(world.tenant):
        approve(world)
    result = fire(world)
    run = WorkflowRun.objects.get(pk=int(result.run_input.workflow_run_id))
    run.run_id = "snapshot-read"
    run.save(update_fields=["run_id", "updated_at", "version"])
    monkeypatch.setattr(activities, "_capture_gate_interaction", lambda *a, **k: None)
    notices = []
    monkeypatch.setattr(
        activities, "_notify_gate_reviewers", lambda run, stage: notices.append(stage.approvers)
    )
    execution_id = activities._create_stage_execution_sync(str(run.pk), str(world.stage.pk), 1)
    execution = WorkflowStageExecution.objects.get(pk=execution_id)
    other = make_user("replacement-reviewer")
    Member.objects.create(user=other, scope_kind="ORG", scope_id=world.w.org.pk)
    bind_role(
        other,
        permissions=[Permission.WORKFLOW_TRIGGER],
        kind="PROJECT",
        scope_id=world.w.medops_project.pk,
        slug=f"replacement-{uuid4().hex}",
    )
    WorkflowStage.objects.filter(pk=world.stage.pk).update(
        kind="checkpoint", role="Replacement", approvers=[other.email]
    )
    query = """query($workflow: String!, $run: String!) {
      workflowStageExecutions(workflowId: $workflow, runId: $run) {
        guid stageKind stageRole stageApprovers stageOrder humanGateState
      }
    }"""
    with tenant_context(world.tenant):
        assert find_gate(world.user, run.guid, execution.guid).pk == execution.pk
        result = schema.execute_sync(
            query,
            variable_values={"workflow": run.workflow_id, "run": run.run_id},
            context_value=world.info.context,
        )
    assert result.errors is None, result.errors
    assert result.data["workflowStageExecutions"] == [
        {
            "guid": str(execution.guid),
            "stageKind": "human_gate",
            "stageRole": "Reviewer",
            "stageApprovers": [world.user.email],
            "stageOrder": 0,
            "humanGateState": "pending",
        }
    ]
    assert notices == [[world.user.email]]
    with tenant_context(TenantContext(organization_id=world.w.org.pk, actor_user_id=other.pk)):
        assert find_gate(other, run.guid, execution.guid) is None


def test_full_review_source_and_authority_queries_are_bounded_for_many_agents(world, monkeypatch):
    from contextlib import contextmanager

    from django.db import connection, connections
    from django.test.utils import CaptureQueriesContext

    from astrolift_registry.models import RegisteredApp, Workload

    bind_role(
        world.user,
        permissions=[Permission.AGENT_READ],
        kind="ORG",
        scope_id=world.w.org.pk,
        slug=f"many-agent-{uuid4().hex}",
    )
    original = atomic_review.capture_connection
    source_counts = []

    @contextmanager
    def counted_capture():
        with original() as (alias, captured_at), CaptureQueriesContext(connections[alias]) as queries:
            yield alias, captured_at
        source_counts.append(len(queries))

    monkeypatch.setattr(atomic_review, "capture_connection", counted_capture)

    def add_agent(index):
        app = RegisteredApp.objects.create(
            organization=world.w.org,
            project=world.w.medops_project,
            team=world.w.medops,
            name=f"App {index}",
            slug=f"review-app-{index}",
            provisioning_status="ready",
        )
        agent = Workload.objects.create(
            registered_app=app, name=f"Agent {index}", slug=f"review-agent-{index}", kind="agent"
        )
        WorkflowStage.objects.create(
            definition=world.definition,
            name=f"Stage {index}",
            slug=f"review-stage-{index}",
            order=index,
            kind="agent_dispatch",
            agent_definition=agent,
        )

    def count():
        with tenant_context(world.tenant), CaptureQueriesContext(connection) as queries:
            atomic_review.capture_review(definition_id=str(world.definition.guid))
        return len(queries)

    add_agent(1)
    one = count()
    for index in range(2, 11):
        add_agent(index)
    many = count()
    assert many == one, (one, many)
    assert source_counts[0] == source_counts[1], source_counts


def test_stage_projection_has_no_plan_or_snapshot_n_plus_one(world):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStageExecution

    run = WorkflowRun.objects.create(
        organization=world.w.org,
        workflow_definition=world.definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="many-reviewed-stages",
        run_id="snapshot",
    )
    snapshot = atomic_review._stage_values(world.stage)
    query = """query { workflowStageExecutions(workflowId:"many-reviewed-stages", runId:"snapshot") {
      stageGuid stageKind stageRole stageOrder stageApprovers humanGateState
    }}"""

    def count():
        with tenant_context(world.tenant), CaptureQueriesContext(connection) as queries:
            result = schema.execute_sync(query, context_value=world.info.context)
        assert result.errors is None, result.errors
        return len(queries), [entry["sql"] for entry in queries.captured_queries]

    WorkflowStageExecution.objects.create(
        workflow_run=run, stage=world.stage, reviewed_stage_snapshot=snapshot, slug="snapshot-0"
    )
    one = count()
    for index in range(1, 10):
        WorkflowStageExecution.objects.create(
            workflow_run=run, stage=world.stage, reviewed_stage_snapshot=snapshot, slug=f"snapshot-{index}"
        )
    many = count()
    assert many[0] == one[0], (one[0], many[0], [q for q in many[1] if "LIMIT 21" in q])


@pytest.mark.parametrize("withdrawal", ["grant", "natural_expiry"])
def test_timer_refreshes_authority_after_waiting_for_content_locks(world, monkeypatch, withdrawal):
    from datetime import timedelta
    from time import sleep

    from django.db import transaction
    from django.utils import timezone

    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken, RoleBinding
    from astrolift_operations.models import WorkflowRun
    from workflows import reviewed_schedules

    token = ApiToken.objects.create(
        user=world.user,
        organization=world.w.org,
        team=world.w.medops,
        name="Expiring approval",
        token_hash="expiry-hash",
        scopes=["read:apps", "write:apps"],
        expires_at=timezone.now() + timedelta(seconds=3) if withdrawal == "natural_expiry" else None,
    )
    marker = set_current_api_token(token)
    try:
        with tenant_context(world.tenant):
            approve(world)
    finally:
        reset_current_api_token(marker)
    entered = Event()
    original = reviewed_schedules._locked_review

    def waiting(*args):
        entered.set()
        return original(*args)

    def timer():
        close_old_connections()
        try:
            return fire(world)
        finally:
            close_old_connections()

    monkeypatch.setattr(reviewed_schedules, "_locked_review", waiting)
    before = WorkflowRun.objects.count()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            WorkflowDefinition.objects.select_for_update().get(pk=world.definition.pk)
            pending = pool.submit(timer)
            assert entered.wait(15)
            if withdrawal == "grant":
                RoleBinding.objects.filter(user=world.user).delete()
            else:
                sleep(max(0, (token.expires_at - timezone.now()).total_seconds()) + 0.1)
        assert pending.result(timeout=15).skipped
    assert WorkflowRun.objects.count() == before


def test_linked_reviewed_run_refuses_a_missing_stage_descriptor_in_postgresql(world):
    from django.db import DatabaseError, transaction

    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStageExecution

    with tenant_context(world.tenant):
        approve(world)
    result = fire(world)
    run = WorkflowRun.objects.get(pk=int(result.run_input.workflow_run_id))
    with pytest.raises(DatabaseError, match="saved stage snapshot"), transaction.atomic():
        WorkflowStageExecution.objects.create(workflow_run=run, stage=world.stage, slug="old-stage-writer")


@pytest.mark.parametrize("change", ["prompt", "approvers", "binding", "inputs", "owner"])
def test_stale_schedule_consent_cannot_enable_or_create_a_receipt(world, change):
    from workflows.models import ReviewedWorkflowScheduleApproval
    from workflows.reviewed_schedules import activate_reviewed_schedule

    with tenant_context(world.tenant):
        review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
        if change in ("prompt", "approvers"):
            WorkflowStage.objects.filter(pk=world.stage.pk).update(
                **{change: "Changed" if change == "prompt" else []}
            )
        else:
            updates = {
                "binding": {"stage_bindings": {"0": {"params": {"prompt": "Changed"}}}},
                "inputs": {"inputs": {"changed": True}},
                "owner": {"created_by_id": world.user.pk},
            }[change]
            Workflow.objects.filter(pk=world.workflow.pk).update(**updates)
        with pytest.raises(atomic_review.WorkflowReviewError):
            activate_reviewed_schedule(
                workflow_id=str(world.workflow.guid),
                expected_digest=review.schedule_review_digest,
                request_id="stale-consent",
                confirmed=True,
            )
    world.workflow.refresh_from_db()
    assert not world.workflow.is_enabled
    assert not ReviewedWorkflowScheduleApproval.objects.exists()


@pytest.mark.parametrize("malformed", [{"0": {"params": {"prompt": 123}}}, ["not-an-object"]])
def test_malformed_changed_configuration_is_an_explicit_skip(world, malformed):
    with tenant_context(world.tenant):
        approve(world)
    Workflow.objects.filter(pk=world.workflow.pk).update(stage_bindings=malformed)
    assert fire(world).skipped


def test_request_id_cannot_be_reused_for_changed_consent(world):
    from workflows.models import ReviewedWorkflowScheduleApproval
    from workflows.reviewed_schedules import activate_reviewed_schedule

    with tenant_context(world.tenant):
        first = approve(world)
        new_review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
        with pytest.raises(atomic_review.WorkflowReviewError, match="requestId"):
            activate_reviewed_schedule(
                workflow_id=str(world.workflow.guid),
                expected_digest=new_review.schedule_review_digest,
                request_id="approval-one",
                confirmed=True,
            )
    assert ReviewedWorkflowScheduleApproval.objects.count() == 1
    world.workflow.refresh_from_db()
    assert world.workflow.reviewed_schedule_approval_id == first.pk


def test_capture_uses_an_independent_transaction_after_queryful_outer_atomic(world):
    from django.db import transaction

    with tenant_context(world.tenant), transaction.atomic():
        assert Workflow.objects.filter(pk=world.workflow.pk).exists()
        capture = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
    assert capture.configuration["workflowGuid"] == str(world.workflow.guid)


def test_capture_does_not_reuse_cached_policy_or_idp_groups(world):
    from astrolift_identity import abac
    from astrolift_identity.models import Policy, RoleBinding

    bindings = RoleBinding.objects.filter(user=world.user)
    bindings.update(user=None, group_external_id="read-review")
    members = Member.objects.filter(user=world.user, scope_kind="ORG", scope_id=world.w.org.pk)
    members.update(idp_groups=["read-review"])
    attrs = abac.RequestAttributes(actor_user_id=world.user.pk)
    with tenant_context(world.tenant), abac.request_attributes(attrs):
        atomic_review.capture_review(definition_id=str(world.definition.guid))
        members.update(idp_groups=[])
        with pytest.raises(PermissionDenied):
            atomic_review.capture_review(definition_id=str(world.definition.guid))
        members.update(idp_groups=["read-review"])
        atomic_review.capture_review(definition_id=str(world.definition.guid))
        Policy.objects.create(
            organization=world.w.org,
            name="Withdraw read",
            slug="withdraw-read",
            scope_level="ORG",
            action_pattern="workflow.read",
        )
        with pytest.raises(PermissionDenied):
            atomic_review.capture_review(definition_id=str(world.definition.guid))


def test_configured_override_dispatches_only_the_selected_agent_with_source_read_retained(world):
    from astrolift_registry.models import Workload

    bind_role(
        world.user,
        permissions=[Permission.AGENT_READ],
        kind="ORG",
        scope_id=world.w.org.pk,
        slug=f"source-read-{uuid4().hex}",
    )
    source = Workload.objects.create(
        registered_app=world.w.platform_app, name="Readable default", slug="readable-default", kind="agent"
    )
    selected = Workload.objects.create(
        registered_app=world.w.medops_app,
        name="Dispatchable override",
        slug="dispatchable-override",
        kind="agent",
    )
    WorkflowStage.objects.filter(pk=world.stage.pk).update(kind="agent_dispatch", agent_definition=source)
    Workflow.objects.filter(pk=world.workflow.pk).update(
        stage_bindings={"0": {"agent_workload_id": str(selected.guid)}}
    )
    with tenant_context(world.tenant):
        approval = approve(world)
    assert approval.pk
    assert not fire(world).skipped


CONSENT_MUTATION = """mutation($input: ActivateReviewedWorkflowScheduleInput!) {
  activateReviewedWorkflowSchedule(input: $input) {
    ok errors { code message } data { id databaseStatus engineState }
  }
}"""


def browser_info(world, *, elevated=False, ttl=300):
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
    from django.contrib.sessions.backends.db import SessionStore

    from astrolift_identity.session_elevation import METHOD_PASSWORD, elevate

    session = SessionStore()
    session[SESSION_KEY] = str(world.user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = world.user.get_session_auth_hash()
    if elevated:
        elevate(session, method=METHOD_PASSWORD, ttl_seconds=ttl)
    session.save()
    return SimpleNamespace(
        context=SimpleNamespace(user=world.user, request=SimpleNamespace(user=world.user, session=session))
    )


def consent(world, review, info, request_id="session-consent"):
    return schema.execute_sync(
        CONSENT_MUTATION,
        variable_values={
            "input": {
                "workflowId": str(world.workflow.guid),
                "expectedScheduleReviewDigest": review.schedule_review_digest,
                "requestId": request_id,
                "confirmed": True,
            }
        },
        context_value=info.context,
    )


@pytest.mark.parametrize("elevated", [False, True])
def test_public_person_consent_uses_existing_install_elevation_policy(world, elevated):
    from constance.test import override_config

    from workflows.models import ReviewedWorkflowScheduleApproval

    info = browser_info(world, elevated=elevated)
    with override_config(REQUIRE_STEP_UP_AUTH=True), tenant_context(world.tenant):
        review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
        result = consent(world, review, info)
    assert result.errors is None, result.errors
    envelope = result.data["activateReviewedWorkflowSchedule"]
    assert envelope["ok"] is elevated, envelope
    if not elevated:
        assert envelope["errors"][0]["code"] == "STEP_UP_REQUIRED"
    assert ReviewedWorkflowScheduleApproval.objects.exists() is elevated


def test_public_bearer_consent_keeps_native_step_up_exemption_and_actual_ceiling(world):
    from constance.test import override_config

    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    token = ApiToken.objects.create(
        user=world.user,
        organization=world.w.org,
        team=world.w.medops,
        name="Normal consent bearer",
        token_hash="normal-consent",
        scopes=["read:apps", "write:apps"],
    )
    info = make_info(world.user)
    info.context.request._api_token = token
    state = set_current_api_token(token)
    try:
        with override_config(REQUIRE_STEP_UP_AUTH=True), tenant_context(world.tenant):
            review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))
            result = consent(world, review, info)
    finally:
        reset_current_api_token(state)
    assert result.errors is None, result.errors
    assert result.data["activateReviewedWorkflowSchedule"]["ok"], result.data


@pytest.mark.parametrize("withdrawal", ["natural_expiry", "deelevation", "session_deleted"])
def test_person_consent_rechecks_persisted_elevation_after_content_wait(world, monkeypatch, withdrawal):
    from time import sleep

    from constance.test import override_config
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.sessions.models import Session
    from django.db import transaction
    from django.utils import timezone

    from astrolift_identity.session_elevation import SESSION_KEY_ELEVATED_UNTIL
    from workflows import reviewed_schedules
    from workflows.models import ReviewedWorkflowScheduleApproval

    info = browser_info(world, elevated=True, ttl=4 if withdrawal == "natural_expiry" else 300)
    entered = Event()
    original = reviewed_schedules._locked_review
    with tenant_context(world.tenant):
        review = atomic_review.capture_review(workflow_id=str(world.workflow.guid))

    def waiting(*args):
        entered.set()
        return original(*args)

    def approve_in_browser():
        close_old_connections()
        try:
            with tenant_context(world.tenant):
                return consent(world, review, info)
        finally:
            close_old_connections()

    monkeypatch.setattr(reviewed_schedules, "_locked_review", waiting)
    with override_config(REQUIRE_STEP_UP_AUTH=True), ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Workflow.objects.select_for_update().get(pk=world.workflow.pk)
            pending = pool.submit(approve_in_browser)
            assert entered.wait(15)
            if withdrawal == "natural_expiry":
                from datetime import datetime

                until = datetime.fromisoformat(info.context.request.session[SESSION_KEY_ELEVATED_UNTIL])
                sleep(max(0, (until - timezone.now()).total_seconds()) + 0.1)
            elif withdrawal == "deelevation":
                stored = SessionStore(info.context.request.session.session_key)
                stored.pop(SESSION_KEY_ELEVATED_UNTIL)
                stored.save()
            else:
                Session.objects.filter(session_key=info.context.request.session.session_key).delete()
        result = pending.result(timeout=15)
    assert result.errors is None, result.errors
    envelope = result.data["activateReviewedWorkflowSchedule"]
    assert not envelope["ok"], envelope
    assert envelope["errors"][0]["code"] in {"STEP_UP_REQUIRED", "PERMISSION_DENIED"}
    assert not ReviewedWorkflowScheduleApproval.objects.exists()
