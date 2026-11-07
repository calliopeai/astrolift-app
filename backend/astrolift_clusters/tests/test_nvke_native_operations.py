from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from nvke.client import NvkeError
from temporalio import workflow
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from astrolift_clusters import nvke_native_operations as native
from astrolift_clusters import nvke_native_runtime
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_runtime as runtime
from astrolift_clusters.tests import test_nvke_access as support
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_clusters.tests.test_nvke_temporal import configure as configure_authority
from astrolift_identity.models import RoleBinding
from core.schema.audit import MutationAuditLog
from core.testing.temporal import temporal_test_env, temporal_worker

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return support.world.__wrapped__()


@pytest.fixture
def configured(world):
    return support.configured.__wrapped__(world)


def prepare(state, *, observation=None, **target):
    authority = configure_authority(state.world, state.site, **target)
    source, _, cluster_id = support.observed(state, overrides=observation)
    authority.site = state.site
    return authority, source, cluster_id


def reserve(state, authority, source, cluster_id, **changed):
    values = {
        "site_id": str(state.site.guid),
        "expected_site_version": state.site.version,
        "expected_actor_id": state.world.user.pk,
        "expected_authority_version": authority.version,
        "source_operation_id": str(source.guid),
        "expected_source_version": source.version,
        "request_id": str(uuid4()),
        "workflow": "RotateEtcdEncryptionKey",
        "parameters": {"clusterID": cluster_id},
    }
    values.update(changed)
    with context(state.world):
        return native.reserve(**values)


def test_native_admission_binds_identity_source_and_explicit_authority(configured):
    authority, source, cluster_id = prepare(configured)
    request = str(uuid4())
    row = reserve(configured, authority, source, cluster_id, request_id=request)
    assert row.operation_key == "temporal.RotateEtcdEncryptionKey"
    assert row.authority["native_temporal_id"] == str(authority.guid)
    assert cluster_id.encode() not in bytes(row.input_ciphertext)
    assert reserve(configured, authority, source, cluster_id, request_id=request).pk == row.pk
    with pytest.raises(NvkeError) as conflict:
        reserve(configured, authority, source, cluster_id, request_id=request, timeout_seconds=60)
    assert conflict.value.code == "CONFLICT"
    with pytest.raises(NvkeError) as busy:
        reserve(configured, authority, source, cluster_id)
    assert busy.value.code == "RESOURCE_BUSY"
    with context(configured.world):
        assert operations.checkpoint(row).pk == authority.pk
    authority.is_enabled = False
    authority.save()
    with pytest.raises(NvkeError) as changed:
        operations.checkpoint(row)
    assert changed.value.code == "SOURCE_CHANGED"


def test_revoked_native_actor_is_refused_before_input_decryption(configured, monkeypatch):
    authority, source, cluster_id = prepare(configured)
    row = reserve(configured, authority, source, cluster_id)
    RoleBinding.objects.filter(user=configured.world.user).update(deleted_at=timezone.now())

    def no_decrypt(_row):
        pytest.fail("a revoked actor must not decrypt native workflow input")

    monkeypatch.setattr(operations, "decode_input", no_decrypt)
    assert (
        runtime.attempt(row.pk, 0, execution=(row.workflow_id, "revoked-native-run", "NvkeOperationWorkflow"))
        == "failed"
    )
    row.refresh_from_db()
    assert row.error_code == "AUTHORITY_REVOKED"


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_actor_id": 0},
        {"expected_authority_version": 0},
        {"expected_source_version": 0},
        {"parameters": {"clusterID": str(uuid4())}},
        {
            "workflow": "RetireLostEtcdRotationNode",
            "parameters": {"clusterID": str(uuid4()), "nodeID": str(uuid4()), "rotationWorkflowID": "owner"},
        },
    ],
)
def test_stale_or_unconfirmed_native_admission_is_refused(configured, changes):
    authority, source, cluster_id = prepare(configured)
    with pytest.raises((NvkeError, support.NvkeSiteError)):
        reserve(configured, authority, source, cluster_id, **changes)
    assert not operations.NvkeOperation.objects.filter(operation_key__startswith="temporal.").exists()


