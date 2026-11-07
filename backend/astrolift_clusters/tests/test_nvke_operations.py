from __future__ import annotations

import base64
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
from django.utils import timezone
from nvke.client import NvkeError

from astrolift_clusters import nvke_operations as service
from astrolift_clusters import nvke_runtime as runtime
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeOperation
from astrolift_clusters.nvke_connections import open_connection
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_sites import context, create, put
from astrolift_clusters.tests.test_nvke_sites import world as _site_world
from core.permissions import PermissionDenied

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return _site_world.__wrapped__()


@pytest.fixture
def configured(world):
    with context(world):
        site = create()
        connection = put(site, credentials={"token": "PRIVATE_NVKE_TOKEN"})
        site.refresh_from_db()
    return SimpleNamespace(world=world, site=site, connection=connection)


def reserve(state, **changes):
    args = {
        "site_id": state.site.guid,
        "expected_site_version": state.site.version,
        "request_id": uuid4(),
        "operation_key": "listClusters",
        "parameters": {},
    }
    args.update(changes)
    with context(state.world):
        return service.reserve(**args)


def test_original_request_is_encrypted_idempotent_and_bound_to_all_execution_inputs(configured):
    request_id = uuid4()
    row = reserve(configured, request_id=request_id, parameters={"query": {"name": "PRIVATE_FILTER"}})
    assert b"PRIVATE_FILTER" not in bytes(row.input_ciphertext)
    assert "PRIVATE_NVKE_TOKEN" not in str(row.authority)
    assert row.authority["connection_id"] == str(configured.connection.guid)
    assert (
        reserve(configured, request_id=request_id, parameters={"query": {"name": "PRIVATE_FILTER"}}).pk
        == row.pk
    )
    for changes in ({"parameters": {}}, {"timeout_seconds": 60}, {"expected_site_version": 0}):
        with pytest.raises(NvkeError) as conflict:
            reserve(configured, request_id=request_id, **changes)
        assert conflict.value.code == "CONFLICT"
    row.soft_delete()
    with pytest.raises(NvkeError) as deleted:
        reserve(configured, request_id=request_id, parameters={"query": {"name": "PRIVATE_FILTER"}})
    assert deleted.value.code == "CONFLICT"
    assert NvkeOperation.all_objects.filter(request_id=request_id).count() == 1


@pytest.mark.parametrize(
    "parameters", [{"query": {"unknown": "PRIVATE"}}, {"body": {}}, {"url": "PRIVATE"}, []]
)
def test_invalid_input_is_refused_before_outbox_or_workflow_creation(configured, parameters):
    with pytest.raises(NvkeError) as error:
        reserve(configured, parameters=parameters)
    assert error.value.code == "INVALID_INPUT"
    assert "PRIVATE" not in str(error.value)
    assert not NvkeOperation.objects.exists()


@pytest.mark.parametrize("change", ["version", "surface"])
def test_browser_rejection_codes_cannot_hide_an_existing_original_receipt(configured, change):
    request_id = uuid4()
    original = reserve(configured, request_id=request_id)
    if change == "version":
        configured.site.save()
        expected_code = "VERSION_MISMATCH"
    else:
        configured.connection.is_enabled = False
        configured.connection.save()
        expected_code = "SURFACE_UNAVAILABLE"
    # The browser retries its original reviewed version, not the new source version.
    assert (
        reserve(configured, request_id=request_id, expected_site_version=original.expected_site_version).pk
        == original.pk
    )
    rejected_id = uuid4()
    with pytest.raises((NvkeError, sites.NvkeSiteError)) as error:
        reserve(configured, request_id=rejected_id, expected_site_version=original.expected_site_version)
    assert error.value.code == expected_code
    assert not NvkeOperation.all_objects.filter(request_id=rejected_id).exists()


def test_original_membership_and_exact_connection_are_checked_again_at_execution(configured):
    row = reserve(configured)
    configured.world.member.soft_delete()
    with pytest.raises(NvkeError) as revoked:
        service.checkpoint(row)
    assert revoked.value.code == "AUTHORITY_REVOKED"


