"""Owned Chromium -> real GraphQL/Postgres/mTLS authority lifecycle qualification."""

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from asgiref.sync import sync_to_async
from django.db import transaction
from nvke.client import NvkeError

from astrolift_clusters.models import NvkeOperation, NvkeOperatorAuthority
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_browser_integration import FRONTEND
from astrolift_clusters.tests.test_nvke_browser_integration import browser_endpoint as _browser_endpoint
from astrolift_clusters.tests.test_nvke_browser_integration import story_origin as _story_origin
from astrolift_clusters.tests.test_nvke_operator import configured as _configured_operator
from astrolift_clusters.tests.test_nvke_operator import request as operator_request
from astrolift_clusters.tests.test_nvke_operator import reserve as reserve_operator
from astrolift_clusters.tests.test_nvke_sites import context, create
from astrolift_clusters.tests.test_nvke_sites import world as _world
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken
from core.schema.audit import MutationAuditLog
from core.testing.temporal import temporal_test_env, temporal_worker

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def story_origin():
    yield from _story_origin.__wrapped__()


@pytest.fixture
def browser_endpoint(settings, story_origin):
    yield from _browser_endpoint.__wrapped__(settings, story_origin)


@pytest.fixture
def redirect_peer():
    state = SimpleNamespace(count=0, private_body_received=False)

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "authorization, content-type, x-astrolift-organization"
            )

        def do_OPTIONS(self):
            self.send_response(204)
            self.cors()
            self.end_headers()

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state.private_body_received = b'"clientKeyPem"' in data and b"PRIVATE KEY" in data
            state.count += 1
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"data":{"configureNvkeOperatorAuthority":{"ok":false,"errors":[{"code":"SOURCE_CHANGED"}]}}}'
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("redirect_probe", [False, True])
def test_browser_configures_once_recovers_fresh_versions_and_verifies_actual_agent(
    story_origin, browser_endpoint, tmp_path, redirect_peer, redirect_probe
):
    world = _world.__wrapped__()
    with context(world):
        site = create()
    source = FRONTEND.parent / "backend/providers/tests/nvke/test_operator_agent_client.py"
    spec = importlib.util.spec_from_file_location("browser_operator_agent_helpers", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent = module.operator_agent(tmp_path, site_id=str(site.guid))
    state = next(agent)
    try:
        issued = mint_token()
        token = ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            name="owned agent browser",
            token_hash=issued.token_hash,
            token_last_4=issued.last4,
            scopes=["read:nvke", "admin:nvke"],
        )
        config = {
            "storyOrigin": story_origin,
            "endpoint": browser_endpoint,
            "token": issued.plaintext,
            "organizationId": str(world.org.guid),
            "actorId": world.user.pk,
            "siteId": str(site.guid),
            "agentEndpoint": str(state.client._http.base_url).rstrip("/"),
            "instanceId": state.instance,
            "bundleSha256": state.digest,
            "caPem": (tmp_path / "ca.pem").read_text(),
            "clientCertificatePem": (tmp_path / "client.pem").read_text(),
            "clientKeyPem": (tmp_path / "client.key").read_text(),
        }
        if redirect_probe:
            config["redirectUrl"] = redirect_peer.url
        node = shutil.which("node")
        assert node
        result = subprocess.run(
            [node, str(FRONTEND / "e2e/nvke-agent-connected-runner.mjs")],
            input=json.dumps(config),
            capture_output=True,
            text=True,
            timeout=100,
            check=False,
            env=os.environ.copy(),
        )
        assert issued.plaintext not in result.stdout + result.stderr
        assert "PRIVATE KEY" not in result.stdout + result.stderr
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        if redirect_probe:
            assert receipt["redirectProbe"] and receipt["writes"]["configure"] == 1
            assert redirect_peer.count == 0, (
                "Private credential redirect reached owned sink",
                redirect_peer.count,
                redirect_peer.private_body_received,
            )
            assert not NvkeOperatorAuthority.all_objects.filter(site=site).exists()
            return
        assert receipt["writes"] == {"configure": 1, "verify": 2, "disable": 1, "enable": 1, "remove": 1}
        assert receipt["recoveredConfiguration"] and receipt["privateDraftCleared"] and receipt["removed"]
        row = NvkeOperatorAuthority.all_objects.get(site=site)
        assert row.deleted_at and not row.is_enabled and row.instance_id == UUID(state.instance)
        assert b"PRIVATE KEY" not in bytes(row.credential_ciphertext)
        assert not list((tmp_path / "state/jobs").iterdir())
        logs = MutationAuditLog.objects.filter(
            organization_id=world.org.pk, operation="nvke.operator_configure"
        )
        assert logs.count() == 1
        assert "PRIVATE KEY" not in str(logs.first().variables)
        ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
    finally:
        agent.close()


