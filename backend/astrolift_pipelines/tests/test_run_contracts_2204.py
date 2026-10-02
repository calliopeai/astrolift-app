from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from temporalio import workflow

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Organization
from astrolift_pipelines.models import Pipeline, PipelineRun
from astrolift_pipelines.run_contracts import (
    PipelineContractError,
    dispatch_pipeline_run,
    observe_pipeline_cancellation,
    request_pipeline_cancellation,
    reserve_pipeline_run,
)
from astrolift_workflows import client
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.testing.temporal import temporal_worker

pytestmark = pytest.mark.django_db(transaction=True)


@workflow.defn(name="PipelineRunWorkflow", sandboxed=False)
class HoldingPipeline:
    def __init__(self):
        self.cancelled = False

    @workflow.run
    async def run(self, run_pk: int):
        await workflow.wait_condition(lambda: self.cancelled)
        return run_pk

    @workflow.signal(name="cancel")
    def cancel(self):
        self.cancelled = True


@pytest.fixture(autouse=True)
def local_cache(settings, monkeypatch):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    monkeypatch.setattr("constance.settings.DATABASE_CACHE_BACKEND", None)


@pytest.fixture
def world(permission_resolver):
    org = Organization.objects.create(name="Pipeline contracts", slug=f"pipeline-contracts-{uuid4().hex[:8]}")
    user = get_user_model().objects.create_user(username=f"pipeline-contract-actor-{uuid4().hex[:8]}")
    pipeline = Pipeline.objects.create(
        organization=org, name="reviewed", repo_url="https://example.test/disposable"
    )
    permission_resolver.grant(Permission.APP_UPDATE)
    return SimpleNamespace(
        org=org,
        user=user,
        pipeline=pipeline,
        tenant=TenantContext(organization_id=org.pk, actor_user_id=user.pk),
    )


def reserve(world, key="stable-pipeline-request", **kwargs):
    with tenant_context(world.tenant):
        return reserve_pipeline_run(
            pipeline_id=world.pipeline.guid,
            expected_version=kwargs.pop("version", world.pipeline.version),
            request_id=key,
            user=world.user,
            **kwargs,
        )


def dispatch(world, run):
    with tenant_context(world.tenant):
        return dispatch_pipeline_run(run)


def cancel(world, run, **kwargs):
    with tenant_context(world.tenant):
        return request_pipeline_cancellation(
            run,
            expected_version=kwargs.pop("version", run.version),
            workflow_id=run.temporal_workflow_id,
            temporal_run_id=kwargs.pop("run_id", run.temporal_run_id),
            **kwargs,
        )


async def engine(temporal_env, monkeypatch):
    async def connect():
        return temporal_env.client

    monkeypatch.setattr(client, "_get_client_async", connect)
    monkeypatch.setattr(client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(client, "_task_queue", lambda: "astrolift-test")


def test_concurrent_number_allocation_and_duplicate_key(world):
    def reserve_in_connection(key):
        close_old_connections()
        try:
            return reserve(world, key).pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=6) as pool:
        repeated = list(pool.map(reserve_in_connection, ["same-key"] * 6))
        distinct = list(pool.map(reserve_in_connection, [f"key-{i}" for i in range(6)]))
    assert len(set(repeated)) == 1
    assert len(set(distinct)) == 6
    runs = list(PipelineRun.objects.order_by("run_number"))
    assert [r.run_number for r in runs] == list(range(1, 8))
    assert len({r.temporal_workflow_id for r in runs}) == 7
    runs[-1].soft_delete()
    assert reserve(world, "after-deletion").run_number == 8


@pytest.mark.parametrize("change", ["ref", "version"])
def test_key_is_payload_bound_and_stale_reviews_refused(world, change):
    first = reserve(world)
    with pytest.raises(PipelineContractError):
        reserve(
            world,
            ref="other" if change == "ref" else None,
            version=world.pipeline.version + 1 if change == "version" else world.pipeline.version,
        )
    world.pipeline.name = "changed"
    world.pipeline.save()
    with pytest.raises(PipelineContractError):
        reserve(world, "fresh-stale-key", version=world.pipeline.version - 1)
    assert PipelineRun.objects.count() == 1
    assert reserve(world, version=world.pipeline.version - 1).pk == first.pk