def test_connection_rotation_does_not_inherit_old_approval(configured):
    row = reserve(configured)
    with context(configured.world):
        put(
            configured.site,
            expected_connection_version=configured.connection.version,
            credentials={"token": "ROTATED"},
        )
    with pytest.raises(NvkeError) as changed:
        service.checkpoint(row)
    assert changed.value.code == "SOURCE_CHANGED"


def test_raw_connection_edit_is_detected_even_without_site_version_bump(configured):
    row = reserve(configured)
    type(configured.connection).objects.filter(pk=configured.connection.pk).update(
        endpoint="https://replacement.invalid"
    )
    with pytest.raises(NvkeError) as changed:
        service.checkpoint(row)
    assert changed.value.code == "SOURCE_CHANGED"


def test_foreign_operations_are_indistinguishable_from_missing(configured):
    row = reserve(configured)
    from astrolift_clusters.tests.test_nvke_sites import world as factory

    other = factory.__wrapped__()
    with context(other):
        for identifier in (row.guid, uuid4()):
            with pytest.raises(NvkeError) as missing:
                service.read(identifier)
            assert missing.value.code == "NOT_FOUND"
    configured.world.member.soft_delete()
    with context(configured.world), pytest.raises(PermissionDenied):
        service.read(row.guid)


def test_cancellation_before_send_has_no_remote_effect(configured):
    row = reserve(configured)
    with context(configured.world):
        service.request_cancel(row.guid)
    assert (
        runtime.attempt(
            row.pk, row.generation, execution=(row.workflow_id, "run-local", "NvkeOperationWorkflow")
        )
        == "cancelled"
    )
    row.refresh_from_db()
    assert row.finished_at
    assert not service.decode_result(row).get("sending")


def test_forged_or_stale_execution_cannot_open_a_connection(configured):
    row = reserve(configured)
    for execution in (
        ("wrong", "run", "NvkeOperationWorkflow"),
        (row.workflow_id, "run", "OtherWorkflow"),
        (row.workflow_id, "", "NvkeOperationWorkflow"),
    ):
        assert runtime.attempt(row.pk, 0, execution=execution) == "execution_refused"
    row.workflow_run_id = "bound-original-run"
    row.save()
    assert (
        runtime.attempt(row.pk, 0, execution=(row.workflow_id, "wrong-run", "NvkeOperationWorkflow"))
        == "execution_refused"
    )
    row.refresh_from_db()
    assert row.status == "queued"
    assert not row.started_at


def test_lost_dispatch_acknowledgement_recovers_original_run_without_restarting(configured, monkeypatch):
    from astrolift_workflows import client

    row = reserve(configured)
    starts = []
    handle = SimpleNamespace(run_id="original-run")
    monkeypatch.setattr(client, "recover_workflow_once", lambda *a, **kw: None)

    def lost(*a, **kw):
        starts.append(kw["workflow_id"])
        raise RuntimeError("PRIVATE_DISPATCH_EXCEPTION")

    monkeypatch.setattr(client, "start_workflow_once", lost)
    row = service.dispatch(row.pk)
    assert row.error_code == "DISPATCH_PENDING"
    assert "PRIVATE" not in row.error_message
    monkeypatch.setattr(client, "recover_workflow_once", lambda *a, **kw: handle)
    monkeypatch.setattr(
        client,
        "describe_workflow_instance",
        lambda *a, **kw: {
            "status": "RUNNING",
            "run_id": handle.run_id,
            "workflow_type": "NvkeOperationWorkflow",
        },
    )
    row = service.dispatch(row.pk)
    assert row.workflow_run_id == handle.run_id
    assert row.generation == 0
    assert len(starts) == 1