@pytest.fixture
def browser_operator_job_source(tmp_path):
    world = _world.__wrapped__()
    source = FRONTEND.parent / "backend/providers/tests/nvke/test_operator_agent_client.py"
    spec = importlib.util.spec_from_file_location("browser_operator_job_helpers", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent = module.real_operator_agent.__wrapped__(tmp_path)
    state = next(agent)
    try:
        yield _configured_operator.__wrapped__(world, state)
    finally:
        agent.close()


@pytest.mark.parametrize("mode", ["confirmed", "lost_reply", "stale_review"])
def test_browser_reviews_original_infrastructure_fence_without_native_dispatch(
    browser_operator_job_source, story_origin, browser_endpoint, mode
):
    state = browser_operator_job_source
    private = operator_request(
        state,
        command="bootstrap.site",
        positionals=[],
        options={"sitespec": "input:site.yaml"},
        files={"site.yaml": "never-dispatch-stop-fixture"},
    )
    # Establish a never-dispatched uncertain fixture before its outbox callback.
    with transaction.atomic():
        row = reserve_operator(state, request_text=private, acknowledge_infrastructure_change=True)
        row.status = "uncertain"
        row.save(update_fields=["status", "version"])
    issued = mint_token()
    ApiToken.objects.create(
        user=state.world.user,
        organization=state.world.org,
        name="owned original stop browser",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["read:nvke", "admin:nvke"],
    )
    result = subprocess.run(
        [shutil.which("node"), str(FRONTEND / "e2e/nvke-stop-connected-runner.mjs")],
        input=json.dumps(
            {
                "storyOrigin": story_origin,
                "endpoint": browser_endpoint,
                "token": issued.plaintext,
                "organizationId": str(state.world.org.guid),
                "actorId": state.world.user.pk,
                "siteId": str(state.site.guid),
                "operationId": str(row.guid),
                "mode": mode,
            }
        ),
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
        env=os.environ.copy(),
    )
    for sensitive in (issued.plaintext, "PRIVATE KEY", "never-dispatch-stop-fixture", row.request_digest):
        assert sensitive not in result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["operationId"] == str(row.guid) and receipt["stops"] == 1
    row.refresh_from_db()
    assert not list((state.agent.root / "state/jobs").iterdir())
    assert not (state.agent.root / "state/workspace/runs").exists()
    if mode == "stale_review":
        assert row.status == "uncertain" and receipt["staleRefused"] and not receipt["fenced"]
        journal = json.loads((state.agent.root / "state/admissions.json").read_bytes())
        assert journal["payload"]["entries"] == {}
    else:
        assert row.status == "cancelled" and receipt["fenced"]
        with pytest.raises(NvkeError) as delayed:
            state.agent.client.submit(str(row.guid), json.loads(private), intent=row.request_digest)
        assert delayed.value.code == "OPERATOR_JOB_FENCED"
    stops = list(
        MutationAuditLog.objects.filter(organization_id=state.world.org.pk, operation="nvke.operator_stop")
    )
    assert len(stops) == 1
    assert row.request_digest not in str(stops[0].variables) and "site.yaml" not in str(stops[0].variables)


async def test_browser_native_generation_recovers_lost_admission_without_replay(
    browser_operator_job_source, story_origin, browser_endpoint, monkeypatch
):
    from astrolift_clusters import nvke_operations as outbox
    from astrolift_workflows import client as workflow_client
    from astrolift_workflows.activities.nvke import nvke_operation_attempt
    from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow

    state = browser_operator_job_source
    issued = mint_token()
    await sync_to_async(ApiToken.objects.create)(
        user=state.world.user,
        organization=state.world.org,
        name="owned browser tooling qualification",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["read:nvke", "admin:nvke"],
    )
    config = {
        "storyOrigin": story_origin,
        "endpoint": browser_endpoint,
        "token": issued.plaintext,
        "organizationId": str(state.world.org.guid),
        "actorId": state.world.user.pk,
        "siteId": str(state.site.guid),
        "revision": state.site.upstream_revision,
    }
    async with temporal_test_env() as env:
        queue = "nvke-browser-agent-" + uuid4().hex

        async def get_client():
            return env.client

        monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
        monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
        monkeypatch.setattr(workflow_client, "_task_queue", lambda: queue)
        async with temporal_worker(
            env, task_queue=queue, workflows=[NvkeOperationWorkflow], activities=[nvke_operation_attempt]
        ):
            result = await asyncio.to_thread(
                subprocess.run,
                [shutil.which("node"), str(FRONTEND / "e2e/nvke-workbench-connected-runner.mjs")],
                input=json.dumps(config),
                capture_output=True,
                text=True,
                timeout=100,
                check=False,
                env=os.environ.copy(),
            )
            assert issued.plaintext not in result.stdout + result.stderr
            assert "PRIVATE KEY" not in result.stdout + result.stderr
            assert result.returncode == 0, result.stderr
            receipt = json.loads(result.stdout)
            assert receipt["admissions"] == receipt["receiptReads"] == 1
            assert receipt["privateDraftCleared"]
            row = await sync_to_async(NvkeOperation.objects.get)(guid=receipt["operationId"])
            assert str(row.request_id) == receipt["requestId"]
            assert row.operation_key == "tooling.generate.sitespec"
            handle = env.client.get_workflow_handle(row.workflow_id, run_id=row.workflow_run_id or None)
            assert await asyncio.wait_for(handle.result(), timeout=65) == "succeeded"
            await sync_to_async(row.refresh_from_db)()
            progress = await sync_to_async(outbox.decode_result)(row)
            assert progress["operator_job"]["state"] == "completed"
            assert progress["operator_job"]["intent"] == row.request_digest
            assert len(list((state.agent.root / "state/workspace/runs").glob("*/started.json"))) == 1
            assert b"browser-private-native-site" not in bytes(row.input_ciphertext)
            assert b"browser-private-native-site" not in bytes(row.result_ciphertext)
            history = (await handle.fetch_history()).to_json()
            for private in ("browser-private-native-site", "nodes.csv", "PRIVATE KEY", row.request_digest):
                assert private not in history
            logs = await sync_to_async(list)(
                MutationAuditLog.objects.filter(
                    organization_id=state.world.org.pk, operation="nvke.operator_request"
                )
            )
            assert len(logs) == 1
            assert "browser-private-native-site" not in str(logs[0].variables)
            assert "nodes.csv" not in str(logs[0].variables)
            row.status = "uncertain"
            await sync_to_async(row.save)(update_fields=["status", "version"])
            reviewed_version = row.version
            result = await asyncio.to_thread(
                subprocess.run,
                [shutil.which("node"), str(FRONTEND / "e2e/nvke-job-connected-runner.mjs")],
                input=json.dumps({**config, "operationId": str(row.guid)}),
                capture_output=True,
                text=True,
                timeout=100,
                check=False,
                env=os.environ.copy(),
            )
            assert issued.plaintext not in result.stdout + result.stderr
            assert "PRIVATE KEY" not in result.stdout + result.stderr
            assert result.returncode == 0, result.stderr
            inspected = json.loads(result.stdout)
            assert inspected["operationId"] == str(row.guid)
            assert inspected["privateArtifactVerified"] and inspected["chunks"] > 0
            assert inspected["reconciliations"] == 1
            await sync_to_async(row.refresh_from_db)()
            assert row.status == "succeeded" and row.version > reviewed_version
            assert len(list((state.agent.root / "state/workspace/runs").glob("*/started.json"))) == 1
            reconciliations = await sync_to_async(list)(
                MutationAuditLog.objects.filter(
                    organization_id=state.world.org.pk, operation="nvke.operator_reconcile"
                )
            )
            assert len(reconciliations) == 1
            assert "browser-private-native-site" not in str(reconciliations[0].variables)
