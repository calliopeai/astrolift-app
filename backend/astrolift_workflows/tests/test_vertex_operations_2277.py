"""Actual PostgreSQL commits/locks around SDK-faithful Vertex operations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from asgiref.sync import sync_to_async
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.utils import timezone
from gcp.managed.model_endpoint_vertex import VertexAIEndpointConfig
from google.cloud import aiplatform_v1
from temporalio.exceptions import ApplicationError

from astrolift_services.models import ManagedService
from astrolift_workflows.managed_service_review import capture_reviewed_binding, reviewed_service_call
from astrolift_workflows.vertex_managed_service import (
    JOURNAL_KEY,
    _iteration,
    _prepare,
    _submit,
    vertex_read_config,
)
from core.testing.temporal import temporal_worker
from core.tests.utils.scope_world import ScopeWorld, make_cluster
from providers.tests.gcp.test_vertex_resource_operations_2277 import ENDPOINT_NAME, PARENT, VertexWire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch):
    w = ScopeWorld("vertex2277")
    w.cluster = make_cluster(w, "vertex2277")
    w.cluster.provider_plugin.slug = "gcp"
    w.cluster.provider_plugin.save()
    w.service = ManagedService.objects.create(
        project=w.medops_project,
        tenant_cluster=w.cluster,
        kind="model_endpoint",
        variant="vertex_ai",
        name="human-name",
        config={"model_artifact": f"{PARENT}/models/617492583"},
        operation_kind="provision",
        operation_workflow_id="ProvisionManagedServiceWorkflow-fixture",
        operation_started_at=timezone.now(),
        lifecycle_policy={"preserve_unrelated": {"retention": 17}},
    )
    w.wire = VertexWire()
    w.config = VertexAIEndpointConfig(project_id="fixture-project", region="us-central1")
    monkeypatch.setattr(aiplatform_v1, "EndpointServiceClient", lambda **_: w.wire)
    monkeypatch.setattr(aiplatform_v1, "ModelServiceClient", lambda **_: object())
    monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: w.config)
    w.binding = capture_reviewed_binding(w.service.pk)
    return w


def run(w, action="provision", **flags):
    return _iteration(
        w.service.pk, w.binding, action, flags.get("delete_data", False), flags.get("force_destroy", False)
    )


def complete(w):
    for _ in range(5):
        result = run(w)
        if result.get("complete"):
            return result
    raise AssertionError("fixture did not finish bounded operation steps")


def test_fresh_instances_persist_real_lros_and_resource_ids(world):
    w = world
    result = complete(w)
    w.service.refresh_from_db()
    state = w.service.lifecycle_policy[JOURNAL_KEY]
    assert state["endpoint"] == ENDPOINT_NAME and state["deployed_model_id"] == "851729643"
    assert state["steps"]["create"]["operation"] != state["steps"]["deploy"]["operation"]
    assert state["context"]["service"] == str(w.service.guid)
    assert state["context"]["organization"] == str(w.org.guid)
    assert w.service.lifecycle_policy["preserve_unrelated"] == {"retention": 17}
    assert result["complete"]
    assert run(w)["complete"]
    assert [name for name, _ in w.wire.calls if name in {"create", "deploy"}] == ["create", "deploy"]


def test_reservation_survives_effect_transaction_rollback_and_lost_response(world):
    w = world
    reservation = reviewed_service_call(
        _prepare, w.service.pk, w.binding, w.binding, "provision", False, False
    )
    original = w.wire.create_endpoint

    def lost(**kwargs):
        original(**kwargs)
        raise TimeoutError("reply lost after remote acceptance")

    w.wire.create_endpoint = lost
    with pytest.raises(ValueError, match="outcome unknown"):
        reviewed_service_call(
            _submit, w.service.pk, w.binding, w.binding, "provision", False, False, reservation
        )
    w.service.refresh_from_db()
    assert w.service.lifecycle_policy[JOURNAL_KEY]["steps"]["create"]["state"] == "reserved"
    assert "operation" not in w.service.lifecycle_policy[JOURNAL_KEY]["steps"]["create"]
    result = run(w)
    assert result["refused"] and "unknown" in result["message"]
    w.service.refresh_from_db()
    assert w.service.lifecycle_policy[JOURNAL_KEY]["endpoint"] == ENDPOINT_NAME
    assert [name for name, _ in w.wire.calls if name in {"create", "deploy"}] == ["create"]


def test_outer_transaction_cannot_make_reservation_appear_durable(world):
    with transaction.atomic(), pytest.raises(ValueError, match="outer transaction"):
        run(world)
    world.service.refresh_from_db()
    assert JOURNAL_KEY not in world.service.lifecycle_policy and not world.wire.calls


def test_real_deferred_commit_failure_cannot_erase_committed_send_reservation(world):
    w = world
    reservation = reviewed_service_call(
        _prepare, w.service.pk, w.binding, w.binding, "provision", False, False
    )
    table = ManagedService._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute("""CREATE FUNCTION vertex2277_receipt_fault() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.lifecycle_policy->'vertex_operation'->'steps'->'create'->>'state' = 'submitted'
            THEN RAISE EXCEPTION 'fixture receipt commit failure'; END IF; RETURN NEW; END $$""")
        cursor.execute(f"""CREATE CONSTRAINT TRIGGER vertex2277_receipt_fault AFTER UPDATE ON {table}
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION vertex2277_receipt_fault()""")
    try:
        with pytest.raises(DatabaseError, match="fixture receipt commit failure"):
            reviewed_service_call(
                _submit, w.service.pk, w.binding, w.binding, "provision", False, False, reservation
            )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER vertex2277_receipt_fault ON {table}")
            cursor.execute("DROP FUNCTION vertex2277_receipt_fault()")
    w.service.refresh_from_db()
    assert w.service.lifecycle_policy[JOURNAL_KEY]["steps"]["create"]["state"] == "reserved"
    assert run(w)["refused"]
    assert [name for name, _ in w.wire.calls if name in {"create", "deploy"}] == ["create"]


@pytest.mark.parametrize("action", ["provision", "deprovision"])
def test_two_concurrent_attempts_send_each_phase_once(world, action):
    w = world
    if action == "deprovision":
        complete(w)
        change_operation(w, action)
        w.wire.calls.clear()

    def invoke():
        return run(w, action, force_destroy=action == "deprovision")

    def attempt():
        close_old_connections()
        try:
            return invoke()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))
    assert any(result.get("pending") for result in results)
    assert all(result.get("pending") or result.get("complete") or result.get("refused") for result in results)
    for _ in range(5):
        if invoke().get("complete"):
            break
    else:
        raise AssertionError("concurrent lifecycle did not complete")
    assert [name for name, _ in w.wire.calls if name in {"create", "deploy", "undeploy", "delete"}] == (
        ["create", "deploy"] if action == "provision" else ["undeploy", "delete"]
    )


@pytest.mark.parametrize("change", ["config", "cluster", "owner", "operation"])
def test_changed_reviewed_target_after_reservation_has_zero_cloud_writes(world, change):
    w = world
    reservation = reviewed_service_call(
        _prepare, w.service.pk, w.binding, w.binding, "provision", False, False
    )
    if change == "config":
        w.service.config = {"model_artifact": f"{PARENT}/models/changed"}
        w.service.save()
    elif change == "cluster":
        w.cluster.provider_config = {"region": "europe-west1"}
        w.cluster.save()
    elif change == "owner":
        w.service.project = w.platform_project
        w.service.save()
    else:
        w.service.operation_workflow_id = "different-operation"
        w.service.save()
    with pytest.raises(ApplicationError, match="context changed"):
        reviewed_service_call(
            _submit, w.service.pk, w.binding, w.binding, "provision", False, False, reservation
        )
    assert all(name in {"list", "get"} for name, _ in w.wire.calls)


def test_unreviewed_legacy_activity_cannot_allocate(world):
    w = world
    with pytest.raises(ValueError, match="legacy direct work refused"):
        _iteration(w.service.pk, None, "provision", False, False)
    assert all(name in {"list", "get"} for name, _ in w.wire.calls)


def test_read_configuration_pins_owner_provider_region_and_desired_request(world):
    w = world
    complete(w)
    w.service.refresh_from_db()
    # Use the actual class imported by worker/provider resolution, not the duplicate package alias.
    from gcp.managed.model_endpoint_vertex import VertexAIEndpointConfig as Config

    config = vertex_read_config(w.service, Config(project_id="fixture-project", region="us-central1"))
    assert config.operation_state["endpoint"] == ENDPOINT_NAME
    for changed in (
        Config(project_id="foreign", region="us-central1"),
        Config(project_id="fixture-project", region="europe-west1"),
    ):
        with pytest.raises(ValueError, match="placement/configuration changed"):
            vertex_read_config(w.service, changed)


def change_operation(w, kind, *, config=None):
    w.service.refresh_from_db()
    w.service.backend_ref = f"model_endpoint/{ENDPOINT_NAME}"
    w.service.operation_kind = kind
    w.service.operation_workflow_id = f"{kind}-fixture-{w.service.guid}"
    w.service.operation_started_at = timezone.now()
    if config is not None:
        w.service.config = config
    w.service.save()
    w.binding = capture_reviewed_binding(w.service.pk)


def test_fresh_update_and_removal_retain_actual_resource_and_lro_identities(world):
    w = world
    complete(w)
    change_operation(
        w, "update", config={"model_artifact": f"{PARENT}/models/617492583", "min_replica_count": 2}
    )
    for _ in range(5):
        result = run(w, "update")
        if result.get("complete"):
            break
    assert result["complete"]
    change_operation(w, "deprovision")
    for _ in range(5):
        result = run(w, "deprovision", force_destroy=True)
        if result.get("complete"):
            break
    assert result["complete"]
    w.service.refresh_from_db()
    steps = w.service.lifecycle_policy[JOURNAL_KEY]["steps"]
    assert steps["undeploy"]["state"] == steps["delete"]["state"] == "complete"
    assert [
        name for name, _ in w.wire.calls if name in {"create", "deploy", "mutate", "undeploy", "delete"}
    ] == [
        "create",
        "deploy",
        "mutate",
        "undeploy",
        "delete",
    ]
    assert run(w, "deprovision", force_destroy=True)["complete"]


@pytest.mark.parametrize("flag", ["is_active", "is_enabled"])
def test_disabled_provider_or_cluster_refuses_reserved_write_even_without_version_bump(world, flag):
    w = world
    reservation = reviewed_service_call(
        _prepare, w.service.pk, w.binding, w.binding, "provision", False, False
    )
    owner = w.cluster if flag == "is_active" else w.cluster.provider_plugin
    type(owner).all_objects.filter(pk=owner.pk).update(**{flag: False})
    with pytest.raises(ValueError, match="unavailable"):
        reviewed_service_call(
            _submit, w.service.pk, w.binding, w.binding, "provision", False, False, reservation
        )
    assert all(name in {"get", "list"} for name, _ in w.wire.calls)


def test_legacy_onboarding_refuses_instead_of_reporting_app_resource_ready(world):
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_workflows.activities.app_lifecycle import _provision_managed_services_initial_sync
    from core.app_deploy import AppDeployError

    w = world
    env = AppEnvironment.objects.create(
        registered_app=w.medops_app, tenant_cluster=w.cluster, name="production"
    )
    ManagedService.objects.create(
        registered_app=w.medops_app,
        app_environment=env,
        kind="model_endpoint",
        variant="vertex_ai",
        name="legacy",
        config={"model_artifact": f"{PARENT}/models/617492583"},
    )
    with pytest.raises(AppDeployError, match="legacy onboarding cannot provision"):
        _provision_managed_services_initial_sync(w.medops_app.pk, env.pk)
    assert not w.wire.calls


async def test_actual_temporal_activity_lifecycle_and_history_replay(world, temporal_env, monkeypatch):
    from temporalio.worker import Replayer

    from astrolift_workflows.activities import managed_service_lifecycle as activities
    from astrolift_workflows.inputs import (
        Actor,
        DeprovisionManagedServiceInput,
        ProvisionManagedServiceInput,
        UpdateManagedServiceInput,
    )
    from astrolift_workflows.workflows.deprovision_managed_service import DeprovisionManagedServiceWorkflow
    from astrolift_workflows.workflows.provision_managed_service import ProvisionManagedServiceWorkflow
    from astrolift_workflows.workflows.update_managed_service import UpdateManagedServiceWorkflow

    w = world
    monkeypatch.setattr("astrolift_workflows.vertex_managed_service._POLL_SECONDS", 0)
    registered = [
        activities.mark_managed_service_provisioning,
        activities.provision_managed_service,
        activities.check_managed_service_ready,
        activities.finalize_managed_service_provision,
        activities.mark_managed_service_failed,
        activities.bounce_workloads_bound_to_managed_service,
        activities.update_managed_service,
        activities.finalize_managed_service_update,
        activities.mark_managed_service_deprovisioning,
        activities.deprovision_managed_service,
        activities.finalize_managed_service_deletion,
    ]
    workflows = [
        ProvisionManagedServiceWorkflow,
        UpdateManagedServiceWorkflow,
        DeprovisionManagedServiceWorkflow,
    ]
    histories = []
    async with temporal_worker(temporal_env, workflows=workflows, activities=registered):
        handle = await temporal_env.client.start_workflow(
            ProvisionManagedServiceWorkflow.run,
            ProvisionManagedServiceInput(
                managed_service_id=w.service.pk, actor=Actor(kind="user"), reviewed_binding=w.binding
            ),
            id=f"vertex-operation-fixture-{w.service.guid}",
            task_queue="astrolift-test",
        )
        result = await handle.result()
        assert result.ok
        histories.append(await handle.fetch_history())
        await sync_to_async(w.service.refresh_from_db)()
        assert w.service.backend_ref == f"model_endpoint/{ENDPOINT_NAME}"
        assert w.service.status == ManagedService.Status.ACTIVE

        await sync_to_async(change_operation)(
            w, "update", config={**w.service.config, "min_replica_count": 2}
        )
        update = await temporal_env.client.start_workflow(
            UpdateManagedServiceWorkflow.run,
            UpdateManagedServiceInput(
                managed_service_id=w.service.pk, actor=Actor(kind="user"), reviewed_binding=w.binding
            ),
            id=f"vertex-update-fixture-{w.service.guid}",
            task_queue="astrolift-test",
        )
        assert (await update.result()).ok
        histories.append(await update.fetch_history())
        await sync_to_async(w.service.refresh_from_db)()
        assert w.service.applied_config["min_replica_count"] == 2
        assert w.service.status == ManagedService.Status.ACTIVE

        await sync_to_async(change_operation)(w, "deprovision")
        delete = await temporal_env.client.start_workflow(
            DeprovisionManagedServiceWorkflow.run,
            DeprovisionManagedServiceInput(
                managed_service_id=w.service.pk,
                actor=Actor(kind="user"),
                reviewed_binding=w.binding,
                force_destroy=True,
            ),
            id=f"vertex-delete-fixture-{w.service.guid}",
            task_queue="astrolift-test",
        )
        assert (await delete.result()).ok
        histories.append(await delete.fetch_history())
    replayer = Replayer(workflows=workflows)
    for history in histories:
        await replayer.replay_workflow(history)
    await sync_to_async(w.service.refresh_from_db)()
    assert w.service.deleted_at is not None
    assert [
        name for name, _ in w.wire.calls if name in {"create", "deploy", "mutate", "undeploy", "delete"}
    ] == ["create", "deploy", "mutate", "undeploy", "delete"]


async def test_pending_activity_timeout_retains_receipt_without_false_ready(world, monkeypatch):
    from astrolift_workflows.vertex_managed_service import vertex_lifecycle_call

    w = world
    w.wire.create_pending = True
    await sync_to_async(run)(w)
    monkeypatch.setattr("astrolift_workflows.vertex_managed_service._MAX_SECONDS", 0)
    with pytest.raises(ApplicationError, match="still pending"):
        await vertex_lifecycle_call(w.service.pk, w.binding, "provision")
    await sync_to_async(w.service.refresh_from_db)()
    assert w.service.lifecycle_policy[JOURNAL_KEY]["steps"]["create"]["state"] == "submitted"
    assert w.service.backend_ref == "" and w.service.status != ManagedService.Status.ACTIVE
    assert [name for name, _ in w.wire.calls if name in {"create", "deploy"}] == ["create"]
