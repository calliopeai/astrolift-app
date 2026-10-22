"""Actual HTTP enqueue/Worker/original receipts and generated SDK + owned TLS."""

import asyncio
import base64
import json
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from threading import Event
from threading import enumerate as active_threads
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from gcp.gke_app_apply import GKEAppApply
from gcp.gke_identity_preparation import GKEIdentityPreparation
from gcp.identity_owned import NativeGCPIdentity
from google.iam.v1 import policy_pb2
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from astrolift_lifecycle.models import DeploymentExecutionReceipt
from astrolift_services.models import (
    GCPGKEAppApplyJournal,
    GCPGKEPreparationJournal,
    GCPIdentityAcknowledgement,
    GCPWorkloadIdentityJournal,
    ManagedService,
)
from astrolift_services.tests.test_gcp_app_identity_plan_2278 import SourceWire
from astrolift_services.tests.test_gcp_deployment_context_2278 import dispatch_context
from astrolift_workflows.activities.native_endpoint_configuration import configure_native_endpoint_deployment
from astrolift_workflows.inputs import DeployAppInput
from astrolift_workflows.native_endpoint_configuration import (
    NativeConfigurationPorts,
    execute_native_endpoint_configuration,
)
from astrolift_workflows.tests.test_native_execution_checkpoint_2278 import world as original_world
from providers.tests.gcp.test_gke_app_apply_2278 import native as tls_fixture
from providers.tests.gcp.test_identity_submission_2278 import SubmissionWire

pytestmark = pytest.mark.django_db(transaction=True)


