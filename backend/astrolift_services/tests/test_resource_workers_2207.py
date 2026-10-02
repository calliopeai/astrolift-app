"""Reviewed enqueue timing and immutable worker placement on real PostgreSQL."""

import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import transaction
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError

from astrolift_services.models import ManagedService
from astrolift_services.schema.mutations.managed_services import (
    _start_project_service_deprovision,
    _start_project_service_provision,
    _start_service_update,
)
from astrolift_services.schema.resource_actions import reviewed_resource_dispatch
from astrolift_workflows.activities import managed_service_lifecycle as activities
from astrolift_workflows.inputs import (
    Actor,
    DeprovisionManagedServiceInput,
    ProvisionManagedServiceInput,
    UpdateManagedServiceInput,
)
from astrolift_workflows.managed_service_review import capture_reviewed_binding, reviewed_service_call
from astrolift_workflows.workflows.deprovision_managed_service import DeprovisionManagedServiceWorkflow
from astrolift_workflows.workflows.provision_managed_service import ProvisionManagedServiceWorkflow
from astrolift_workflows.workflows.update_managed_service import UpdateManagedServiceWorkflow
from core.testing.temporal import temporal_worker
from core.tests.utils.scope_world import make_cluster, make_info

from . import test_resource_reads_2207 as resource_fixtures

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world():
    return resource_fixtures.world.__wrapped__()


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    resource_fixtures.no_search.__wrapped__(monkeypatch)


@pytest.mark.parametrize("kind", ["provision", "update", "deprovision"])
def test_reviewed_enqueue_waits_for_commit_and_keeps_metadata_only(world, monkeypatch, kind):
    calls = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(enqueued=True, run_id="run2207"),
    )
    marker = reviewed_resource_dispatch.set(True)
    try:
        with transaction.atomic():
            if kind == "provision":
                _start_project_service_provision(make_info(world.user), world.service)
            elif kind == "update":
                _start_service_update(make_info(world.user), world.service)
            else:
                _start_project_service_deprovision(
                    make_info(world.user), world.service, delete_data=False, force_destroy=False
                )
            assert not calls
        assert len(calls) == 1
        payload = calls[0][1]["args"][0]
        proof = payload.reviewed_binding
        assert proof.service_guid == str(world.service.guid)
        assert proof.cluster_id == world.cluster.pk
        assert proof.accepted_service_version >= world.service.version
        assert "RESOURCE_PRIVATE_MARKER" not in json.dumps(dataclasses.asdict(payload))
        world.service.refresh_from_db()
        assert world.service.operation_run_id == "run2207"
    finally:
        reviewed_resource_dispatch.reset(marker)


def test_rollback_never_enqueues_and_failure_is_not_completion(world, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow", lambda *args, **kwargs: calls.append(args)
    )
    marker = reviewed_resource_dispatch.set(True)
    try:
        with pytest.raises(RuntimeError, match="rollback"), transaction.atomic():
            _start_project_service_provision(make_info(world.user), world.service)
            raise RuntimeError("rollback")
        assert not calls
        monkeypatch.setattr(
            "astrolift_workflows.client.start_workflow",
            lambda *args, **kwargs: SimpleNamespace(enqueued=False, run_id=None),
        )
        with pytest.raises(RuntimeError, match="could not be enqueued"), transaction.atomic():
            _start_project_service_provision(make_info(world.user), world.service)
        world.service.refresh_from_db()
        assert world.service.status == ManagedService.Status.FAILED
        assert world.service.operation_run_id == ""
    finally:
        reviewed_resource_dispatch.reset(marker)


@pytest.mark.parametrize("changed", ["owner", "cluster", "environment", "config", "parent", "deleted"])
def test_worker_refuses_changed_binding_before_body(world, changed):
    proof = capture_reviewed_binding(world.service.pk)
    if changed == "owner":
        world.service.project = world.platform_project
    elif changed == "cluster":
        world.service.tenant_cluster = make_cluster(world, "another-placement2207")
    elif changed == "environment":
        world.service.environment_name = "another-environment"
    elif changed == "config":
        world.service.config = {"size": "different"}
    elif changed == "parent":
        world.medops_project.name = "Updated owner"
        world.medops_project.save()
    elif changed == "deleted":
        world.service.soft_delete()
    if changed not in {"parent", "deleted"}:
        world.service.save()
    calls = []
    with pytest.raises(ApplicationError, match="context changed") as error:
        reviewed_service_call(lambda service_id: calls.append(service_id), world.service.pk, proof)
    assert error.value.non_retryable
    assert calls == []


def test_worker_status_and_handle_changes_do_not_break_same_binding_retries(world):
    proof = capture_reviewed_binding(world.service.pk)
    reviewed_service_call(
        activities._mark_status_sync, world.service.pk, proof, ManagedService.Status.PROVISIONING
    )
    world.service.refresh_from_db()
    world.service.backend_ref = "a-new-provider-handle"
    world.service.applied_config = {"size": "previous"}
    world.service.save()
    assert reviewed_service_call(lambda service_id: service_id, world.service.pk, proof) == world.service.pk


