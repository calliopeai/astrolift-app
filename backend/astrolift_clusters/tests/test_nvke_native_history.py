from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from nvke.client import NvkeError
from nvke.native_workflows import activity_names
from temporalio import activity, workflow
from temporalio.api.common.v1 import ActivityType, Payload, Payloads, WorkflowExecution
from temporalio.api.enums.v1 import EventType, ResetReapplyType
from temporalio.api.history.v1 import ActivityTaskScheduledEventAttributes, HistoryEvent
from temporalio.api.workflowservice.v1 import ResetWorkflowExecutionRequest
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from astrolift_clusters import nvke_native_history as history
from astrolift_clusters.nvke_sites import NvkeSiteError
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_native_operations import prepare
from astrolift_clusters.tests.test_nvke_operations import configured as _configured
from astrolift_clusters.tests.test_nvke_operations import world as _world
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context
from core.testing.temporal import temporal_test_env

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return _world.__wrapped__()


@pytest.fixture
def configured(world):
    return _configured.__wrapped__(world)


def projection_event(name):
    return HistoryEvent(
        event_id=9007199254740993,
        event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,
        activity_task_scheduled_event_attributes=ActivityTaskScheduledEventAttributes(
            activity_type=ActivityType(name=name),
            workflow_task_completed_event_id=9007199254740992,
            input=Payloads(payloads=[Payload(data=b"PRIVATE_NATIVE_KEY")]),
        ),
    )


def test_projected_native_history_has_exact_ids_and_never_payload_or_unknown_names():
    assert "PrepareEtcdEncryptionKeyRotation" in activity_names()
    public = history.project(projection_event("PrepareEtcdEncryptionKeyRotation"))
    assert public["event_id"] == "9007199254740993"
    assert public["workflow_task_completed_event_id"] == "9007199254740992"
    assert public["activity_type"] == "PrepareEtcdEncryptionKeyRotation"
    assert "PRIVATE" not in str(public)
    assert history.project(projection_event("PRIVATE_UNKNOWN_ACTIVITY"))["activity_type"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"expected_actor_id": 0},
        {"expected_authority_version": 0},
        {"expected_source_version": 0},
        {"workflow": "PRIVATE_UNKNOWN_WORKFLOW"},
        {"limit": 0},
        {"after": "PRIVATE_FORGED_CURSOR"},
    ],
)
def test_invalid_native_history_review_never_opens_credentials(configured, monkeypatch, change):
    authority, source, _ = prepare(
        configured,
        observation={
            "conditions": {
                "updateInProgress": {"type": "etcd_key_rotation"},
                "etcdKeyRotation": {"workflowID": "rotation-owner", "state": "failed", "phase": "rewrite"},
            }
        },
    )

    def no_credentials(_row):
        pytest.fail("invalid native history review must not decrypt credentials or connect")

    monkeypatch.setattr(history.native, "credentials_for", no_credentials)
    with pytest.raises((NvkeError, NvkeSiteError)) as refused:
        read(configured, authority, source, **change)
    assert "PRIVATE" not in str(refused.value)


@activity.defn(name="PrepareEtcdEncryptionKeyRotation")
async def prepare_activity(parameters: dict) -> str:
    return "PRIVATE_ACTIVITY_RESULT"


@activity.defn(name="PRIVATE_UNKNOWN_ACTIVITY")
async def private_activity(parameters: dict) -> str:
    return "PRIVATE_UNKNOWN_RESULT"


@workflow.defn(name="RotateEtcdEncryptionKey")
class HistoryWorkflow:
    @workflow.run
    async def run(self, parameters: dict) -> str:
        await workflow.execute_activity(
            prepare_activity, {"private": "PRIVATE_NATIVE_KEY"}, start_to_close_timeout=timedelta(seconds=10)
        )
        await workflow.execute_activity(
            private_activity,
            {"private": "PRIVATE_ACTIVITY_INPUT"},
            start_to_close_timeout=timedelta(seconds=10),
        )
        return "PRIVATE_WORKFLOW_RESULT"


@workflow.defn(name="RenewK8sCertsV2")
class RenewalHistoryWorkflow(HistoryWorkflow):
    @workflow.run
    async def run(self, parameters: dict) -> str:
        return await super().run(parameters)


def read(state, authority, source, **changes):
    values = {
        "site_id": str(state.site.guid),
        "expected_site_version": state.site.version,
        "expected_actor_id": state.world.user.pk,
        "expected_authority_version": authority.version,
        "source_operation_id": str(source.guid),
        "expected_source_version": source.version,
        "workflow": "RotateEtcdEncryptionKey",
        "limit": 3,
    }
    values.update(changes)
    with context(state.world):
        return history.read(**values)