@workflow.defn(name="DeployAppWorkflow")
class ConfigurationWorkflow:
    @workflow.run
    async def run(self, input: DeployAppInput) -> dict:
        try:
            result = await workflow.execute_activity(
                configure_native_endpoint_deployment,
                input,
                start_to_close_timeout=timedelta(seconds=900),
                heartbeat_timeout=timedelta(seconds=2),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            return asdict(result)
        except ActivityError:
            return {"refused": True}
        except CancelledError:
            return {"cancelled": True}


@workflow.defn(name="DeployAppWorkflow")
class WithoutHeartbeatWorkflow:
    @workflow.run
    async def run(self, input: DeployAppInput) -> dict:
        try:
            await workflow.execute_activity(
                configure_native_endpoint_deployment,
                input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except ActivityError:
            return {"refused": True}
        return {"unexpectedly_accepted": True}


@workflow.defn(name="DeployAppWorkflow")
class SameRunRecoveryWorkflow:
    @workflow.run
    async def run(self, input: DeployAppInput) -> dict:
        attempts = []
        for _ in range(2):
            try:
                result = await workflow.execute_activity(
                    configure_native_endpoint_deployment,
                    input,
                    start_to_close_timeout=timedelta(seconds=900),
                    heartbeat_timeout=timedelta(seconds=2),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                attempts.append(asdict(result))
            except ActivityError:
                attempts.append({"refused": True})
        return {"attempts": attempts}


@pytest.fixture
def native_world(monkeypatch, client, tmp_path):
    w = original_world.__wrapped__(monkeypatch, client, tmp_path)
    gen = tls_fixture.__wrapped__(tmp_path, monkeypatch)
    driver, _, http, wire = next(gen)
    w.cluster.endpoint = wire.cluster.endpoint
    w.cluster.ca_cert = wire.cluster.master_auth.cluster_ca_certificate
    w.cluster.save()
    w.native.cluster.endpoint = w.cluster.endpoint
    w.native.cluster.master_auth.cluster_ca_certificate = w.cluster.ca_cert
    w.native.cluster.name = wire.cluster.name
    w.native.cluster.id = wire.cluster.id
    w.native.cluster.location = wire.cluster.location
    w.medops_app.manifest_raw = "\n".join(
        (
            'name = "fixture"',
            "[[workloads]]",
            'name = "web"',
            'kind = "deployment"',
            "replicas = 1",
            "[[workloads.containers]]",
            'name = "web"',
            "is_primary = true",
            "port = 8080",
        )
    )
    w.medops_app.save()
    w.http, w.wire = http, wire
    wire.cluster.locations = [wire.cluster.location]
    wire.pools.node_pools[0].locations = [wire.cluster.location]
    w.monkeypatch = monkeypatch
    monkeypatch.setattr(
        "providers.tests.gcp.test_identity_owned_2278.ROLE",
        w.cluster.provider_config["endpoint_prediction_role"],
    )
    http["before_write"] = None
    w.endpoint_wires, w.iam_wires = [], []

    def source(declaration):
        return w.native.port(declaration)

    def endpoints(identity, *, checkpoint):
        endpoint_wire = SourceWire(identity, w.source.endpoint)
        w.endpoint_wires.append(endpoint_wire)
        return endpoint_wire.observer(identity, checkpoint=checkpoint)

    def preparation(context):
        wire.cluster.name, wire.cluster.id, wire.cluster.location = (
            context.cluster_name,
            context.native_cluster_id,
            context.location,
        )
        value = GKEIdentityPreparation(context, clients=wire.clients)
        value.observer._credentials = driver.observer._credentials
        return value

    def iam(identity):
        value = SubmissionWire()
        value.account.name = f"projects/{identity.project_id}/serviceAccounts/{identity.email}"
        value.account.email = identity.email
        value.account.unique_id = identity.service_account_unique_id
        value.account.description = identity.owner_description
        value.role.name = w.cluster.provider_config["endpoint_prediction_role"]
        resource = "projects/" + identity.project_number + "/" + w.native.endpoint.name.split("/", 2)[2]
        value.policies = {
            name: policy_pb2.Policy(version=3, etag=b"initial")
            for name in (identity.service_account_resource, resource)
        }
        w.iam_wires.append(value)
        return NativeGCPIdentity(identity, clients=value.clients)

    def apply(context):
        value = GKEAppApply(context, clients=wire.clients)
        value.observer._credentials = driver.observer._credentials
        return value

    w.failures = []
    w.after_refusal = None
    w.before_ports = None
    w.checkpoint_calls = 0
    w.checkpoint_stages = {}
    from astrolift_lifecycle.deployment_execution_checkpoint import _ScopedDeploymentExecutionCheckpoint

    original_admitted = _ScopedDeploymentExecutionCheckpoint._admitted

    def measured_admitted(self):
        w.checkpoint_calls += 1
        frame = sys._getframe()
        stage = "direct_db_transition"
        names = {
            "bootstrap_original_identity": "source_bootstrap",
            "produce_endpoint_app_plan": "endpoint_source",
            "refresh_endpoint_app_sources": "endpoint_refresh",
            "prepare": "preparation",
            "annotate": "annotation",
            "reconcile": "iam",
            "capture_placement": "placement",
            "apply": "apply",
            "observe_handoff_runtime": "runtime",
        }
        while frame:
            if frame.f_code.co_name in names:
                stage = names[frame.f_code.co_name]
                break
            frame = frame.f_back
        w.checkpoint_stages[stage] = w.checkpoint_stages.get(stage, 0) + 1
        return original_admitted(self)

    monkeypatch.setattr(_ScopedDeploymentExecutionCheckpoint, "_admitted", measured_admitted)

    def recorded(input, *, ports):
        try:
            return execute_native_endpoint_configuration(input, ports=ports)
        except Exception as error:
            reason = str(error)
            w.frames = [(row.name, row.lineno) for row in traceback.extract_tb(error.__traceback__)]
            w.failures.append(
                (
                    type(error).__name__,
                    reason if re.fullmatch("[A-Z_]+", reason) else "PRIVATE_DIAGNOSTIC_SUPPRESSED",
                )
            )
            if w.after_refusal is not None:
                w.after_refusal()
            raise

    monkeypatch.setattr(
        "astrolift_workflows.activities.native_endpoint_configuration.execute_native_endpoint_configuration",
        recorded,
    )
    ports = NativeConfigurationPorts(source, endpoints, preparation, iam, apply)
    w.ports = ports
    from django.db import connection
    from gcp import gke_identity_preparation

    actual_http = gke_identity_preparation._http
    w.preparation_http_calls = 0

    def outside_transaction(*args, **kwargs):
        assert not connection.in_atomic_block and connection.get_autocommit()
        w.preparation_http_calls += 1
        return actual_http(*args, **kwargs)

    monkeypatch.setattr(gke_identity_preparation, "_http", outside_transaction)
    monkeypatch.setattr(
        "astrolift_workflows.activities.native_endpoint_configuration._server_ports", lambda: ports
    )

    def after_write(method, path):
        row = http["objects"][path]
        if row["kind"] == "Deployment":
            row["metadata"]["generation"] = 1
            row["status"] = {
                "observedGeneration": 1,
                "replicas": 1,
                "readyReplicas": 1,
                "updatedReplicas": 1,
                "availableReplicas": 1,
            }
            namespace, name = row["metadata"]["namespace"], row["metadata"]["name"]
            labels = deepcopy(row["spec"]["template"]["metadata"]["labels"])
            labels["pod-template-hash"] = "fixture-hash"
            child_uid, pod_uid, node_uid = (str(uuid4()) for _ in range(3))
            child = {
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {
                    "name": name + "-rs",
                    "namespace": namespace,
                    "uid": child_uid,
                    "resourceVersion": "fixture-rv",
                    "generation": 1,
                    "labels": labels,
                    "ownerReferences": [
                        {
                            "apiVersion": "apps/v1",
                            "kind": "Deployment",
                            "name": name,
                            "uid": row["metadata"]["uid"],
                            "controller": True,
                        }
                    ],
                },
                "spec": {"replicas": 1, "template": deepcopy(row["spec"]["template"])},
                "status": {"observedGeneration": 1, "replicas": 1, "readyReplicas": 1},
            }
            child["spec"]["template"]["metadata"]["labels"] = labels
            pod = {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": name + "-pod",
                    "namespace": namespace,
                    "uid": pod_uid,
                    "resourceVersion": "fixture-rv",
                    "labels": labels,
                    "ownerReferences": [
                        {
                            "apiVersion": "apps/v1",
                            "kind": "ReplicaSet",
                            "name": name + "-rs",
                            "uid": child_uid,
                            "controller": True,
                        }
                    ],
                },
                "spec": {**deepcopy(row["spec"]["template"]["spec"]), "nodeName": "owned-node"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "containerStatuses": [
                        {"name": c["name"], "ready": True, "state": {"running": {}}}
                        for c in row["spec"]["template"]["spec"]["containers"]
                    ],
                },
            }
            http["objects"][f"/apis/apps/v1/namespaces/{namespace}/replicasets"] = {
                "apiVersion": "apps/v1",
                "kind": "ReplicaSetList",
                "metadata": {"resourceVersion": "fixture-rv"},
                "items": [child],
            }
            http["objects"][f"/apis/apps/v1/namespaces/{namespace}/replicasets?limit=257"] = http["objects"][
                f"/apis/apps/v1/namespaces/{namespace}/replicasets"
            ]
            http["objects"][f"/api/v1/namespaces/{namespace}/pods"] = {
                "apiVersion": "v1",
                "kind": "PodList",
                "metadata": {"resourceVersion": "fixture-rv"},
                "items": [pod],
            }
            http["objects"][f"/api/v1/namespaces/{namespace}/pods?limit=257"] = http["objects"][
                f"/api/v1/namespaces/{namespace}/pods"
            ]
            http["objects"]["/api/v1/nodes/owned-node"] = {
                "apiVersion": "v1",
                "kind": "Node",
                "metadata": {
                    "name": "owned-node",
                    "uid": node_uid,
                    "resourceVersion": "fixture-rv",
                    "labels": {
                        "cloud.google.com/gke-nodepool": "owned-pool",
                        "topology.kubernetes.io/zone": wire.cluster.location,
                        "iam.gke.io/gke-metadata-server-enabled": "true",
                    },
                },
                "spec": {"providerID": f"gce://{wire.project.project_id}/{wire.cluster.location}/owned-node"},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }

    http["after_write"] = after_write
    try:
        yield w
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


async def run(
    w,
    client,
    temporal_env,
    *,
    before=None,
    during=None,
    hold_activity=False,
    workflow_type=ConfigurationWorkflow,
):
    started = time.monotonic()
    from astrolift_lifecycle.schema.mutations import helpers
    from astrolift_workflows import client as native_client

    queue = "native-configuration-" + uuid4().hex
    released = Event()

    async def current_client():
        return temporal_env.client

    def actual_start(name, args, **kwargs):
        result = native_client.start_workflow(name, args, **kwargs)
        assert result.enqueued
        w.sdk_run_id = result.run_id
        w.starts.append(args[0])
        return result

    def scheduled_ports():
        assert released.wait(30), "HTTP admission/controlled withdrawal must settle"
        if w.before_ports is not None:
            w.before_ports()
        return w.ports

    w.monkeypatch.setattr(native_client, "_get_client_async", current_client)
    w.monkeypatch.setattr(native_client, "_temporal_enabled", lambda: True)
    w.monkeypatch.setattr(native_client, "_task_queue", lambda: queue)
    w.monkeypatch.setattr(helpers, "start_workflow", actual_start)
    w.monkeypatch.setattr(
        "astrolift_workflows.activities.native_endpoint_configuration._server_ports", scheduled_ports
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[workflow_type],
            activities=[configure_native_endpoint_deployment],
            activity_executor=executor,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            try:
                row, _, _ = await sync_to_async(dispatch_context)(w, client)
                expected = await sync_to_async(DeploymentExecutionReceipt.objects.get)(
                    deployment=row, kind="EXPECTED"
                )
                if before:
                    await sync_to_async(before)()
            finally:
                if not hold_activity:
                    released.set()
            handle = temporal_env.client.get_workflow_handle(expected.workflow_id, run_id=w.sdk_run_id)
            if during:
                try:
                    await during(handle)
                finally:
                    released.set()
            try:
                result = await handle.result()
            except Exception:
                print(json.dumps({"safe_failures": w.failures, "safe_frames": getattr(w, "frames", [])}))
                raise
            history = await handle.fetch_history()
    await Replayer(workflows=[workflow_type], workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(
        history
    )
    assert w.headers["HTTP_AUTHORIZATION"] not in history.to_json()
    assert "private-manifest-canary" not in history.to_json()
    assert not any(thread.name == "native-configuration-heartbeat" for thread in active_threads())
    print(
        json.dumps(
            {
                "execution_checkpoints": w.checkpoint_calls,
                "checkpoint_stages": w.checkpoint_stages,
                "native_request_counts": {
                    "source_sdk": len(w.native.calls),
                    "endpoint_sdk": sum(len(value.calls) for value in w.endpoint_wires),
                    "iam_sdk": sum(len(value.calls) for value in w.iam_wires),
                    "gke_sdk": len(w.wire.calls),
                    "kubernetes_http": len(w.http["requests"]),
                },
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        )
    )
    return result, history


async def activity_reaches(w, event, *, stalled_after=60):
    """Wait for the activity to set ``event``, bounded by its progress, not a total.

    Reaching the Namespace create takes about 575 execution checkpoints, each a
    full re-admission of the original caller: about 90 s on a quiet machine
    and past 120 s on a slow CI runner, so a fixed budget raced the runner
    (#2509). Fail when the activity refuses or no checkpoint advances for
    ``stalled_after`` seconds.
    """
    seen, since = w.checkpoint_calls, time.monotonic()
    while not await sync_to_async(event.wait)(1):
        assert not w.failures, w.failures
        if w.checkpoint_calls != seen:
            seen, since = w.checkpoint_calls, time.monotonic()
        assert time.monotonic() - since < stalled_after, f"activity stalled at checkpoint {seen}"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_actual_registered_configuration_worker_commits_every_stage_and_returns_metadata_only(
    native_world, client, temporal_env
):
    w = native_world
    result, history = await run(w, client, temporal_env)
    assert not result.get("refused"), (w.failures, getattr(w, "frames", []))
    # Rollout is observed; token exchange and prediction are not.
    assert result["configuration_observed"] and result["workload_ready"] and not result["invocation_verified"]
    for cls in (GCPGKEPreparationJournal, GCPWorkloadIdentityJournal, GCPGKEAppApplyJournal):
        assert await sync_to_async(lambda cls=cls: cls.objects.get().state)() == "OBSERVED"
    assert await sync_to_async(GCPIdentityAcknowledgement.objects.count)() >= 5
    assert w.native.writes == 1
    assert w.preparation_http_calls > 0
    assert any(path.endswith("/pods?limit=257") for _, path, _ in w.http["requests"]), result[
        "runtime_observation_reason"
    ]
    assert any(path == "/api/v1/nodes/owned-node" for _, path, _ in w.http["requests"])
    assert result["runtime_observation_reason"] == "WORKLOAD_ROLLOUT_OBSERVED"
    assert any(path.endswith("/deployments/web") for _, path, _ in w.http["effects"])
    secret = next(row for row in w.http["objects"].values() if row["kind"] == "Secret")
    assert base64.b64decode(secret["data"]["MODEL_AUTH_MODE"]) == b"cloud_identity"
    controller = next(row for row in w.http["objects"].values() if row["kind"] == "Deployment")
    assert {"secretRef": {"name": secret["metadata"]["name"]}} in controller["spec"]["template"]["spec"][
        "containers"
    ][0]["envFrom"]
    serialized = json.dumps(result)
    assert "stringData" not in serialized and "ModelEndpoint" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "withdrawal",
    [
        "token",
        "terminal",
        "mixed",
        "saved_literal",
        "malformed_snapshot",
        "malformed_literals",
        "zero_replicas",
    ],
)
async def test_current_original_withdrawal_and_mixed_union_refuse_before_native_effects(
    native_world, client, temporal_env, withdrawal
):
    w = native_world

    def before():
        if withdrawal == "token":
            type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)
        elif withdrawal == "terminal":
            from astrolift_lifecycle.models import Deployment

            Deployment.objects.filter(pk=w.starts[0].deployment_id).update(status="superseded")
        elif withdrawal in ("saved_literal", "malformed_snapshot", "malformed_literals"):
            from astrolift_lifecycle.models import Deployment

            snapshot = {
                "saved_literal": {"literals": {"PRIVATE_MARKER": "private-manifest-canary"}},
                "malformed_snapshot": ["private-manifest-canary"],
                "malformed_literals": {"literals": "private-manifest-canary"},
            }[withdrawal]
            Deployment.objects.filter(pk=w.starts[0].deployment_id).update(secret_snapshot=snapshot)
        elif withdrawal == "zero_replicas":
            type(w.medops_app).objects.filter(pk=w.medops_app.pk).update(
                manifest_raw=w.medops_app.manifest_raw.replace("replicas = 1", "replicas = 0")
            )
        else:
            ManagedService.objects.create(
                registered_app=w.medops_app, app_environment=w.env, kind="postgres", name="unsupported"
            )

    result, _ = await run(w, client, temporal_env, before=before)
    assert result == {"refused": True}
    assert not w.native.writes and not w.http["effects"] and not w.iam_wires
    assert await sync_to_async(GCPGKEPreparationJournal.objects.count)() == 0


@pytest.mark.asyncio
async def test_actual_namespace_reply_retains_original_uid_after_token_withdrawal(
    native_world, client, temporal_env
):
    w = native_world
    from django.db import connections

    original = w.http["after_write"]
    namespace_uids = []

    def withdraw_after_create(method, path):
        original(method, path)
        if method == "POST" and w.http["objects"][path]["kind"] == "Namespace":
            namespace_uids.append(w.http["objects"][path]["metadata"]["uid"])
            try:
                type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)
            finally:
                # This server thread owns its fresh default connection only.
                connections["default"].close()

    w.http["after_write"] = withdraw_after_create
    result, _ = await run(w, client, temporal_env)
    assert result == {"refused": True}
    assert len(namespace_uids) == 1 and len(w.http["effects"]) == 1
    ack = await sync_to_async(GCPIdentityAcknowledgement.objects.get)(kind="PREPARATION")
    assert ack.acknowledgement["uid"] == namespace_uids[0]
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.state == "SENT" and journal.prepared_revision == journal.observed_revision == 0
    assert all(value["state"] == "SENT" for value in journal.submissions.values())
    assert not w.iam_wires
    assert await sync_to_async(GCPWorkloadIdentityJournal.objects.count)() == 0
    assert await sync_to_async(GCPGKEAppApplyJournal.objects.count)() == 0


@pytest.mark.asyncio
async def test_lost_namespace_reply_same_actual_run_refuses_name_adoption(native_world, client, temporal_env):
    w = native_world
    original = w.http["after_write"]

    def lose_namespace_reply(method, path):
        original(method, path)
        if method == "POST" and w.http["objects"][path]["kind"] == "Namespace":
            w.http["lost"] = True

    w.http["after_write"] = lose_namespace_reply
    result, _ = await run(w, client, temporal_env, workflow_type=SameRunRecoveryWorkflow)
    assert result == {"attempts": [{"refused": True}, {"refused": True}]}
    assert len(w.http["effects"]) == 1
    assert await sync_to_async(GCPIdentityAcknowledgement.objects.count)() == 0
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.state == "SENT" and journal.prepared_revision == journal.observed_revision == 0
    assert not w.iam_wires
    assert await sync_to_async(GCPWorkloadIdentityJournal.objects.count)() == 0
    assert await sync_to_async(GCPGKEAppApplyJournal.objects.count)() == 0
    bound = await sync_to_async(DeploymentExecutionReceipt.objects.get)(kind="BOUND")
    assert bound.run_id == w.sdk_run_id


@pytest.mark.asyncio
async def test_missing_heartbeat_contract_refuses_before_bound_or_native_effect(
    native_world, client, temporal_env
):
    w = native_world
    result, _ = await run(w, client, temporal_env, workflow_type=WithoutHeartbeatWorkflow)
    assert result == {"refused": True}
    assert not w.native.calls and not w.http["requests"]
    assert await sync_to_async(DeploymentExecutionReceipt.objects.filter(kind="BOUND").count)() == 0
    assert await sync_to_async(GCPIdentityAcknowledgement.objects.count)() == 0


@pytest.mark.asyncio
async def test_actual_cancel_delivered_before_held_reply_release_preserves_ack_only(
    native_world, client, temporal_env
):
    w = native_world
    from temporalio import activity

    created, released, observed_cancel = Event(), Event(), Event()
    original_write = w.http["after_write"]
    real_is_cancelled = activity.is_cancelled
    namespace_uids = []

    def observe_actual_cancel():
        value = real_is_cancelled()
        if value:
            observed_cancel.set()
        return value

    w.monkeypatch.setattr(activity, "is_cancelled", observe_actual_cancel)

    def hold_reply(method, path):
        original_write(method, path)
        if method == "POST" and w.http["objects"][path]["kind"] == "Namespace":
            namespace_uids.append(w.http["objects"][path]["metadata"]["uid"])
            created.set()
            assert released.wait(8), "owned native reply must remain bounded"

    async def cancel_before_reply(handle):
        await activity_reaches(w, created)
        try:
            description = await handle.describe()
            assert description.raw_description.pending_activities
            assert all(
                not row.heartbeat_details.payloads for row in description.raw_description.pending_activities
            )
            await handle.cancel()
            # This observes the real activity SDK flag while both Worker and
            # original request remain live, before any shutdown cancellation.
            assert await sync_to_async(observed_cancel.wait)(5)
        finally:
            released.set()

    w.http["after_write"] = hold_reply
    result, history = await run(w, client, temporal_env, during=cancel_before_reply)
    assert result in ({"cancelled": True}, {"refused": True})
    assert observed_cancel.is_set() and len(namespace_uids) == 1
    assert len(w.http["effects"]) == 1 and not w.iam_wires
    ack = await sync_to_async(GCPIdentityAcknowledgement.objects.get)(kind="PREPARATION")
    assert ack.acknowledgement["uid"] == namespace_uids[0]
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.state == "SENT" and journal.prepared_revision == journal.observed_revision == 0
    assert await sync_to_async(GCPWorkloadIdentityJournal.objects.count)() == 0
    assert await sync_to_async(GCPGKEAppApplyJournal.objects.count)() == 0
    # Heartbeats carry no control data, invocation arguments or forensic body.
    for event in history.events:
        if event.HasField("activity_task_failed_event_attributes"):
            details = event.activity_task_failed_event_attributes.failure
            assert "private-manifest-canary" not in str(details)


@pytest.mark.asyncio
async def test_actual_cancel_before_ports_release_has_no_native_effect(native_world, client, temporal_env):
    w = native_world
    from temporalio import activity

    cancelled = Event()
    real_is_cancelled = activity.is_cancelled

    def observe_actual_cancel():
        value = real_is_cancelled()
        if value:
            cancelled.set()
        return value

    w.monkeypatch.setattr(activity, "is_cancelled", observe_actual_cancel)

    async def cancel_while_ports_wait(handle):
        await handle.cancel()
        assert await sync_to_async(cancelled.wait)(5)

    result, _ = await run(w, client, temporal_env, during=cancel_while_ports_wait, hold_activity=True)
    assert result in ({"cancelled": True}, {"refused": True})
    assert not w.native.calls and not w.http["requests"]
    assert await sync_to_async(DeploymentExecutionReceipt.objects.filter(kind="BOUND").count)() == 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_restored_current_policy_requires_fresh_original_uid_observation_on_same_run(
    native_world, client, temporal_env
):
    w = native_world
    from django.db import connections

    from astrolift_identity.models import Policy
    from astrolift_services.tests.test_gcp_deployment_context_2278 import approval_policy

    policies = []
    original_write = w.http["after_write"]
    namespace_uids, retained_before_restore = [], []

    def withdraw_policy_after_create(method, path):
        original_write(method, path)
        if method == "POST" and w.http["objects"][path]["kind"] == "Namespace":
            namespace_uids.append(w.http["objects"][path]["metadata"]["uid"])
            try:
                Policy.objects.filter(pk=policies[0].pk).update(
                    conditions=[{"kind": "approval_required", "min_approvers": 2}]
                )
            finally:
                connections["default"].close()

    def restore_original_policy():
        ack = GCPIdentityAcknowledgement.objects.get(kind="PREPARATION")
        journal = GCPGKEPreparationJournal.objects.get()
        assert journal.state == "SENT" and journal.prepared_revision == journal.observed_revision == 0
        assert ack.acknowledgement["uid"] == namespace_uids[0]
        retained_before_restore.append(ack.acknowledgement_sha256)
        Policy.objects.filter(pk=policies[0].pk).update(
            conditions=[{"kind": "approval_required", "min_approvers": 1}]
        )
        w.after_refusal = None

    w.http["after_write"], w.after_refusal = withdraw_policy_after_create, restore_original_policy
    result, _ = await run(
        w,
        client,
        temporal_env,
        # Initial ordinary deploy decorators stay unchanged. This policy is
        # introduced only after actual HTTP dispatch/vote/enqueue, before Worker.
        before=lambda: policies.append(approval_policy(w)),
        workflow_type=SameRunRecoveryWorkflow,
    )
    first, second = result["attempts"]
    assert first == {"refused": True}
    assert (
        second["configuration_observed"]
        and second["runtime_observation_reason"] == "WORKLOAD_ROLLOUT_OBSERVED"
    )
    assert second["workload_ready"] and not second["invocation_verified"]
    assert len(namespace_uids) == len(retained_before_restore) == 1
    assert w.native.writes == 1
    for cls in (GCPGKEPreparationJournal, GCPWorkloadIdentityJournal, GCPGKEAppApplyJournal):
        assert await sync_to_async(lambda cls=cls: cls.objects.get().state)() == "OBSERVED"
    assert await sync_to_async(
        GCPIdentityAcknowledgement.objects.filter(acknowledgement_sha256=retained_before_restore[0]).exists
    )()
    bound = await sync_to_async(DeploymentExecutionReceipt.objects.get)(kind="BOUND")
    assert bound.run_id == w.sdk_run_id


@pytest.mark.asyncio
async def test_registered_activity_drains_delayed_real_heartbeat_callback_on_refusal(
    native_world, client, temporal_env
):
    w = native_world
    from threading import current_thread

    from temporalio import activity

    entered, release, returned = Event(), Event(), Event()
    real_heartbeat = activity.heartbeat

    def delayed_empty_heartbeat(*details):
        assert not details
        real_heartbeat(*details)
        if current_thread().name == "native-configuration-heartbeat":
            entered.set()
            assert release.wait(6)
            returned.set()

    w.monkeypatch.setattr(activity, "heartbeat", delayed_empty_heartbeat)
    w.before_ports = lambda: entered.wait(5) or pytest.fail("registered heartbeat must start")

    def unsupported_before_effect():
        type(w.medops_app).objects.filter(pk=w.medops_app.pk).update(
            manifest_raw=w.medops_app.manifest_raw.replace("replicas = 1", "replicas = 0")
        )

    async def release_in_flight_heartbeat(_handle):
        assert await sync_to_async(entered.wait)(5)
        try:
            # Delay return after the real SDK send, longer than the old 2s join.
            await asyncio.sleep(2.2)
            assert not returned.is_set()
            assert any(thread.name == "native-configuration-heartbeat" for thread in active_threads())
        finally:
            release.set()

    result, _ = await run(
        w,
        client,
        temporal_env,
        before=unsupported_before_effect,
        during=release_in_flight_heartbeat,
    )
    assert result == {"refused": True} and returned.is_set()
    assert not w.native.calls and not w.http["requests"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["image", "saved_literal", "raw_manifest"])
async def test_render_source_withdrawal_during_actual_sdk_read_refuses_before_gsa_create(
    native_world, client, temporal_env, changed
):
    w = native_world
    from astrolift_lifecycle.models import Deployment

    held, release = Event(), Event()

    def hold_before_create(name):
        if name == "GetServiceAccount" and not w.native.writes:
            held.set()
            assert release.wait(8)

    async def change_retained_render_tuple(_handle):
        assert await sync_to_async(held.wait)(120)
        try:

            def change():
                if changed == "image":
                    Deployment.objects.filter(pk=w.starts[0].deployment_id).update(image_tag="changed-image")
                elif changed == "saved_literal":
                    Deployment.objects.filter(pk=w.starts[0].deployment_id).update(
                        secret_snapshot={"literals": {"PRIVATE_MARKER": "private-manifest-canary"}}
                    )
                else:
                    type(w.medops_app).objects.filter(pk=w.medops_app.pk).update(
                        manifest_raw=w.medops_app.manifest_raw + "\n# changed raw manifest\n"
                    )

            await sync_to_async(change)()
        finally:
            release.set()

    w.native.after = hold_before_create
    result, _ = await run(w, client, temporal_env, during=change_retained_render_tuple)
    assert result == {"refused": True}
    assert not w.native.writes and not w.http["effects"] and not w.iam_wires
    assert await sync_to_async(GCPIdentityAcknowledgement.objects.count)() == 0
    assert await sync_to_async(GCPGKEPreparationJournal.objects.count)() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("withdrawal", ["render", "token"])
async def test_runtime_db_boundary_rechecks_render_and_original_caller_after_journal_read(
    native_world, client, temporal_env, monkeypatch, withdrawal
):
    from astrolift_lifecycle.models import Deployment
    from astrolift_services.gcp_gke_app_apply_journal import AppApplyStore

    w = native_world
    original = AppApplyStore.validate_runtime_handoff
    changed = []

    def withdraw_after_current_read(store, *args, **kwargs):
        original(store, *args, **kwargs)
        if not changed:
            changed.append(len(w.http["requests"]))
            if withdrawal == "render":
                Deployment.objects.filter(pk=w.starts[0].deployment_id).update(image_tag="changed-image")
            else:
                type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)

    monkeypatch.setattr(AppApplyStore, "validate_runtime_handoff", withdraw_after_current_read)
    result, _ = await run(w, client, temporal_env)
    assert result == {"refused": True}
    assert len(changed) == 1 and len(w.http["requests"]) == changed[0]
    assert not any(path.endswith("/pods?limit=257") for _, path, _ in w.http["requests"])
    assert await sync_to_async(lambda: GCPGKEAppApplyJournal.objects.get().state)() == "OBSERVED"
    if withdrawal == "render":
        assert w.failures[-1] == ("NativeConfigurationError", "CONFIGURATION_RENDER_SOURCE_CHANGED")
    else:
        assert w.failures[-1][0] in ("DeploymentExecutionError", "PermissionDenied")