def test_graphql_native_admission_masks_even_invalid_parameters(configured):
    authority, source, cluster_id = prepare(configured)
    query = """mutation($review:RequestNvkeNativeWorkflowInput!){requestNvkeNativeWorkflow(input:$review){ok errors{code message} data{id operationKey}}}"""
    values = {
        "siteId": str(configured.site.guid),
        "expectedSiteVersion": configured.site.version,
        "expectedActorId": configured.world.user.pk,
        "expectedAuthorityVersion": authority.version,
        "requestId": str(uuid4()),
        "workflow": "RotateEtcdEncryptionKey",
        "parametersText": json.dumps({"clusterID": cluster_id}),
        "sourceOperationId": str(source.guid),
        "expectedSourceVersion": source.version,
    }
    result = execute(configured.world, query, {"review": values})
    assert not result.errors and result.data["requestNvkeNativeWorkflow"]["ok"]
    values.update(requestId=str(uuid4()), parametersText='{"PRIVATE_NATIVE_KEY":"PRIVATE_VALUE"}')
    refused = execute(configured.world, query, {"review": values})
    assert not refused.errors and not refused.data["requestNvkeNativeWorkflow"]["ok"]
    logs = MutationAuditLog.objects.filter(operation="nvke.native_workflow_request")
    assert logs.count() == 2
    assert all(
        "PRIVATE" not in str(log.variables) and log.variables["review"]["parametersText"] == "***REDACTED***"
        for log in logs
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mismatch", ["memo", "input", "queue", "missing", "boolean", "float"])
async def test_real_native_recovery_refuses_unproven_or_missing_history(configured, settings, mismatch):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    async with temporal_test_env() as env:
        queue = "native-proof-" + uuid4().hex
        authority, source, cluster_id = await sync_to_async(prepare)(
            configured,
            address=env.client.service_client.config.target_host,
            namespace=env.client.namespace,
            task_queue=queue,
            use_tls=False,
        )
        name = "RenewK8sCertsV2" if mismatch in {"boolean", "float"} else "RotateEtcdEncryptionKey"
        parameters = {"clusterID": cluster_id}
        if name == "RenewK8sCertsV2":
            parameters["thresholdDays"] = 1
        row = await sync_to_async(reserve)(
            configured, authority, source, cluster_id, workflow=name, parameters=parameters
        )
        identity = nvke_native_runtime.native_workflow_id(row)
        handle = None
        if mismatch != "missing":
            handle = await env.client.start_workflow(
                name,
                {
                    **parameters,
                    **(
                        {"thresholdDays": True if mismatch == "boolean" else 1.0}
                        if name == "RenewK8sCertsV2"
                        else {}
                    ),
                    "clusterID": str(uuid4()) if mismatch == "input" else cluster_id,
                },
                id=identity,
                task_queue=queue + "-other" if mismatch == "queue" else queue,
                memo={"astro_nvke_request": "wrong-proof" if mismatch == "memo" else row.request_digest},
            )
        else:
            await sync_to_async(operations.save_result)(
                row, {"sending": True, "target": {"workflow_id": identity}}
            )
            await sync_to_async(row.save)(
                update_fields=["result_backend_kind", "result_ciphertext", "updated_at", "version"]
            )
        try:
            execution = (row.workflow_id, row.workflow_run_id or "native-proof-run", "NvkeOperationWorkflow")
            assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "uncertain"
            await sync_to_async(row.refresh_from_db)()
            assert not row.remote_reference
            assert row.error_code == (
                "NATIVE_TEMPORAL_UNAVAILABLE" if mismatch == "missing" else "SOURCE_CHANGED"
            )
            assert "PRIVATE" not in row.error_message
            if handle:
                assert (await handle.describe()).run_id == handle.result_run_id
            else:
                with pytest.raises(RPCError) as missing:
                    await env.client.get_workflow_handle(identity).describe()
                assert missing.value.status == RPCStatusCode.NOT_FOUND
        finally:
            if handle:
                await handle.terminate()


@workflow.defn(name="RotateEtcdEncryptionKey")
class QualifiedRotation:
    def __init__(self):
        self.complete_requested = False

    @workflow.signal
    async def complete(self):
        self.complete_requested = True

    @workflow.run
    async def run(self, parameters: dict) -> None:
        await workflow.wait_condition(lambda: self.complete_requested)


@workflow.defn(name="RenewK8sCertsV2")
class QualifiedRenewal:
    @workflow.run
    async def run(self, parameters: dict) -> None:
        return None


@workflow.defn(name="RetireLostEtcdRotationNode")
class QualifiedRetirement:
    @workflow.run
    async def run(self, parameters: dict) -> None:
        return None


@pytest.mark.django_db(transaction=True)
async def test_real_astro_workflow_dispatches_native_workflow_with_ids_only(
    configured, settings, monkeypatch
):
    from astrolift_workflows import client as workflow_client
    from astrolift_workflows.activities.nvke import nvke_operation_attempt
    from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    async with temporal_test_env() as env:
        queue = "native-parent-" + uuid4().hex
        astro_queue = "astro-parent-" + uuid4().hex
        authority, source, cluster_id = await sync_to_async(prepare)(
            configured,
            address=env.client.service_client.config.target_host,
            namespace=env.client.namespace,
            task_queue=queue,
            use_tls=False,
        )

        async def get_client():
            return env.client

        monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
        monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
        monkeypatch.setattr(workflow_client, "_task_queue", lambda: astro_queue)
        async with (
            Worker(
                env.client,
                task_queue=queue,
                workflows=[QualifiedRenewal],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            temporal_worker(
                env,
                task_queue=astro_queue,
                workflows=[NvkeOperationWorkflow],
                activities=[nvke_operation_attempt],
            ),
        ):
            row = await sync_to_async(reserve)(
                configured,
                authority,
                source,
                cluster_id,
                workflow="RenewK8sCertsV2",
                parameters={"clusterID": cluster_id, "thresholdDays": 120, "force": True},
            )
            handle = env.client.get_workflow_handle(row.workflow_id, run_id=row.workflow_run_id)
            assert await asyncio.wait_for(handle.result(), timeout=45) == "succeeded"
            history = await handle.fetch_history()
            started = history.events[0].workflow_execution_started_event_attributes
            assert await env.client.data_converter.decode(started.input.payloads) == [row.pk, 0]
            assert cluster_id not in history.to_json()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "kind,owner_state",
    [
        ("RenewK8sCertsV2", None),
        ("RetireLostEtcdRotationNode", "terminated"),
        ("RetireLostEtcdRotationNode", "running"),
        ("RetireLostEtcdRotationNode", "wrong_cluster"),
    ],
)
async def test_real_native_operator_input_and_retirement_owner_proof(configured, settings, kind, owner_state):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    async with temporal_test_env() as env:
        queue = "native-operator-" + uuid4().hex
        owner_id = "retained-rotation-" + uuid4().hex
        observation = {
            "conditions": {
                "updateInProgress": {"type": "etcd_key_rotation"},
                "etcdKeyRotation": {"state": "failed", "phase": "rewrite", "workflowID": owner_id},
            }
        }
        authority, source, cluster_id = await sync_to_async(prepare)(
            configured,
            observation=observation,
            address=env.client.service_client.config.target_host,
            namespace=env.client.namespace,
            task_queue=queue,
            use_tls=False,
        )
        parameters = {"clusterID": cluster_id}
        if kind == "RenewK8sCertsV2":
            parameters.update(thresholdDays=120, force=True)
        else:
            parameters.update(nodeID=str(uuid4()), rotationWorkflowID=owner_id)
        row = await sync_to_async(reserve)(
            configured,
            authority,
            source,
            cluster_id,
            workflow=kind,
            parameters=parameters,
            acknowledge_lost_node_teardown=kind == "RetireLostEtcdRotationNode",
        )
        if kind == "RetireLostEtcdRotationNode":
            owner = await env.client.start_workflow(
                "RotateEtcdEncryptionKey",
                {"clusterID": str(uuid4()) if owner_state == "wrong_cluster" else cluster_id},
                id=owner_id,
                task_queue=queue,
            )
            if owner_state != "running":
                await owner.terminate()
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[QualifiedRenewal, QualifiedRetirement],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            execution = (
                row.workflow_id,
                row.workflow_run_id or "native-operator-run",
                "NvkeOperationWorkflow",
            )
            if owner_state in {"running", "wrong_cluster"}:
                assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "failed"
                with pytest.raises(RPCError) as missing:
                    await env.client.get_workflow_handle(
                        nvke_native_runtime.native_workflow_id(row)
                    ).describe()
                assert missing.value.status == RPCStatusCode.NOT_FOUND
                if owner_state == "running":
                    await owner.terminate()
                return
            assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) in {
                "awaiting_ready",
                "succeeded",
            }
            handle = env.client.get_workflow_handle(nvke_native_runtime.native_workflow_id(row))
            await asyncio.wait_for(handle.result(), timeout=10)
            assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "succeeded"
            first = (await handle.fetch_history()).events[0].workflow_execution_started_event_attributes
            assert await env.client.data_converter.decode(first.input.payloads) == [parameters]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("lost_acknowledgement", [False, True])
