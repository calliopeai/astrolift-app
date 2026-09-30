"""Scoped policies must reduce collection rows and effective capabilities."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_identity.permission_resolver import granted_scopes, resolve_effective_permissions_anywhere
from astrolift_identity.scope_visibility import visible_apps, visible_projects
from astrolift_identity.tests import test_operation_context_2164 as operation_support
from astrolift_identity.tests.test_access_enforcement_2157 import _no_opensearch  # noqa: F401
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.tenancy import get_current_tenant
from core.tests.utils.scope_world import as_tenant, bind_role, make_user

pytestmark = pytest.mark.django_db
operation_world = operation_support.operation_world
policy = operation_support.policy


@pytest.mark.parametrize("scope", ["APP", "PROJECT", "resource"])
def test_narrow_deny_is_absent_from_an_org_granted_app_collection(operation_world, scope):
    w = operation_world
    row = policy(w, action="app.read")
    if scope == "resource":
        row.resource_pattern = {"app_slug": w.world.medops_app.slug}
    else:
        row.scope_level = scope
        row.scope_id = w.world.medops_app.pk if scope == "APP" else w.world.medops_project.pk
    row.save()
    with as_tenant(w.world, w.user):
        rows = visible_apps(RegisteredApp.objects.filter(organization=w.world.org), Permission.APP_READ)
        assert set(rows.values_list("pk", flat=True)) == {w.world.platform_app.pk}
        scopes = granted_scopes(get_current_tenant(), Permission.APP_READ)
        assert not scopes.org
        assert scopes.app_ids == {w.world.platform_app.pk}


def test_capability_is_removed_when_the_only_granted_app_is_denied(operation_world):
    w = operation_world
    user = make_user(f"narrow-{uuid4().hex}")
    bind_role(
        user,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=w.world.medops_app.pk,
        slug=f"role-{uuid4().hex}",
    )
    policy(w, action="app.read", resource={"app_slug": w.world.medops_app.slug})
    with as_tenant(w.world, user):
        assert not granted_scopes(get_current_tenant(), Permission.APP_READ)
        assert Permission.APP_READ.value not in resolve_effective_permissions_anywhere(get_current_tenant())
    bind_role(
        user,
        permissions=[Permission.APP_READ],
        kind="APP",
        scope_id=w.world.platform_app.pk,
        slug=f"role-{uuid4().hex}",
    )
    with as_tenant(w.world, user):
        assert Permission.APP_READ.value in resolve_effective_permissions_anywhere(get_current_tenant())
        assert granted_scopes(get_current_tenant(), Permission.APP_READ).app_ids == {w.world.platform_app.pk}


def test_environment_specific_policy_keeps_an_app_with_an_allowed_environment(operation_world):
    w = operation_world
    policy(w, action="app.read", resource={"env": ["production"]})
    with as_tenant(w.world, w.user):
        scopes = granted_scopes(get_current_tenant(), Permission.APP_READ)
        assert w.world.medops_app.pk in scopes.app_ids
    w.staging.soft_delete()
    with as_tenant(w.world, w.user):
        assert w.world.medops_app.pk not in granted_scopes(get_current_tenant(), Permission.APP_READ).app_ids


def test_narrow_policy_does_not_make_an_allowed_project_inherit_into_a_denied_app(operation_world):
    from astrolift_identity.models import Project

    w = operation_world
    bind_role(
        w.user,
        permissions=[Permission.PROJECT_READ],
        kind="ORG",
        scope_id=w.world.org.pk,
        slug=f"proj-{uuid4().hex}",
    )
    policy(w, action="*.read", resource={"app_slug": w.world.medops_app.slug})
    with as_tenant(w.world, w.user):
        assert w.world.medops_project in visible_projects(
            Project.objects.filter(organization=w.world.org), Permission.PROJECT_READ
        )
        assert w.world.medops_app not in visible_apps(
            RegisteredApp.objects.filter(organization=w.world.org), Permission.APP_READ
        )


def test_scoped_collection_query_count_does_not_scale_with_app_count(operation_world):
    w = operation_world
    policy(w, action="app.read", resource={"app_slug": "medops*"})
    with as_tenant(w.world, w.user), CaptureQueriesContext(connection) as small:
        granted_scopes(get_current_tenant(), Permission.APP_READ)
    RegisteredApp.objects.bulk_create(
        [
            RegisteredApp(
                organization=w.world.org,
                team=w.world.medops,
                project=w.world.medops_project,
                name=f"App {i}",
                slug=f"extra-{i}-{uuid4().hex}",
            )
            for i in range(25)
        ]
    )
    with as_tenant(w.world, w.user), CaptureQueriesContext(connection) as large:
        granted_scopes(get_current_tenant(), Permission.APP_READ)
    assert len(small) == len(large)


def test_environment_and_deployment_lists_hide_denied_environment_rows(operation_world):
    from astrolift_lifecycle.models import Deployment
    from astrolift_lifecycle.schema.queries import LifecycleQuery

    w = operation_world
    deployments = [
        Deployment.objects.create(registered_app=w.world.medops_app, app_environment=env)
        for env in (w.production, w.staging)
    ]
    policy(w, action="app.read", resource={"env": ["production"]})
    with as_tenant(w.world, w.user):
        environments = LifecycleQuery().astrolift_environments(w.info, app_slug=w.world.medops_app.slug)
        listed = LifecycleQuery().astrolift_deployments(w.info, app_slug=w.world.medops_app.slug)
        page = LifecycleQuery().astrolift_environments_page(w.info, app_slug=w.world.medops_app.slug)
        metrics = LifecycleQuery().astrolift_deployment_metrics(w.info)
        health = LifecycleQuery().astrolift_app_health_summary(w.info)
        comparison = LifecycleQuery().astrolift_compare_deployments(
            w.info, id_a=str(deployments[0].guid), id_b=str(deployments[1].guid)
        )
    assert [str(row.id) for row in environments] == [str(w.staging.guid)]
    assert [str(row.id) for row in listed] == [str(deployments[1].guid)]
    assert page.total_count == 1
    assert metrics.total == 1
    assert next(row for row in health if row.app_slug == w.world.medops_app.slug).environment_count == 1
    assert comparison is None


def test_approval_aware_deployment_list_uses_each_rows_own_count(operation_world):
    from astrolift_lifecycle.models import Deployment
    from astrolift_lifecycle.schema.queries import LifecycleQuery

    w = operation_world
    rows = [
        Deployment.objects.create(
            registered_app=w.world.medops_app,
            app_environment=w.production,
            status="pending_approval",
            approvals_required=3,
            approvals_received=count,
        )
        for count in (0, 0, 2)
    ]
    operation_support.approve_deployment_as(w, rows[0], w.user)
    operation_support.approve_deployment_as(w, rows[1], w.user)
    operation_support.approve_deployment_as(w, rows[1], make_user(f"other-voter-{uuid4().hex}"))
    policy(w, action="app.read", conditions=[{"kind": "approval_required", "min_approvers": 2}])
    with as_tenant(w.world, w.user):
        listed = LifecycleQuery().astrolift_deployments(w.info, app_slug=w.world.medops_app.slug)
    assert [str(row.id) for row in listed] == [str(rows[1].guid)]
    assert rows[2].approvals_received == 2  # A legacy bare counter is not voter evidence.


@pytest.mark.parametrize("resource", [{"env": ["production"]}, {"region": ["us-east-1"]}])
def test_secret_and_service_collections_only_return_allowed_environment_rows(operation_world, resource):
    from astrolift_services.models import ManagedService, SecretChangeProposal
    from astrolift_services.schema.queries import ServicesQuery

    w = operation_world
    w.world.medops_app.manifest_raw = '[env]\nAPI_KEY = "secret"\n'
    w.world.medops_app.save()
    services = [
        ManagedService.objects.create(
            registered_app=w.world.medops_app, app_environment=env, name=env.name, kind="redis"
        )
        for env in (w.production, w.staging)
    ]
    proposals = [
        SecretChangeProposal.objects.create(
            registered_app=w.world.medops_app,
            app_environment=env,
            proposer=w.user,
            op="attach_bundle",
            expires_at=timezone.now(),
        )
        for env in (w.production, w.staging)
    ]
    SecretChangeProposal.objects.create(
        registered_app=w.world.medops_app,
        app_environment=w.staging,
        proposer=w.user,
        op="set",
        expires_at=timezone.now(),
    )
    policy(w, action="app.read", resource=resource)
    with as_tenant(w.world, w.user):
        secrets = ServicesQuery().astrolift_app_secrets(w.info, app_slug=w.world.medops_app.slug)
        page = ServicesQuery().astrolift_managed_services_page(w.info, app_slug=w.world.medops_app.slug)
        queue = ServicesQuery().astrolift_secret_change_proposals(w.info)
    assert {row.environment_name for row in secrets} == {"staging"}
    assert [str(row.id) for row in page.items] == [str(services[1].guid)]
    assert page.total_count == 1
    assert [str(row.id) for row in queue] == [str(proposals[1].guid)]


def test_secret_proposal_collection_checks_its_own_durable_approval_votes(operation_world):
    from astrolift_services.models import SecretChangeApproval, SecretChangeProposal
    from astrolift_services.schema.queries import ServicesQuery

    w = operation_world
    proposals = [
        SecretChangeProposal.objects.create(
            registered_app=w.world.medops_app,
            app_environment=w.production,
            proposer=w.user,
            op="set",
            expires_at=timezone.now(),
        )
        for _ in range(2)
    ]
    SecretChangeApproval.objects.create(proposal=proposals[1], approver=w.user, decision="approved")
    policy(w, action="app.read", conditions=[{"kind": "approval_required"}])
    with as_tenant(w.world, w.user):
        queue = ServicesQuery().astrolift_secret_change_proposals(w.info)
    assert [str(row.id) for row in queue] == [str(proposals[1].guid)]


def test_no_environment_secret_metadata_preserves_the_unknown_fact_gate(operation_world):
    from astrolift_services.schema.queries import ServicesQuery

    w = operation_world
    app = w.world.platform_app
    app.manifest_raw = '[env]\nAPI_KEY = "secret"\n'
    app.save()
    with as_tenant(w.world, w.user):
        assert len(ServicesQuery().astrolift_app_secrets(w.info, app_slug=app.slug)) == 1
    policy(w, action="app.read", resource={"env": ["production"]})
    with as_tenant(w.world, w.user), pytest.raises(operation_support.PermissionDenied):
        ServicesQuery().astrolift_app_secrets(w.info, app_slug=app.slug)


def test_workflow_collection_uses_exact_persisted_votes(operation_world):
    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution
    from workflows.scopes import visible_runs

    w = operation_world
    bind_role(
        w.user,
        permissions=[Permission.WORKFLOW_READ],
        kind="ORG",
        scope_id=w.world.org.pk,
        slug=f"wf-{uuid4().hex}",
    )
    definition = WorkflowDefinition.objects.create(
        organization=w.world.org, slug=f"visible-{uuid4().hex}", name="Visible"
    )
    stage = WorkflowStage.objects.create(definition=definition, kind="human_gate", order=0, slug="gate")
    runs = [
        WorkflowRun.objects.create(
            organization=w.world.org,
            workflow_definition=definition,
            registered_app=w.world.medops_app,
            app_environment=w.production,
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_id="shared",
            run_id=str(i),
        )
        for i in range(2)
    ]
    for user_id in (w.user.pk, w.user.pk + 1):
        WorkflowStageExecution.objects.create(
            workflow_run=runs[1],
            stage=stage,
            status="completed",
            slug=f"vote-{user_id}",
            output={"human_gate": {"decision": "approved", "decided_by_user_id": user_id}},
        )
    policy(w, action="workflow.read", conditions=[{"kind": "approval_required", "min_approvers": 2}])
    with as_tenant(w.world, w.user):
        listed = visible_runs(
            WorkflowRun.objects.filter(organization=w.world.org), w.world.org.pk, Permission.WORKFLOW_READ
        )
        assert list(listed.values_list("pk", flat=True)) == [runs[1].pk]
        assert Permission.WORKFLOW_READ.value in resolve_effective_permissions_anywhere(get_current_tenant())


@pytest.mark.parametrize("default_region", ["us-west-2", "us-east-1"])
@pytest.mark.parametrize("owner", ["app", "project"])
def test_agent_collection_uses_each_frozen_region_and_current_dispatch_target(
    operation_world, default_region, owner
):
    from astrolift_agents.models import AgentTask
    from astrolift_agents.schema.queries import AgentsQuery
    from astrolift_agents.visibility import agent_tasks, agent_workloads
    from astrolift_registry.models import Workload

    w = operation_world
    bind_role(
        w.user,
        permissions=[Permission.AGENT_READ],
        kind="ORG",
        scope_id=w.world.org.pk,
        slug=f"agents-{uuid4().hex}",
    )
    default_cluster = w.staging.tenant_cluster
    default_cluster.region = default_region
    default_cluster.lifecycle = "managed"
    default_cluster.save()
    # The registered agent has no AppEnvironment. Its task history must
    # remain independently readable when the default dispatch region changes.
    agent = Workload.objects.create(
        registered_app=w.world.platform_app, name="Agent", slug=f"agent-{uuid4().hex}", kind="agent"
    )
    west = operation_support.make_cluster(w.world, f"frozen-{uuid4().hex}")
    west.region = "us-west-2"
    west.save()
    rows = [
        AgentTask.objects.create(
            organization=w.world.org,
            agent_definition=agent,
            project=w.world.platform_project if owner == "project" else None,
            dispatch_target={"cluster_guid": str(guid)},
        )
        for guid in (west.guid, w.production.tenant_cluster.guid, uuid4())
    ]
    policy(w, action="agent.read", resource={"region": ["us-east-1"]})
    with as_tenant(w.world, w.user):
        assert list(agent_tasks(w.world.org.pk, Permission.AGENT_READ).values_list("pk", flat=True)) == [
            rows[0].pk
        ]
        listed = list(agent_workloads(w.world.org.pk).values_list("pk", flat=True))
        assert listed == ([agent.pk] if default_region == "us-west-2" else [])
        page = AgentsQuery().agent_tasks_page(w.info, org_id=str(w.world.org.guid))
        assert page.total_count == 1
        assert [str(row.id) for row in page.items] == [str(rows[0].guid)]
        assert Permission.AGENT_READ.value in resolve_effective_permissions_anywhere(get_current_tenant())
