"""Persisted operation + actual registered AWS/Kubernetes HTTP/Helm proof.

Local fixture events establish admission/recovery/activation behavior, not live
Fluent Bit ingestion or deployed app/CLI acceptance.
"""

import copy
import os
import platform
import shutil
from functools import partial
from pathlib import Path

import pytest
from aws.cloudwatch_collector_render import render_collector
from aws.session import clear_credential_cache
from django.db import DatabaseError, transaction

from astrolift_clusters import log_collector_runtime as runtime
from astrolift_clusters.models import ClusterLogCollectorOperation
from astrolift_clusters.tests import test_log_collector_operation_1706 as admission
from astrolift_clusters.tests.collector_http_1706 import ACCOUNT, AwsSocket
from providers.tests.aws import test_cloudwatch_collector_execution_http as native

world = admission.world
no_search = admission.no_search
ready = admission.ready
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(scope="module")
def collector_artifacts():
    if os.environ.get("COLLECTOR_RENDER_ARTIFACTS"):
        directory = Path(os.environ["COLLECTOR_RENDER_ARTIFACTS"])
        target = (
            platform.system().lower()
            + "-"
            + {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64"}.get(platform.machine(), "unsupported")
        )
        archive = (directory / "fluent-bit-0.58.2.tgz").read_bytes()
        binary = str(directory / f"helm-v4.3.0-{target}")
    else:
        binary = shutil.which("helm")
        if not binary:
            pytest.skip("Native collector runtime proof requires the pinned Helm runtime")
        import subprocess

        version = subprocess.run(
            [binary, "version", "--template", "{{.Version}}"], capture_output=True, text=True, timeout=10
        )
        if version.returncode or version.stdout.strip() != "v4.3.0":
            pytest.skip("Native collector runtime proof requires Helm4.3.0")
        from urllib.request import urlopen

        with urlopen(
            "https://github.com/fluent/helm-charts/releases/download/fluent-bit-0.58.2/fluent-bit-0.58.2.tgz",
            timeout=20,
        ) as response:
            archive = response.read(10_000_001)
    renderer = partial(render_collector, helm_binary=binary)

    return archive, renderer


@pytest.fixture
def native_setup(ready, tmp_path, monkeypatch, collector_artifacts):
    gen = native.wire.__wrapped__(tmp_path)
    wire = next(gen)
    ready.cluster.endpoint = wire["kube"]._api_client.configuration.host
    ready.cluster.provider_config["account_id"] = ACCOUNT
    ready.cluster.provider_config["credential"] = {
        "mode": "aws_assume_role",
        "role_arn": f"arn:aws:iam::{ACCOUNT}:role/registered-cluster",
        "external_id": "owned-fixture-external-id",
    }
    ready.cluster.save()
    wire["objects"]["/api/v1/namespaces/astrolift-system"]["metadata"]["labels"].update(
        {"astrolift.io/managed-by": "platform", "astrolift.io/cluster-guid": str(ready.cluster.guid)}
    )
    aws = AwsSocket(ready.cluster, wire)
    check = runtime.DurableCollectorGate.check

    def recorded(gate, action):
        wire["last_check"] = action
        return check(gate, action)

    monkeypatch.setattr(runtime.DurableCollectorGate, "check", recorded)
    try:
        yield ready, wire, aws, collector_artifacts
    finally:
        aws.close()
        clear_credential_cache()
        try:
            next(gen)
        except StopIteration:
            pass


@pytest.fixture
def complete(native_setup):
    ready, wire, aws, (archive, renderer) = native_setup
    request, result = admission.install(ready)
    assert result["ok"]
    row = ClusterLogCollectorOperation.objects.get(guid=result["data"]["id"])

    def attempt(**kwargs):
        return runtime.install_attempt(
            row.pk,
            row.generation,
            execution=(row.workflow_id, row.workflow_run_id, "InstallClusterLogCollectorWorkflow"),
            client_session=aws.new_session(),
            archive_fetcher=lambda gate: archive,
            renderer=renderer,
            idle=lambda: None,
            **kwargs,
        )

    yield ready, row, request, wire, aws, attempt


def test_complete_exact_cleanup_then_atomic_activation(complete):
    world, row, request, wire, aws, attempt = complete
    before = copy.deepcopy(world.cluster.provider_config)
    outcome = attempt()
    row.refresh_from_db()
    world.cluster.refresh_from_db()
    assert outcome == row.status == "activated", (row.error_code, row.error_message)
    assert (
        row.post_loss_verified_at
        and row.activated_at
        and row.activated_cluster_version == world.cluster.version
    )
    assert not row.cleanup_pending and row.event_hash and row.checkpoints["executor"]["probe_deleted"]
    assert row.checkpoints["role_identity"]["id"] == aws.role_id
    assert row.checkpoints["group_identity"]["created"] == aws.group_time
    assert {
        k: v for k, v in world.cluster.provider_config.items() if k not in {"log_driver", "log_config"}
    } == before
    assert (
        world.cluster.provider_config["log_config"]["log_group"]
        == f"/astrolift/clusters/{world.cluster.guid}/pods"
    )
    assert "role_arn" not in world.cluster.provider_config["log_config"]
    assert aws.assumption == {
        "role": before["credential"]["role_arn"],
        "external_id": "owned-fixture-external-id",
    }
    deleted = [(i, c) for i, c in enumerate(wire["calls"]) if c[0] == "DELETE"]
    assert (
        len(deleted) == 1
        and deleted[0][1][2]["preconditions"]["uid"] == wire["deleted_probe"]["metadata"]["uid"]
    )
    assert any(i > deleted[0][0] and c[0] == "POST" and c[1] == "/" for i, c in enumerate(wire["calls"]))
    effects = len(wire["calls"]), len(aws.calls)
    assert attempt() == "activated" and effects == (len(wire["calls"]), len(aws.calls))
    replay = admission.agent_cases.gql(world, admission.INSTALL, {"input": request})["data"][
        "astroliftInstallClusterLogCollector"
    ]
    assert replay["data"]["id"] == str(row.guid) and replay["data"]["status"] == "ACTIVATED"


def test_pending_reader_grant_no_probe_no_activation_and_recovery(complete):
    world, row, request, wire, aws, attempt = complete
    aws.reader_denied = True
    assert attempt() == "reader_grant_pending"
    row.refresh_from_db()
    world.cluster.refresh_from_db()
    assert row.activated_at is None and "log_driver" not in world.cluster.provider_config
    assert not any(obj["kind"] == "Pod" for obj in wire["objects"].values())
    ids = copy.deepcopy(row.checkpoints)
    created = aws.calls.count("CreateRole"), aws.calls.count("CreateLogGroup")
    aws.reader_denied = False
    assert attempt() == "activated"
    row.refresh_from_db()
    assert (
        row.checkpoints["role_identity"] == ids["role_identity"]
        and row.checkpoints["group_identity"] == ids["group_identity"]
    )
    assert created == (aws.calls.count("CreateRole"), aws.calls.count("CreateLogGroup"))


@pytest.mark.parametrize("fault", ["endpoint", "role", "group", "source", "authority"])
def test_refuses_changed_identity_or_source_without_activation(complete, fault):
    world, row, request, wire, aws, attempt = complete
    if fault == "endpoint":
        aws.foreign_endpoint = True
    else:
        aws.reader_denied = True
        assert attempt() == "reader_grant_pending"
        aws.reader_denied = False
        if fault == "role":
            aws.role_id = "ROLE-REPLACED"
        if fault == "group":
            aws.group_time += 1
        if fault == "source":
            world.cluster.region = "us-east-1"
            world.cluster.save()
        if fault == "authority":
            world.token.is_revoked = True
            world.token.save()
    before = sum(c.startswith(("Create", "Put", "Update")) for c in aws.calls)
    assert attempt() == "refused"
    row.refresh_from_db()
    world.cluster.refresh_from_db()
    assert row.activated_at is None and "log_driver" not in world.cluster.provider_config
    assert before == sum(c.startswith(("Create", "Put", "Update")) for c in aws.calls)


def test_partial_write_failure_retry_preserves_owned_identity(complete):
    world, row, request, wire, aws, attempt = complete
    aws.fail_action = "PutRolePolicy"
    assert attempt() == "uncertain"
    row.refresh_from_db()
    ids = copy.deepcopy(row.checkpoints)
    assert ids["role_identity"] and ids["group_identity"]
    assert not row.activated_at and all(c[0] == "GET" for c in wire["calls"])
    aws.fail_action = None
    assert attempt() == "activated"
    row.refresh_from_db()
    assert (
        row.checkpoints["role_identity"] == ids["role_identity"]
        and aws.calls.count("CreateRole") == 1
        and aws.calls.count("CreateLogGroup") == 1
    )


def test_accepted_terminating_probe_cleanup_recovery(complete):
    world, row, request, wire, aws, attempt = complete
    wire["terminating_delete"] = True
    assert attempt() == "probe_deletion_pending"
    row.refresh_from_db()
    assert row.cleanup_pending and row.activated_at is None
    [path] = [p for p, obj in wire["objects"].items() if obj["kind"] == "Pod"]
    wire["deleted_probe"] = wire["objects"].pop(path)
    wire["terminating_delete"] = False
    assert attempt() == "activated"
    assert len([c for c in wire["calls"] if c[0] == "DELETE"]) == 1


def test_stale_generation_and_run_cannot_touch_provider(complete):
    world, row, request, wire, aws, attempt = complete
    assert (
        runtime.install_attempt(
            row.pk,
            row.generation,
            execution=(row.workflow_id, "foreign-run", "InstallClusterLogCollectorWorkflow"),
        )
        == "refused"
    )
    assert (
        runtime.install_attempt(
            row.pk,
            row.generation + 1,
            execution=(row.workflow_id, row.workflow_run_id, "InstallClusterLogCollectorWorkflow"),
        )
        == "refused"
    )
    assert runtime.install_attempt(row.pk, row.generation) == "refused"
    assert not aws.calls and not wire["calls"]


@pytest.mark.parametrize(
    "field,value",
    [("retention_days", 7), ("source_digest", "f" * 64), ("probe_image", "foreign"), ("generation", -1)],
)
def test_database_preserves_original_request(complete, field, value):
    world, row, request, wire, aws, attempt = complete
    with pytest.raises(DatabaseError), transaction.atomic():
        ClusterLogCollectorOperation.objects.filter(pk=row.pk).update(**{field: value})
    row.refresh_from_db()
    assert getattr(row, field) != value


def test_lost_delete_response_recovers_observed_disappearance_without_fake_ack(complete):
    world, row, request, wire, aws, attempt = complete
    wire["lost_delete"] = True
    assert attempt() == "uncertain"
    row.refresh_from_db()
    state = row.checkpoints["executor"]
    assert state["delete_intent"] and not state.get("delete_accepted") and not row.activated_at
    assert attempt() == "activated"
    row.refresh_from_db()
    assert row.checkpoints["executor"]["probe_deleted"] and not row.checkpoints["executor"].get(
        "delete_accepted"
    )
    assert len([c for c in wire["calls"] if c[0] == "DELETE"]) == 1


@pytest.mark.parametrize("target", ["role", "group"])
def test_physical_replacement_after_post_loss_read_refuses_activation(complete, target):
    world, row, request, wire, aws, attempt = complete

    def replace():
        if wire.get("deleted_probe"):
            if target == "role":
                aws.role_id = "REPLACED-AFTER-READ"
            else:
                aws.group_time += 1

    aws.after_filter = replace
    assert attempt() == "refused"
    row.refresh_from_db()
    world.cluster.refresh_from_db()
    assert (
        row.error_code == "RESOURCE_REPLACED"
        and row.activated_at is None
        and "log_driver" not in world.cluster.provider_config
    )
    assert not row.cleanup_pending


def test_authority_withdrawal_during_preparation_is_refused_not_uncertain(complete, monkeypatch):
    world, row, request, wire, aws, attempt = complete
    check = runtime.DurableCollectorGate.check

    def withdraw(gate, action):
        if action == "aws.effect.put_role_policy":
            gate.close()
            world.token.is_revoked = True
            world.token.save()
        return check(gate, action)

    monkeypatch.setattr(runtime.DurableCollectorGate, "check", withdraw)
    assert attempt() == "refused"
    row.refresh_from_db()
    assert row.error_code == "PERMISSION_DENIED" and row.activated_at is None
    assert "PutRolePolicy" not in aws.calls


def test_binding_change_before_activation_preserves_new_external_reader(complete, monkeypatch):
    world, row, request, wire, aws, attempt = complete
    check = runtime.DurableCollectorGate.check

    def change(gate, action):
        if action == "collector.result":
            gate.close()
            world.cluster.refresh_from_db()
            world.cluster.provider_config.update(
                {"log_driver": "loki", "log_config": {"endpoint": "https://external.invalid"}}
            )
            world.cluster.save()
        return check(gate, action)

    monkeypatch.setattr(runtime.DurableCollectorGate, "check", change)
    assert attempt() == "refused"
    row.refresh_from_db()
    world.cluster.refresh_from_db()
    assert world.cluster.provider_config["log_driver"] == "loki" and row.activated_at is None
    assert not row.cleanup_pending


@pytest.mark.parametrize("fault", ["fargate", "namespace"])
def test_unsupported_coverage_or_namespace_refuses_before_aws_writes(complete, fault):
    world, row, request, wire, aws, attempt = complete
    if fault == "fargate":
        wire["nodes"][0]["metadata"]["labels"]["eks.amazonaws.com/compute-type"] = "fargate"
    else:
        wire["objects"]["/api/v1/namespaces/astrolift-system"]["metadata"]["labels"][
            "astrolift.io/managed-by"
        ] = "foreign"
    assert attempt() == "refused"
    row.refresh_from_db()
    assert row.error_code == ("UNSUPPORTED_NODE_COVERAGE" if fault == "fargate" else "NAMESPACE_UNOWNED")
    if fault == "fargate":
        assert row.coverage == "UNSUPPORTED"
    assert not any(c.startswith(("Create", "Put", "Update")) for c in aws.calls)


def test_no_provider_body_or_synthetic_credentials_in_diagnostics(complete, caplog, capsys):
    world, row, request, wire, aws, attempt = complete
    aws.reader_denied = True
    assert attempt() == "reader_grant_pending"
    captured = capsys.readouterr()
    text = caplog.text + captured.out + captured.err
    assert all(
        marker not in text
        for marker in (
            "PRIVATE_PROVIDER_MARKER",
            "PRIVATE_READER_MARKER",
            "ASIACOLLECTORFIXTURE",
            "local-fixture-only",
        )
    )


@pytest.mark.parametrize("loss", [False, True])
async def test_actual_temporal_original_operation_and_lost_dispatch_recovery(
    native_setup, temporal_env, monkeypatch, loss
):
    from asgiref.sync import sync_to_async

    from astrolift_workflows import client as workflow_client
    from astrolift_workflows.activities.install_cluster_log_collector import (
        install_cluster_log_collector_attempt,
    )
    from astrolift_workflows.workflows.install_cluster_log_collector import InstallClusterLogCollectorWorkflow
    from core.testing.temporal import temporal_worker

    world, wire, aws, (archive, renderer) = native_setup

    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
    monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(workflow_client, "_task_queue", lambda: "astrolift-test")
    monkeypatch.setattr(workflow_client, "start_workflow_once", world.original_start)
    monkeypatch.setattr(workflow_client, "recover_workflow_once", world.original_recover)
    attempt = runtime.install_attempt

    def bounded_attempt(operation_id, generation, *, execution):
        return attempt(
            operation_id,
            generation,
            execution=execution,
            client_session=aws.new_session(),
            archive_fetcher=lambda gate: archive,
            renderer=renderer,
            idle=lambda: None,
        )

    monkeypatch.setattr(runtime, "install_attempt", bounded_attempt)
    if loss:

        def lose(*args, **kwargs):
            world.original_start(*args, **kwargs)
            raise RuntimeError("synthetic dispatch reply lost")

        monkeypatch.setattr(workflow_client, "start_workflow_once", lose)
    async with temporal_worker(
        temporal_env,
        workflows=[InstallClusterLogCollectorWorkflow],
        activities=[install_cluster_log_collector_attempt],
    ):
        request, result = await sync_to_async(admission.install)(world)
        row = await sync_to_async(ClusterLogCollectorOperation.objects.get)(guid=result["data"]["id"])
        handle = temporal_env.client.get_workflow_handle(row.workflow_id, run_id=row.workflow_run_id or None)
        assert await handle.result() == "activated"
        await sync_to_async(row.refresh_from_db)()
        assert row.activated_at and row.post_loss_verified_at and row.workflow_run_id
        history = await handle.fetch_history()
        started = history.events[0].workflow_execution_started_event_attributes
        assert await temporal_env.client.data_converter.decode(started.input.payloads) == [row.pk, 0]
        raw = history.to_json()
        assert all(
            marker not in raw
            for marker in (
                "ASIACOLLECTORFIXTURE",
                "local-fixture-only",
                "owned-fixture-external-id",
                "PRIVATE_PROVIDER_MARKER",
            )
        )
        replay = await sync_to_async(admission.agent_cases.gql)(world, admission.INSTALL, {"input": request})
        assert replay["data"]["astroliftInstallClusterLogCollector"]["data"]["id"] == str(row.guid)