async def test_real_native_start_recovery_and_short_observation_deadline(
    configured, settings, monkeypatch, lost_acknowledgement
):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    if lost_acknowledgement:
        original = Client.start_workflow

        async def start_then_lose_reply(self, *args, **kwargs):
            await original(self, *args, **kwargs)
            raise RuntimeError("PRIVATE_LOST_NATIVE_ACK")

        monkeypatch.setattr(Client, "start_workflow", start_then_lose_reply)
    async with temporal_test_env() as env:
        queue = "native-rotation-" + uuid4().hex
        authority, source, cluster_id = await sync_to_async(prepare)(
            configured,
            address=env.client.service_client.config.target_host,
            namespace=env.client.namespace,
            task_queue=queue,
            use_tls=False,
        )
        row = await sync_to_async(reserve)(configured, authority, source, cluster_id, timeout_seconds=30)
        execution = (row.workflow_id, row.workflow_run_id or "native-local-run", "NvkeOperationWorkflow")
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[QualifiedRotation],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "awaiting_ready"
            await sync_to_async(row.refresh_from_db)()
            progress = await sync_to_async(operations.decode_result)(row)
            identity = nvke_native_runtime.native_workflow_id(row)
            run_id = progress["native_run_id"]
            handle = env.client.get_workflow_handle(identity, run_id=run_id)
            history = await handle.fetch_history()
            started = history.events[0].workflow_execution_started_event_attributes
            assert await env.client.data_converter.decode(started.input.payloads) == [
                {"clusterID": cluster_id}
            ]
            assert (
                started.workflow_execution_timeout.seconds == 0 and started.workflow_run_timeout.seconds == 0
            )
            await sync_to_async(operations.save_result)(
                row, {"sending": True, "target": {"workflow_id": identity}}
            )
            await sync_to_async(row.save)(
                update_fields=["result_backend_kind", "result_ciphertext", "updated_at", "version"]
            )
            assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "awaiting_ready"
            await sync_to_async(row.refresh_from_db)()
            assert row.remote_reference["run_id"] == run_id
            await sync_to_async(operations.NvkeOperation.objects.filter(pk=row.pk).update)(
                deadline=timezone.now() - timedelta(seconds=1)
            )
            assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "uncertain"
            # Astro's short observation deadline does not cancel the native owner.
            assert (await handle.describe()).status.name == "RUNNING"
            await handle.signal("complete")
            await asyncio.wait_for(handle.result(), timeout=10)
            await sync_to_async(operations.NvkeOperation.objects.filter(pk=row.pk).update)(
                deadline=timezone.now() + timedelta(seconds=30)
            )
            assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "succeeded"
            assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "succeeded"
            assert (await handle.describe()).run_id == run_id
