"""Owned browser/API/Temporal provider collection with controlled Kubernetes responses."""

import asyncio
import json
import os
import shutil
import subprocess
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async

from astrolift_clusters import nvke_operations as operations
from astrolift_clusters.models import NvkeOperation
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_browser_integration import FRONTEND
from astrolift_clusters.tests.test_nvke_browser_integration import browser_endpoint as _browser_endpoint
from astrolift_clusters.tests.test_nvke_browser_integration import story_origin as _story_origin
from astrolift_clusters.tests.test_nvke_provider_observations import configured as _configured
from astrolift_clusters.tests.test_nvke_provider_observations import peer as _peer
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken
from astrolift_workflows.activities.nvke import nvke_operation_attempt
from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow
from core.testing.temporal import temporal_test_env, temporal_worker

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def provider_peer():
    yield from _peer.__wrapped__()


@pytest.fixture
def provider_source(provider_peer):
    return _configured.__wrapped__(provider_peer)


@pytest.fixture
def story_origin():
    yield from _story_origin.__wrapped__()


@pytest.fixture
def browser_endpoint(settings, story_origin):
    yield from _browser_endpoint.__wrapped__(settings, story_origin)


async def test_browser_collects_once_recovers_read_only_and_reads_history_after_connection_removal(
    provider_source, story_origin, browser_endpoint, monkeypatch
):
    from astrolift_workflows import client as workflow_client

    state = provider_source
    tokens = []
    for scopes in (["read:nvke", "admin:nvke"], ["read:nvke"]):
        issued = mint_token()
        await sync_to_async(ApiToken.objects.create)(
            user=state.world.user,
            organization=state.world.org,
            name="owned provider browser",
            token_hash=issued.token_hash,
            token_last_4=issued.last4,
            scopes=scopes,
        )
        tokens.append(issued.plaintext)
    async with temporal_test_env() as env:
        queue = "nvke-browser-providers-" + uuid4().hex

        async def get_client():
            return env.client

        monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
        monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
        monkeypatch.setattr(workflow_client, "_task_queue", lambda: queue)
        async with temporal_worker(
            env, task_queue=queue, workflows=[NvkeOperationWorkflow], activities=[nvke_operation_attempt]
        ):

            async def remove_after_collection():
                for _ in range(1200):
                    row = await sync_to_async(
                        lambda: NvkeOperation.objects.filter(
                            site=state.site, operation_key="infrastructure.providers", status="succeeded"
                        ).first()
                    )()
                    if row is not None:
                        await sync_to_async(state.connection.soft_delete)()
                        await sync_to_async(state.site.save)()
                        return len(state.peer.calls)
                    await asyncio.sleep(0.05)
                raise AssertionError("Owned provider observation never completed")

            removal = asyncio.create_task(remove_after_collection())
            try:
                config = {
                    "storyOrigin": story_origin,
                    "endpoint": browser_endpoint,
                    "token": tokens[0],
                    "readerToken": tokens[1],
                    "organizationId": str(state.world.org.guid),
                    "actorId": state.world.user.pk,
                    "siteId": str(state.site.guid),
                    "connectionId": str(state.connection.guid),
                    "managementEndpoint": state.peer.url,
                }
                node = shutil.which("node")
                assert node
                result = await asyncio.to_thread(
                    subprocess.run,
                    [node, str(FRONTEND / "e2e/nvke-provider-connected-runner.mjs")],
                    input=json.dumps(config),
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                    env=os.environ.copy(),
                )
                for token in tokens:
                    assert token not in result.stdout + result.stderr
                assert result.returncode == 0, result.stderr
                receipt = json.loads(result.stdout)
                assert (
                    receipt["admissions"] == 1 and receipt["readOnlyRecovery"] and receipt["historicalRead"]
                )
                native_calls_at_removal = await removal
                assert native_calls_at_removal > 0 and len(state.peer.calls) == native_calls_at_removal
                row = await sync_to_async(NvkeOperation.objects.get)(guid=receipt["operationId"])
                assert row.status == "succeeded" and str(row.request_id) == receipt["requestId"]
                assert (
                    await sync_to_async(
                        NvkeOperation.objects.filter(
                            site=state.site, operation_key="infrastructure.providers"
                        ).count
                    )()
                    == 1
                )
                assert state.peer.calls and all(
                    path.startswith(("/apis/", "/api/")) for path, _ in state.peer.calls
                )
                payload = await sync_to_async(operations.decode_result)(row)
                assert "PRIVATE" not in json.dumps(payload)
                assert b"capnico-system" not in bytes(row.result_ciphertext)
                history = (await env.client.get_workflow_handle(row.workflow_id).fetch_history()).to_json()
                for private in (
                    "PRIVATE",
                    "capnico-system",
                    "nico-credentials",
                    "extra-030",
                    "9007199254740993",
                ):
                    assert private not in history
            finally:
                if not removal.done():
                    removal.cancel()
                    await asyncio.gather(removal, return_exceptions=True)
