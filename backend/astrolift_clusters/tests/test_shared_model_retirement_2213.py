"""Shared deployments remain manageable until model cleanup is confirmed."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import close_old_connections, connection, transaction

from astrolift_clusters.models import TenantCluster
from astrolift_clusters.schema import mutations
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    DecommissionClusterInputType,
    UnregisterTenantClusterInput,
)
from astrolift_graphql import GUID
from astrolift_identity.models import Member
from astrolift_services.cluster_retirement import MODEL_CLEANUP_REQUIRED
from astrolift_services.models import ManagedService
from astrolift_workflows.activities.cluster_management import (
    _ensure_cluster_drained_sync,
    _mark_decommissioning_sync,
)
from core.cluster_management import ClusterManagementError
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *_: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda *_: None))
    w = ScopeWorld("modelretirement2213")
    w.user = make_user("modelretirement2213")
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    w.cluster = make_cluster(w, "modelretirement2213")
    w.cluster.lifecycle = "managed"
    w.cluster.save()
    bind_role(
        w.user,
        permissions=[Permission.CLUSTER_UNREGISTER],
        kind="ORG",
        scope_id=w.org.pk,
        slug="modelretirement2213",
    )
    w.queued = []
    monkeypatch.setattr(mutations, "start_workflow", lambda *args, **kwargs: w.queued.append((args, kwargs)))
    return w


def create_model(w, status="active"):
    return ManagedService.objects.create(
        organization=w.org,
        tenant_cluster=w.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="app-free model",
        status=status,
    )


def retire(w, action):
    with tenant_context(TenantContext(organization_id=w.org.pk, actor_user_id=w.user.pk)):
        if action == "unregister":
            return ClustersMutation().unregister_tenant_cluster(
                make_info(w.user), UnregisterTenantClusterInput(id=GUID(str(w.cluster.guid)))
            )
        return ClustersMutation().decommission_cluster(
            make_info(w.user), DecommissionClusterInputType(cluster_id=GUID(str(w.cluster.guid)))
        )


@pytest.mark.parametrize("action", ["unregister", "decommission"])
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("status", ["pending", "active", "failed"])
def test_app_free_model_blocks_cluster_retirement_before_writes(world, action, shared, status):
    if shared:
        world.cluster.organization = None
        world.cluster.save()
        world.user.is_superuser = True
        world.user.save()
    model = create_model(world, status)
    result = retire(world, action)
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    assert result.errors[0].message == MODEL_CLEANUP_REQUIRED
    world.cluster.refresh_from_db()
    assert world.cluster.deleted_at is None and world.cluster.lifecycle == "managed"
    assert world.queued == []
    assert model.attachments.count() == 0
    assert model.registered_app_id is model.project_id is model.app_environment_id is None


@pytest.mark.parametrize("action", ["unregister", "decommission"])
@pytest.mark.parametrize("removed_model", [False, True])
def test_no_live_shared_model_preserves_cluster_retirement(world, action, removed_model):
    if removed_model:
        create_model(world).soft_delete()
    result = retire(world, action)
    assert result.ok
    world.cluster.refresh_from_db()
    if action == "unregister":
        assert world.cluster.deleted_at is not None
        assert world.queued == []
    else:
        assert world.cluster.lifecycle == "decommissioning"
        assert len(world.queued) == 1


@pytest.mark.parametrize("activity", [_ensure_cluster_drained_sync, _mark_decommissioning_sync])
def test_direct_worker_boundary_refuses_app_free_models(world, activity):
    create_model(world)
    with pytest.raises(ClusterManagementError, match="Remove all shared model deployments"):
        activity(world.cluster.pk)
    world.cluster.refresh_from_db()
    assert world.cluster.lifecycle == "managed"


def test_worker_rechecks_model_creation_after_initial_drained_read(world):
    assert _ensure_cluster_drained_sync(world.cluster.pk) == 0
    create_model(world)
    with pytest.raises(ClusterManagementError, match="Remove all shared model deployments"):
        _mark_decommissioning_sync(world.cluster.pk)
    world.cluster.refresh_from_db()
    assert world.cluster.lifecycle == "managed"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action", ["unregister", "decommission"])
def test_retirement_serializes_with_accepted_model_creation(world, action):
    waiting = Event()

    def concurrent_retirement():
        close_old_connections()

        def observed_lock(execute, sql, params, many, context):
            if "FOR UPDATE" in sql and TenantCluster._meta.db_table in sql:
                waiting.set()
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(observed_lock):
                return retire(world, action)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with transaction.atomic():
            TenantCluster.objects.select_for_update().get(pk=world.cluster.pk)
            future = executor.submit(concurrent_retirement)
            assert waiting.wait(timeout=5), "Retirement did not attempt the canonical cluster lock."
            assert not future.done()
            create_model(world)
        result = future.result(timeout=10)
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    world.cluster.refresh_from_db()
    assert world.cluster.deleted_at is None and world.cluster.lifecycle == "managed"
    assert world.queued == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action", ["unregister", "decommission"])
@pytest.mark.parametrize(
    "change", ["grant", "actor", "revoked_token", "scopes", "team", "expiry", "operator", "region"]
)
def test_authority_refreshes_after_cluster_lock_wait(world, action, change):
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_identity import abac
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken, Policy, RoleBinding

    token = None
    if change in {"revoked_token", "scopes", "team", "expiry"}:
        token = ApiToken.objects.create(
            user=world.user, organization=world.org, name="retire", token_hash="test-only", scopes=["admin"]
        )
    if change == "operator":
        world.cluster.organization = None
        world.cluster.save()
        world.user.is_superuser = True
        world.user.save()
    if change == "region":
        world.cluster.region = "us-west-2"
        world.cluster.save()
        Policy.objects.create(
            organization=world.org,
            name="deny-east",
            slug="deny-east",
            scope_level="ORG",
            scope_id=world.org.pk,
            effect="DENY",
            action_pattern="cluster.unregister",
            resource_pattern={"region": ["us-east-1"]},
        )
    waiting = Event()

    def concurrent_retirement():
        close_old_connections()
        marker = set_current_api_token(token) if token else None

        def lock_wait(execute, sql, params, many, context):
            if "FOR UPDATE" in sql and TenantCluster._meta.db_table in sql:
                waiting.set()
            return execute(sql, params, many, context)

        try:
            with (
                abac.request_attributes(
                    abac.RequestAttributes(actor_user_id=world.user.pk, region="us-west-2")
                ),
                connection.execute_wrapper(lock_wait),
            ):
                return retire(world, action)
        finally:
            if marker is not None:
                reset_current_api_token(marker)
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with transaction.atomic():
            TenantCluster.objects.select_for_update().get(pk=world.cluster.pk)
            future = executor.submit(concurrent_retirement)
            assert waiting.wait(5) and not future.done()
            if change == "grant":
                RoleBinding.objects.filter(user=world.user).update(deleted_at=timezone.now())
            elif change == "actor":
                type(world.user).objects.filter(pk=world.user.pk).update(is_active=False)
            elif change == "revoked_token":
                ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
            elif change == "scopes":
                ApiToken.objects.filter(pk=token.pk).update(scopes=["read:clusters"])
            elif change == "team":
                ApiToken.objects.filter(pk=token.pk).update(team_id=world.medops.pk)
            elif change == "expiry":
                ApiToken.objects.filter(pk=token.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
            elif change == "operator":
                type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
            else:
                TenantCluster.objects.filter(pk=world.cluster.pk).update(region="us-east-1")
        result = future.result(timeout=15)
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    world.cluster.refresh_from_db()
    assert world.cluster.deleted_at is None and world.cluster.lifecycle == "managed"
    assert world.queued == []


@pytest.mark.parametrize("action", ["bring", "refresh"])
@pytest.mark.parametrize("state", ["decommissioning", "decommissioned"])
def test_management_api_cannot_reopen_retiring_cluster(world, action, state):
    from astrolift_clusters.schema.mutations import (
        BringClusterIntoManagementInputType,
        RefreshClusterManagementInputType,
    )

    bind_role(
        world.user, permissions=[Permission.CLUSTER_MANAGE], kind="ORG", scope_id=world.org.pk, slug="manage"
    )
    world.cluster.lifecycle = state
    world.cluster.save()
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        if action == "bring":
            result = ClustersMutation().bring_cluster_into_management(
                make_info(world.user),
                BringClusterIntoManagementInputType(cluster_id=GUID(str(world.cluster.guid))),
            )
        else:
            result = ClustersMutation().refresh_cluster_management(
                make_info(world.user),
                RefreshClusterManagementInputType(cluster_id=GUID(str(world.cluster.guid))),
            )
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    world.cluster.refresh_from_db()
    assert world.cluster.lifecycle == state and world.queued == []


@pytest.mark.parametrize("state", ["decommissioning", "decommissioned"])
@pytest.mark.parametrize("activity_name", ["_mark_managing_sync", "_mark_managed_sync"])
def test_stale_management_activity_cannot_reopen_retirement(world, state, activity_name):
    from astrolift_workflows.activities import cluster_management

    world.cluster.lifecycle = state
    world.cluster.save()
    with pytest.raises(ClusterManagementError, match="cannot return"):
        getattr(cluster_management, activity_name)(world.cluster.pk)
    world.cluster.refresh_from_db()
    assert world.cluster.lifecycle == state


@pytest.mark.parametrize("boundary", ["rbac", "infra"])
@pytest.mark.parametrize("changed", ["model", "environment", "managed", "error", "deleted"])
def test_destructive_boundary_rechecks_current_locked_cluster(world, monkeypatch, boundary, changed):
    from astrolift_workflows.activities import cluster_management

    world.cluster.lifecycle = "decommissioning"
    world.cluster.save()
    if changed == "model":
        create_model(world)
    elif changed == "environment":
        from astrolift_lifecycle.models import AppEnvironment

        AppEnvironment.objects.create(
            registered_app=world.medops_app, tenant_cluster=world.cluster, name="prod"
        )
    elif changed == "deleted":
        world.cluster.soft_delete()
    else:
        world.cluster.lifecycle = changed
        world.cluster.save()
    calls = []
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda *_: calls.append("driver"))
    monkeypatch.setattr(
        "core.cluster_management.teardown_cluster_dispatch", lambda **_: calls.append("infra")
    )
    with pytest.raises(ClusterManagementError):
        if boundary == "rbac":
            cluster_management._remove_platform_rbac_sync(world.cluster.pk)
        else:
            cluster_management._teardown_cluster_infra_sync(world.cluster.pk, True)
    assert calls == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("boundary", ["rbac", "infra"])
def test_destructive_call_holds_placement_lock_until_completion(world, monkeypatch, boundary):
    from types import SimpleNamespace

    from astrolift_workflows.activities import cluster_management

    world.cluster.lifecycle = "decommissioning"
    world.cluster.save()
    calling, finish, waiting = Event(), Event(), Event()

    def driver_call(*args, **kwargs):
        calling.set()
        assert finish.wait(10)
        return SimpleNamespace(success=True, deleted=[], skipped=[], messages=[])

    monkeypatch.setattr(
        "core.cluster_management._driver_for_cluster",
        lambda *_: SimpleNamespace(delete_namespace=driver_call),
    )
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda *_: SimpleNamespace(slug=world.cluster.slug)
    )
    monkeypatch.setattr("core.cluster_management.teardown_cluster_dispatch", driver_call)

    def destruction():
        close_old_connections()
        try:
            if boundary == "rbac":
                return cluster_management._remove_platform_rbac_sync(world.cluster.pk)
            return cluster_management._teardown_cluster_infra_sync(world.cluster.pk, True)
        finally:
            close_old_connections()

    def management():
        close_old_connections()

        def lock_wait(execute, sql, params, many, context):
            if "FOR UPDATE" in sql and TenantCluster._meta.db_table in sql:
                waiting.set()
            return execute(sql, params, many, context)

        try:
            with (
                connection.execute_wrapper(lock_wait),
                pytest.raises(ClusterManagementError, match="cannot return"),
            ):
                cluster_management._mark_managed_sync(world.cluster.pk)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(destruction)
        assert calling.wait(5)
        second = executor.submit(management)
        try:
            assert waiting.wait(5) and not second.done()
        finally:
            finish.set()
        first.result(timeout=15)
        second.result(timeout=15)
    world.cluster.refresh_from_db()
    assert world.cluster.lifecycle == "decommissioning"


@pytest.mark.parametrize("retirement", [False, True])
def test_failure_source_preserves_retiring_state_until_actual_retirement_failure(world, retirement):
    from astrolift_workflows.activities.cluster_management import _mark_error_sync

    world.cluster.lifecycle = "decommissioning"
    world.cluster.save()
    _mark_error_sync(world.cluster.pk, "bounded failure", retirement)
    world.cluster.refresh_from_db()
    assert world.cluster.lifecycle == ("error" if retirement else "decommissioning")
    assert world.cluster.last_management_error == ("bounded failure" if retirement else "")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("source", ["retirement", "management"])
async def test_actual_failure_source_keeps_two_argument_history_and_replays(world, temporal_env, source):
    from uuid import uuid4

    from asgiref.sync import sync_to_async
    from temporalio.worker import Replayer

    from astrolift_workflows.activities import cluster_management
    from astrolift_workflows.inputs import Actor, BringClusterIntoManagementInput, DecommissionClusterInput
    from astrolift_workflows.workflows.bring_cluster_into_management import BringClusterIntoManagementWorkflow
    from astrolift_workflows.workflows.decommission_cluster import DecommissionClusterWorkflow
    from core.testing.temporal import temporal_worker

    await sync_to_async(TenantCluster.objects.filter(pk=world.cluster.pk).update)(lifecycle="decommissioning")
    await sync_to_async(create_model)(world)
    identity = f"cluster-retirement-2213-{uuid4().hex}"
    workflow_cls = (
        DecommissionClusterWorkflow if source == "retirement" else BringClusterIntoManagementWorkflow
    )
    input_cls = DecommissionClusterInput if source == "retirement" else BringClusterIntoManagementInput
    first_activity = (
        cluster_management.ensure_cluster_drained
        if source == "retirement"
        else cluster_management.mark_managing
    )
    async with temporal_worker(
        temporal_env,
        task_queue=identity,
        workflows=[workflow_cls],
        activities=[first_activity, cluster_management.mark_error],
    ):
        result = await temporal_env.client.execute_workflow(
            workflow_cls.run,
            input_cls(cluster_id=world.cluster.pk, actor=Actor(kind="system")),
            id=identity,
            task_queue=identity,
        )
        history = await temporal_env.client.get_workflow_handle(identity).fetch_history()
    assert not result.ok
    await sync_to_async(world.cluster.refresh_from_db)()
    if source == "retirement":
        assert world.cluster.lifecycle == "error"
        assert world.cluster.last_management_error.startswith("decommission refused:")
    else:
        assert world.cluster.lifecycle == "decommissioning" and not world.cluster.last_management_error
    error_calls = [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
        and event.activity_task_scheduled_event_attributes.activity_type.name
        == "astrolift.cluster.mark_error"
    ]
    assert len(error_calls) == 1 and len(error_calls[0].input.payloads) == 2
    await Replayer(workflows=[workflow_cls]).replay_workflow(history)
