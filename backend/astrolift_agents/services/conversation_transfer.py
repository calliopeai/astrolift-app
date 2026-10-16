"""Paused admission for a managed SDK conversation Move (#1971).

The IDE (calliope-vscode ``calliope-agents``) moves a Calliope AI managed
conversation to an Astrolift session. Ownership stays with the source until
this receiver acknowledges the exact snapshot, so the flow is paused:

1. **Prepare** (first call) checks the destination can host the conversation
   and refuses, with a reason, before anything is created. It records the
   transfer and returns a presigned PUT URL for the full envelope: the
   TaskTransferEnvelope JSON (record, snapshot, and its transport: workspace
   artifact, commit bundle, checkpoint artifact, ``undoCheckpoints``,
   ``undoDirectoryModes``, ``undoDirectoryFingerprints``).
2. **Prepare** (again, after the PUT) verifies the upload against the
   declared digest and size, validates the envelope, and dispatches a paused
   task. The runner downloads the envelope, installs Git state, progress,
   conversation and undo history, reports progress, and finally an install
   receipt. Only an exact receipt makes the transfer ``destination_ready``.
   A retry gets the same acknowledgement and never reinstalls.
3. **Verify** (no transport) accepts only the exact acknowledged arrival.
4. **Commit** starts the session from the installed state, exactly once.
5. **Abort** retires the arrival: the stored envelope is deleted and the
   paused task cancelled, so nothing it created is left behind.

Every call after the first names the stored arrival by ``transferId`` and is
bound to it by the record's immutable fields and the snapshot digest.
Canonical JSON (keys sorted, no whitespace, UTF-8) is the digest input,
matching calliope-agents' ``undoCheckpointsSha256``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
HARNESS_HOST_CONTRACT = "1.17"
# Runtimes whose runner implements the paused-admission contract. Env-driven
# like the rest of the runtime catalog (``runtime_catalog``); empty means no
# runtime on this install hosts a moved conversation yet.
RUNTIMES_ENV = "ASTROLIFT_CONVERSATION_TRANSFER_RUNTIMES"

# Limits mirror calliope-agents: the managed transfer transport limit, the
# undo history limit, its per-file limit, and the 32 MiB clipboard limit for
# Git artifacts.
MAX_ENVELOPE_BYTES = 150 * 1024 * 1024
MAX_UNDO_BYTES = 64 * 1024 * 1024
MAX_UNDO_FILE_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
URL_TTL_SECONDS = 900
MAX_PROGRESS_ENTRIES = 64
MAX_REASON_CHARS = 1000

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_OPERATION_ID = re.compile(r"^[a-f0-9-]{36}$")
_TRANSFER_NAMESPACE = uuid.UUID("5d0c5d1e-1971-4c6b-9a0e-6d2f7a1c1971")
_SOURCE_KINDS = {"computer", "container"}
_RUNNER_PHASES = {"download", "workspace", "conversation", "progress", "undo", "installed", "refused"}
_RUNNER_STATUSES = {"running", "done", "failed"}
# Record fields a later call may never change.
_IMMUTABLE_RECORD_FIELDS = (
    "version",
    "transferId",
    "taskId",
    "source",
    "destination",
    "checkpointId",
    "checkpointSha256",
    "checkpointRefreshOf",
    "createdAt",
)


@dataclass(slots=True)
class ConversationTransferError(Exception):
    code: str
    message: str
    field: str = ""

    def __str__(self) -> str:
        return self.message


def _invalid(message: str, field: str = "envelope") -> ConversationTransferError:
    return ConversationTransferError("validation", message, field)


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------


def destination_identity(*, organization, workload, environment_spec) -> str:
    """The identity the IDE writes into ``record.destination.identity``.

    Binds the arrival to this organization, agent and environment, so an
    envelope prepared for one destination cannot be replayed into another.
    """
    return json.dumps(
        [
            "astrolift-session",
            str(organization.guid),
            str(workload.guid),
            str(environment_spec.guid) if environment_spec is not None else "",
        ],
        separators=(",", ":"),
    )


def transfer_capability(*, organization, workload, environment_spec) -> dict:
    """What the destination advertises before the IDE sends anything."""
    from astrolift_registry.models import Workload

    runtimes = {name.strip() for name in os.environ.get(RUNTIMES_ENV, "").split(",") if name.strip()}
    runtime = environment_spec.runtime if environment_spec is not None else ""
    reason = ""
    if workload.run_family != Workload.RunFamily.TASK:
        reason = "only a Task agent can host a moved conversation"
    elif not runtime:
        reason = (
            "the environment names no catalog runtime, so no runner is known to host a moved conversation"
        )
    elif runtime not in runtimes:
        reason = f"the {runtime!r} runtime does not host a moved conversation on this install"
    supported = not reason
    return {
        "managedConversation": supported,
        "harnessHostContract": HARNESS_HOST_CONTRACT if supported else "",
        "undo": supported,
        "protocolVersion": PROTOCOL_VERSION,
        "destinationIdentity": destination_identity(
            organization=organization, workload=workload, environment_spec=environment_spec
        ),
        "maxEnvelopeBytes": MAX_ENVELOPE_BYTES,
        "reason": reason,
    }


def transfer_destination(org_pk: int, agent_slug: str, environment_spec_id, permission):
    """``(organization, workload, spec)`` for a Move into the agent the caller
    reaches at ``permission``; the spec resolves as dispatch resolves it."""
    from astrolift_agents.models import AgentEnvironmentSpec
    from astrolift_agents.services.task_preparation import default_environment_spec
    from astrolift_agents.visibility import agent_by_slug, spec_usable_by_app
    from astrolift_identity.models import Organization
    from astrolift_registry.models import Workload

    organization = Organization.objects.filter(pk=org_pk, deleted_at__isnull=True).first()
    workload = agent_by_slug(org_pk, (agent_slug or "").strip(), permission) if organization else None
    if workload is None or workload.kind != Workload.Kind.AGENT:
        raise ConversationTransferError("not_found", "agent not found", "agentSlug")
    if environment_spec_id is None:
        return organization, workload, default_environment_spec(workload)
    spec = AgentEnvironmentSpec.objects.filter(
        guid=str(environment_spec_id), organization_id=org_pk, deleted_at__isnull=True
    ).first()
    if spec is None or not spec_usable_by_app(spec, workload.registered_app):
        raise ConversationTransferError("not_found", "environment spec not found", "environmentSpecId")
    return organization, workload, spec


def owned_transfer(org_pk: int, transfer_id, permission, requester_id):
    """The requester's own transfer, reachable at ``permission``, or None.

    Reached through its paused task, or before the task exists through its
    agent, so a transfer reads exactly as its session does.
    """
    from django.db.models import Q

    from astrolift_agents.models import AgentConversationTransfer
    from astrolift_agents.visibility import agent_tasks, agent_workloads

    if not isinstance(transfer_id, str) or not transfer_id:
        return None
    return (
        AgentConversationTransfer.objects.filter(
            organization_id=org_pk, transfer_id=transfer_id, created_by_id=requester_id
        )
        .filter(
            Q(agent_task__in=agent_tasks(org_pk, permission))
            | Q(agent_task__isnull=True, agent_definition__in=agent_workloads(org_pk, permission))
        )
        .select_related("organization", "agent_task")
        .first()
    )


# ---------------------------------------------------------------------------
# Envelope validation (before any destination IO)
# ---------------------------------------------------------------------------


def _identifier(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 256


def _path(value) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 4096
        and not re.search(r"[\\:\x00]", value)
        and all(part and part not in {".", ".."} for part in value.split("/"))
    )


def _base64(value, limit: int) -> bytes | None:
    if not isinstance(value, str) or len(value) > -(-limit // 3) * 4:
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(decoded) > limit or base64.b64encode(decoded).decode("ascii") != value:
        return None
    return decoded


def _keys(value: dict, allowed: set[str]) -> bool:
    return set(value) <= allowed


def check_record(record, *, destination: str, state: str) -> dict:
    """A TaskTransferRecord addressed to this destination in ``state``."""
    if not isinstance(record, dict):
        raise _invalid("record must be an object", "record")
    if record.get("version") != 2:
        raise _invalid("only a version 2 record carries a managed conversation", "record")
    for key in ("transferId", "taskId", "checkpointId"):
        if not _identifier(record.get(key)):
            raise _invalid(f"record.{key} is required", "record")
    source = record.get("source")
    if (
        not isinstance(source, dict)
        or source.get("kind") not in _SOURCE_KINDS
        or not _identifier(source.get("identity"))
    ):
        raise _invalid("record.source must be a computer or container", "record")
    if record.get("destination") != {"kind": "astrolift", "identity": destination}:
        raise ConversationTransferError(
            "precondition", "the transfer is addressed to another destination", "record"
        )
    if record.get("state") != state:
        raise _invalid(f"record.state must be {state!r}", "record")
    if not isinstance(record.get("createdAt"), str):
        raise _invalid("record.createdAt is required", "record")
    return record


def _check_snapshot_digest(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.match(value):
        raise _invalid(f"{field} must be a lowercase SHA-256 hex digest", field)
    return value


def _check_undo_checkpoints(value) -> None:
    """Structural port of calliope-agents ``captureCheckpointTransfer``.

    The runner repeats the full check, including rename projection, before
    it touches the box workspace; this refuses malformed history early.
    """
    if (
        not isinstance(value, dict)
        or value.get("version") not in (1, 2)
        or not _keys(value, {"version", "checkpoints", "directories"})
        or not isinstance(value.get("checkpoints"), list)
        or len(value["checkpoints"]) > 100
        or not isinstance(value.get("directories"), list)
        or len(value["directories"]) > 1000
    ):
        raise _invalid("invalid undo checkpoint transfer")
    if len(canonical_json(value)) > MAX_UNDO_BYTES:
        raise _invalid("undo history exceeds its size limit")
    ids: set[str] = set()
    files: set[str] = set()
    operations: set[str] = set()
    bindings: set[tuple[str, str, str]] = set()
    file_count = operation_count = 0
    file_keys = {
        "id",
        "filePath",
        "content",
        "encoding",
        "destinationExpected",
        "existedBefore",
        "timestamp",
        "toolCallId",
        "toolName",
        "description",
    }
    for checkpoint in value["checkpoints"]:
        if (
            not isinstance(checkpoint, dict)
            or not _keys(
                checkpoint, {"id", "userMessage", "timestamp", "fileCheckpoints", "destinationOperations"}
            )
            or not _identifier(checkpoint.get("id"))
            or checkpoint["id"] in ids
            or not isinstance(checkpoint.get("userMessage"), str)
            or not isinstance(checkpoint.get("timestamp"), str)
            or not isinstance(checkpoint.get("fileCheckpoints"), list)
        ):
            raise _invalid("invalid transferred checkpoint")
        ids.add(checkpoint["id"])
        file_count += len(checkpoint["fileCheckpoints"])
        if file_count > 1000:
            raise _invalid("undo history exceeds its file limit")
        for entry in checkpoint["fileCheckpoints"]:
            if (
                not isinstance(entry, dict)
                or not _keys(entry, file_keys)
                or not _identifier(entry.get("id"))
                or entry["id"] in files
                or not _path(entry.get("filePath"))
                or entry.get("encoding") != "base64"
                or _base64(entry.get("content"), MAX_UNDO_FILE_BYTES) is None
                or _base64(entry.get("destinationExpected"), MAX_UNDO_FILE_BYTES) is None
                or not isinstance(entry.get("existedBefore"), bool)
                or (not entry["existedBefore"] and entry["content"] != "")
                or not isinstance(entry.get("timestamp"), str)
                or any(
                    key in entry and not isinstance(entry[key], str)
                    for key in ("toolCallId", "toolName", "description")
                )
            ):
                raise _invalid("invalid transferred file checkpoint; conditional undo evidence is required")
            files.add(entry["id"])
        ops = checkpoint.get("destinationOperations")
        if ops is None:
            continue
        if not isinstance(ops, list):
            raise _invalid("invalid transferred operations")
        operation_count += len(ops)
        if operation_count > 1000:
            raise _invalid("undo history exceeds its operation limit")
        for op in ops:
            if (
                not isinstance(op, dict)
                or not isinstance(op.get("id"), str)
                or not _OPERATION_ID.match(op["id"])
                or op["id"] in operations
                or not _path(op.get("path"))
                or ("reverted" in op and not isinstance(op["reverted"], bool))
            ):
                raise _invalid("invalid transferred operation")
            operations.add(op["id"])
            common = {"id", "kind", "path", "reverted"}
            kind = op.get("kind")
            if kind == "mkdir":
                created = op.get("created")
                if not _keys(op, common | {"created"}) or not isinstance(created, list) or len(created) > 128:
                    raise _invalid("invalid transferred directory creation")
                seen: set[str] = set()
                for directory in created:
                    path = directory.get("path") if isinstance(directory, dict) else None
                    if (
                        not isinstance(directory, dict)
                        or not _keys(directory, {"path"})
                        or not _path(path)
                        or path in seen
                        or (op["path"] != path and not op["path"].startswith(path + "/"))
                    ):
                        raise _invalid("invalid transferred directory path")
                    seen.add(path)
                    if not op.get("reverted"):
                        bindings.add((checkpoint["id"], op["id"], path))
            elif kind == "write":
                before = op.get("before")
                if (
                    not _keys(op, common | {"before", "after"})
                    or (before is not None and _base64(before, MAX_UNDO_FILE_BYTES) is None)
                    or _base64(op.get("after"), MAX_UNDO_FILE_BYTES) is None
                ):
                    raise _invalid("invalid transferred write")
            elif kind == "rename":
                if (
                    not _keys(op, common | {"destination", "snapshot"})
                    or not _path(op.get("destination"))
                    or not _snapshot(op.get("snapshot"))
                ):
                    raise _invalid("invalid transferred rename")
            else:
                raise _invalid("unknown transferred operation")
    for directory in value["directories"]:
        allowed = {"checkpointId", "operationId", "recordedPath", "currentPath", "snapshot"}
        if value["version"] == 2:
            allowed.add("retired")
        if (
            not isinstance(directory, dict)
            or not _keys(directory, allowed)
            or (
                "retired" in directory
                and (directory["retired"] is not True or directory.get("snapshot") is not None)
            )
            or not _identifier(directory.get("checkpointId"))
            or not _identifier(directory.get("operationId"))
            or not _path(directory.get("recordedPath"))
            or not _path(directory.get("currentPath"))
            or "snapshot" not in directory
            or (
                directory["snapshot"] is not None
                and (not _snapshot(directory["snapshot"]) or directory["snapshot"]["type"] != "directory")
            )
        ):
            raise _invalid("invalid transferred directory binding")
        key = (directory["checkpointId"], directory["operationId"], directory["recordedPath"])
        if key not in bindings:
            raise _invalid("invalid transferred directory binding")
        bindings.discard(key)
    if bindings:
        raise _invalid("undo history is missing directory evidence")


def _snapshot(value) -> bool:
    return (
        isinstance(value, dict)
        and _keys(value, {"type", "fingerprint"})
        and value.get("type") in ("file", "directory")
        and isinstance(value.get("fingerprint"), str)
        and bool(_SHA256.match(value["fingerprint"]))
    )


def _undo_directories(checkpoints: dict) -> list[str]:
    return sorted({d["currentPath"] for d in checkpoints["directories"] if d.get("snapshot") is not None})


def _check_directory_entries(value, wanted: list[str], *, field: str, check) -> None:
    if not isinstance(value, list) or len(value) != len(wanted):
        raise _invalid(f"{field} do not match the undo history")
    paths = []
    for entry in value:
        if not isinstance(entry, dict) or not _path(entry.get("path")) or not check(entry):
            raise _invalid(f"invalid {field}")
        paths.append(entry["path"])
    if sorted(paths) != wanted:
        raise _invalid(f"{field} do not match the undo history")


def _check_artifacts(envelope: dict, snapshot: dict) -> None:
    workspace = snapshot.get("workspaceState")
    if workspace is not None and (
        not isinstance(workspace, dict)
        or workspace.get("version") not in (1, 2)
        or not isinstance(workspace.get("head"), str)
        or not isinstance(workspace.get("sha256"), str)
        or not isinstance(workspace.get("relativeCwd"), str)
        or not isinstance(workspace.get("dirty"), bool)
    ):
        raise _invalid("invalid workspace state")
    artifact = envelope.get("workspaceArtifact")
    if artifact is not None:
        if (
            workspace is None
            or not isinstance(artifact, dict)
            or artifact.get("version") != workspace.get("version")
            or artifact.get("encoding") != "gzip-base64"
            or not isinstance(artifact.get("rawBytes"), int)
            or not 0 <= artifact["rawBytes"] <= MAX_ARTIFACT_BYTES
            or _base64(artifact.get("data"), MAX_ARTIFACT_BYTES) is None
        ):
            raise _invalid("invalid workspace artifact")
    bundle = envelope.get("commitArtifact")
    if bundle is not None:
        data = _base64(bundle.get("data"), MAX_ARTIFACT_BYTES) if isinstance(bundle, dict) else None
        if (
            workspace is None
            or data is None
            or bundle.get("version") != 1
            or bundle.get("encoding") != "git-bundle-base64"
            or bundle.get("bytes") != len(data)
            or bundle.get("sha256") != sha256_hex(data)
        ):
            raise _invalid("invalid commit bundle")
    checkpoint = envelope.get("checkpointArtifact")
    if checkpoint is not None and (
        not isinstance(checkpoint, dict)
        or checkpoint.get("version") != 1
        or checkpoint.get("harness") not in ("claude-code-cli", "codex-cli")
        or checkpoint.get("encoding") != "gzip-base64"
        or _base64(checkpoint.get("data"), MAX_ARTIFACT_BYTES) is None
    ):
        raise _invalid("invalid checkpoint artifact")


def validate_envelope(envelope, *, record: dict, undo: bool) -> tuple[str, dict]:
    """Validate an uploaded envelope; return its snapshot digest and the
    receipt the runner must repeat once the install is durable."""
    allowed = {
        "version",
        "record",
        "snapshot",
        "checkpointArtifact",
        "workspaceArtifact",
        "commitArtifact",
        "undoCheckpoints",
        "undoDirectoryModes",
        "undoDirectoryFingerprints",
    }
    if not isinstance(envelope, dict) or envelope.get("version") != 1 or not _keys(envelope, allowed):
        raise _invalid("invalid transfer envelope")
    if envelope.get("record") != record:
        raise ConversationTransferError(
            "precondition", "the uploaded envelope carries a different record", "record"
        )
    snapshot = envelope.get("snapshot")
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("taskId") != record["taskId"]
        or snapshot.get("checkpointId") != record["checkpointId"]
        or not isinstance(snapshot.get("title"), str)
        or not isinstance(snapshot.get("description"), str)
        or not isinstance(snapshot.get("attachments"), list)
        or not isinstance(snapshot.get("conversationHistory"), list)
    ):
        raise _invalid("invalid transfer snapshot")
    conversation = snapshot.get("managedConversation")
    if not isinstance(conversation, dict) or conversation.get("version") not in (1, 2, 3, 4):
        raise _invalid("a managed conversation is required")
    _check_artifacts(envelope, snapshot)

    digest = conversation.get("checkpointsSha256") if conversation["version"] == 4 else None
    unavailable = conversation.get("undoUnavailable") if conversation["version"] == 4 else None
    undo_keys = {"undoCheckpoints", "undoDirectoryModes", "undoDirectoryFingerprints"} & set(envelope)
    if digest is not None and unavailable is not None:
        raise _invalid("undo history is both bound and unavailable")
    if conversation["version"] == 4 and digest is None and unavailable != "legacy-history":
        raise _invalid("a version 4 conversation must bind its undo history or state why it cannot")
    if digest is None:
        if undo_keys:
            raise _invalid("undo history was sent without a bound digest")
    else:
        if not undo:
            raise ConversationTransferError(
                "precondition", "this destination cannot hold undo history", "envelope"
            )
        _check_snapshot_digest(digest, "managedConversation.checkpointsSha256")
        checkpoints = envelope.get("undoCheckpoints")
        _check_undo_checkpoints(checkpoints)
        if sha256_hex(canonical_json(checkpoints)) != digest:
            raise _invalid("undo history does not match its bound digest")
        wanted = _undo_directories(checkpoints)
        _check_directory_entries(
            envelope.get("undoDirectoryModes"),
            wanted,
            field="undo directory modes",
            check=lambda e: set(e) == {"path", "mode"}
            and isinstance(e["mode"], int)
            and not isinstance(e["mode"], bool)
            and 0 <= e["mode"] <= 0o777,
        )
        if "undoDirectoryFingerprints" in envelope:
            _check_directory_entries(
                envelope["undoDirectoryFingerprints"],
                wanted,
                field="undo directory fingerprints",
                check=lambda e: set(e) == {"path", "fingerprint"}
                and isinstance(e["fingerprint"], str)
                and bool(_SHA256.match(e["fingerprint"])),
            )

    expected = {}
    workspace = snapshot.get("workspaceState")
    if workspace is not None:
        expected["workspaceSha256"] = workspace["sha256"]
    if digest is not None:
        expected["undoCheckpointsSha256"] = digest
    if isinstance(conversation.get("conversationId"), str):
        expected["conversationId"] = conversation["conversationId"]
    return sha256_hex(canonical_json(snapshot)), expected


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _driver(organization):
    from astrolift_pipelines.artifact_store import _get_blob_driver

    return _get_blob_driver(organization)


def envelope_key(transfer) -> str:
    return f"agent-transfers/{transfer.organization.guid}/{transfer.guid}/envelope.json"


def upload_url(transfer) -> str:
    """Write-once target for the envelope while the arrival awaits it."""
    if transfer.state != transfer.State.PREPARING or transfer.agent_task_id:
        return ""
    return _driver(transfer.organization).presigned_upload_url(
        transfer.envelope_key, expires_in=URL_TTL_SECONDS, content_type="application/json"
    )


def _delete_envelope(transfer) -> None:
    if not transfer.envelope_key:
        return
    try:
        _driver(transfer.organization).delete(transfer.envelope_key)
    except Exception:  # noqa: BLE001 - retirement must still settle the arrival
        logger.warning("could not delete transfer envelope %s", transfer.envelope_key, exc_info=True)


def _progress(transfer, phase: str, status: str, reason: str = "") -> None:
    entry = {"phase": phase, "status": status, "at": timezone.now().isoformat()}
    if reason:
        entry["reason"] = reason[:MAX_REASON_CHARS]
    transfer.progress = [*transfer.progress, entry][-MAX_PROGRESS_ENTRIES:]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def _same_immutables(stored: dict, incoming: dict) -> bool:
    return all(stored.get(key) == incoming.get(key) for key in _IMMUTABLE_RECORD_FIELDS)


def _retire(transfer, reason: str, *, by_destination: bool) -> None:
    """Abort the arrival and remove everything it created."""
    now = timezone.now()
    record = dict(transfer.record)
    record.update({"state": "aborted", "abortedAt": record.get("abortedAt") or now.isoformat()})
    if by_destination:
        record["abortReason"] = reason[:MAX_REASON_CHARS]
    transfer.record = record
    transfer.state = transfer.State.ABORTED
    transfer.aborted_at = now
    transfer.abort_reason = reason[:MAX_REASON_CHARS]
    _progress(transfer, "abort", "done", reason)
    transfer.save()
    _delete_envelope(transfer)
    task = transfer.agent_task
    if task is not None and task.status not in task.TERMINAL_STATUSES:
        from astrolift_workflows.activities.agent_stage import _cancel_agent_task_sync

        outcome = _cancel_agent_task_sync(str(task.guid))
        if not outcome.get("ok"):
            logger.warning("transfer %s: paused task %s not cancelled: %s", transfer.guid, task.guid, outcome)


def prepare_transfer(
    *,
    organization,
    workload,
    environment_spec,
    record,
    envelope_sha256: str,
    envelope_bytes: int,
    actor,
    team_id: int | None = None,
):
    """Record or advance one paused arrival. See the module docstring."""
    from astrolift_agents.models import AgentConversationTransfer

    capability = transfer_capability(
        organization=organization, workload=workload, environment_spec=environment_spec
    )
    if not capability["managedConversation"]:
        raise ConversationTransferError("precondition", capability["reason"], "agentSlug")
    check_record(record, destination=capability["destinationIdentity"], state="preparing")
    if record.get("owner") != "source":
        raise _invalid("the source owns the transfer until it commits", "record")
    _check_snapshot_digest(envelope_sha256, "envelopeSha256")
    if not isinstance(envelope_bytes, int) or not 0 < envelope_bytes <= MAX_ENVELOPE_BYTES:
        raise _invalid(f"envelopeBytes must be between 1 and {MAX_ENVELOPE_BYTES}", "envelopeBytes")

    requester_id = getattr(actor, "user_id", None)
    transfer = AgentConversationTransfer.all_objects.filter(
        organization=organization, transfer_id=record["transferId"]
    ).first()
    if transfer is not None:
        if (
            transfer.deleted_at is not None
            or transfer.created_by_id != requester_id
            or transfer.agent_definition_id != workload.pk
            or transfer.environment_spec_id != (environment_spec.pk if environment_spec else None)
            or not _same_immutables(transfer.record, record)
            or transfer.envelope_sha256 != envelope_sha256
            or transfer.envelope_bytes != envelope_bytes
        ):
            raise ConversationTransferError(
                "precondition", "transferId already names a different transfer", "record"
            )
        if transfer.state != transfer.State.PREPARING or transfer.agent_task_id:
            # A retry after a lost reply: the same acknowledgement, never a reinstall.
            return transfer
    else:
        transfer = AgentConversationTransfer(
            organization=organization,
            agent_definition=workload,
            environment_spec=environment_spec,
            transfer_id=record["transferId"],
            source_task_id=record["taskId"],
            record=record,
            envelope_sha256=envelope_sha256,
            envelope_bytes=envelope_bytes,
            created_by_id=requester_id,
        )
        transfer.envelope_key = envelope_key(transfer)
        _progress(transfer, "upload", "waiting")
        try:
            with transaction.atomic():
                transfer.save()
        except IntegrityError:
            existing = AgentConversationTransfer.all_objects.filter(
                organization=organization, transfer_id=record["transferId"]
            ).first()
            if existing is None:
                raise
            return existing
        return transfer

    data = _uploaded_envelope(transfer)
    if data is None:
        return transfer
    try:
        envelope = json.loads(data, parse_constant=_reject_constant)
        snapshot_sha256, expected = validate_envelope(
            envelope, record=transfer.record, undo=capability["undo"]
        )
    except ConversationTransferError as exc:
        _retire(transfer, f"envelope refused: {exc.message}", by_destination=True)
        raise
    except ValueError as exc:
        _retire(transfer, "envelope refused: invalid JSON", by_destination=True)
        raise _invalid("the uploaded envelope is not valid JSON") from exc
    transfer.snapshot_sha256 = snapshot_sha256
    transfer.expected_receipt = {"envelopeSha256": transfer.envelope_sha256, **expected}
    _progress(transfer, "upload", "done")
    transfer.save()
    return _admit(transfer, actor=actor, team_id=team_id)


def _reject_constant(value: str):
    raise ValueError(f"non-finite JSON number {value!r}")


def _uploaded_envelope(transfer) -> bytes | None:
    """The uploaded envelope when it is complete and exact, else None.

    A partial or mismatched upload is deleted so the IDE can PUT again.
    """
    from providers._sdk.blob_store import BlobStoreNotFoundError

    driver = _driver(transfer.organization)
    sizes = [
        blob.size_bytes
        for blob in driver.list_keys(transfer.envelope_key)
        if blob.key.endswith(transfer.envelope_key)
    ]
    if not sizes:
        return None
    if sizes[0] != transfer.envelope_bytes:
        if sizes[0] > transfer.envelope_bytes:
            driver.delete(transfer.envelope_key)
        return None
    try:
        data = driver.download(transfer.envelope_key)
    except BlobStoreNotFoundError:
        return None
    if len(data) != transfer.envelope_bytes or sha256_hex(data) != transfer.envelope_sha256:
        driver.delete(transfer.envelope_key)
        raise ConversationTransferError(
            "validation",
            "the uploaded envelope does not match envelopeSha256; upload it again",
            "envelopeSha256",
        )
    return data


def _admit(transfer, *, actor, team_id):
    """Dispatch the paused task that installs the arrival."""
    from astrolift_agents.services.agent_dispatch import AgentDispatchError, dispatch_registered_agent
    from core.run_trigger import request_trigger

    request_id = uuid.uuid5(_TRANSFER_NAMESPACE, f"{transfer.organization.guid}:{transfer.transfer_id}")
    try:
        task = dispatch_registered_agent(
            organization_id=transfer.organization_id,
            team_id=team_id,
            agent_slug=transfer.agent_definition.slug,
            workload_id=transfer.agent_definition_id,
            actor=actor,
            environment_spec_guid=str(transfer.environment_spec.guid) if transfer.environment_spec else "",
            trigger="conversation_transfer",
            trigger_kind=request_trigger(),
            client_request_id=str(request_id),
            conversation_transfer=transfer,
        )
    except AgentDispatchError as exc:
        transfer.refresh_from_db()
        _retire(transfer, f"the paused session could not start: {exc.message}", by_destination=True)
        raise ConversationTransferError(exc.code, exc.message) from exc
    transfer.refresh_from_db()
    if transfer.agent_task_id is None:
        transfer.agent_task = task
    _progress(transfer, "install", "waiting")
    transfer.save()
    return transfer


def verify_transfer(transfer, *, record, snapshot_sha256: str):
    """Accept only the exact acknowledged arrival (no transport)."""
    if transfer.state != transfer.State.DESTINATION_READY:
        raise ConversationTransferError(
            "precondition", f"the transfer is {transfer.state}, not destination_ready", "record"
        )
    if record != transfer.record or snapshot_sha256 != transfer.snapshot_sha256:
        raise ConversationTransferError("precondition", "this is not the acknowledged transfer", "record")
    return transfer


def commit_transfer(transfer, *, record, snapshot_sha256: str):
    """Start the session from the installed state, exactly once."""
    if not isinstance(record, dict) or record.get("state") != "committed":
        raise _invalid("record.state must be 'committed'", "record")
    if snapshot_sha256 != transfer.snapshot_sha256 or not _same_immutables(transfer.record, record):
        raise ConversationTransferError("precondition", "this is not the acknowledged transfer", "record")
    with transaction.atomic():
        locked = type(transfer).objects.select_for_update().get(pk=transfer.pk)
        if locked.state == locked.State.COMMITTED:
            if record != locked.record:
                raise ConversationTransferError(
                    "precondition", "the transfer already committed with another record", "record"
                )
            return locked
        if locked.state != locked.State.DESTINATION_READY:
            raise ConversationTransferError(
                "precondition", f"the transfer is {locked.state}, not destination_ready", "record"
            )
        if record.get("acknowledgedAt") != locked.record.get("acknowledgedAt"):
            raise ConversationTransferError("precondition", "this is not the acknowledged transfer", "record")
        if not isinstance(record.get("sourceStoppedAt"), str) or not isinstance(
            record.get("committedAt"), str
        ):
            raise _invalid("a commit needs the source's stop and commit times", "record")
        if record.get("owner") != "destination":
            raise _invalid("a committed transfer is owned by its destination", "record")
        locked.record = record
        locked.state = locked.State.COMMITTED
        locked.committed_at = timezone.now()
        _progress(locked, "commit", "done")
        locked.save()
    # Once the session starts, a restarted runner must never reinstall
    # over its later edits.
    _delete_envelope(locked)
    return locked


def abort_transfer(transfer, *, record, reason: str = ""):
    """Retire the arrival before commit; idempotent once aborted."""
    if not isinstance(record, dict) or record.get("state") != "aborted":
        raise _invalid("record.state must be 'aborted'", "record")
    if not _same_immutables(transfer.record, record):
        raise ConversationTransferError("precondition", "this is not the stored transfer", "record")
    with transaction.atomic():
        locked = type(transfer).objects.select_for_update().get(pk=transfer.pk)
        if locked.state == locked.State.ABORTED:
            return locked
        if locked.state == locked.State.COMMITTED:
            raise ConversationTransferError(
                "precondition", "the session already started from this transfer", "record"
            )
        locked.record = {**locked.record, **record}
        _retire(
            locked, str(record.get("abortReason") or reason or "aborted by the source"), by_destination=False
        )
    return locked


# ---------------------------------------------------------------------------
# Runner half (task-scoped callback key)
# ---------------------------------------------------------------------------


def runner_view(transfer) -> dict:
    """What the paused runner sees when it polls."""
    view = {
        "protocolVersion": PROTOCOL_VERSION,
        "transferId": transfer.transfer_id,
        "state": transfer.state,
        "start": transfer.state == transfer.State.COMMITTED,
        "abort": transfer.state == transfer.State.ABORTED,
        "envelopeSha256": transfer.envelope_sha256,
        "envelopeBytes": transfer.envelope_bytes,
        "expectedReceipt": transfer.expected_receipt,
    }
    if transfer.state == transfer.State.PREPARING:
        view["envelopeUrl"] = _driver(transfer.organization).presigned_url(
            transfer.envelope_key, expires_in=URL_TTL_SECONDS
        )
    return view


def record_runner_progress(transfer, body) -> dict:
    """Apply one runner progress report; return the runner's next view."""
    if not isinstance(body, dict):
        raise _invalid("progress must be a JSON object", "body")
    phase, status = body.get("phase"), body.get("status")
    reason = body.get("reason") or ""
    if phase not in _RUNNER_PHASES or status not in _RUNNER_STATUSES or not isinstance(reason, str):
        raise _invalid("progress needs a known phase and status", "body")
    with transaction.atomic():
        locked = type(transfer).objects.select_for_update().get(pk=transfer.pk)
        if locked.state != locked.State.PREPARING:
            raise ConversationTransferError("conflict", f"the transfer is {locked.state}", "state")
        if phase == "refused" or status == "failed":
            _retire(locked, f"destination refused: {reason or phase}", by_destination=True)
            return runner_view(locked)
        if phase == "installed" and status == "done":
            receipt = body.get("receipt")
            if not isinstance(receipt, dict) or len(canonical_json(receipt)) > 256 * 1024:
                raise _invalid("an install receipt is required", "receipt")
            mismatched = sorted(k for k, v in locked.expected_receipt.items() if receipt.get(k) != v)
            if mismatched:
                _retire(
                    locked, f"install receipt does not match: {', '.join(mismatched)}", by_destination=True
                )
                return runner_view(locked)
            now = timezone.now()
            locked.receipt = receipt
            locked.state = locked.State.DESTINATION_READY
            locked.acknowledged_at = now
            locked.record = {
                **locked.record,
                "state": "destination_ready",
                "acknowledgedAt": now.isoformat().replace("+00:00", "Z"),
            }
            _progress(locked, phase, status)
            locked.save()
            return runner_view(locked)
        _progress(locked, phase, status, reason)
        locked.save()
        return runner_view(locked)
