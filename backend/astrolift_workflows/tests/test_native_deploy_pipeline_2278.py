"""The ordinary DeployAppWorkflow runs the Endpoint-only native pipeline (#2278).

Actual HTTP dispatch, PostgreSQL journals, Temporal workers and history replay,
with SDK-faithful GCP fakes and owned TLS Kubernetes; no cloud is touched.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from google.iam.v1 import policy_pb2
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from astrolift_lifecycle.models import Deployment, DeploymentExecutionReceipt
from astrolift_services.models import (
    GCPGKEAppApplyJournal,
    GCPGKEPreparationJournal,
    GCPWorkloadIdentityJournal,
    ManagedService,
)
from astrolift_services.tests.test_gcp_deployment_context_2278 import dispatch_context
from astrolift_workflows.activities import mark_deploying
from astrolift_workflows.activities.deployment_identity_origin import (
    mark_deployment_identity_refused,
    validate_deployment_identity_origin,
)
from astrolift_workflows.activities.native_endpoint_configuration import (
    complete_native_endpoint_deployment,
    configure_native_endpoint_deployment,
)
from astrolift_workflows.inputs import WorkflowResult
from astrolift_workflows.tests import test_native_endpoint_configuration_2278 as executor_tests
from astrolift_workflows.workflows import deploy_app
from astrolift_workflows.workflows.deploy_app import DeployAppWorkflow

FIXTURES = Path(__file__).parent / "fixtures"
# The private executor's actual SDK/TLS/PostgreSQL world, shared, not copied.
native_world = executor_tests.native_world
NATIVE = [
    "validate_deployment_identity_origin",
    "astrolift.deploy.mark_deploying",
    "astrolift.app.configure_native_endpoint",
]
ACTIVITIES = [
    validate_deployment_identity_origin,
    mark_deployment_identity_refused,
    mark_deploying,
    configure_native_endpoint_deployment,
    complete_native_endpoint_deployment,
]


def _scheduled(history):
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


async def deploy(
    w, client, temporal_env, *, before=None, during=None, sandboxed=True, dispatch=dispatch_context
):
    """One actual HTTP deploy request, approved, run by the ordinary workflow."""
    from astrolift_lifecycle.schema.mutations import helpers
    from astrolift_workflows import client as native_client

    queue = "native-deploy-" + uuid4().hex

    async def current_client():
        return temporal_env.client

    def actual_start(name, args, **kwargs):
        result = native_client.start_workflow(name, args, **kwargs)
        assert result.enqueued
        w.sdk_run_id = result.run_id
        w.starts.append(args[0])
        return result

    w.monkeypatch.setattr(native_client, "_get_client_async", current_client)
    w.monkeypatch.setattr(native_client, "_temporal_enabled", lambda: True)
    w.monkeypatch.setattr(native_client, "_task_queue", lambda: queue)
    w.monkeypatch.setattr(helpers, "start_workflow", actual_start)
    w.starts.clear()
    runner = {} if sandboxed else {"workflow_runner": UnsandboxedWorkflowRunner()}
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[DeployAppWorkflow],
            activities=ACTIVITIES,
            activity_executor=executor,
            **runner,
        ):
            row, _, _ = await sync_to_async(dispatch)(w, client)
            if before:
                await sync_to_async(before)()
            expected = await sync_to_async(DeploymentExecutionReceipt.objects.get)(
                deployment=row, kind="EXPECTED"
            )
            handle = temporal_env.client.get_workflow_handle(
                expected.workflow_id, run_id=w.sdk_run_id, result_type=WorkflowResult
            )
            if during:
                await during(handle)
            result = await handle.result()
            history = await handle.fetch_history()
    await Replayer(workflows=[DeployAppWorkflow], **runner).replay_workflow(history)
    assert w.headers["HTTP_AUTHORIZATION"] not in history.to_json()
    row = await sync_to_async(Deployment.objects.get)(pk=row.pk)
    return result, history, row


def with_iam(w, wrap):
    """Serve this world's IAM port through ``wrap(identity, wire)``."""
    iam = w.ports.iam

    def port(identity):
        driver = iam(identity)
        wrap(identity, w.iam_wires[-1])
        return driver

    ports = type(w.ports)(w.ports.source, w.ports.endpoints, w.ports.preparation, port, w.ports.apply)
    w.monkeypatch.setattr(
        "astrolift_workflows.activities.native_endpoint_configuration._server_ports", lambda: ports
    )


