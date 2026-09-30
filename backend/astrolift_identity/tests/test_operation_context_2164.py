"""Operation policies gate the real resolver-entry paths against real rows."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_identity.abac import RequestAttributes, current_attributes, request_attributes
from astrolift_identity.models import Member, Policy
from astrolift_identity.operation_context import environment_operation, named_environment, workflow_operation
from astrolift_identity.tests.test_access_enforcement_2157 import _no_opensearch  # noqa: F401
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.schema.mutations import AbortDeploymentInput, LifecycleMutation
from astrolift_lifecycle.schema.mutations.environments import EnvironmentMutations
from astrolift_lifecycle.schema.mutations.types import EnvironmentByIdInput
from astrolift_operations.models import WorkflowRun
from astrolift_services.schema.mutations import RevealAppSecretInput, ServicesMutation, SetAppSecretInput
from astrolift_workflows.schema.mutations import _gate_instance_op
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_cluster, make_info, make_user
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("regions", [("us-west-2",), ("us-west-2", "us-east-1")])
def test_any_scope_operation_resets_grants_between_targets(operation_world, regions):
    from astrolift_identity.operation_context import OperationContext
    from astrolift_identity.scope_visibility import visible_apps
    from astrolift_registry.models import RegisteredApp
    from core.permissions import require_permission

    w = operation_world
    policy(w, action="app.read", resource={"region": ["us-east-1"]})
    calls = []

    @require_permission(
        Permission.APP_READ,
        any_scope=True,
        operation=lambda args: tuple(OperationContext(region=region) for region in regions),
    )
    def collection():
        calls.append(current_attributes().region)
        return list(visible_apps(RegisteredApp.objects.filter(organization=w.world.org), Permission.APP_READ))

    attrs = RequestAttributes(actor_user_id=w.user.pk)
    with as_tenant(w.world, w.user), request_attributes(attrs):
        if len(regions) == 1:
            assert len(collection()) == 2
            assert calls == ["us-west-2"]
        else:
            with pytest.raises(PermissionDenied):
                collection()
            assert calls == []
        assert current_attributes() is attrs and attrs.region is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_operation_subscriptions_restore_caller_facts_and_close_in_another_task(operation_world):
    import asyncio

    import core.permissions as permissions
    from astrolift_identity.operation_context import OperationContext
    from core.permissions import PermissionScope, ScopeKind, require_permission

    w = operation_world
    closed = []

    @require_permission(
        Permission.APP_READ,
        scope=lambda args: PermissionScope(kind=ScopeKind.APP, id=w.world.medops_app.pk),
        operation=lambda args: (OperationContext(region=args["region"]),),
    )
    async def stream(region):
        try:
            while True:
                assert permissions._scopes_memo.get() == {}
                yield current_attributes().region
        finally:
            closed.append(current_attributes().region)

    attrs = RequestAttributes(actor_user_id=w.user.pk, region="caller-region")
    caller_memo = {"caller": "grant"}
    memo_token = permissions._scopes_memo.set(caller_memo)
    first, second = stream("us-east-1"), stream("us-west-2")
    try:
        with as_tenant(w.world, w.user), request_attributes(attrs):
            for generator, expected in [(first, "us-east-1"), (second, "us-west-2"), (first, "us-east-1")]:
                assert await anext(generator) == expected
                assert current_attributes() is attrs
                assert permissions._scopes_memo.get() is caller_memo
            await asyncio.create_task(first.aclose())
            await asyncio.create_task(second.aclose())
            assert current_attributes() is attrs
            assert permissions._scopes_memo.get() is caller_memo
        assert closed == ["us-east-1", "us-west-2"]
    finally:
        await first.aclose()
        await second.aclose()
        permissions._scopes_memo.reset(memo_token)


@pytest.fixture
def operation_world():
    tag = uuid4().hex[:8]
    world = ScopeWorld(f"abac-{tag}")
    user = make_user(f"abac-{tag}")
    Member.objects.create(
        user=user, scope_kind="ORG", scope_id=world.org.pk, lifecycle="active", is_active=True
    )
    bind_role(
        user,
        permissions=[
            Permission.APP_DEPLOY,
            Permission.APP_UPDATE,
            Permission.APP_READ,
            Permission.SECRET_READ,
            Permission.WORKFLOW_TRIGGER,
            Permission.AGENT_DISPATCH,
        ],
        kind="ORG",
        scope_id=world.org.pk,
        slug=f"ops-{tag}",
    )
    east = make_cluster(world, f"east-{tag}")
    east.region = "us-east-1"
    east.save(update_fields=["region", "updated_at", "version"])
    west = make_cluster(world, f"west-{tag}")
    west.region = "us-west-2"
    west.save(update_fields=["region", "updated_at", "version"])
    production = AppEnvironment.objects.create(
        registered_app=world.medops_app, name="production", tenant_cluster=east
    )
    staging = AppEnvironment.objects.create(
        registered_app=world.medops_app, name="staging", tenant_cluster=west
    )
    return SimpleNamespace(
        world=world, user=user, info=make_info(user), production=production, staging=staging
    )


def policy(w, *, action="app.deploy", resource=None, conditions=None, effect="DENY"):
    return Policy.objects.create(
        organization=w.world.org,
        name="Operation",
        slug=f"operation-{uuid4().hex}",
        scope_level="ORG",
        effect=effect,
        action_pattern=action,
        resource_pattern=resource or {},
        actor_pattern={},
        conditions=conditions or [],
    )


def approve_deployment_as(w, deployment, voter):
    from astrolift_lifecycle.schema.mutations import DeploymentByIdInput

    Member.objects.get_or_create(
        user=voter,
        scope_kind="ORG",
        scope_id=w.world.org.pk,
        defaults={"lifecycle": "active", "is_active": True},
    )
    bind_role(
        voter,
        permissions=[Permission.APP_APPROVE_DEPLOY],
        kind="ORG",
        scope_id=w.world.org.pk,
        slug=f"voter-{uuid4().hex}",
    )
    with as_tenant(w.world, voter):
        result = LifecycleMutation().approve_deployment(
            make_info(voter), DeploymentByIdInput(id=str(deployment.guid))
        )
    assert result.ok, result.errors
    deployment.refresh_from_db()
    return result


@pytest.mark.parametrize("resource", [{"env": ["production"]}, {"region": ["us-east-1"]}])
def test_environment_and_region_deny_only_the_matching_target(operation_world, resource):
    w = operation_world
    policy(w, resource=resource)
    attrs = RequestAttributes(actor_user_id=w.user.pk)
    with as_tenant(w.world, w.user), request_attributes(attrs):
        refused = EnvironmentMutations().pause_environment(
            w.info, EnvironmentByIdInput(id=str(w.production.guid))
        )
        assert not refused.ok
        assert refused.errors[0].code == "PERMISSION_DENIED"
        allowed = EnvironmentMutations().pause_environment(
            w.info, EnvironmentByIdInput(id=str(w.staging.guid))
        )
        assert allowed.ok, allowed.errors
        assert current_attributes() is attrs
        assert attrs.environment is None
        assert attrs.region is None
    w.production.refresh_from_db()
    w.staging.refresh_from_db()
    assert not w.production.deploys_paused
    assert w.staging.deploys_paused


def test_env_match_condition_uses_the_resolved_environment(operation_world):
    w = operation_world
    policy(w, effect="ALLOW", conditions=[{"kind": "env_match", "env_in": ["staging"]}])
    with as_tenant(w.world, w.user):
        assert (
            not EnvironmentMutations()
            .pause_environment(w.info, EnvironmentByIdInput(id=str(w.production.guid)))
            .ok
        )
        assert (
            EnvironmentMutations().pause_environment(w.info, EnvironmentByIdInput(id=str(w.staging.guid))).ok
        )


def test_deployment_gate_reads_recorded_approvals(operation_world):
    w = operation_world
    deployment = Deployment.objects.create(
        registered_app=w.world.medops_app,
        app_environment=w.production,
        status="pending_approval",
        approvals_required=3,
        approvals_received=0,
    )
    policy(
        w, resource={"env": ["production"]}, conditions=[{"kind": "approval_required", "min_approvers": 2}]
    )
    with as_tenant(w.world, w.user):
        refused = LifecycleMutation().abort_deployment(
            w.info, AbortDeploymentInput(id=str(deployment.guid), reason="stop")
        )
        assert not refused.ok
        deployment.refresh_from_db()
        assert deployment.status == "pending_approval"
    approve_deployment_as(w, deployment, w.user)
    approve_deployment_as(w, deployment, w.user)
    assert deployment.approvals_received == 1
    with as_tenant(w.world, w.user):
        assert (
            not LifecycleMutation()
            .abort_deployment(w.info, AbortDeploymentInput(id=str(deployment.guid), reason="stop"))
            .ok
        )
    approve_deployment_as(w, deployment, make_user(f"second-voter-{uuid4().hex}"))
    assert deployment.approvals_received == 2
    with as_tenant(w.world, w.user):
        allowed = LifecycleMutation().abort_deployment(
            w.info, AbortDeploymentInput(id=str(deployment.guid), reason="stop")
        )
        assert allowed.ok, allowed.errors
    deployment.refresh_from_db()
    assert deployment.status == "failed"


def test_secret_reveal_uses_the_environment_encoded_in_the_secret_id(operation_world):
    w = operation_world
    app = w.world.medops_app
    app.manifest_raw = 'astrolift_version = 1\nname = "hello"\n[env]\nAPI_KEY = "classified"\n[[workloads]]\nname = "web"\nkind = "deployment"\n'
    app.save(update_fields=["manifest_raw", "updated_at", "version"])
    policy(w, action="secret.read", resource={"region": ["us-east-1"]})
    with as_tenant(w.world, w.user):
        refused = ServicesMutation().reveal_app_secret(
            w.info, RevealAppSecretInput(app_slug=app.slug, secret_id="literal:production:API_KEY")
        )
        assert not refused.ok
        assert refused.data is None
        allowed = ServicesMutation().reveal_app_secret(
            w.info, RevealAppSecretInput(app_slug=app.slug, secret_id="literal:staging:API_KEY")
        )
        assert allowed.ok, allowed.errors
        assert allowed.data.value == "classified"


def test_app_wide_secret_write_checks_production_as_well_as_staging(operation_world):
    w = operation_world
    policy(w, action="app.update", resource={"env": ["production"]})
    app = w.world.medops_app
    before = app.manifest_raw_staged
    with as_tenant(w.world, w.user):
        refused = ServicesMutation().set_app_secret(
            w.info, SetAppSecretInput(app_slug=app.slug, key="API_KEY", value="new")
        )
        assert not refused.ok
    app.refresh_from_db()
    assert app.manifest_raw_staged == before


def test_literal_secret_proposal_checks_all_environments_despite_its_display_target(operation_world):
    from astrolift_identity.operation_context import secret_proposal_operation
    from astrolift_services.models import SecretChangeApproval, SecretChangeProposal
    from astrolift_services.scopes import secret_change_proposal_app_scope
    from core.permissions import require_permission

    w = operation_world
    proposal = SecretChangeProposal.objects.create(
        registered_app=w.world.medops_app,
        app_environment=w.staging,
        proposer=w.user,
        op="set",
        expires_at=timezone.now(),
    )
    SecretChangeApproval.objects.create(proposal=proposal, approver=None, decision="approved")
    policy(w, action="app.update", resource={"env": ["production"]})

    @require_permission(
        Permission.APP_UPDATE,
        scope=secret_change_proposal_app_scope("id"),
        operation=secret_proposal_operation("id"),
    )
    def apply(id):
        pytest.fail("a staging display target must not authorize an app-wide production change")

    with as_tenant(w.world, w.user):
        contexts = secret_proposal_operation("id")({"id": str(proposal.guid)})
        assert {context.environment for context in contexts} == {"production", "staging"}
        assert all(context.approvals == 0 for context in contexts)
        with pytest.raises(PermissionDenied):
            apply(str(proposal.guid))


def test_workflow_gate_counts_distinct_persisted_approved_voters(operation_world):
    w = operation_world
    definition = WorkflowDefinition.objects.create(
        name="Approval", slug=f"approval-{uuid4().hex}", organization=w.world.org
    )
    run = WorkflowRun.objects.create(
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id=f"approval-{uuid4()}",
        run_id="execution",
        organization=w.world.org,
        registered_app=w.world.medops_app,
        app_environment=w.production,
        workflow_definition=definition,
    )
    policy(w, action="workflow.trigger", conditions=[{"kind": "approval_required", "min_approvers": 2}])
    stage = WorkflowStage.objects.create(
        definition=definition, kind="human_gate", order=0, slug=f"gate-{uuid4().hex}"
    )

    def vote(user_id, *, decision="approved", status="completed"):
        return WorkflowStageExecution.objects.create(
            workflow_run=run,
            stage=stage,
            status=status,
            slug=f"vote-{uuid4().hex}",
            output={"human_gate": {"decision": decision, "decided_by_user_id": user_id}},
        )

    with as_tenant(w.world, w.user):
        with pytest.raises(PermissionDenied):
            _gate_instance_op(w.user, run.workflow_id)
        vote(w.user.pk)
        vote(w.user.pk)
        vote(w.user.pk + 1, decision="rejected")
        vote(w.user.pk + 2, status="running")
        assert workflow_operation(run.workflow_id).approvals == 1
        with pytest.raises(PermissionDenied):
            _gate_instance_op(w.user, run.workflow_id)
        second = make_user(f"approver-{uuid4().hex}")
        vote(second.pk)
        assert _gate_instance_op(w.user, run.workflow_id) is None
        assert workflow_operation(run.workflow_id).approvals == 2


def test_operation_loaders_do_not_accept_foreign_or_unresolved_targets(operation_world):
    w = operation_world
    with as_tenant(w.world, w.user):
        assert environment_operation("id")({"id": "not-a-guid"})[0].environment is None
        assert (
            named_environment("slug", "env")({"slug": w.world.medops_app.slug, "env": "missing"})[
                0
            ].environment
            is None
        )
    foreign = ScopeWorld(f"foreign-{uuid4().hex}")
    with as_tenant(foreign, w.user):
        assert environment_operation("id")({"id": str(w.production.guid)})[0].environment is None


def test_agent_control_uses_its_frozen_dispatch_region(operation_world):
    from astrolift_agents.models import AgentTask
    from astrolift_agents.schema.mutations import AgentsMutation

    w = operation_world
    tasks = [
        AgentTask.objects.create(
            organization=w.world.org,
            status="queued",
            dispatch_target={"cluster_guid": str(env.tenant_cluster.guid)},
        )
        for env in (w.production, w.staging)
    ]
    policy(w, action="agent.dispatch", resource={"region": ["us-east-1"]})
    with as_tenant(w.world, w.user):
        denied = AgentsMutation().cancel_task(w.info, id=str(tasks[0].guid))
        allowed = AgentsMutation().cancel_task(w.info, id=str(tasks[1].guid))
    assert not denied.ok
    assert allowed.ok, allowed.errors
    tasks[0].refresh_from_db()
    tasks[1].refresh_from_db()
    assert tasks[0].status == "queued"
    assert tasks[1].status == "cancelled"


def test_exact_execution_does_not_borrow_latest_executions_approvals(operation_world):
    from astrolift_identity.operation_context import execution_operation

    w = operation_world
    definition = WorkflowDefinition.objects.create(
        name="Exact", slug=f"exact-{uuid4().hex}", organization=w.world.org
    )
    runs = [
        WorkflowRun.objects.create(
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_id="shared-id",
            run_id=run_id,
            organization=w.world.org,
            registered_app=w.world.medops_app,
            app_environment=w.production,
            workflow_definition=definition,
        )
        for run_id in ("older", "newer")
    ]
    stage = WorkflowStage.objects.create(definition=definition, kind="human_gate", order=0, slug="gate")
    WorkflowStageExecution.objects.create(
        workflow_run=runs[0],
        stage=stage,
        status="completed",
        slug="vote",
        output={"human_gate": {"decision": "approved", "decided_by_user_id": w.user.pk}},
    )
    with as_tenant(w.world, w.user):
        assert execution_operation({"execution_id": str(runs[0].guid)})[0].approvals == 1
        assert execution_operation({"execution_id": str(runs[1].guid)})[0].approvals == 0
        assert workflow_operation("shared-id", "older").approvals == 1


def test_visibility_backed_scope_uses_the_operation_region_before_resolving(operation_world):
    from astrolift_agents.scopes import agent_workload_app_scope
    from astrolift_identity.operation_context import OperationContext
    from astrolift_registry.models import Workload
    from core.permissions import ScopeKind, require_permission

    w = operation_world
    workload = Workload.objects.create(
        registered_app=w.world.platform_app, name="Agent", slug=f"agent-{uuid4().hex}", kind="agent"
    )
    policy(w, action="agent.dispatch", resource={"region": ["us-east-1"]})
    seen = []

    def region(args):
        return (OperationContext(region=args["region"], approvals=0),)

    @require_permission(
        Permission.AGENT_DISPATCH,
        scope=agent_workload_app_scope("slug", Permission.AGENT_DISPATCH),
        operation=region,
    )
    def act(slug, region):
        # No app environment exists on this agent. The bound dispatcher
        # region must be visible to the scope factory and handler alike.
        scope = agent_workload_app_scope("slug", Permission.AGENT_DISPATCH)({"slug": slug})
        seen.append(scope.kind)

    with as_tenant(w.world, w.user):
        act(workload.slug, "us-west-2")
        with pytest.raises(PermissionDenied):
            act(workload.slug, "us-east-1")
    assert seen == [ScopeKind.APP]


def test_unavailable_agent_cluster_region_remains_unknown_and_denied(operation_world, monkeypatch):
    from astrolift_identity.operation_context import agent_region_operation
    from core.permissions import require_permission

    w = operation_world
    monkeypatch.setattr(
        "astrolift_agents.services.agent_cluster.resolve_agent_cluster", lambda _organization: object()
    )
    policy(w, action="agent.dispatch", resource={"region": ["us-east-1"]})

    @require_permission(Permission.AGENT_DISPATCH, operation=agent_region_operation)
    def dispatch():
        pytest.fail("unavailable cluster metadata must not bypass the region policy")

    with as_tenant(w.world, w.user):
        assert agent_region_operation({})[0].region is None
        with pytest.raises(PermissionDenied):
            dispatch()


def test_stage_execution_graphql_reader_cannot_borrow_the_latest_runs_approvals(operation_world):
    import strawberry

    from workflows.schema.queries import Query as LegacyQuery

    w = operation_world
    bind_role(
        w.user,
        permissions=[Permission.WORKFLOW_READ],
        kind="ORG",
        scope_id=w.world.org.pk,
        slug=f"stage-reader-{uuid4().hex}",
    )
    definition = WorkflowDefinition.objects.create(
        organization=w.world.org, slug=f"read-{uuid4().hex}", name="Read"
    )
    stage = WorkflowStage.objects.create(definition=definition, kind="human_gate", order=0, slug="gate")
    runs = [
        WorkflowRun.objects.create(
            organization=w.world.org,
            registered_app=w.world.medops_app,
            app_environment=w.production,
            workflow_definition=definition,
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_id="reused-workflow-id",
            run_id=run_id,
        )
        for run_id in ("older-unapproved", "latest-approved")
    ]
    executions = [
        WorkflowStageExecution.objects.create(
            workflow_run=run,
            stage=stage,
            status="completed",
            slug=run.run_id,
            output={
                "human_gate": {
                    "decision": "approved" if index else "rejected",
                    "decided_by_user_id": w.user.pk,
                }
            },
        )
        for index, run in enumerate(runs)
    ]
    policy(w, action="workflow.read", conditions=[{"kind": "approval_required"}])
    schema = strawberry.Schema(query=LegacyQuery)
    query = """query($run: String!) {
      workflowStageExecutions(workflowId: "reused-workflow-id", runId: $run) { guid status }
    }"""
    with as_tenant(w.world, w.user):
        denied = schema.execute_sync(
            query, variable_values={"run": runs[0].run_id}, context_value=w.info.context
        )
        allowed = schema.execute_sync(
            query, variable_values={"run": runs[1].run_id}, context_value=w.info.context
        )
    assert denied.errors and isinstance(denied.errors[0].original_error, PermissionDenied)
    assert denied.data is None
    assert not allowed.errors
    assert allowed.data["workflowStageExecutions"] == [
        {"guid": str(executions[1].guid), "status": "completed"}
    ]


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("deny_scope", ["APP", "PROJECT", "environment", "region"])
def test_bulk_approval_checks_each_app_scope_and_operation_before_its_side_effects(
    operation_world, action, deny_scope
):
    from astrolift_lifecycle.schema.mutations import (
        BulkApproveDeploymentsInput,
        BulkRejectDeploymentsInput,
        DeploymentByIdInput,
    )

    w = operation_world
    bind_role(
        w.user,
        permissions=[Permission.APP_APPROVE_DEPLOY],
        kind="ORG",
        scope_id=w.world.org.pk,
        slug=f"bulk-approver-{uuid4().hex}",
    )
    permitted_env = AppEnvironment.objects.create(
        registered_app=w.world.platform_app, name="staging", tenant_cluster=w.staging.tenant_cluster
    )
    deployments = [
        Deployment.objects.create(
            registered_app=env.registered_app,
            app_environment=env,
            status="pending_approval",
            approvals_required=2,
        )
        for env in (w.production, permitted_env)
    ]
    denied = policy(w, action="app.approve_deploy")
    if deny_scope in ("APP", "PROJECT"):
        denied.scope_level = deny_scope
        denied.scope_id = w.world.medops_app.pk if deny_scope == "APP" else w.world.medops_project.pk
    else:
        denied.resource_pattern = (
            {"env": ["production"]} if deny_scope == "environment" else {"region": ["us-east-1"]}
        )
    denied.save()
    with as_tenant(w.world, w.user):
        single = LifecycleMutation().approve_deployment(
            w.info, DeploymentByIdInput(id=str(deployments[0].guid))
        )
        assert not single.ok and single.errors[0].code == "PERMISSION_DENIED"
        ids = [str(deployment.guid) for deployment in deployments]
        if action == "approve":
            result = LifecycleMutation().bulk_approve_deployments(
                w.info, BulkApproveDeploymentsInput(deployment_ids=ids)
            )
        else:
            result = LifecycleMutation().bulk_reject_deployments(
                w.info, BulkRejectDeploymentsInput(deployment_ids=ids, reason="refused")
            )
    assert result.ok
    assert (result.data.failed_count, result.data.succeeded_count) == (1, 1)
    assert not result.data.results[0].ok
    assert result.data.results[0].errors[0].code == "PERMISSION_DENIED"
    assert result.data.results[1].ok
    for deployment in deployments:
        deployment.refresh_from_db()
    assert deployments[0].approvals_received == 0
    assert deployments[0].status == "pending_approval"
    assert not deployments[0].aborted_reason
    assert deployments[1].approvals_received == (1 if action == "approve" else 0)
    assert deployments[1].status == ("pending_approval" if action == "approve" else "failed")


def test_legacy_deployment_counter_and_anonymous_credential_do_not_supply_human_votes(operation_world):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    from astrolift_identity.operation_context import deployment_approval_count
    from astrolift_lifecycle.models import DeploymentApproval

    w = operation_world
    deployment = Deployment.objects.create(
        registered_app=w.world.medops_app,
        app_environment=w.production,
        status="pending_approval",
        approvals_required=3,
        approvals_received=2,
    )
    DeploymentApproval.objects.create(deployment=deployment, credential_hash="a" * 64)
    assert deployment_approval_count(deployment) == 0
    policy(w, conditions=[{"kind": "approval_required", "min_approvers": 2}])
    with as_tenant(w.world, w.user):
        assert (
            not LifecycleMutation()
            .abort_deployment(
                w.info, AbortDeploymentInput(id=str(deployment.guid), reason="no identity proof")
            )
            .ok
        )
    previous = (
        MigrationExecutor(connection)
        .loader.project_state([("astrolift_lifecycle", "0043_previewenvironment_opened_by_failure_reason")])
        .apps.get_model("astrolift_lifecycle", "Deployment")
    )
    assert previous.objects.get(pk=deployment.pk).status == "pending_approval"
    assert previous.objects.get(pk=deployment.pk).approvals_received == 2
    approve_deployment_as(w, deployment, w.user)
    assert deployment.approvals_received == 1  # multi-person quorum requires identified humans
    assert deployment_approval_count(deployment) == 1


def test_multi_person_quorum_starts_only_after_two_authenticated_voters(operation_world, monkeypatch):
    from astrolift_identity.operation_context import deployment_approval_count
    from astrolift_lifecycle.approval import mint_magic_link
    from astrolift_lifecycle.schema.mutations import ApproveByTokenInput

    w = operation_world
    issued = mint_magic_link(now=timezone.now())
    deployment = Deployment.objects.create(
        registered_app=w.world.medops_app,
        app_environment=w.production,
        status="pending_approval",
        approvals_required=2,
        approval_token_hash=issued.token_hash,
        approval_token_expires_at=issued.expires_at,
    )
    queued = []
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.mutations.helpers._start_deploy_workflow_on_commit",
        lambda **kwargs: queued.append(kwargs),
    )
    token_vote = LifecycleMutation().approve_deployment_by_token(
        w.info, ApproveByTokenInput(token=issued.plaintext_token)
    )
    assert token_vote.ok, token_vote.errors
    approve_deployment_as(w, deployment, w.user)
    approve_deployment_as(w, deployment, w.user)
    assert deployment.approvals_received == 1
    assert deployment.status == "pending_approval"
    assert not queued
    approve_deployment_as(w, deployment, make_user(f"quorum-voter-{uuid4().hex}"))
    assert deployment.approvals_received == 2
    assert deployment_approval_count(deployment) == 2
    assert deployment.status == "pending"
    assert len(queued) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_approval_requests_from_one_user_record_one_vote(operation_world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    from astrolift_lifecycle.schema.mutations import DeploymentByIdInput

    w = operation_world
    deployment = Deployment.objects.create(
        registered_app=w.world.medops_app,
        app_environment=w.production,
        status="pending_approval",
        approvals_required=3,
    )
    bind_role(
        w.user,
        permissions=[Permission.APP_APPROVE_DEPLOY],
        kind="ORG",
        scope_id=w.world.org.pk,
        slug=f"concurrent-voter-{uuid4().hex}",
    )
    barrier = Barrier(2)

    def vote():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            with as_tenant(w.world, w.user):
                return LifecycleMutation().approve_deployment(
                    w.info, DeploymentByIdInput(id=str(deployment.guid))
                )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: vote(), range(2)))
    assert all(result.ok for result in results)
    deployment.refresh_from_db()
    assert deployment.approvals_received == 1
    assert deployment.approval_votes.count() == 1
    assert deployment.status == "pending_approval"