def test_closed_execution_recovers_saved_sending_state_in_a_new_generation(configured, monkeypatch):
    from astrolift_workflows import client

    row = reserve(configured)
    row.workflow_run_id = "stopped-run"
    service.save_result(row, {"sending": True, "target": {"key": "getCluster"}})
    row.save()
    monkeypatch.setattr(
        client, "recover_workflow_once", lambda *a, **kw: SimpleNamespace(run_id="stopped-run")
    )
    monkeypatch.setattr(
        client,
        "describe_workflow_instance",
        lambda *a, **kw: {
            "status": "TERMINATED",
            "run_id": "stopped-run",
            "workflow_type": "NvkeOperationWorkflow",
            "closed_at": timezone.now(),
        },
    )
    started = []

    def start(name, args, **kw):
        started.append((name, args, kw))
        return SimpleNamespace(run_id="replacement-run")

    monkeypatch.setattr(client, "start_workflow_once", start)
    row = service.dispatch(row.pk)
    assert row.generation == 1
    assert row.workflow_run_id == "replacement-run"
    assert started[0][1] == [row.pk, 1]
    assert service.decode_result(row)["sending"] is True
    assert (
        runtime.attempt(
            row.pk, 0, execution=(started[0][2]["workflow_id"], "stopped-run", "NvkeOperationWorkflow")
        )
        == "execution_refused"
    )


def test_uncertain_requests_are_excluded_from_automatic_sweep(configured, monkeypatch):
    row = reserve(configured)
    row.status = "uncertain"
    row.save()
    calls = []
    monkeypatch.setattr(service, "dispatch", calls.append)
    assert service.sweep_outbox() == 0
    assert calls == []


@pytest.fixture
def datastore_site(world):
    path = os.getenv("NVKE_TEST_KUBECONFIG")
    if not path:
        pytest.skip("set NVKE_TEST_KUBECONFIG to the task-owned envtest kubeconfig")
    config = yaml.safe_load(Path(path).read_text())
    assert config["current-context"] == "astro-nvke-local"
    ctx = next(c["context"] for c in config["contexts"] if c["name"] == config["current-context"])
    cluster = next(c["cluster"] for c in config["clusters"] if c["name"] == ctx["cluster"])
    user = next(c["user"] for c in config["users"] if c["name"] == ctx["user"])
    from urllib.parse import urlsplit

    assert urlsplit(cluster["server"]).hostname == "127.0.0.1"
    credentials = {
        name: base64.b64decode(data).decode()
        for name, data in (
            ("ca_pem", cluster["certificate-authority-data"]),
            ("client_certificate_pem", user["client-certificate-data"]),
            ("client_key_pem", user["client-key-data"]),
        )
    }
    with context(world):
        site = create()
        connection = put(site, surface="datastore", endpoint=cluster["server"], credentials=credentials)
        site.refresh_from_db()
    state = SimpleNamespace(world=world, site=site, connection=connection)
    yield state
    with open_connection(
        connection, namespace=site.organization_namespace, checkpoint=lambda: None
    ) as client:
        items = []
        token = ""
        while True:
            page = client.list(
                "MachineImage",
                page_size=500,
                continue_token=token,
                label_selector=f"astrolift.test/run={site.guid}",
            )
            items.extend(page.data["items"])
            token = page.data["metadata"].get("continue", "")
            if not token:
                break
        for item in items:
            client.delete(
                "MachineImage",
                item["metadata"]["name"],
                uid=item["metadata"]["uid"],
                resource_version=item["metadata"]["resourceVersion"],
                propagation_policy="Background",
            )


def image_request(state):
    return {
        "document": {
            "metadata": {
                "name": "astro-operation-" + uuid4().hex[:12],
                "labels": {"astrolift.test/run": str(state.site.guid)},
            },
            "spec": {
                "architecture": "amd64",
                "buildDate": "2026-11-07",
                "nico": {"ipxeScript": "#!ipxe\nchain https://boot.example.test/PRIVATE_BOOT_SCRIPT.ipxe\n"},
                "operatingSystem": {"family": "ubuntu", "version": "24.04"},
            },
        }
    }


