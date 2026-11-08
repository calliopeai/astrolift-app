"""Owned Chromium collection, lost API reply and READ-only historical recovery."""

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
from astrolift_clusters.tests.test_nvke_access import controller
from astrolift_clusters.tests.test_nvke_browser_integration import (
    FRONTEND,
)
from astrolift_clusters.tests.test_nvke_browser_integration import (
    browser_endpoint as _browser_endpoint,
)
from astrolift_clusters.tests.test_nvke_browser_integration import (
    browser_source as _browser_source,
)
from astrolift_clusters.tests.test_nvke_browser_integration import (
    story_origin as _story_origin,
)
from astrolift_clusters.tests.test_nvke_credential_observations import configure, listener, sample
from astrolift_clusters.tests.test_nvke_operations import reserve
from astrolift_clusters.tests.test_nvke_sites import context, put
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken
from astrolift_workflows.activities.nvke import nvke_operation_attempt
from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow
from core.testing.temporal import temporal_test_env, temporal_worker

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def browser_source():
    return _browser_source.__wrapped__()


@pytest.fixture
def story_origin():
    yield from _story_origin.__wrapped__()


@pytest.fixture
def browser_endpoint(settings, story_origin):
    yield from _browser_endpoint.__wrapped__(settings, story_origin)


@pytest.fixture
def metrics_peer():
    yield from listener.__wrapped__()


async def test_browser_collects_once_and_recovers_read_only_after_authority_removal(
    browser_source, metrics_peer, story_origin, browser_endpoint, monkeypatch
):
    from astrolift_workflows import client as workflow_client

    state = browser_source
    authority = await sync_to_async(configure)(state, metrics_peer)
    tokens = []
    for scopes in (["read:nvke", "admin:nvke"], ["read:nvke"]):
        issued = mint_token()
        await sync_to_async(ApiToken.objects.create)(
            user=state.world.user,
            organization=state.world.org,
            name="owned credential browser",
            token_hash=issued.token_hash,
            token_last_4=issued.last4,
            scopes=scopes,
        )
        tokens.append(issued.plaintext)
    node = {"tenant_cluster": "browser-credential-cluster", "node": "cp-1"}
    metrics_peer.body += "".join(
        [
            sample("credential_monitoring_enabled", 0),
            sample("certificate_inspection_available", 0, **node),
            sample("certificate_observation_age_seconds", "7200.125", **node),
            sample("certificate_required", 0, **node, credential="super-admin"),
            sample("certificate_observation_available", 1, **node, credential="super-admin"),
            sample("certificate_not_after_timestamp_seconds", 1, **node, credential="super-admin"),
            sample(
                "serving_csr_count",
                "9007199254740993",
                tenant_cluster=node["tenant_cluster"],
                state="pending",
            ),
            sample(
                "certificate_inspection_available", 1, tenant_cluster="PRIVATE_FOREIGN", node="PRIVATE_NODE"
            ),
        ]
    )
    metrics_peer.body += "".join(
        sample(
            "certificate_inspection_available",
            0,
            tenant_cluster=node["tenant_cluster"],
            node=f"cp-extra-{index:02}",
        )
        for index in range(27)
    )
    with controller(
        {"body": {"name": node["tenant_cluster"]}},
        subresources={"parent_override": {"conditions": {"provisioned": {"status": "True"}}}},
    ) as (endpoint, identity, calls):

        def configure_controller():
            with context(state.world):
                state.connection = put(
                    state.site,
                    expected_connection_version=state.connection.version,
                    endpoint=endpoint,
                    allow_http=True,
                )
            state.site.refresh_from_db()

        await sync_to_async(configure_controller)()
        async with temporal_test_env() as env:
            queue = "nvke-browser-credentials-" + uuid4().hex

            async def get_client():
                return env.client

            monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
            monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
            monkeypatch.setattr(workflow_client, "_task_queue", lambda: queue)
            async with temporal_worker(
                env, task_queue=queue, workflows=[NvkeOperationWorkflow], activities=[nvke_operation_attempt]
            ):
                source = await sync_to_async(reserve)(
                    state, operation_key="getCluster", parameters={"path": {"clusterID": identity}}
                )
                assert (
                    await asyncio.wait_for(env.client.get_workflow_handle(source.workflow_id).result(), 30)
                    == "succeeded"
                )
                await sync_to_async(source.refresh_from_db)()
                config = {
                    "storyOrigin": story_origin,
                    "endpoint": browser_endpoint,
                    "token": tokens[0],
                    "readerToken": tokens[1],
                    "organizationId": str(state.world.org.guid),
                    "actorId": state.world.user.pk,
                    "siteId": str(state.site.guid),
                    "siteVersion": state.site.version,
                    "authorityVersion": authority.version,
                    "operationId": str(source.guid),
                    "clusterId": identity,
                    "metricsEndpoint": metrics_peer.url,
                }
                node_binary = shutil.which("node")
                assert node_binary
                result = await asyncio.to_thread(
                    subprocess.run,
                    [node_binary, str(FRONTEND / "e2e/nvke-credential-connected-runner.mjs")],
                    input=json.dumps(config),
                    capture_output=True,
                    text=True,
                    timeout=80,
                    check=False,
                    env=os.environ.copy(),
                )
                for token in tokens:
                    assert token not in result.stdout + result.stderr
                assert result.returncode == 0, result.stderr
                receipt = json.loads(result.stdout)
                assert receipt["admissions"] == 1 and receipt["readOnlyRecovery"]
                row = await sync_to_async(NvkeOperation.objects.get)(guid=receipt["operationId"])
                assert str(row.request_id) == receipt["requestId"] and row.status == "succeeded"
                assert (
                    await sync_to_async(
                        NvkeOperation.objects.filter(
                            site=state.site, operation_key="metrics.credentials"
                        ).count
                    )()
                    == 1
                )
                assert len(metrics_peer.calls) == 1 and len(calls) == 3
                payload = await sync_to_async(operations.decode_result)(row)
                assert "PRIVATE" not in json.dumps(payload)
                assert b"cp-1" not in bytes(row.result_ciphertext)
                history = (await env.client.get_workflow_handle(row.workflow_id).fetch_history()).to_json()
                for private in ("PRIVATE", node["tenant_cluster"], "cp-1", "9007199254740993", identity):
                    assert private not in history
