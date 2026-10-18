"""Operator settlement of a lost send, and native rollback, through the ordinary workflow (#2278).

Actual HTTP dispatch, PostgreSQL journals, Temporal workers and history replay,
with the SDK-faithful GCP fakes and owned TLS Kubernetes of the pipeline suite.
"""

from datetime import timedelta
from threading import Event

import pytest
from asgiref.sync import sync_to_async
from django.db import DatabaseError, transaction
from django.utils import timezone

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import APPROVE, reviewer
from astrolift_services.gcp_native_journal_resolution import JournalResolutionError, resolve_native_journals
from astrolift_services.models import (
    GCPGKEPreparationJournal,
    GCPGKEPreparationOperation,
    GCPNativeJournalResolution,
)
from astrolift_services.tests.test_cluster_model_queries_2213 import grant
from astrolift_services.tests.test_model_connection_2270 import graphql_http
from astrolift_workflows.tests import test_native_deploy_pipeline_2278 as pipeline
from astrolift_workflows.workflows import deploy_app
from core.permissions import Permission

native_world = pipeline.native_world
ATTESTATION = "worker pod gone since 09:10; stray namespace checked and removed"
ROLLBACK = (
    "mutation($input:DeploymentByIdInput!){rollbackDeployment(input:$input)"
    "{ok errors{code message} data{id status}}}"
)


async def _lose_worker_after_namespace_create(w, client, temporal_env):
    """The pipeline suite's lost-worker case: the namespace create lands, the reply is lost."""
    from temporalio import activity

    heartbeat_timeout = deploy_app._NATIVE_HEARTBEAT
    w.monkeypatch.setattr(deploy_app, "_NATIVE_HEARTBEAT", timedelta(seconds=3))
    created, release, silent = Event(), Event(), Event()
    real_heartbeat = activity.heartbeat
    w.monkeypatch.setattr(activity, "heartbeat", lambda *d: None if silent.is_set() else real_heartbeat(*d))
    original = w.http["after_write"]

    def lose_worker_after_create(method, path):
        original(method, path)
        if method == "POST" and w.http["objects"][path]["kind"] == "Namespace":
            silent.set()
            created.set()
            assert release.wait(900)

    async def until_workflow_refused(handle):
        assert await sync_to_async(created.wait, thread_sensitive=False)(1800)
        try:
            await handle.result()
        finally:
            release.set()

    w.http["after_write"] = lose_worker_after_create
    result, _, first = await pipeline.deploy(
        w, client, temporal_env, during=until_workflow_refused, sandboxed=False
    )
    assert not result.ok and first.status == "failed"
    for _ in range(600):
        if w.failures:
            break
        await sync_to_async(Event().wait, thread_sensitive=False)(0.1)
    assert w.failures
    # The worker is back: later Deployments heartbeat normally again.
    w.http["after_write"] = original
    silent.clear()
    w.monkeypatch.setattr(deploy_app, "_NATIVE_HEARTBEAT", heartbeat_timeout)
    return first


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_operator_settles_a_lost_send_then_the_next_deployment_runs(native_world, client, temporal_env):
    w = native_world
    await _lose_worker_after_namespace_create(w, client, temporal_env)
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.state in ("SENT", "UNKNOWN")
    app, cluster = w.medops_app, w.env.tenant_cluster
    resolve = sync_to_async(resolve_native_journals)

    # A send may still be on the wire: no settlement inside the guard window,
    # and none without an operator and a real attestation.
    with pytest.raises(JournalResolutionError, match="RESOLUTION_GUARD_WINDOW"):
        await resolve(app, cluster, user=w.user, attestation=ATTESTATION)
    with pytest.raises(JournalResolutionError, match="ATTESTATION_REQUIRED"):
        await resolve(app, cluster, user=w.user, attestation="ok", now=timezone.now() + timedelta(hours=3))
    unchanged = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert (unchanged.state, unchanged.version) == (journal.state, journal.version)
    assert not await sync_to_async(GCPNativeJournalResolution.objects.exists)()

    # The operator removes the namespace the lost create made, then settles.
    strays = [path for path, row in w.http["objects"].items() if row["kind"] == "Namespace"]
    assert len(strays) == 1
    del w.http["objects"][strays[0]]
    [resolution] = await resolve(
        app, cluster, user=w.user, attestation=ATTESTATION, now=timezone.now() + timedelta(hours=3)
    )
    assert resolution.kind == "PREPARATION" and resolution.prior_state == journal.state
    assert resolution.unresolved == [
        {"kind": "Namespace", "path": strays[0], "method": "POST", "phase": "SENT"}
    ]
    settled = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert settled.state == "RESOLVED" and settled.ledger["pending"] == []
    operation = await sync_to_async(GCPGKEPreparationOperation.objects.get)(journal=settled)
    assert operation.state == "RESOLVED" and operation.ledger == settled.ledger

    def mutate():
        with transaction.atomic():
            GCPNativeJournalResolution.objects.filter(pk=resolution.pk).update(attestation="rewritten")

    with pytest.raises(DatabaseError):
        await sync_to_async(mutate)()

    result, history, second = await pipeline.deploy(w, client, temporal_env, sandboxed=False)
    assert result.ok, (result.message, w.failures)
    assert pipeline._scheduled(history) == [*pipeline.NATIVE, "astrolift.app.complete_native_endpoint"]
    assert second.status == "running"
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.generation == 2 and journal.state == "OBSERVED"
    assert await pipeline._states() == {name: ["OBSERVED"] for name in await pipeline._states()}


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_native_rollback_runs_the_prior_image_and_marks_the_bad_deploy_rolled_back(
    native_world, client, temporal_env
):
    w = native_world
    pipeline.persistent_policies(w)
    result, _, first = await pipeline.deploy(w, client, temporal_env)
    assert result.ok, (result.message, w.failures)
    result, _, second = await pipeline.deploy(w, client, temporal_env)
    assert result.ok, (result.message, w.failures)
    await sync_to_async(grant)(w, Permission.APP_ROLLBACK, "APP", w.medops_app.pk)

    def rollback(w, client):
        reply = graphql_http(client, w.headers, ROLLBACK, {"input": {"id": str(second.guid)}})
        outcome = reply["data"]["rollbackDeployment"]
        assert outcome["ok"], outcome["errors"]
        row = Deployment.objects.get(guid=outcome["data"]["id"])
        if row.status == "pending_approval":
            _, headers = reviewer(w, "native-rollback-e2e-")
            approved = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})
            assert approved["data"]["approveDeployment"]["ok"]
        return row, None, None

    result, history, row = await pipeline.deploy(w, client, temporal_env, dispatch=rollback)
    assert result.ok, (result.message, w.failures)
    assert pipeline._scheduled(history) == [*pipeline.NATIVE, "astrolift.app.complete_native_endpoint"]
    assert row.status == "running" and row.trigger_kind == "rollback"
    assert (row.image_tag, row.promoted_from_id) == (first.image_tag, first.pk)
    statuses = await sync_to_async(lambda: dict(Deployment.objects.values_list("pk", "status")))()
    assert statuses[second.pk] == "rolled_back" and statuses[first.pk] == "superseded"
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.generation == 3 and journal.state == "OBSERVED"