def test_current_permission_and_revoked_credential_prevent_dispatch(world, permission_resolver):
    run = reserve(world)
    permission_resolver.deny(Permission.APP_UPDATE)
    with pytest.raises(PermissionDenied):
        dispatch(world, run)
    permission_resolver.grant(Permission.APP_UPDATE)
    token = ApiToken.objects.create(
        organization=world.org,
        user=world.user,
        name="Disposable",
        token_hash="not-a-real-credential",
        scopes=["write:apps"],
    )
    ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
    state = set_current_api_token(token)
    try:
        with pytest.raises(PermissionDenied, match="no longer valid"):
            dispatch(world, run)
    finally:
        reset_current_api_token(state)
    run.refresh_from_db()
    assert run.temporal_run_id == ""


async def test_real_engine_lost_response_recovery_and_exact_cancel(world, temporal_env, monkeypatch):
    await engine(temporal_env, monkeypatch)
    run = await sync_to_async(reserve)(world)
    async with temporal_worker(temporal_env, workflows=[HoldingPipeline]):
        original = await temporal_env.client.start_workflow(
            HoldingPipeline.run, run.pk, id=run.temporal_workflow_id, task_queue="astrolift-test"
        )
        started = await sync_to_async(dispatch)(world, run)
        assert started.temporal_run_id == original.first_execution_run_id
        assert (await sync_to_async(dispatch)(world, run)).temporal_run_id == started.temporal_run_id
        with pytest.raises(PipelineContractError, match="execution changed"):
            await sync_to_async(cancel)(world, started, run_id="different-incarnation")
        with pytest.raises(PipelineContractError, match="run changed"):
            await sync_to_async(cancel)(world, started, version=started.version - 1)
        acknowledged = await sync_to_async(cancel)(world, started)
        assert acknowledged.cancellation_status == "acknowledged"
        assert acknowledged.status == "pending"
        assert acknowledged.cancellation_observed_at is None
        assert acknowledged.cleanup_status == "pending"
        assert await asyncio.wait_for(original.result(), 10) == run.pk
        observed = await sync_to_async(observe_pipeline_cancellation)(acknowledged)
        assert observed.cancellation_status == "observed"
        assert observed.cancellation_observed_at
        assert observed.cleanup_status == "complete"
        duplicate = await sync_to_async(dispatch)(world, run)
        assert duplicate.temporal_run_id == started.temporal_run_id
        assert (await original.describe()).status.name == "COMPLETED"


async def test_engine_outage_cannot_report_cancelled(world, temporal_env, monkeypatch):
    await engine(temporal_env, monkeypatch)
    run = await sync_to_async(reserve)(world)
    async with temporal_worker(temporal_env, workflows=[HoldingPipeline]):
        started = await sync_to_async(dispatch)(world, run)
        monkeypatch.setattr(client, "_temporal_enabled", lambda: False)
        uncertain = await sync_to_async(cancel)(world, started)
        assert uncertain.cancellation_status == "uncertain"
        assert uncertain.status == "pending"
        assert uncertain.cancellation_observed_at is None
        await temporal_env.client.get_workflow_handle(
            started.temporal_workflow_id, run_id=started.temporal_run_id
        ).terminate(reason="disposable cleanup")


async def test_changed_incarnation_is_not_cancelled(world, temporal_env, monkeypatch):
    await engine(temporal_env, monkeypatch)
    run = await sync_to_async(reserve)(world)
    async with temporal_worker(temporal_env, workflows=[HoldingPipeline]):
        original = await temporal_env.client.start_workflow(
            HoldingPipeline.run, run.pk, id=run.temporal_workflow_id, task_queue="astrolift-test"
        )
        started = await sync_to_async(dispatch)(world, run)
        await original.signal("cancel")
        await original.result()
        neighbor = await temporal_env.client.start_workflow(
            HoldingPipeline.run, run.pk + 1, id=run.temporal_workflow_id, task_queue="astrolift-test"
        )
        try:
            refusal = await sync_to_async(cancel)(world, started)
            assert refusal.cancellation_status == "uncertain"
            assert (await neighbor.describe()).status.name == "RUNNING"
        finally:
            await neighbor.terminate(reason="disposable cleanup")
