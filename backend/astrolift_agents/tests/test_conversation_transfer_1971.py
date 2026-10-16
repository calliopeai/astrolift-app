"""Paused admission of a managed SDK conversation Move (#1971).

Pins the receiver half against a real database and a local blob store:
refusal before any change, prepare, upload binding, the worker's envelope
validation, the runner's install report, verify, commit exactly once, abort
with nothing left behind, retry idempotency, size and digest limits, the
admission deadline and its expiry sweep, side effects only after commit, and
tenant/requester isolation.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.test import Client
from django.utils import timezone

from astrolift_agents.models import AgentConversationTransfer, AgentEnvironmentSpec, AgentTask
from astrolift_agents.schema.mutations import (
    AgentsMutation,
    ConversationTransferRecordInput,
    PrepareConversationTransferInput,
)
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.services import conversation_transfer as service
from astrolift_agents.services.task_timeout import admission_allowance_seconds, task_timeout_reason
from astrolift_dispatch.brief_injector import conversation_transfer_env_vars
from astrolift_dispatch.model_gateway import KEY_TTL_MARGIN_SECONDS, task_key_ttl
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_workflows.inputs import Actor
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx
from providers._sdk.blob_store import LocalFsBlobStoreDriver

pytestmark = pytest.mark.django_db

OP_ID = "11111111-1111-4111-8111-111111111111"
VALIDATE = "ValidateConversationTransferWorkflow"
DISPATCH = "DispatchAgentTaskWorkflow"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def blobs(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("PIPELINE_ARTIFACT_LOCAL_PATH", str(tmp_path))
    monkeypatch.setattr("core.blob_store_resolution.driver_for_org", lambda org, *, purpose: None)
    monkeypatch.setenv(service.RUNTIMES_ENV, "claude")
    settings.PLATFORM_API_URL = "https://astrolift.example"
    return tmp_path


@pytest.fixture
def starts(monkeypatch):
    recorded = []

    def _start(name, args, *, workflow_id, task_queue=None, replace_running=True):
        from astrolift_workflows.client import WorkflowHandle

        recorded.append((name, workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id="run", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _start)
    return recorded


@pytest.fixture
def cancels(monkeypatch):
    calls = []

    def _cancel(guid, **_kwargs):
        calls.append(guid)
        AgentTask.objects.filter(guid=guid).update(status=AgentTask.Status.CANCELLED)
        return {"ok": True, "status": "cancelled", "error": ""}

    monkeypatch.setattr("astrolift_workflows.activities.agent_stage._cancel_agent_task_sync", _cancel)
    return calls


@pytest.fixture
def org():
    return Organization.objects.create(name="Move Org", slug="move-org")


@pytest.fixture
def user():
    return get_user_model().objects.create(username="mover", email="mover@astrolift.dev")


@pytest.fixture
def info(user):
    request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


@pytest.fixture
def agent(org):
    team = Team.objects.create(organization=org, name="Movers", slug="movers")
    app = RegisteredApp.objects.create(
        organization=org, team=team, name="Coder", slug="coder", provisioning_status="ready"
    )
    workload = Workload.objects.create(
        registered_app=app, name="Coder", slug="coder", kind=Workload.Kind.AGENT
    )
    AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Coder",
        slug="coder",
        runtime="claude",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
    )
    return workload


def _tenant(org, user):
    return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=user.pk if user else None))


def _identity(org, workload):
    spec = AgentEnvironmentSpec.objects.get(organization=org, slug=workload.slug)
    return service.destination_identity(organization=org, workload=workload, environment_spec=spec)


def _record(org, workload, **overrides):
    record = {
        "version": 2,
        "transferId": "transfer-1",
        "taskId": "task-1",
        "source": {"kind": "computer", "identity": "laptop"},
        "destination": {"kind": "astrolift", "identity": _identity(org, workload)},
        "checkpointId": "checkpoint-1",
        "state": "preparing",
        "owner": "source",
        "createdAt": "2026-10-16T00:00:00.000Z",
    }
    record.update(overrides)
    return record


def _undo():
    return {
        "version": 2,
        "checkpoints": [
            {
                "id": "cp-1",
                "userMessage": "add a directory",
                "timestamp": "2026-10-16T00:00:00.000Z",
                "fileCheckpoints": [
                    {
                        "id": "f-1",
                        "filePath": "src/a.txt",
                        "content": _b64("old"),
                        "encoding": "base64",
                        "destinationExpected": _b64("new"),
                        "existedBefore": True,
                        "timestamp": "2026-10-16T00:00:00.000Z",
                    }
                ],
                "destinationOperations": [
                    {"id": OP_ID, "kind": "mkdir", "path": "src/new", "created": [{"path": "src/new"}]}
                ],
            }
        ],
        "directories": [
            {
                "checkpointId": "cp-1",
                "operationId": OP_ID,
                "recordedPath": "src/new",
                "currentPath": "src/new",
                "snapshot": {"type": "directory", "fingerprint": "a" * 64},
            }
        ],
    }


def _envelope(record, *, undo=None):
    undo = _undo() if undo is None else undo
    digest = hashlib.sha256(service.canonical_json(_undo())).hexdigest()
    return {
        "version": 1,
        "record": record,
        "snapshot": {
            "taskId": record["taskId"],
            "checkpointId": record["checkpointId"],
            "title": "Refactor",
            "description": "",
            "attachments": [],
            "conversationHistory": [{"role": "user", "content": "hi"}],
            "workspaceState": {
                "version": 2,
                "head": "abc",
                "sha256": "w" * 64,
                "relativeCwd": "",
                "rawBytes": 0,
                "dirty": False,
            },
            "managedConversation": {"version": 4, "conversationId": "conv-1", "checkpointsSha256": digest},
        },
        "undoCheckpoints": undo,
        "undoDirectoryModes": [{"path": "src/new", "mode": 0o755}],
    }


def _bytes(envelope) -> bytes:
    return json.dumps(envelope).encode()


def _prepare(info, org, user, record, data: bytes):
    with _tenant(org, user):
        return AgentsMutation().prepare_conversation_transfer(
            info,
            input=PrepareConversationTransferInput(
                agent_slug="coder",
                record=record,
                envelope_sha256=hashlib.sha256(data).hexdigest(),
                envelope_bytes=len(data),
            ),
        )


def _upload(blobs, transfer, data: bytes):
    path = blobs / transfer.envelope_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _actor(user):
    return Actor(kind="user", user_id=user.pk, display=user.username)


def _validate(transfer, user):
    """The worker half of prepare, as ValidateConversationTransferWorkflow runs it."""
    return service.validate_uploaded_envelope(transfer.pk, actor=_actor(user), undo=True)


def _named(starts, name):
    return [workflow_id for workflow, workflow_id in starts if workflow == name]


def _validating(permission_resolver, info, org, user, agent, blobs, envelope=None, data=None):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    envelope = _envelope(record) if envelope is None else envelope
    data = _bytes(envelope) if data is None else data
    first = _prepare(info, org, user, record, data)
    assert first.ok, first
    transfer = AgentConversationTransfer.objects.get(transfer_id="transfer-1")
    path = _upload(blobs, transfer, data)
    second = _prepare(info, org, user, record, data)
    assert second.ok and second.data.state == "validating", second
    transfer.refresh_from_db()
    return transfer, envelope, path


def _admitted(permission_resolver, info, org, user, agent, blobs, starts):
    transfer, envelope, path = _validating(permission_resolver, info, org, user, agent, blobs)
    assert _validate(transfer, user) == "preparing"
    transfer.refresh_from_db()
    return transfer, envelope, path


def _install(transfer, **receipt):
    body = {
        "envelopeSha256": transfer.envelope_sha256,
        "workspaceSha256": "w" * 64,
        "undoCheckpointsSha256": transfer.expected_receipt["undoCheckpointsSha256"],
        "conversationId": "conv-1",
        "directories": [{"path": "src/new", "receipt": "1:2:3"}],
    }
    body.update(receipt)
    return service.record_runner_progress(transfer, {"phase": "installed", "status": "done", "receipt": body})


def _ready(permission_resolver, info, org, user, agent, blobs, starts):
    transfer, envelope, path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    _install(transfer)
    transfer.refresh_from_db()
    assert transfer.state == "destination_ready"
    return transfer, envelope, path


def _committed_record(transfer):
    return {
        **transfer.record,
        "state": "committed",
        "owner": "destination",
        "sourceStoppedAt": "2026-10-16T00:01:00.000Z",
        "committedAt": "2026-10-16T00:02:00.000Z",
    }


def _snapshot_digest(envelope):
    return hashlib.sha256(service.canonical_json(envelope["snapshot"])).hexdigest()


# ---------------------------------------------------------------------------
# Capability and refusal before any change
# ---------------------------------------------------------------------------


def test_capability_advertises_only_a_runtime_whose_runner_hosts_the_move(
    permission_resolver, info, org, user, agent, blobs, monkeypatch
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    with _tenant(org, user):
        cap = AgentsQuery().conversation_transfer_capability(info, agent_slug="coder")
    assert cap.managed_conversation is True
    assert cap.undo is True
    assert cap.harness_host_contract == "1.17"
    assert cap.destination_identity == _identity(org, agent)

    monkeypatch.setenv(service.RUNTIMES_ENV, "")
    with _tenant(org, user):
        cap = AgentsQuery().conversation_transfer_capability(info, agent_slug="coder")
    assert cap.managed_conversation is False
    assert "does not host a moved conversation" in cap.reason


def test_prepare_refuses_an_unhosting_destination_before_creating_anything(
    permission_resolver, info, org, user, agent, blobs, monkeypatch, starts
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    monkeypatch.setenv(service.RUNTIMES_ENV, "codex")
    record = _record(org, agent)
    result = _prepare(info, org, user, record, _bytes(_envelope(record)))
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert not AgentConversationTransfer.all_objects.exists()
    assert starts == []


def test_prepare_refuses_a_record_addressed_elsewhere(permission_resolver, info, org, user, agent, blobs):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent, destination={"kind": "astrolift", "identity": "another"})
    result = _prepare(info, org, user, record, b"{}")
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert not AgentConversationTransfer.all_objects.exists()


def test_prepare_refuses_an_envelope_over_the_size_limit(permission_resolver, info, org, user, agent, blobs):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    with _tenant(org, user):
        result = AgentsMutation().prepare_conversation_transfer(
            info,
            input=PrepareConversationTransferInput(
                agent_slug="coder",
                record=record,
                envelope_sha256="0" * 64,
                envelope_bytes=service.MAX_ENVELOPE_BYTES + 1,
            ),
        )
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert not AgentConversationTransfer.all_objects.exists()


def test_prepare_without_dispatch_permission_is_denied(permission_resolver, info, org, user, agent, blobs):
    record = _record(org, agent)
    with _tenant(org, user):
        result = AgentsMutation().prepare_conversation_transfer(
            info,
            input=PrepareConversationTransferInput(
                agent_slug="coder", record=record, envelope_sha256="0" * 64, envelope_bytes=1
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value


# ---------------------------------------------------------------------------
# Prepare, upload, worker validation and admission
# ---------------------------------------------------------------------------


def test_prepare_waits_for_the_upload_then_a_worker_admits_one_paused_session(
    permission_resolver, info, org, user, agent, blobs, starts
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    data = _bytes(_envelope(record))

    before = timezone.now()
    first = _prepare(info, org, user, record, data)
    assert first.ok
    assert first.data.state == "preparing"
    assert first.data.upload_url.startswith("file://")
    assert first.data.agent_task_id is None
    window = timedelta(seconds=service.DEFAULT_ADMISSION_SECONDS)
    assert before + window <= first.data.admission_deadline <= timezone.now() + window
    assert starts == []

    transfer = AgentConversationTransfer.objects.get(transfer_id="transfer-1")
    _upload(blobs, transfer, data)
    second = _prepare(info, org, user, record, data)
    assert second.ok
    assert second.data.state == "validating"
    assert second.data.upload_url == ""
    assert second.data.agent_task_id is None
    assert _named(starts, VALIDATE) == [service.validation_workflow_id(transfer)]
    assert _named(starts, DISPATCH) == []

    # Polling while the worker runs joins the same validation.
    again = _prepare(info, org, user, record, data)
    assert again.ok and again.data.state == "validating"
    assert set(_named(starts, VALIDATE)) == {service.validation_workflow_id(transfer)}
    assert AgentTask.objects.count() == 0

    assert _validate(transfer, user) == "preparing"
    transfer.refresh_from_db()
    task = transfer.agent_task
    assert task is not None and task.status == AgentTask.Status.QUEUED
    assert task.created_by_id == user.pk
    assert _named(starts, DISPATCH) == [f"DispatchAgentTaskWorkflow-{task.guid}"]
    assert transfer.expected_receipt["workspaceSha256"] == "w" * 64
    assert transfer.snapshot_sha256

    # A retry after a lost reply, or a repeated activity, never admits or
    # installs again.
    third = _prepare(info, org, user, record, data)
    assert third.ok and str(third.data.agent_task_id) == str(task.guid)
    assert third.data.state == "preparing" and third.data.upload_url == ""
    assert _validate(transfer, user) == "preparing"
    assert len(_named(starts, DISPATCH)) == 1
    assert AgentTask.objects.count() == 1

    env = conversation_transfer_env_vars(task)
    assert env == [
        {
            "name": "ASTROLIFT_CONVERSATION_TRANSFER_URL",
            "value": f"https://astrolift.example/api/dispatch/v1/agents/{task.guid}/transfer/",
        }
    ]


def test_prepare_never_reads_the_envelope_on_the_api_pod(
    permission_resolver, info, org, user, agent, blobs, starts, monkeypatch
):
    reads = []
    monkeypatch.setattr(LocalFsBlobStoreDriver, "download", lambda self, key: reads.append(key))
    real_presign = LocalFsBlobStoreDriver.presigned_url
    monkeypatch.setattr(
        LocalFsBlobStoreDriver, "presigned_url", lambda self, key, **kw: reads.append(key) or ""
    )
    transfer, _envelope_value, _path = _validating(permission_resolver, info, org, user, agent, blobs)
    assert reads == []

    monkeypatch.setattr(LocalFsBlobStoreDriver, "presigned_url", real_presign)
    assert _validate(transfer, user) == "preparing"
    assert reads == []  # the worker streams it; it never downloads it whole


def test_an_ordinary_task_gets_no_transfer_endpoint(org, agent):
    task = AgentTask.objects.create(organization=org, agent_definition=agent)
    assert conversation_transfer_env_vars(task) == []


def test_a_different_envelope_under_the_same_transfer_id_is_refused(
    permission_resolver, info, org, user, agent, blobs, starts
):
    transfer, _envelope_value, _path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    other = _record(org, agent, taskId="task-2")
    result = _prepare(info, org, user, other, b"{}")
    assert result.errors[0].code == ErrorCode.PRECONDITION.value


@pytest.mark.parametrize("stored", ["short", "long"])
def test_an_upload_of_the_wrong_size_is_refused_deleted_and_can_be_sent_again(
    permission_resolver, info, org, user, agent, blobs, starts, django_capture_on_commit_callbacks, stored
):
    """A store lists an object only once its upload completed, so a short
    one is final too: it used to leave prepare waiting forever."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    data = _bytes(_envelope(record))
    _prepare(info, org, user, record, data)
    transfer = AgentConversationTransfer.objects.get(transfer_id="transfer-1")
    wrong = data[:-10] if stored == "short" else data + b" " * 10
    path = _upload(blobs, transfer, wrong)

    with django_capture_on_commit_callbacks(execute=True):
        result = _prepare(info, org, user, record, data)
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "envelopeBytes"
    assert f"is {len(wrong)} bytes, not the declared {len(data)}" in result.errors[0].message
    assert not path.exists()
    transfer.refresh_from_db()
    assert transfer.state == "preparing" and transfer.agent_task_id is None
    assert transfer.progress[-1]["phase"] == "upload" and transfer.progress[-1]["status"] == "failed"
    assert starts == []

    # The upload URL is offered again, and a correct upload proceeds.
    waiting = _prepare(info, org, user, record, data)
    assert waiting.ok and waiting.data.upload_url.startswith("file://")
    _upload(blobs, transfer, data)
    assert _prepare(info, org, user, record, data).data.state == "validating"


