"""Operation policies gate the real resolver-entry paths against real rows."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

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
        status="pending",
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
        assert deployment.status == "pending"
        deployment.approvals_received = 2
        deployment.save(update_fields=["approvals_received", "updated_at", "version"])
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
