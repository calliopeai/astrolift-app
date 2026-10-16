"""Paused admission of a managed SDK conversation Move (#1971).

Pins the receiver half against a real database and a local blob store:
refusal before any change, prepare, upload binding, the runner's install
report, verify, commit exactly once, abort with nothing left behind, retry
idempotency, size and digest limits, and tenant/requester isolation.
"""

from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from astrolift_agents.models import AgentConversationTransfer, AgentEnvironmentSpec, AgentTask
from astrolift_agents.schema.mutations import (
    AgentsMutation,
    ConversationTransferRecordInput,
    PrepareConversationTransferInput,
)
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.services import conversation_transfer as service
from astrolift_dispatch.brief_injector import conversation_transfer_env_vars
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp, Workload
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db

OP_ID = "11111111-1111-4111-8111-111111111111"


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

    def _start(name, args, *, workflow_id, task_queue=None):
        from astrolift_workflows.client import WorkflowHandle

        recorded.append(workflow_id)
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
    return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=user.pk))


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


def _admitted(permission_resolver, info, org, user, agent, blobs, starts):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    envelope = _envelope(record)
    data = _bytes(envelope)
    first = _prepare(info, org, user, record, data)
    assert first.ok, first
    transfer = AgentConversationTransfer.objects.get(transfer_id="transfer-1")
    path = _upload(blobs, transfer, data)
    second = _prepare(info, org, user, record, data)
    assert second.ok, second
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
# Prepare, upload and admission
# ---------------------------------------------------------------------------


def test_prepare_waits_for_the_upload_then_admits_one_paused_session(
    permission_resolver, info, org, user, agent, blobs, starts
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    data = _bytes(_envelope(record))

    first = _prepare(info, org, user, record, data)
    assert first.ok
    assert first.data.state == "preparing"
    assert first.data.upload_url.startswith("file://")
    assert first.data.agent_task_id is None
    assert starts == []

    transfer = AgentConversationTransfer.objects.get(transfer_id="transfer-1")
    _upload(blobs, transfer, data)
    second = _prepare(info, org, user, record, data)
    assert second.ok
    assert second.data.state == "preparing"
    assert second.data.upload_url == ""
    transfer.refresh_from_db()
    task = transfer.agent_task
    assert task is not None and task.status == AgentTask.Status.QUEUED
    assert len(starts) == 1
    assert transfer.expected_receipt["workspaceSha256"] == "w" * 64

    # A retry after a lost reply never admits or installs again.
    third = _prepare(info, org, user, record, data)
    assert third.ok and str(third.data.agent_task_id) == str(task.guid)
    assert len(starts) == 1
    assert AgentTask.objects.count() == 1

    env = conversation_transfer_env_vars(task)
    assert env == [
        {
            "name": "ASTROLIFT_CONVERSATION_TRANSFER_URL",
            "value": f"https://astrolift.example/api/dispatch/v1/agents/{task.guid}/transfer/",
        }
    ]


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


def test_an_upload_that_does_not_match_its_digest_is_discarded(
    permission_resolver, info, org, user, agent, blobs, starts
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    data = _bytes(_envelope(record))
    _prepare(info, org, user, record, data)
    transfer = AgentConversationTransfer.objects.get(transfer_id="transfer-1")
    path = _upload(blobs, transfer, data[:-1] + b" ")

    result = _prepare(info, org, user, record, data)
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert not path.exists()
    transfer.refresh_from_db()
    assert transfer.state == "preparing" and transfer.agent_task_id is None
    assert starts == []


def test_undo_history_that_does_not_match_its_digest_aborts_and_leaves_nothing(
    permission_resolver, info, org, user, agent, blobs, starts
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    tampered = _undo()
    tampered["checkpoints"][0]["userMessage"] = "something else"
    data = _bytes(_envelope(record, undo=tampered))
    _prepare(info, org, user, record, data)
    transfer = AgentConversationTransfer.objects.get(transfer_id="transfer-1")
    path = _upload(blobs, transfer, data)

    result = _prepare(info, org, user, record, data)
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    transfer.refresh_from_db()
    assert transfer.state == "aborted"
    assert "digest" in transfer.abort_reason
    assert not path.exists()
    assert starts == []


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
    permission_resolver, info, org, user, agent, blobs, starts, cancels
):
    transfer, _envelope_value, path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    view = _install(transfer, undoCheckpointsSha256="b" * 64)
    assert view["abort"] is True
    transfer.refresh_from_db()
    assert transfer.state == "aborted" and "undoCheckpointsSha256" in transfer.abort_reason
    assert not path.exists()
    assert cancels == [str(transfer.agent_task.guid)]


def test_a_box_that_cannot_hold_undo_refuses_with_its_reason(
    permission_resolver, info, org, user, agent, blobs, starts, cancels
):
    transfer, _envelope_value, path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    service.record_runner_progress(
        transfer, {"phase": "refused", "status": "failed", "reason": "no directory birth time"}
    )
    transfer.refresh_from_db()
    assert transfer.state == "aborted"
    assert transfer.record["abortReason"] == "destination refused: no directory birth time"
    assert not path.exists()


def test_commit_starts_the_session_exactly_once(permission_resolver, info, org, user, agent, blobs, starts):
    transfer, envelope, path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    _install(transfer)
    transfer.refresh_from_db()
    committed = {
        **transfer.record,
        "state": "committed",
        "owner": "destination",
        "sourceStoppedAt": "2026-10-16T00:01:00.000Z",
        "committedAt": "2026-10-16T00:02:00.000Z",
    }
    digest = _snapshot_digest(envelope)
    with _tenant(org, user):
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


def test_abort_retires_the_arrival_and_removes_what_it_created(
    permission_resolver, info, org, user, agent, blobs, starts, cancels
):
    transfer, _envelope_value, path = _admitted(permission_resolver, info, org, user, agent, blobs, starts)
    aborted = {**transfer.record, "state": "aborted", "abortedAt": "2026-10-16T00:05:00.000Z"}
    with _tenant(org, user):
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


def test_abort_before_the_upload_needs_no_session(
    permission_resolver, info, org, user, agent, blobs, cancels
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    record = _record(org, agent)
    _prepare(info, org, user, record, _bytes(_envelope(record)))
    with _tenant(org, user):
        result = AgentsMutation().abort_conversation_transfer(
            info, input=ConversationTransferRecordInput(record={**record, "state": "aborted"})
        )
    assert result.ok and result.data.state == "aborted"
    assert cancels == []


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


def test_a_task_without_a_transfer_has_no_runner_view(org, agent):
    task = AgentTask.objects.create(organization=org, agent_definition=agent)
    key = "alft_cb_plain"
    AgentTask.objects.filter(pk=task.pk).update(callback_token_hash=hashlib.sha256(key.encode()).hexdigest())
    response = Client().get(
        f"/api/dispatch/v1/agents/{task.guid}/transfer/", HTTP_AUTHORIZATION=f"Bearer {key}"
    )
    assert response.status_code == 404


def test_transfer_payload_type_exposes_no_envelope_key(org, agent, user):
    transfer = AgentConversationTransfer.objects.create(
        organization=org, agent_definition=agent, transfer_id="t", source_task_id="s"
    )
    from astrolift_agents.schema.types import conversation_transfer_to_type

    data = conversation_transfer_to_type(transfer)
    assert not hasattr(data, "envelope_key")
    assert data.id == GUID(str(transfer.guid))
