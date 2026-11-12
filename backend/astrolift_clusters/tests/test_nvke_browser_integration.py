"""Owned Chromium -> real GraphQL/Postgres/Temporal maintenance qualification.

ASTROLIFT_NVKE_STORYBOOK selects a built local Storybook. Credentials travel only
over child stdin and browser memory. Native/controller fixture workers do not
qualify hardware or deployed authentication.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

import pytest
import uvicorn
from asgiref.sync import sync_to_async
from django.urls import resolve
from temporalio.client import WorkflowFailureError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_operations import configured as _configured
from astrolift_clusters.tests.test_nvke_operations import reserve
from astrolift_clusters.tests.test_nvke_sites import context, put
from astrolift_clusters.tests.test_nvke_sites import world as _world
from astrolift_clusters.tests.test_nvke_temporal import configure
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken
from core.testing.temporal import temporal_test_env, temporal_worker

pytestmark = pytest.mark.django_db(transaction=True)
FRONTEND = Path(__file__).resolve().parents[3] / "frontend"


@pytest.fixture
def browser_source():
    return _configured.__wrapped__(_world.__wrapped__())


@contextmanager
def controller_source(state, address, namespace, queue, owner):
    from astrolift_clusters.tests.test_nvke_access import controller

    configure(state.world, state.site, address=address, namespace=namespace, task_queue=queue, use_tls=False)
    cluster_id = str(uuid4())
    details = {
        "parent_override": {
            "conditions": {
                "updateInProgress": {"type": "etcd_key_rotation"},
                "etcdKeyRotation": {"workflowID": owner, "state": "failed", "phase": "rewrite"},
            }
        }
    }
    with controller({"body": {"name": "browser-reset-cluster"}}, cluster_id, details) as (
        endpoint,
        _,
        _calls,
    ):
        with context(state.world):
            state.connection = put(
                state.site,
                expected_connection_version=state.connection.version,
                endpoint=endpoint,
                allow_http=True,
            )
            state.site.refresh_from_db()
        yield cluster_id


@pytest.fixture
def story_origin():
    build = os.getenv("ASTROLIFT_NVKE_STORYBOOK")
    if not build:
        pytest.skip("set ASTROLIFT_NVKE_STORYBOOK to an owned static build")
    assert (Path(build) / "index.json").is_file()
    node = shutil.which("node")
    assert node, "Node is required for connected browser qualification"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [node, str(FRONTEND / "e2e/serve-static.mjs"), build, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            assert process.poll() is None
            try:
                with urlopen(origin + "/index.json", timeout=1) as response:
                    assert response.status == 200
                break
            except OSError:
                assert time.monotonic() < deadline, "Owned Storybook did not start"
                time.sleep(0.02)
        yield origin
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def browser_endpoint(settings, story_origin):
    from config.asgi import application

    # Socket readiness must follow real URLconf/schema initialization.
    assert callable(resolve("/app/gql/config/").func)
    settings.DEBUG = False
    settings.CORS_ALLOWED_ORIGINS = [story_origin]
    settings.CORS_ALLOW_HEADERS = (*settings.CORS_ALLOW_HEADERS, "x-astrolift-organization")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(application, lifespan="off", access_log=False, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/app/gql/config/"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


async def test_connected_browser_reset_recovers_original_admission_and_native_lost_reply(
    browser_source, story_origin, browser_endpoint, monkeypatch
):
    from astrolift_clusters import nvke_native_reset
    from astrolift_clusters.models import NvkeOperation
    from astrolift_clusters.tests.test_nvke_native_reset import (
        REWRITE_CALLS,
        FailedRotation,
        begin_activity,
        prepare_activity,
        record_phase,
        rewrite_activity,
    )
    from astrolift_workflows import client as workflow_client
    from astrolift_workflows.activities.nvke import nvke_operation_attempt
    from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow
    from core.schema.audit import MutationAuditLog

    state = browser_source
    issued = mint_token()
    await sync_to_async(ApiToken.objects.create)(
        user=state.world.user,
        organization=state.world.org,
        name="owned browser native qualification",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["read:nvke", "admin:nvke"],
    )
    async with temporal_test_env() as env:
        native_queue, astro_queue = "browser-native-" + uuid4().hex, "browser-astro-" + uuid4().hex
        owner = "browser-rotation-更新-" + uuid4().hex
        stack = ExitStack()
        try:
            cluster_id = await sync_to_async(stack.enter_context)(
                controller_source(
                    state,
                    env.client.service_client.config.target_host,
                    env.client.namespace,
                    native_queue,
                    owner,
                )
            )

            async def get_client():
                return env.client

            monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
            monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
            monkeypatch.setattr(workflow_client, "_task_queue", lambda: astro_queue)
            calls = []
            send = nvke_native_reset.send_reset

            async def lose_reply(*args):
                calls.append(args[3])
                await send(*args)
                raise RuntimeError("PRIVATE_BROWSER_LOST_NATIVE_REPLY")

            monkeypatch.setattr(nvke_native_reset, "send_reset", lose_reply)
            async with (
                Worker(
                    env.client,
                    task_queue=native_queue,
                    workflows=[FailedRotation],
                    activities=[begin_activity, prepare_activity, record_phase, rewrite_activity],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                temporal_worker(
                    env,
                    task_queue=astro_queue,
                    workflows=[NvkeOperationWorkflow],
                    activities=[nvke_operation_attempt],
                ),
            ):
                handle = await env.client.start_workflow(
                    "RotateEtcdEncryptionKey", {"clusterID": cluster_id}, id=owner, task_queue=native_queue
                )
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), timeout=15)
                source = await sync_to_async(reserve)(
                    state, operation_key="getCluster", parameters={"path": {"clusterID": cluster_id}}
                )
                await asyncio.wait_for(
                    env.client.get_workflow_handle(source.workflow_id).result(), timeout=20
                )
                await sync_to_async(source.refresh_from_db)()
                assert source.status == "succeeded"
                config = {
                    "storyOrigin": story_origin,
                    "endpoint": browser_endpoint,
                    "token": issued.plaintext,
                    "organizationId": str(state.world.org.guid),
                    "actorId": state.world.user.pk,
                    "siteId": str(state.site.guid),
                    "operationId": str(source.guid),
                    "ownerId": owner,
                }
                result = await asyncio.to_thread(
                    subprocess.run,
                    [shutil.which("node"), str(FRONTEND / "e2e/nvke-connected-runner.mjs")],
                    input=json.dumps(config),
                    capture_output=True,
                    text=True,
                    timeout=90,
                    cwd=FRONTEND,
                )
                assert issued.plaintext not in result.stdout + result.stderr
                assert "PRIVATE_BROWSER" not in result.stdout + result.stderr
                assert result.returncode == 0, result.stderr
                receipt = json.loads(result.stdout)
                assert receipt["admissions"] == 1
                row = await sync_to_async(NvkeOperation.objects.get)(request_id=receipt["requestId"])
                await asyncio.wait_for(env.client.get_workflow_handle(row.workflow_id).result(), timeout=30)
                await sync_to_async(row.refresh_from_db)()
                assert row.status == "succeeded" and not row.error_code
                assert len(calls) == 1 and REWRITE_CALLS[cluster_id] == 2
                assert b"PRIVATE_BROWSER" not in bytes(row.input_ciphertext)
                astro_history = (
                    await env.client.get_workflow_handle(row.workflow_id).fetch_history()
                ).to_json()
                assert "PRIVATE_BROWSER" not in astro_history and cluster_id not in astro_history
                audits = await sync_to_async(list)(
                    MutationAuditLog.objects.filter(operation="nvke.native_reset_request")
                )
                assert len(audits) == 1
                assert "PRIVATE_BROWSER" not in json.dumps(audits[0].variables)
                assert (
                    await sync_to_async(
                        NvkeOperation.objects.filter(
                            site=state.site, operation_key="temporal.reset.RotateEtcdEncryptionKey"
                        ).count
                    )()
                    == 1
                )
        finally:
            await sync_to_async(stack.close)()