def test_real_database_and_kubernetes_operation_dry_run_and_observed_create(datastore_site):
    state = datastore_site
    request = image_request(state)
    dry = reserve(
        state, operation_key="datastore.createMachineImage", parameters={**request, "dry_run": True}
    )
    assert (
        runtime.attempt(dry.pk, 0, execution=(dry.workflow_id, "dry-run", "NvkeOperationWorkflow"))
        == "succeeded"
    )
    dry.refresh_from_db()
    assert dry.stage == "dry_run_complete"
    row = reserve(state, operation_key="datastore.createMachineImage", parameters=request)
    assert (
        runtime.attempt(row.pk, 0, execution=(row.workflow_id, "create-run", "NvkeOperationWorkflow"))
        == "succeeded"
    )
    row.refresh_from_db()
    result = service.decode_result(row)
    uid = result["observation"]["data"]["metadata"]["uid"]
    assert uid == result["response"]["data"]["metadata"]["uid"]
    assert "PRIVATE_BOOT_SCRIPT" not in str(bytes(row.result_ciphertext))
    assert (
        runtime.attempt(row.pk, 0, execution=(row.workflow_id, "create-run", "NvkeOperationWorkflow"))
        == "succeeded"
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("lose_acknowledgement", [False, True])
async def test_real_temporal_kubernetes_and_postgres_keep_private_payloads_out_of_history(
    datastore_site, temporal_env, monkeypatch, lose_acknowledgement
):
    import asyncio

    from asgiref.sync import sync_to_async

    from astrolift_workflows import client as workflow_client
    from astrolift_workflows.activities.nvke import nvke_operation_attempt
    from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow
    from core.testing.temporal import temporal_worker

    async def get_client():
        return temporal_env.client

    queue = "nvke-test-" + uuid4().hex
    monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
    monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(workflow_client, "_task_queue", lambda: queue)
    if lose_acknowledgement:
        original_start = workflow_client.start_workflow_once

        def lost(*args, **kwargs):
            original_start(*args, **kwargs)
            raise RuntimeError("PRIVATE_DISPATCH_REPLY")

        monkeypatch.setattr(workflow_client, "start_workflow_once", lost)
    request = image_request(datastore_site)
    async with temporal_worker(
        temporal_env,
        task_queue=queue,
        workflows=[NvkeOperationWorkflow],
        activities=[nvke_operation_attempt],
    ):
        row = await sync_to_async(reserve)(
            datastore_site, operation_key="datastore.createMachineImage", parameters=request
        )
        handle = temporal_env.client.get_workflow_handle(row.workflow_id, run_id=row.workflow_run_id or None)
        assert await asyncio.wait_for(handle.result(), timeout=30) == "succeeded"
        await sync_to_async(row.refresh_from_db)()
        assert row.finished_at and row.workflow_run_id
        result = await sync_to_async(service.decode_result)(row)
        assert result["observation"]["data"]["metadata"]["uid"]
        history = await handle.fetch_history()
        started = history.events[0].workflow_execution_started_event_attributes
        assert await temporal_env.client.data_converter.decode(started.input.payloads) == [row.pk, 0]
        raw = history.to_json()
        for private in (
            "PRIVATE_BOOT_SCRIPT",
            "PRIVATE_DISPATCH_REPLY",
            "BEGIN PRIVATE KEY",
            "client-key-data",
            "ipxeScript",
        ):
            assert private not in raw
        repeated = await sync_to_async(reserve)(
            datastore_site,
            request_id=row.request_id,
            operation_key="datastore.createMachineImage",
            parameters=request,
        )
        assert repeated.pk == row.pk
        assert repeated.workflow_run_id == row.workflow_run_id


@pytest.fixture
def dropped_write_site(datastore_site):
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = datastore_site
    calls = []
    endpoint = state.connection.endpoint.rstrip("/")
    with open_connection(
        state.connection, namespace=state.site.organization_namespace, checkpoint=lambda: None
    ) as upstream:
        # Proxy through the same admitted TLS connection, then deliberately lose
        # the client reply after Kubernetes has committed the POST.
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                calls.append((self.command, self.path))
                response = upstream._http.request(
                    self.command,
                    endpoint + self.path,
                    content=body,
                    headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
                )
                if self.command == "POST":
                    assert response.status_code == 201
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.send_response(response.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)

            do_POST = do_GET
            do_DELETE = do_GET

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with context(state.world):
                state.connection = put(
                    state.site,
                    surface="datastore",
                    endpoint=f"http://127.0.0.1:{server.server_port}",
                    allow_http=True,
                    expected_connection_version=state.connection.version,
                    credentials={},
                )
                state.site.refresh_from_db()
            yield state, calls
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_committed_write_with_lost_reply_requires_review_and_resume_never_reposts(dropped_write_site):
    state, calls = dropped_write_site
    row = reserve(state, operation_key="datastore.createMachineImage", parameters=image_request(state))
    execution = (row.workflow_id, "lost-response-run", "NvkeOperationWorkflow")
    assert runtime.attempt(row.pk, 0, execution=execution) == "uncertain"
    row.refresh_from_db()
    assert row.status == "uncertain"
    assert service.decode_result(row)["sending"] is True
    with context(state.world):
        row = service.resume(row.guid, expected_version=row.version)
    assert runtime.attempt(row.pk, 0, execution=execution) == "succeeded"
    row.refresh_from_db()
    assert row.stage == "ready"
    assert sum(method == "POST" for method, _ in calls) == 1


@pytest.mark.django_db(transaction=True)
async def test_real_temporal_reconciliation_advances_generation_without_replaying_the_committed_write(
    dropped_write_site, temporal_env, monkeypatch
):
    import asyncio

    from asgiref.sync import sync_to_async

    from astrolift_workflows import client as workflow_client
    from astrolift_workflows.activities.nvke import nvke_operation_attempt
    from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow
    from core.testing.temporal import temporal_worker

    state, calls = dropped_write_site
    queue = "nvke-reconcile-" + uuid4().hex

    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
    monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(workflow_client, "_task_queue", lambda: queue)
    async with temporal_worker(
        temporal_env, task_queue=queue, workflows=[NvkeOperationWorkflow], activities=[nvke_operation_attempt]
    ):
        row = await sync_to_async(reserve)(
            state, operation_key="datastore.createMachineImage", parameters=image_request(state)
        )
        first = temporal_env.client.get_workflow_handle(row.workflow_id)
        assert await asyncio.wait_for(first.result(), timeout=30) == "uncertain"
        await sync_to_async(row.refresh_from_db)()
        original_id = row.workflow_id
        original_run = row.workflow_run_id

        def resume():
            with context(state.world):
                return service.resume(row.guid, expected_version=row.version)

        await sync_to_async(resume)()
        await sync_to_async(row.refresh_from_db)()
        assert row.generation == 1
        assert row.workflow_id != original_id
        assert row.workflow_run_id != original_run
        second = temporal_env.client.get_workflow_handle(row.workflow_id, run_id=row.workflow_run_id)
        assert await asyncio.wait_for(second.result(), timeout=30) == "succeeded"
        assert sum(method == "POST" for method, _ in calls) == 1
        history = (await first.fetch_history()).to_json() + (await second.fetch_history()).to_json()
        assert "PRIVATE_BOOT_SCRIPT" not in history


@pytest.mark.parametrize("revocation", ["revoked", "expired", "narrowed"])
def test_async_execution_preserves_original_bearer_authority_and_never_falls_back_to_user_roles(
    configured, revocation
):
    from datetime import timedelta

    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    token = ApiToken.objects.create(
        user=configured.world.user,
        organization=configured.world.org,
        name="NVKE original caller",
        token_hash=uuid4().hex,
        scopes=["admin"],
    )
    original = set_current_api_token(token)
    try:
        row = reserve(configured)
    finally:
        reset_current_api_token(original)
    assert row.authority["token_id"] == str(token.guid)
    service.checkpoint(row)
    if revocation == "revoked":
        token.is_revoked = True
    elif revocation == "expired":
        token.expires_at = timezone.now() - timedelta(seconds=1)
    else:
        token.scopes = ["read:clusters"]
    token.save()
    with pytest.raises(NvkeError) as refused:
        service.checkpoint(row)
    assert refused.value.code == "AUTHORITY_REVOKED"
