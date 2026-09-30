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