@pytest.mark.parametrize("operation", ["provision", "update", "deprovision"])
async def test_real_temporal_refuses_moved_owner_before_any_provider_lookup(world, temporal_env, operation):
    from asgiref.sync import sync_to_async

    proof = await sync_to_async(capture_reviewed_binding)(world.service.pk)
    world.service.project = world.platform_project
    await sync_to_async(world.service.save)()
    definitions = {
        "provision": (ProvisionManagedServiceWorkflow, ProvisionManagedServiceInput),
        "update": (UpdateManagedServiceWorkflow, UpdateManagedServiceInput),
        "deprovision": (DeprovisionManagedServiceWorkflow, DeprovisionManagedServiceInput),
    }
    workflow_class, input_class = definitions[operation]
    registered = [
        activities.mark_managed_service_provisioning,
        activities.mark_managed_service_deprovisioning,
        activities.provision_managed_service,
        activities.update_managed_service,
        activities.deprovision_managed_service,
        activities.check_managed_service_ready,
        activities.finalize_managed_service_provision,
        activities.finalize_managed_service_update,
        activities.finalize_managed_service_deletion,
        activities.mark_managed_service_failed,
        activities.bounce_workloads_bound_to_managed_service,
    ]
    with patch("astrolift_drivers.registry.plugins.get") as provider:
        async with temporal_worker(temporal_env, workflows=[workflow_class], activities=registered):
            with pytest.raises(WorkflowFailureError):
                await temporal_env.client.execute_workflow(
                    workflow_class.run,
                    input_class(
                        managed_service_id=world.service.pk,
                        actor=Actor(kind="system"),
                        reviewed_binding=proof,
                    ),
                    id=f"resource-reviewed-{uuid4()}",
                    task_queue="astrolift-test",
                )
        provider.assert_not_called()
    await sync_to_async(world.service.refresh_from_db)()
    assert world.service.status == ManagedService.Status.PENDING


def test_waiting_worker_rechecks_moved_owner_under_real_postgres_locks(world, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

    from django.db import close_old_connections, connection

    from astrolift_workflows import managed_service_review

    proof = capture_reviewed_binding(world.service.pk)
    worker_pid = []
    entered = threading.Event()
    original_atomic = transaction.atomic
    calls = []

    @contextmanager
    def traced_atomic(*args, **kwargs):
        with original_atomic(*args, **kwargs):
            if threading.current_thread().name.startswith("resource-worker2207"):
                worker_pid.append(connection.connection.get_backend_pid())
                entered.set()
            yield

    monkeypatch.setattr(managed_service_review.transaction, "atomic", traced_atomic)

    def invoke():
        close_old_connections()
        try:
            return reviewed_service_call(lambda service_id: calls.append(service_id), world.service.pk, proof)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="resource-worker2207") as executor:
        with original_atomic():
            ManagedService.objects.select_for_update().get(pk=world.service.pk)
            future = executor.submit(invoke)
            assert entered.wait(10)
            deadline = time.monotonic() + 10
            while True:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", worker_pid)
                    state = cursor.fetchone()
                if state and state[0] == "Lock":
                    break
                assert time.monotonic() < deadline
                time.sleep(0.01)
            ManagedService.objects.filter(pk=world.service.pk).update(project=world.platform_project)
        with pytest.raises(ApplicationError, match="context changed"):
            future.result(timeout=10)
    assert calls == []


async def test_real_temporal_reviewed_provision_survives_its_own_status_and_binding_writes(temporal_env):
    from asgiref.sync import sync_to_async

    from astrolift_workflows.tests.test_provision_managed_service_temporal import (
        _FakeAuroraLikeDriver,
        _make_service,
    )

    def shared_service():
        service = _make_service()
        service.project = service.registered_app.project
        service.tenant_cluster = service.app_environment.tenant_cluster
        service.registered_app = None
        service.app_environment = None
        service.save()
        return service, capture_reviewed_binding(service.pk)

    service, proof = await sync_to_async(shared_service)()
    _FakeAuroraLikeDriver.calls = []
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=_FakeAuroraLikeDriver),
        patch("core.cluster_observability.managed_config_for", return_value={}),
    ):
        async with temporal_worker(
            temporal_env,
            workflows=[ProvisionManagedServiceWorkflow],
            activities=[
                activities.mark_managed_service_provisioning,
                activities.provision_managed_service,
                activities.check_managed_service_ready,
                activities.finalize_managed_service_provision,
                activities.mark_managed_service_failed,
                activities.bounce_workloads_bound_to_managed_service,
            ],
        ):
            result = await temporal_env.client.execute_workflow(
                ProvisionManagedServiceWorkflow.run,
                ProvisionManagedServiceInput(
                    managed_service_id=service.pk, actor=Actor(kind="system"), reviewed_binding=proof
                ),
                id=f"reviewed-provision-{uuid4()}",
                task_queue="astrolift-test",
            )
    assert result.ok
    assert _FakeAuroraLikeDriver.calls == ["provision", "binding"]
    await sync_to_async(service.refresh_from_db)()
    assert service.status == ManagedService.Status.ACTIVE
