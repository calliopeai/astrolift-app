"""Saved-source redeploys at the real public/DB/Temporal boundary."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio import activity, workflow
from temporalio.worker import Worker

from astrolift_identity.models import RoleBinding
from astrolift_lifecycle.models import Deployment, DeploymentExecutionReceipt, DeploymentIdentityOrigin
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import APPROVE, reviewer
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import world as public_world
from astrolift_services.tests.test_model_connection_2270 import graphql_http
from astrolift_workflows.client import WorkflowHandle
from astrolift_workflows.inputs import DeployAppInput

pytestmark = pytest.mark.django_db(transaction=True)
REDEPLOY = (
    "mutation($input:DeploymentByIdInput!){redeployApp(input:$input){ok errors{code message} data{id}}}"
)
SHA = "a1" * 20


@pytest.fixture
def world(monkeypatch):
    return public_world.__wrapped__(monkeypatch)


def source(w, commit=SHA):
    w.medops_app.build_mode = "platform_build"
    w.medops_app.build_strategy = "dockerfile"
    w.medops_app.source_repo = "example/source"
    w.medops_app.default_branch = "moving-main"
    w.medops_app.save()
    return Deployment.objects.create(
        registered_app=w.medops_app,
        app_environment=w.env,
        status="running",
        image_tag="original-image",
        image_digest="sha256:" + "1" * 64,
        commit_sha=commit,
        config_snapshot={"replicas": 2},
    )


def redeploy(w, client, row):
    return graphql_http(client, w.headers, REDEPLOY, {"input": {"id": str(row.guid)}})["data"]["redeployApp"]


def counts():
    return (
        Deployment.objects.count(),
        DeploymentIdentityOrigin.objects.count(),
        DeploymentExecutionReceipt.objects.count(),
    )


@pytest.mark.parametrize("commit", [SHA, "b2" * 32])
def test_http_copies_saved_source_into_row_and_dispatch(world, client, commit):
    original = source(world, commit)
    result = redeploy(world, client, original)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    assert row.commit_sha == commit
    assert world.starts[0][1].commit_sha == commit
    assert (row.image_tag, row.image_digest, row.config_snapshot) == (
        original.image_tag,
        original.image_digest,
        original.config_snapshot,
    )


@pytest.mark.parametrize("commit", ["", "main", "refs/heads/main", "a" * 39, "a" * 41, "g" * 40])
def test_missing_or_invalid_rebuild_source_refuses_before_durable_effects(world, client, commit):
    original = source(world, commit)
    before = counts()
    result = redeploy(world, client, original)
    assert not result["ok"] and result["errors"][0]["code"] == "PRECONDITION"
    assert result["errors"][0]["message"] == (
        "The original deployment has no valid immutable source commit. "
        "Start a new deployment with an explicitly reviewed source ref."
    )
    assert before == counts() and not world.starts


@pytest.mark.parametrize(
    "mode,strategy",
    [("ci_pushed", "dockerfile"), ("platform_build", "off"), ("ci_pushed", "off"), ("none", "dockerfile")],
)
def test_artifact_only_redeploy_keeps_blank_source_compatibility(world, client, mode, strategy):
    original = source(world, "")
    world.medops_app.build_mode, world.medops_app.build_strategy = mode, strategy
    world.medops_app.save()
    result = redeploy(world, client, original)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    assert row.commit_sha == world.starts[0][1].commit_sha == ""
    assert row.image_digest == original.image_digest


def test_pending_approval_retains_source_for_later_dispatch(world, client):
    original = source(world)
    world.env.required_approvals = 1
    world.env.save()
    result = redeploy(world, client, original)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    assert row.commit_sha == SHA and row.status == "pending_approval" and not world.starts
    _, headers = reviewer(world, "redeploy-pin-")
    result = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
        "approveDeployment"
    ]
    assert result["ok"], result["errors"]
    assert world.starts[0][1].commit_sha == SHA


@pytest.mark.parametrize("withdrawal", ["grant", "scope", "token"])
def test_source_pin_does_not_bypass_current_authority(world, client, withdrawal):
    original = source(world)
    if withdrawal == "grant":
        RoleBinding.objects.filter(user=world.user).delete()
    elif withdrawal == "scope":
        world.token.scopes = ["read:apps"]
        world.token.save()
    else:
        world.token.is_revoked = True
        world.token.save()
    before = counts()
    reply = client.post(
        "/app/gql/config/",
        json.dumps({"query": REDEPLOY, "variables": {"input": {"id": str(original.guid)}}}),
        content_type="application/json",
        **world.headers,
    )
    if withdrawal == "token":
        assert reply.status_code == 401
    else:
        assert reply.status_code == 200
        payload = reply.json()
        assert payload.get("errors") or not payload["data"]["redeployApp"]["ok"]
    assert counts() == before and not world.starts


@activity.defn(name="source_pin_builder_ref")
def builder_ref(inp: DeployAppInput) -> str:
    from astrolift_workflows.activities.build_image import BuildImageInput, _resolve_source_url

    row = Deployment.objects.select_related("registered_app").get(pk=inp.deployment_id)
    build = BuildImageInput(row.pk, inp.image_tags["app"], inp.commit_sha)
    return _resolve_source_url(row.registered_app, build.commit_sha).rsplit("#", 1)[1]


@workflow.defn(name="DeployAppWorkflow", sandboxed=False)
class SourcePinDispatchProbe:
    @workflow.run
    async def run(self, inp: DeployAppInput) -> str:
        return await workflow.execute_activity(builder_ref, inp, start_to_close_timeout=timedelta(seconds=10))


@pytest.mark.asyncio
async def test_http_actual_temporal_serialization_reaches_builder_saved_ref(
    world, client, temporal_env, monkeypatch
):
    original = await sync_to_async(source)(world)
    queue = "redeploy-source-" + uuid4().hex
    loop = asyncio.get_running_loop()
    handles = []

    def dispatch(name, args, *, workflow_id, **kwargs):
        assert name == "DeployAppWorkflow"
        handle = asyncio.run_coroutine_threadsafe(
            temporal_env.client.start_workflow(name, args[0], id=workflow_id, task_queue=queue), loop
        ).result(timeout=20)
        handles.append(handle)
        return WorkflowHandle(handle.id, handle.first_execution_run_id, True)

    monkeypatch.setattr("astrolift_lifecycle.schema.mutations.helpers.start_workflow", dispatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[SourcePinDispatchProbe],
            activities=[builder_ref],
            activity_executor=executor,
        ):
            result = await sync_to_async(redeploy)(world, client, original)
            assert result["ok"], result["errors"]
            assert await handles[0].result() == SHA
            history = await handles[0].fetch_history()
    assert world.headers["HTTP_AUTHORIZATION"] not in history.to_json()


@pytest.mark.parametrize(
    "value,expected",
    [
        (SHA, True),
        ("B" * 64, True),
        (None, False),
        ("refs/heads/main", False),
        ("a" * 41, False),
        ("a" * 63, False),
        ("a" * 65, False),
        (" " + SHA, False),
    ],
)
def test_resolved_source_validator_matches_source_host_contract(value, expected):
    from astrolift_scm.providers.revisions import is_resolved_commit_sha

    assert is_resolved_commit_sha(value) is expected


@activity.defn(name="astrolift.app.resync_manifest_for_deploy")
async def no_source_sync(deployment_id: int) -> dict:
    return {"status": "unchanged", "workload_count": 1}


@activity.defn(name="astrolift.deploy.pre_flight")
async def no_preflight(deployment_id: int) -> None:
    pass


@activity.defn(name="astrolift.deploy.mark_deploying")
async def no_mark(deployment_id: int) -> None:
    pass


@activity.defn(name="astrolift.deploy.restore_previous_secrets")
async def no_restore(deployment_id: int) -> bool:
    return True


@activity.defn(name="astrolift.deploy.mark_failed")
async def no_failure_write(deployment_id: int, reason: str) -> None:
    pass


@pytest.mark.asyncio
async def test_production_workflow_build_input_uses_saved_ref_before_native_effects(
    world, client, temporal_env, monkeypatch
):
    from temporalio.exceptions import ApplicationError

    from astrolift_workflows.activities.build_image import (
        BuildImageInput,
        _resolve_source_url,
        fetch_app_build_strategy,
    )
    from astrolift_workflows.activities.deployment_identity_origin import validate_deployment_identity_origin
    from astrolift_workflows.workflows.deploy_app import DeployAppWorkflow

    def non_native_source():
        world.endpoint.delete()
        return source(world)

    original = await sync_to_async(non_native_source)()
    queue = "redeploy-production-build-" + uuid4().hex
    loop = asyncio.get_running_loop()
    handles, refs = [], []

    @activity.defn(name="astrolift.build.build_image")
    def inspect_build(inp: BuildImageInput) -> dict:
        row = Deployment.objects.select_related("registered_app").get(pk=inp.deployment_id)
        refs.append(_resolve_source_url(row.registered_app, inp.commit_sha).rsplit("#", 1)[1])
        raise ApplicationError("OWNED_TEST_STOPS_BEFORE_BUILD", non_retryable=True)

    def dispatch(name, args, *, workflow_id, **kwargs):
        handle = asyncio.run_coroutine_threadsafe(
            temporal_env.client.start_workflow(name, args[0], id=workflow_id, task_queue=queue), loop
        ).result(timeout=20)
        handles.append(handle)
        return WorkflowHandle(handle.id, handle.first_execution_run_id, True)

    monkeypatch.setattr("astrolift_lifecycle.schema.mutations.helpers.start_workflow", dispatch)
    activities = [
        validate_deployment_identity_origin,
        no_source_sync,
        no_preflight,
        no_mark,
        fetch_app_build_strategy,
        inspect_build,
        no_restore,
        no_failure_write,
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[DeployAppWorkflow],
            activities=activities,
            activity_executor=executor,
        ):
            result = await sync_to_async(redeploy)(world, client, original)
            assert result["ok"], result["errors"]
            outcome = await handles[0].result()
            history = await handles[0].fetch_history()
    assert outcome["ok"] is False and "OWNED_TEST_STOPS_BEFORE_BUILD" in outcome["message"]
    assert refs == [SHA]
    scheduled = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert "astrolift.build.build_image" in scheduled
    assert "astrolift.app.provision_namespace" not in scheduled
    assert world.headers["HTTP_AUTHORIZATION"] not in history.to_json()


def approved_after_strategy_flip(w, client, commit):
    original = source(w, commit)
    w.medops_app.build_mode = "ci_pushed"
    w.medops_app.save()
    w.env.required_approvals = 1
    w.env.save()
    result = redeploy(w, client, original)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    assert row.status == "pending_approval" and row.commit_sha == commit and not w.starts
    w.medops_app.build_mode = "platform_build"
    w.medops_app.save()
    _, headers = reviewer(w, "delayed-pin-")
    result = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
        "approveDeployment"
    ]
    assert result["ok"], result["errors"]
    return row, w.starts[0][1]


@pytest.mark.parametrize("saved,dispatched", [("", ""), ("main", "main"), (SHA, "b" * 40)])
def test_delayed_rebuild_refuses_missing_or_changed_pin_before_preparation(
    world, client, monkeypatch, saved, dispatched
):
    from astrolift_workflows.activities.build_image import BuildImageInput, _build_image_sync

    row, inp = approved_after_strategy_flip(world, client, saved)
    calls = []

    def forbidden(*args):
        calls.append(True)
        raise AssertionError("No build preparation or moving source resolution is allowed")

    monkeypatch.setattr(
        import_module("astrolift_workflows.activities.build_image"), "_prepare_build", forbidden
    )
    monkeypatch.setattr(
        import_module("astrolift_workflows.activities.build_image"), "_resolve_source_url", forbidden
    )
    with pytest.raises(
        RuntimeError, match="^Redeploy rebuild requires the original saved immutable source commit\\.$"
    ):
        _build_image_sync(BuildImageInput(row.pk, inp.image_tags["app"], dispatched))
    assert not calls


def test_delayed_rebuild_with_saved_pin_keeps_exact_builder_ref(world, client, monkeypatch):
    from astrolift_workflows.activities.build_image import (
        BuildImageInput,
        _build_image_sync,
        _resolve_source_url,
    )

    row, inp = approved_after_strategy_flip(world, client, SHA)
    observed = []

    def inspect_prepare(app, cluster, deployment_id, image_tag, commit_sha):
        observed.append((commit_sha, _resolve_source_url(app, commit_sha).rsplit("#", 1)[1]))
        return None  # Deliberately no provider, identity, registry or build effect.

    monkeypatch.setattr(
        import_module("astrolift_workflows.activities.build_image"), "_prepare_build", inspect_prepare
    )
    assert _build_image_sync(BuildImageInput(row.pk, inp.image_tags["app"], inp.commit_sha))["stub"]
    assert observed == [(SHA, SHA)]


def test_effective_off_builder_still_skips_blank_redeploy_pin(world, client, monkeypatch):
    from astrolift_workflows.activities.build_image import BuildImageInput, _build_image_sync

    original = source(world, "")
    world.medops_app.build_mode = "ci_pushed"
    world.medops_app.save()
    result = redeploy(world, client, original)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])

    def forbidden(*args):
        pytest.fail("Artifact-only repeats cannot prepare a build")

    monkeypatch.setattr(
        import_module("astrolift_workflows.activities.build_image"), "_prepare_build", forbidden
    )
    monkeypatch.setattr(
        import_module("astrolift_workflows.activities.build_image"), "_resolve_source_url", forbidden
    )
    assert _build_image_sync(BuildImageInput(row.pk, row.image_tag, "")) == {
        "ok": True,
        "image_ref": row.image_tag,
        "stub": True,
    }