def test_a_same_size_upload_with_another_digest_is_refused_by_the_worker(
    permission_resolver, info, org, user, agent, blobs, starts, django_capture_on_commit_callbacks
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    data = _bytes(_envelope(record))
    _prepare(info, org, user, record, data)
    transfer = AgentConversationTransfer.objects.get(transfer_id="transfer-1")
    path = _upload(blobs, transfer, data[:-1] + b" ")
    assert _prepare(info, org, user, record, data).data.state == "validating"

    with django_capture_on_commit_callbacks(execute=True):
        assert _validate(transfer, user) == "aborted"
    transfer.refresh_from_db()
    assert transfer.abort_reason == "envelope refused: the uploaded envelope does not match envelopeSha256"
    assert transfer.record["abortReason"] == transfer.abort_reason
    assert not path.exists()
    assert transfer.agent_task_id is None
    assert _named(starts, DISPATCH) == []


def test_an_upload_replaced_after_the_size_check_is_never_read_whole(
    permission_resolver, info, org, user, agent, blobs, starts, django_capture_on_commit_callbacks
):
    transfer, _envelope_value, path = _validating(permission_resolver, info, org, user, agent, blobs)
    # The upload URL outlives prepare's size check, so the object can change.
    path.write_bytes(b"x" * (transfer.envelope_bytes * 50))

    with django_capture_on_commit_callbacks(execute=True):
        assert _validate(transfer, user) == "aborted"
    transfer.refresh_from_db()
    assert "is larger than the declared" in transfer.abort_reason
    assert not path.exists()
    assert _named(starts, DISPATCH) == []


def test_envelope_reads_stop_at_their_cap(tmp_path):
    driver = LocalFsBlobStoreDriver(str(tmp_path))
    driver.upload("big/envelope.json", b"y" * (3 * 1024 * 1024))
    url = driver.presigned_url("big/envelope.json")
    assert b"".join(service._stored_chunks(driver, url, 1000)) == b"y" * 1000

    with pytest.raises(RuntimeError, match="file://"):
        list(service._stored_chunks(object(), url, 10))


class _BodyHandler(BaseHTTPRequestHandler):
    body = b""
    headers_out: dict = {}

    def do_GET(self):  # noqa: N802 - http.server's handler name
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        for name, value in self.headers_out.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(self.body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        return


@pytest.fixture
def body_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BodyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_envelope_reads_over_http_stop_at_their_cap_and_keep_the_stored_bytes(body_server):
    url = f"http://127.0.0.1:{body_server.server_port}/envelope.json"
    _BodyHandler.body = b"z" * (8 * 1024 * 1024)
    _BodyHandler.headers_out = {}
    assert b"".join(service._stored_chunks(None, url, 1000)) == b"z" * 1000

    # A Content-Encoding the store replays is never inflated: the digest is
    # over what the IDE uploaded.
    stored = gzip.compress(b"{}" * 4096)
    _BodyHandler.body = stored
    _BodyHandler.headers_out = {"Content-Encoding": "gzip"}
    assert b"".join(service._stored_chunks(None, url, len(stored) + 1)) == stored


def test_undo_history_that_does_not_match_its_digest_aborts_and_leaves_nothing(
    permission_resolver, info, org, user, agent, blobs, starts, django_capture_on_commit_callbacks
):
    record = _record(org, agent)
    tampered = _undo()
    tampered["checkpoints"][0]["userMessage"] = "something else"
    transfer, _envelope_value, path = _validating(
        permission_resolver, info, org, user, agent, blobs, envelope=_envelope(record, undo=tampered)
    )

    with django_capture_on_commit_callbacks(execute=True):
        assert _validate(transfer, user) == "aborted"
    transfer.refresh_from_db()
    assert transfer.state == "aborted"
    assert "digest" in transfer.abort_reason
    assert not path.exists()
    assert _named(starts, DISPATCH) == []


@pytest.mark.parametrize("data", [b"\xff\xfe not utf-8", b'{"version": 1,', b"[" * 200000 + b"]" * 200000])
def test_an_envelope_that_is_not_json_is_refused(
    permission_resolver, info, org, user, agent, blobs, starts, django_capture_on_commit_callbacks, data
):
    transfer, _envelope_value, path = _validating(
        permission_resolver, info, org, user, agent, blobs, data=data
    )
    with django_capture_on_commit_callbacks(execute=True):
        assert _validate(transfer, user) == "aborted"
    transfer.refresh_from_db()
    assert transfer.abort_reason == "envelope refused: the uploaded envelope is not valid JSON"
    assert not path.exists()


def test_the_worker_validates_one_envelope_at_a_time(
    permission_resolver, info, org, user, agent, blobs, starts, monkeypatch
):
    transfer, _envelope_value, _path = _validating(permission_resolver, info, org, user, agent, blobs)
    monkeypatch.setattr(service, "_ENVELOPE_SLOT_WAIT_SECONDS", 0.01)
    assert service._ENVELOPE_SLOT.acquire(timeout=1)
    try:
        # Busy: a retryable failure, nothing settled.
        with pytest.raises(RuntimeError, match="still being validated"):
            _validate(transfer, user)
    finally:
        service._ENVELOPE_SLOT.release()
    transfer.refresh_from_db()
    assert transfer.state == "validating"
    assert _validate(transfer, user) == "preparing"


def test_canonical_digest_streams_the_canonical_json_bytes():
    value = {
        "b": [1, 2.5, None, True, {"z": "é中\U0001f600", "a": ""}],
        "a": {"nested": [[], {}], "quote": 'say "hi"\n'},
    }
    canonical = service.canonical_json(value)
    assert service.canonical_digest(value) == (hashlib.sha256(canonical).hexdigest(), len(canonical))


def test_undo_directory_modes_must_cover_exactly_the_created_directories(org, agent):
    record = _record(org, agent)
    envelope = _envelope(record)
    envelope["undoDirectoryModes"] = []
    with pytest.raises(service.ConversationTransferError, match="modes"):
        service.validate_envelope(envelope, record=record, undo=True)


def test_a_file_checkpoint_without_conditional_evidence_is_refused(org, agent):
    record = _record(org, agent)
    undo = _undo()
    del undo["checkpoints"][0]["fileCheckpoints"][0]["destinationExpected"]
    envelope = _envelope(record, undo=undo)
    envelope["snapshot"]["managedConversation"]["checkpointsSha256"] = hashlib.sha256(
        service.canonical_json(undo)
    ).hexdigest()
    with pytest.raises(service.ConversationTransferError, match="conditional undo evidence"):
        service.validate_envelope(envelope, record=record, undo=True)


def test_undo_history_is_refused_where_the_destination_cannot_hold_it(org, agent):
    record = _record(org, agent)
    with pytest.raises(service.ConversationTransferError, match="cannot hold undo"):
        service.validate_envelope(_envelope(record), record=record, undo=False)


# ---------------------------------------------------------------------------
# Runner install report, verify, commit, abort
# ---------------------------------------------------------------------------


def test_exact_install_receipt_acknowledges_and_verify_accepts_only_it(
    permission_resolver, info, org, user, agent, blobs, starts
):
    transfer, envelope, _path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    service.record_runner_progress(transfer, {"phase": "workspace", "status": "done"})
    view = _install(transfer)
    assert view["state"] == "destination_ready" and view["start"] is False

    transfer.refresh_from_db()
    assert transfer.record["state"] == "destination_ready"
    assert transfer.record["acknowledgedAt"]
    assert [p["phase"] for p in transfer.progress][-2:] == ["workspace", "installed"]

    with _tenant(org, user):
        ok = AgentsMutation().verify_conversation_transfer(
            info,
            input=ConversationTransferRecordInput(
                record=transfer.record, snapshot_sha256=_snapshot_digest(envelope)
            ),
        )
        wrong = AgentsMutation().verify_conversation_transfer(
            info,
            input=ConversationTransferRecordInput(
                record={**transfer.record, "checkpointId": "other"},
                snapshot_sha256=_snapshot_digest(envelope),
            ),
        )
    assert ok.ok and ok.data.state == "destination_ready"
    assert wrong.errors[0].code == ErrorCode.PRECONDITION.value


def test_a_mismatched_install_receipt_aborts_the_arrival(
    permission_resolver, info, org, user, agent, blobs, starts, cancels, django_capture_on_commit_callbacks
):
    transfer, _envelope_value, path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    with django_capture_on_commit_callbacks(execute=True):
        view = _install(transfer, undoCheckpointsSha256="b" * 64)
    assert view["abort"] is True
    transfer.refresh_from_db()
    assert transfer.state == "aborted" and "undoCheckpointsSha256" in transfer.abort_reason
    assert not path.exists()
    assert cancels == [str(transfer.agent_task.guid)]


def test_a_box_that_cannot_hold_undo_refuses_with_its_reason(
    permission_resolver, info, org, user, agent, blobs, starts, cancels, django_capture_on_commit_callbacks
):
    transfer, _envelope_value, path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    with django_capture_on_commit_callbacks(execute=True):
        service.record_runner_progress(
            transfer, {"phase": "refused", "status": "failed", "reason": "no directory birth time"}
        )
    transfer.refresh_from_db()
    assert transfer.state == "aborted"
    assert transfer.record["abortReason"] == "destination refused: no directory birth time"
    assert not path.exists()


def test_commit_starts_the_session_exactly_once(
    permission_resolver, info, org, user, agent, blobs, starts, django_capture_on_commit_callbacks
):
    transfer, envelope, path = _ready(permission_resolver, info, org, user, agent, blobs, starts)
    committed = _committed_record(transfer)
    digest = _snapshot_digest(envelope)
    with _tenant(org, user), django_capture_on_commit_callbacks(execute=True):
        first = AgentsMutation().commit_conversation_transfer(
            info, input=ConversationTransferRecordInput(record=committed, snapshot_sha256=digest)
        )
        again = AgentsMutation().commit_conversation_transfer(
            info, input=ConversationTransferRecordInput(record=committed, snapshot_sha256=digest)
        )
        other = AgentsMutation().commit_conversation_transfer(
            info,
            input=ConversationTransferRecordInput(
                record={**committed, "committedAt": "2026-10-16T00:03:00.000Z"}, snapshot_sha256=digest
            ),
        )
        abort = AgentsMutation().abort_conversation_transfer(
            info, input=ConversationTransferRecordInput(record={**committed, "state": "aborted"})
        )
    assert first.ok and first.data.state == "committed"
    assert again.ok and again.data.committed_at == first.data.committed_at
    assert other.errors[0].code == ErrorCode.PRECONDITION.value
    assert abort.errors[0].code == ErrorCode.PRECONDITION.value
    transfer.refresh_from_db()
    assert service.runner_view(transfer)["start"] is True
    # A restarted runner can never reinstall over the session's later edits.
    assert not path.exists()


def test_commit_before_acknowledgement_is_refused(permission_resolver, info, org, user, agent, blobs, starts):
    transfer, envelope, _path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    committed = {
        **transfer.record,
        "state": "committed",
        "owner": "destination",
        "sourceStoppedAt": "x",
        "committedAt": "y",
    }
    with _tenant(org, user):
        result = AgentsMutation().commit_conversation_transfer(
            info,
            input=ConversationTransferRecordInput(
                record=committed, snapshot_sha256=_snapshot_digest(envelope)
            ),
        )
    assert result.errors[0].code == ErrorCode.PRECONDITION.value


@pytest.mark.parametrize("ended", ["timed_out", "failed", "cancelled", "cancel_requested"])
def test_commit_refuses_a_paused_session_that_ended_and_aborts_the_transfer(
    permission_resolver,
    info,
    org,
    user,
    agent,
    blobs,
    starts,
    cancels,
    django_capture_on_commit_callbacks,
    ended,
):
    transfer, envelope, path = _ready(permission_resolver, info, org, user, agent, blobs, starts)
    if ended == "cancel_requested":
        AgentTask.objects.filter(pk=transfer.agent_task_id).update(cancel_requested_at=timezone.now())
    else:
        AgentTask.objects.filter(pk=transfer.agent_task_id).update(status=ended)

    with _tenant(org, user), django_capture_on_commit_callbacks(execute=True):
        result = AgentsMutation().commit_conversation_transfer(
            info,
            input=ConversationTransferRecordInput(
                record=_committed_record(transfer), snapshot_sha256=_snapshot_digest(envelope)
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    reason = (
        "the paused session is being cancelled, so it cannot start this conversation"
        if ended == "cancel_requested"
        else f"the paused session already ended ({ended}), so it cannot start this conversation"
    )
    assert result.errors[0].message == reason
    transfer.refresh_from_db()
    assert transfer.state == "aborted" and transfer.committed_at is None
    assert transfer.abort_reason == reason and transfer.record["abortReason"] == reason
    assert not path.exists()
    assert service.runner_view(transfer)["start"] is False
    # An ended task is not cancelled again; one being cancelled is cancelled.
    assert cancels == ([str(transfer.agent_task.guid)] if ended == "cancel_requested" else [])


def test_verify_refuses_a_paused_session_that_ended(
    permission_resolver, info, org, user, agent, blobs, starts, django_capture_on_commit_callbacks
):
    transfer, envelope, path = _ready(permission_resolver, info, org, user, agent, blobs, starts)
    AgentTask.objects.filter(pk=transfer.agent_task_id).update(status=AgentTask.Status.FAILED)
    with _tenant(org, user), django_capture_on_commit_callbacks(execute=True):
        result = AgentsMutation().verify_conversation_transfer(
            info,
            input=ConversationTransferRecordInput(
                record=transfer.record, snapshot_sha256=_snapshot_digest(envelope)
            ),
        )
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert "already ended (failed)" in result.errors[0].message
    transfer.refresh_from_db()
    assert transfer.state == "aborted"
    assert not path.exists()


def test_abort_retires_the_arrival_and_removes_what_it_created(
    permission_resolver, info, org, user, agent, blobs, starts, cancels, django_capture_on_commit_callbacks
):
    transfer, _envelope_value, path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    aborted = {**transfer.record, "state": "aborted", "abortedAt": "2026-10-16T00:05:00.000Z"}
    with _tenant(org, user), django_capture_on_commit_callbacks(execute=True):
        first = AgentsMutation().abort_conversation_transfer(
            info, input=ConversationTransferRecordInput(record=aborted, reason="user cancelled")
        )
        again = AgentsMutation().abort_conversation_transfer(
            info, input=ConversationTransferRecordInput(record=aborted)
        )
    assert first.ok and first.data.state == "aborted"
    assert again.ok and again.data.state == "aborted"
    assert not path.exists()
    assert cancels == [str(transfer.agent_task.guid)]
    transfer.refresh_from_db()
    assert transfer.agent_task.status == AgentTask.Status.CANCELLED
    assert service.runner_view(transfer)["abort"] is True


def test_retirement_deletes_and_cancels_only_after_it_commits(
    permission_resolver, info, org, user, agent, blobs, starts, cancels, django_capture_on_commit_callbacks
):
    transfer, _envelope_value, path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    aborted = {**transfer.record, "state": "aborted"}

    # Rolled back: the pod and the envelope are untouched.
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError, match="roll back"):
            with transaction.atomic():
                service.abort_transfer(transfer, record=aborted)
                assert cancels == [] and path.exists()
                raise RuntimeError("roll back")
    assert cancels == [] and path.exists()
    transfer.refresh_from_db()
    assert transfer.state == "preparing"

    # Committed: the external removal runs once the transaction has.
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        service.abort_transfer(transfer, record=aborted)
        assert cancels == [] and path.exists()
    assert callbacks
    for callback in callbacks:
        callback()
    assert cancels == [str(transfer.agent_task.guid)]
    assert not path.exists()


def test_abort_before_the_upload_needs_no_session(
    permission_resolver, info, org, user, agent, blobs, cancels, django_capture_on_commit_callbacks
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    _prepare(info, org, user, record, _bytes(_envelope(record)))
    with _tenant(org, user), django_capture_on_commit_callbacks(execute=True):
        result = AgentsMutation().abort_conversation_transfer(
            info, input=ConversationTransferRecordInput(record={**record, "state": "aborted"})
        )
    assert result.ok and result.data.state == "aborted"
    assert cancels == []


# ---------------------------------------------------------------------------
# Admission deadline, the paused task's clock, and the expiry sweep
# ---------------------------------------------------------------------------


def test_the_admission_window_is_configurable_and_bounded(settings):
    settings.CONVERSATION_TRANSFER_ADMISSION_SECONDS = 120
    assert service.admission_seconds() == 120
    settings.CONVERSATION_TRANSFER_ADMISSION_SECONDS = 5
    assert service.admission_seconds() == service.MIN_ADMISSION_SECONDS
    settings.CONVERSATION_TRANSFER_ADMISSION_SECONDS = 10**9
    assert service.admission_seconds() == service.MAX_ADMISSION_SECONDS


def test_prepare_sets_the_configured_admission_deadline(
    permission_resolver, info, org, user, agent, blobs, settings
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    settings.CONVERSATION_TRANSFER_ADMISSION_SECONDS = 120
    record = _record(org, agent)
    before = timezone.now()
    result = _prepare(info, org, user, record, _bytes(_envelope(record)))
    deadline = AgentConversationTransfer.objects.get(transfer_id="transfer-1").admission_deadline
    assert result.data.admission_deadline == deadline
    assert before + timedelta(seconds=120) <= deadline <= timezone.now() + timedelta(seconds=120)


def test_paused_time_does_not_count_against_the_task_timeout(
    permission_resolver, info, org, user, agent, blobs, starts
):
    transfer, _envelope_value, _path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    task = transfer.agent_task
    now = timezone.now()
    timeout = task.timeout_seconds
    AgentTask.objects.filter(pk=task.pk).update(provisioning_at=now - timedelta(seconds=timeout * 4))
    task.refresh_from_db()

    # Paused well past its own timeout: only the admission deadline bounds it.
    assert task_timeout_reason(task, now=now) is None
    left = (transfer.admission_deadline - now).total_seconds()
    assert abs(admission_allowance_seconds(task, now=now) - left) <= 1
    assert task_key_ttl(task) >= timeout + left - 1 + KEY_TTL_MARGIN_SECONDS
    past = transfer.admission_deadline + timedelta(seconds=1)
    assert task_timeout_reason(task, now=past) == service.EXPIRED_REASON

    # After the commit the task's own timeout counts, from the commit.
    committed_at = now - timedelta(seconds=timeout // 2)
    AgentConversationTransfer.objects.filter(pk=transfer.pk).update(
        state="committed", committed_at=committed_at
    )
    assert task_timeout_reason(task, now=now) is None
    assert task_timeout_reason(task, now=committed_at + timedelta(seconds=timeout)) == (
        f"agent exceeded its {timeout}s timeout"
    )
    assert admission_allowance_seconds(task, now=now) == pytest.approx(
        (committed_at - task.provisioning_at).total_seconds(), abs=1
    )


def test_the_paused_jobs_kubernetes_deadline_covers_the_admission(
    permission_resolver, info, org, user, agent, blobs, starts
):
    from astrolift_dispatch.spawners.k8s_job import _render_agent_job

    transfer, _envelope_value, _path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    task = AgentTask.objects.select_related("environment_spec").get(pk=transfer.agent_task_id)
    job = _render_agent_job(job_name="agent-task-moved", workload=agent, namespace="agents", task=task)
    left = (transfer.admission_deadline - timezone.now()).total_seconds()
    assert abs(job["spec"]["activeDeadlineSeconds"] - (task.timeout_seconds + left)) <= 2

    ordinary = AgentTask.objects.create(
        organization=org, agent_definition=agent, environment_spec=task.environment_spec, timeout_seconds=300
    )
    job = _render_agent_job(job_name="agent-task-plain", workload=agent, namespace="agents", task=ordinary)
    assert job["spec"]["activeDeadlineSeconds"] == 300
    assert admission_allowance_seconds(ordinary) == 0
    assert task_key_ttl(ordinary) == 300 + KEY_TTL_MARGIN_SECONDS


def test_commit_after_the_admission_deadline_is_refused(
    permission_resolver, info, org, user, agent, blobs, starts, cancels, django_capture_on_commit_callbacks
):
    transfer, envelope, path = _ready(permission_resolver, info, org, user, agent, blobs, starts)
    AgentConversationTransfer.objects.filter(pk=transfer.pk).update(
        admission_deadline=timezone.now() - timedelta(seconds=1)
    )
    with _tenant(org, user), django_capture_on_commit_callbacks(execute=True):
        result = AgentsMutation().commit_conversation_transfer(
            info,
            input=ConversationTransferRecordInput(
                record=_committed_record(transfer), snapshot_sha256=_snapshot_digest(envelope)
            ),
        )
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert result.errors[0].message == service.EXPIRED_REASON
    transfer.refresh_from_db()
    assert transfer.state == "aborted" and transfer.abort_reason == service.EXPIRED_REASON
    assert cancels == [str(transfer.agent_task.guid)]
    assert not path.exists()


def test_a_prepare_poll_after_the_deadline_returns_the_aborted_transfer(
    permission_resolver, info, org, user, agent, blobs, starts, django_capture_on_commit_callbacks
):
    transfer, envelope, path = _validating(permission_resolver, info, org, user, agent, blobs)
    AgentConversationTransfer.objects.filter(pk=transfer.pk).update(
        admission_deadline=timezone.now() - timedelta(seconds=1)
    )
    with django_capture_on_commit_callbacks(execute=True):
        result = _prepare(info, org, user, transfer.record, _bytes(envelope))
    assert result.ok and result.data.state == "aborted"
    assert result.data.abort_reason == service.EXPIRED_REASON
    assert not path.exists()
    # The worker, arriving late, settles nothing.
    assert _validate(transfer, user) == "aborted"
    assert _named(starts, DISPATCH) == []


def test_the_expiry_sweep_retires_only_dead_open_transfers(
    permission_resolver, info, org, user, agent, blobs, starts, cancels, django_capture_on_commit_callbacks
):
    past = timezone.now() - timedelta(seconds=1)
    future = timezone.now() + timedelta(hours=1)

    def row(transfer_id, state, deadline, **fields):
        transfer = AgentConversationTransfer(
            organization=org,
            agent_definition=agent,
            transfer_id=transfer_id,
            source_task_id="task",
            state=state,
            record={"transferId": transfer_id, "state": "preparing"},
            created_by=user,
            admission_deadline=deadline,
            **fields,
        )
        transfer.envelope_key = service.envelope_key(transfer)
        transfer.save()
        return transfer, _upload(blobs, transfer, b"{}")

    waiting, waiting_blob = row("waiting", "preparing", past)
    checking, checking_blob = row("checking", "validating", past)
    live_task = AgentTask.objects.create(organization=org, agent_definition=agent, status="running")
    ready, ready_blob = row("ready", "destination_ready", past, agent_task=live_task)
    dead_task = AgentTask.objects.create(organization=org, agent_definition=agent, status="failed")
    installing, installing_blob = row("installing", "preparing", future, agent_task=dead_task)
    fresh, fresh_blob = row("fresh", "preparing", future)
    done, done_blob = row("done", "committed", past)

    with django_capture_on_commit_callbacks(execute=True):
        summary = service.expire_transfers()
    assert summary == {"evaluated": 4, "expired": 3, "ended": 1, "errors": 0}
    states = dict(AgentConversationTransfer.objects.values_list("transfer_id", "state"))
    assert states == {
        "waiting": "aborted",
        "checking": "aborted",
        "ready": "aborted",
        "installing": "aborted",
        "fresh": "preparing",
        "done": "committed",
    }
    ready.refresh_from_db()
    installing.refresh_from_db()
    assert ready.abort_reason == service.EXPIRED_REASON
    assert installing.abort_reason == (
        "the paused session already ended (failed), so it cannot start this conversation"
    )
    assert not any(path.exists() for path in (waiting_blob, checking_blob, ready_blob, installing_blob))
    assert fresh_blob.exists() and done_blob.exists()
    # Only the live paused task is cancelled; the failed one is left alone.
    assert cancels == [str(live_task.guid)]

    with django_capture_on_commit_callbacks(execute=True):
        assert service.expire_transfers() == {"evaluated": 0, "expired": 0, "ended": 0, "errors": 0}


def test_the_expiry_sweep_is_scheduled_active_and_served():
    from astrolift_workflows.activities.conversation_transfer import (
        expire_conversation_transfers_tick,
        validate_conversation_transfer,
    )
    from astrolift_workflows.schedule_boot import PHASE_3A_ACTIVE_KINDS
    from astrolift_workflows.schedule_registry import ScheduleKind, get_schedule
    from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS
    from astrolift_workflows.workflows.conversation_transfer import (
        ConversationTransferExpiryTickWorkflow,
        ValidateConversationTransferWorkflow,
    )

    kind = ScheduleKind.CONVERSATION_TRANSFER_EXPIRY
    assert kind in PHASE_3A_ACTIVE_KINDS
    schedule = get_schedule(kind=kind)
    assert schedule.interval_seconds == 60
    assert schedule.workflow_name == "ConversationTransferExpiryTickWorkflow"
    assert ConversationTransferExpiryTickWorkflow in WORKFLOWS
    assert ValidateConversationTransferWorkflow in WORKFLOWS
    assert expire_conversation_transfers_tick in ACTIVITIES
    assert validate_conversation_transfer in ACTIVITIES


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_validation_workflow_admits_the_paused_session_on_a_real_worker(
    temporal_env, permission_resolver, blobs, settings
):
    from asgiref.sync import sync_to_async

    from astrolift_workflows.activities.conversation_transfer import validate_conversation_transfer
    from astrolift_workflows.inputs import ValidateConversationTransferInput
    from astrolift_workflows.workflows.conversation_transfer import ValidateConversationTransferWorkflow
    from core.testing.temporal import temporal_worker

    # The admitted task's own dispatch start is a no-op on this test server.
    settings.ASTROLIFT_TEMPORAL_ENABLED = False

    def arrange():
        org = Organization.objects.create(name="Move Org", slug=f"move-{uuid4().hex[:8]}")
        user = get_user_model().objects.create(username=f"mover-{uuid4().hex[:8]}", email="m@astrolift.dev")
        team = Team.objects.create(organization=org, name="Movers", slug="movers")
        app = RegisteredApp.objects.create(
            organization=org, team=team, name="Coder", slug="coder", provisioning_status="ready"
        )
        workload = Workload.objects.create(
            registered_app=app, name="Coder", slug="coder", kind=Workload.Kind.AGENT
        )
        AgentEnvironmentSpec.objects.create(
            organization=org,
            name="Coder",
            slug="coder",
            runtime="claude",
            agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        )
        info = SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))
        transfer, _envelope_value, _path = _validating(permission_resolver, info, org, user, workload, blobs)
        return transfer, user

    transfer, user = await sync_to_async(arrange)()
    queue = f"conversation-transfer-{uuid4()}"
    async with temporal_worker(
        temporal_env,
        task_queue=queue,
        workflows=[ValidateConversationTransferWorkflow],
        activities=[validate_conversation_transfer],
    ):
        state = await asyncio.wait_for(
            temporal_env.client.execute_workflow(
                ValidateConversationTransferWorkflow.run,
                ValidateConversationTransferInput(transfer_id=transfer.pk, actor=_actor(user)),
                id=service.validation_workflow_id(transfer),
                task_queue=queue,
            ),
            timeout=60,
        )

    assert state == "preparing"
    await sync_to_async(transfer.refresh_from_db)()
    assert transfer.state == "preparing" and transfer.agent_task_id is not None
    task = await sync_to_async(AgentTask.objects.get)(pk=transfer.agent_task_id)
    assert task.status == AgentTask.Status.QUEUED and task.created_by_id == user.pk


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_another_requester_or_org_cannot_reach_the_transfer(
    permission_resolver, info, org, user, agent, blobs, starts
):
    transfer, _envelope_value, _path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    permission_resolver.grant(Permission.AGENT_READ)
    other_user = get_user_model().objects.create(username="other", email="other@astrolift.dev")
    other_info = SimpleNamespace(
        context=SimpleNamespace(user=other_user, request=SimpleNamespace(user=other_user))
    )
    with _tenant(org, other_user):
        result = AgentsMutation().abort_conversation_transfer(
            other_info, input=ConversationTransferRecordInput(record={**transfer.record, "state": "aborted"})
        )
        read = AgentsQuery().conversation_transfer(other_info, transfer_id="transfer-1")
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    assert read is None

    other_org = Organization.objects.create(name="Elsewhere", slug="elsewhere")
    with _tenant(other_org, user):
        read = AgentsQuery().conversation_transfer(info, transfer_id="transfer-1")
    assert read is None

    with _tenant(org, user):
        read = AgentsQuery().conversation_transfer(info, transfer_id="transfer-1")
    assert read is not None and read.state == "preparing"


def test_a_caller_without_a_user_identity_can_neither_move_nor_reach_a_conversation(
    permission_resolver, org, user, agent, blobs, starts
):
    """An org API token with no user resolves to no requester. Before, a
    transfer it created had created_by NULL, which every other user-less
    caller in the org matched."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    permission_resolver.grant(Permission.AGENT_READ)
    anonymous = AnonymousUser()
    token_info = SimpleNamespace(
        context=SimpleNamespace(user=anonymous, request=SimpleNamespace(user=anonymous))
    )
    record = _record(org, agent)

    result = _prepare(token_info, org, None, record, _bytes(_envelope(record)))
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert result.errors[0].message == service.NO_REQUESTER_REASON
    assert not AgentConversationTransfer.all_objects.exists()
    assert starts == []

    # A transfer with no owner is reachable by no one without a user.
    ownerless = AgentConversationTransfer.objects.create(
        organization=org,
        agent_definition=agent,
        transfer_id="transfer-1",
        source_task_id="task-1",
        record=record,
        admission_deadline=timezone.now() + timedelta(hours=1),
    )
    with _tenant(org, None):
        aborted = AgentsMutation().abort_conversation_transfer(
            token_info, input=ConversationTransferRecordInput(record={**record, "state": "aborted"})
        )
        verified = AgentsMutation().verify_conversation_transfer(
            token_info, input=ConversationTransferRecordInput(record=record)
        )
        read = AgentsQuery().conversation_transfer(token_info, transfer_id="transfer-1")
    assert aborted.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert verified.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert read is None
    assert service.owned_transfer(org.pk, "transfer-1", Permission.AGENT_DISPATCH, None) is None
    ownerless.refresh_from_db()
    assert ownerless.state == "preparing"


def test_a_concurrent_prepare_by_another_user_never_returns_their_transfer(
    permission_resolver, info, org, user, agent, blobs, monkeypatch
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    data = _bytes(_envelope(record))
    assert _prepare(info, org, user, record, data).ok

    # The other user's prepare loses the creation race to the stored row.
    real = service._stored_transfer
    calls = []

    def first_misses(organization, transfer_id):
        calls.append(transfer_id)
        return None if len(calls) == 1 else real(organization, transfer_id)

    monkeypatch.setattr(service, "_stored_transfer", first_misses)
    other = get_user_model().objects.create(username="racer", email="racer@astrolift.dev")
    other_info = SimpleNamespace(context=SimpleNamespace(user=other, request=SimpleNamespace(user=other)))
    result = _prepare(other_info, org, other, record, data)
    assert len(calls) == 2
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert AgentConversationTransfer.objects.get().created_by_id == user.pk


# ---------------------------------------------------------------------------
# Runner endpoint
# ---------------------------------------------------------------------------


def test_runner_endpoint_is_bound_to_the_task_callback_key(
    permission_resolver, info, org, user, agent, blobs, starts
):
    transfer, _envelope_value, _path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    task = transfer.agent_task
    key = "alft_cb_test-key"
    AgentTask.objects.filter(pk=task.pk).update(callback_token_hash=hashlib.sha256(key.encode()).hexdigest())
    url = f"/api/dispatch/v1/agents/{task.guid}/transfer/"
    client = Client()

    assert client.get(url).status_code == 401
    assert client.get(url, HTTP_AUTHORIZATION="Bearer alft_cb_wrong").status_code == 401

    view = client.get(url, HTTP_AUTHORIZATION=f"Bearer {key}").json()
    assert view["state"] == "preparing"
    assert view["envelopeUrl"].startswith("file://")
    assert view["expectedReceipt"]["envelopeSha256"] == transfer.envelope_sha256

    bad = client.post(
        url,
        data=json.dumps({"phase": "nope", "status": "done"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {key}",
    )
    assert bad.status_code == 400

    receipt = {
        **transfer.expected_receipt,
        "directories": [{"path": "src/new", "receipt": "1:2:3"}],
    }
    done = client.post(
        url,
        data=json.dumps({"phase": "installed", "status": "done", "receipt": receipt}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {key}",
    )
    assert done.status_code == 200 and done.json()["state"] == "destination_ready"

    late = client.post(
        url,
        data=json.dumps({"phase": "undo", "status": "done"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {key}",
    )
    assert late.status_code == 409


def test_a_runner_report_after_the_deadline_aborts_the_arrival(
    permission_resolver, info, org, user, agent, blobs, starts, cancels, django_capture_on_commit_callbacks
):
    transfer, _envelope_value, path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    AgentConversationTransfer.objects.filter(pk=transfer.pk).update(
        admission_deadline=timezone.now() - timedelta(seconds=1)
    )
    with django_capture_on_commit_callbacks(execute=True):
        view = _install(transfer)
    assert view["abort"] is True and view["state"] == "aborted"
    transfer.refresh_from_db()
    assert transfer.abort_reason == service.EXPIRED_REASON
    assert cancels == [str(transfer.agent_task.guid)]
    assert not path.exists()


def test_a_task_without_a_transfer_has_no_runner_view(org, agent):
    task = AgentTask.objects.create(organization=org, agent_definition=agent)
    key = "alft_cb_plain"
    AgentTask.objects.filter(pk=task.pk).update(callback_token_hash=hashlib.sha256(key.encode()).hexdigest())
    response = Client().get(
        f"/api/dispatch/v1/agents/{task.guid}/transfer/", HTTP_AUTHORIZATION=f"Bearer {key}"
    )
    assert response.status_code == 404


def test_transfer_payload_type_exposes_no_envelope_key(org, agent, user):
    deadline = timezone.now() + timedelta(minutes=30)
    transfer = AgentConversationTransfer.objects.create(
        organization=org,
        agent_definition=agent,
        transfer_id="t",
        source_task_id="s",
        admission_deadline=deadline,
    )
    from astrolift_agents.schema.types import conversation_transfer_to_type

    data = conversation_transfer_to_type(transfer)
    assert not hasattr(data, "envelope_key")
    assert data.id == GUID(str(transfer.guid))
    assert data.admission_deadline == deadline