def persistent_policies(w):
    """Each port gets a fresh fake connection; the cloud's policies persist."""

    def carry(_identity, wire):
        if len(w.iam_wires) > 1:
            wire.policies = w.iam_wires[-2].policies

    with_iam(w, carry)


async def _states():
    return await sync_to_async(
        lambda: {
            cls.__name__: sorted(cls.objects.values_list("state", flat=True))
            for cls in (GCPGKEPreparationJournal, GCPWorkloadIdentityJournal, GCPGKEAppApplyJournal)
        }
    )()


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_ordinary_workflow_deploys_then_redeploys_endpoint_only_app_natively(
    native_world, client, temporal_env
):
    w = native_world
    persistent_policies(w)
    result, history, first = await deploy(w, client, temporal_env)
    assert result.ok, (result.message, w.failures)
    assert _scheduled(history) == [*NATIVE, "astrolift.app.complete_native_endpoint"]
    assert first.status == "running" and first.aborted_reason == ""
    assert await _states() == {name: ["OBSERVED"] for name in await _states()}
    assert w.native.writes == 1
    endpoint_writes = sum(wire.writes for wire in w.iam_wires)
    assert endpoint_writes >= 1

    # A later Deployment is the next generation over resolved history: same
    # GSA, same KSA UIDs, no second identity create and no IAM write when the
    # Endpoint union is unchanged.
    requests = len(w.http["requests"])
    result, history, second = await deploy(w, client, temporal_env)
    assert result.ok, (result.message, w.failures)
    assert _scheduled(history) == [*NATIVE, "astrolift.app.complete_native_endpoint"]
    assert second.status == "running"
    assert await sync_to_async(lambda: Deployment.objects.get(pk=first.pk).status)() == "superseded"
    assert w.native.writes == 1
    assert len(w.iam_wires) == 2 and w.iam_wires[-1].writes == 0
    assert len(w.http["requests"]) > requests
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.generation == 2 and journal.state == "OBSERVED"
    assert str(journal.operation_id) == str(
        await sync_to_async(
            lambda: DeploymentExecutionReceipt.objects.get(
                deployment_id=second.pk, kind="BOUND"
            ).operation_uuid
        )()
    )


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_configuration_alone_never_marks_the_deployment_running(native_world, client, temporal_env):
    from astrolift_workflows import native_endpoint_configuration as executor

    w = native_world
    w.monkeypatch.setattr(executor, "_RUNTIME_OBSERVATIONS", 2)
    w.monkeypatch.setattr(executor, "_RUNTIME_POLL_SECONDS", 0)
    ready = w.http["after_write"]

    def never_ready(method, path):
        ready(method, path)
        row = w.http["objects"][path]
        if row["kind"] == "Deployment":
            row["status"] = {**row["status"], "readyReplicas": 0, "availableReplicas": 0}

    w.http["after_write"] = never_ready
    result, history, row = await deploy(w, client, temporal_env)
    assert not result.ok
    assert _scheduled(history) == [
        *NATIVE,
        "astrolift.app.complete_native_endpoint",
        "mark_deployment_identity_refused",
    ]
    assert row.status == "failed"
    assert row.aborted_reason == "NATIVE_CONFIGURATION_REFUSED:NATIVE_ROLLOUT_UNOBSERVED"
    # Configuration was observed and stays recorded; it is not readiness.
    assert await _states() == {name: ["OBSERVED"] for name in await _states()}


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_mixed_union_refuses_before_any_ksa_or_iam_effect(native_world, client, temporal_env):
    w = native_world

    def legacy_service():
        ManagedService.objects.create(
            registered_app=w.medops_app, app_environment=w.env, kind="postgres", name="legacy-gsa-user"
        )

    result, history, row = await deploy(w, client, temporal_env, before=legacy_service)
    assert not result.ok
    assert _scheduled(history) == [*NATIVE, "mark_deployment_identity_refused"]
    assert row.status == "failed"
    assert row.aborted_reason == "NATIVE_CONFIGURATION_REFUSED:UNSUPPORTED_CONFIGURATION_SERVICE_UNION"
    assert not w.native.calls and not w.http["requests"] and not w.iam_wires
    assert await _states() == {name: [] for name in await _states()}


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_endpoint_etag_race_refuses_and_preserves_the_foreign_grant(native_world, client, temporal_env):
    w = native_world
    foreign = policy_pb2.Binding(role="roles/viewer", members=["user:someone-else@example.com"])

    def racing(_identity, wire):
        def concurrent_writer(request):
            # Another actor updates the policy between our read and our write.
            current = deepcopy(wire.policies[request.resource])
            current.bindings.append(foreign)
            current.etag = b"foreign-write"
            wire.policies[request.resource] = current

        wire.before_set = concurrent_writer

    with_iam(w, racing)
    result, history, row = await deploy(w, client, temporal_env)
    assert not result.ok
    assert row.status == "failed" and row.aborted_reason.startswith("NATIVE_CONFIGURATION_REFUSED:")
    assert "astrolift.app.complete_native_endpoint" not in _scheduled(history)
    policies = [
        policy for wire in w.iam_wires for policy in wire.policies.values() if foreign in policy.bindings
    ]
    assert policies and all(wire.writes == 0 for wire in w.iam_wires)
    states = await _states()
    assert states["GCPWorkloadIdentityJournal"] != ["OBSERVED"] and states["GCPGKEAppApplyJournal"] == []
    # No KSA annotation without completed IAM.
    assert not any(effect[0] == "PATCH" for effect in w.http["effects"])


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_lost_worker_leaves_send_unresolved_and_blocks_the_next_deployment(
    native_world, client, temporal_env
):
    from temporalio import activity

    w = native_world
    w.monkeypatch.setattr(deploy_app, "_NATIVE_HEARTBEAT", timedelta(seconds=3))
    created, release, silent = Event(), Event(), Event()
    real_heartbeat = activity.heartbeat
    w.monkeypatch.setattr(activity, "heartbeat", lambda *d: None if silent.is_set() else real_heartbeat(*d))
    original = w.http["after_write"]

    def lose_worker_after_create(method, path):
        original(method, path)
        if method == "POST" and w.http["objects"][path]["kind"] == "Namespace":
            # The send landed; the worker goes silent as if it died mid-reply.
            silent.set()
            created.set()
            assert release.wait(900)

    async def until_workflow_refused(handle):
        # Off the thread-sensitive executor, which the async activities need.
        assert await sync_to_async(created.wait, thread_sensitive=False)(1800)
        try:
            await handle.result()
        finally:
            release.set()

    w.http["after_write"] = lose_worker_after_create
    result, history, first = await deploy(
        w, client, temporal_env, during=until_workflow_refused, sandboxed=False
    )
    assert not result.ok
    assert first.status == "failed"
    assert first.aborted_reason == "NATIVE_CONFIGURATION_REFUSED:CONFIGURATION_UNCONFIRMED"
    assert "astrolift.app.complete_native_endpoint" not in _scheduled(history)
    # The still-running attempt resumes after its reply and refuses further effects.
    effects = len(w.http["effects"])
    for _ in range(600):
        if w.failures:
            break
        await sync_to_async(Event().wait, thread_sensitive=False)(0.1)
    assert w.failures and len(w.http["effects"]) == effects == 1 and not w.iam_wires
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.state in ("SENT", "UNKNOWN")

    # The next Deployment cannot resume or take over while that send may be live.
    w.http["after_write"] = original
    native_calls, requests = len(w.native.calls), len(w.http["requests"])
    result, history, second = await deploy(w, client, temporal_env, sandboxed=False)
    assert not result.ok
    assert second.aborted_reason == "NATIVE_CONFIGURATION_REFUSED:PRIOR_SEND_UNRESOLVED"
    assert (len(w.native.calls), len(w.http["requests"])) == (native_calls, requests)
    assert not w.iam_wires
    assert (await sync_to_async(GCPGKEPreparationJournal.objects.get)()).state == journal.state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["legacy-deploy-app-original-authority-gate.json", "native-refused-not-configured-2278.json"]
)
async def test_histories_recorded_before_the_native_pipeline_still_replay(name):
    """Captured on the previous release: a legacy deploy through the first gate,
    and a native request refused NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED."""
    history = WorkflowHistory.from_json(name, (FIXTURES / name).read_text())
    scheduled = _scheduled(history)
    assert scheduled[0] == "validate_deployment_identity_origin"
    assert "astrolift.app.configure_native_endpoint" not in scheduled
    await Replayer(workflows=[DeployAppWorkflow]).replay_workflow(history)