@pytest.mark.django_db(transaction=True)
async def test_real_native_renewal_history_binds_the_recorded_cycle(configured, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    async with temporal_test_env() as env:
        owner, queue = "renewal-history-" + uuid4().hex, "renewal-history-queue-" + uuid4().hex
        authority, source, cluster_id = await sync_to_async(prepare)(
            configured,
            observation={
                "conditions": {
                    "updateInProgress": {"type": "certificate_renewal"},
                    "certificateRenewal": {"cycleID": owner, "state": "failed", "phase": "preflight"},
                }
            },
            address=env.client.service_client.config.target_host,
            namespace=env.client.namespace,
            task_queue=queue,
            use_tls=False,
        )
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[RenewalHistoryWorkflow],
            activities=[prepare_activity, private_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                "RenewK8sCertsV2",
                {"clusterID": cluster_id, "thresholdDays": 120, "force": True},
                id=owner,
                task_queue=queue,
            )
            await asyncio.wait_for(handle.result(), timeout=10)
            page = await sync_to_async(read)(configured, authority, source, workflow="RenewK8sCertsV2")
            assert page["workflow_id"] == owner and page["run_id"] == handle.result_run_id
            assert page["recorded_phase"] == "preflight" and page["retained_mutation_lock"] is True
            assert page["source_observed_at"] == source.finished_at
            assert "PRIVATE" not in str(page)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mismatch", [None, "cluster", "queue"])
async def test_real_native_history_is_owner_bound_paged_and_secret_safe(configured, settings, mismatch):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    async with temporal_test_env() as env:
        queue, owner = "history-native-" + uuid4().hex, "rotation-history-" + uuid4().hex
        authority, source, cluster_id = await sync_to_async(prepare)(
            configured,
            observation={
                "conditions": {
                    "updateInProgress": {"type": "etcd_key_rotation"},
                    "etcdKeyRotation": {"workflowID": owner, "state": "failed", "phase": "rewrite"},
                }
            },
            address=env.client.service_client.config.target_host,
            namespace=env.client.namespace,
            task_queue=queue,
            use_tls=False,
        )
        real_queue = queue + "-wrong" if mismatch == "queue" else queue
        async with Worker(
            env.client,
            task_queue=real_queue,
            workflows=[HistoryWorkflow],
            activities=[prepare_activity, private_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                "RotateEtcdEncryptionKey",
                {"clusterID": str(uuid4()) if mismatch == "cluster" else cluster_id},
                id=owner,
                task_queue=real_queue,
            )
            assert await asyncio.wait_for(handle.result(), timeout=10) == "PRIVATE_WORKFLOW_RESULT"
            if mismatch:
                with pytest.raises(NvkeError) as refused:
                    await sync_to_async(read)(configured, authority, source)
                assert refused.value.code == "SOURCE_CHANGED"
                return
            page = await sync_to_async(read)(configured, authority, source)
            assert page["workflow_id"] == owner and page["run_id"] == handle.result_run_id
            assert page["recorded_state"] == "failed" and page["status"] == "COMPLETED"
            assert page["recorded_phase"] == "rewrite" and page["retained_mutation_lock"] is True
            cursor = page["next_cursor"]
            assert cursor
            for bad in (cursor + "broken",):
                with pytest.raises(NvkeError) as invalid:
                    await sync_to_async(read)(configured, authority, source, after=bad)
                assert invalid.value.code == "INVALID_CURSOR"
            with pytest.raises(NvkeError) as changed:
                await sync_to_async(read)(configured, authority, source, after=cursor, limit=2)
            assert changed.value.code == "INVALID_CURSOR"
            rows = []
            while True:
                assert len(page["items"]) <= 3
                assert "PRIVATE" not in str(page)
                rows.extend(page["items"])
                if page["next_cursor"] is None:
                    break
                page = await sync_to_async(read)(configured, authority, source, after=page["next_cursor"])
            original = await handle.fetch_history()
            assert [row["event_id"] for row in rows] == [str(event.event_id) for event in original.events]
            scheduled = [row for row in rows if row["event_type"] == "ACTIVITY_TASK_SCHEDULED"]
            assert [row["activity_type"] for row in scheduled] == ["PrepareEtcdEncryptionKeyRotation", None]
            query = """query($input:ReadNvkeNativeHistoryInput!){astroliftNvkeNativeHistory(input:$input){ok errors{code} data{workflowId runId status recordedState items{eventId eventType activityType workflowTaskCompletedEventId} nextCursor}}}"""
            response = await sync_to_async(execute)(
                configured.world,
                query,
                {
                    "input": {
                        "siteId": str(configured.site.guid),
                        "expectedSiteVersion": configured.site.version,
                        "expectedActorId": configured.world.user.pk,
                        "expectedAuthorityVersion": authority.version,
                        "sourceOperationId": str(source.guid),
                        "expectedSourceVersion": source.version,
                        "workflow": "RotateEtcdEncryptionKey",
                        "limit": 3,
                    }
                },
            )
            assert not response.errors and response.data["astroliftNvkeNativeHistory"]["ok"]
            assert "PRIVATE" not in str(response.data)
            boundary = next(
                event.event_id
                for event in original.events
                if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED
            )
            reset_request = ResetWorkflowExecutionRequest(
                namespace=env.client.namespace,
                workflow_execution=WorkflowExecution(workflow_id=owner, run_id=handle.result_run_id),
                workflow_task_finish_event_id=boundary,
                request_id=str(uuid4()),
                reason="owned native history fixture reset",
                reset_reapply_type=ResetReapplyType.RESET_REAPPLY_TYPE_NONE,
            )
            reset = await env.client.workflow_service.reset_workflow_execution(
                reset_request, retry=False, timeout=timedelta(seconds=10)
            )
            assert reset.run_id != handle.result_run_id
            assert (
                await asyncio.wait_for(
                    env.client.get_workflow_handle(owner, run_id=reset.run_id).result(), timeout=10
                )
                == "PRIVATE_WORKFLOW_RESULT"
            )
            assert (await env.client.get_workflow_handle(owner).describe()).run_id == reset.run_id
            with pytest.raises(NvkeError) as changed_run:
                await sync_to_async(read)(configured, authority, source, after=cursor)
            assert changed_run.value.code == "SOURCE_CHANGED"
