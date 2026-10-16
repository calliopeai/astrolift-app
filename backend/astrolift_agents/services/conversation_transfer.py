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
2. **Prepare** (again, after the PUT) checks the stored size against the
   declared one, refusing a mismatch, and hands the upload to a worker: the
   transfer is ``validating`` while ``ValidateConversationTransferWorkflow``
   streams it, checks its digest, validates the envelope and dispatches a
   paused task, which turns the transfer back to ``preparing`` while it
   installs. The runner downloads the envelope, installs Git state, progress,
   conversation and undo history, reports progress, and finally an install
   receipt. Only an exact receipt makes the transfer ``destination_ready``.
   The IDE keeps calling prepare (or reads ``conversationTransfer``) until
   then; a retry gets the same acknowledgement and never reinstalls.
3. **Verify** (no transport) accepts only the exact acknowledged arrival.
4. **Commit** starts the session from the installed state, exactly once.
5. **Abort** retires the arrival: the stored envelope is deleted and the
   paused task cancelled, so nothing it created is left behind.

The whole paused admission must commit before ``admission_deadline``
(``CONVERSATION_TRANSFER_ADMISSION_SECONDS`` after prepare). The paused
task's own timeout starts at commit; until then the deadline bounds it, and
the expiry sweep retires a transfer that passed it or whose paused session
ended. A retired arrival's envelope deletion and task cancellation run only
once the retirement commits.

Only a caller with a user identity can move a conversation: the transfer
belongs to that user, and every later call must come from them.

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
import threading
import uuid
from dataclasses import dataclass
from datetime import timedelta

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

# The paused admission window, ``CONVERSATION_TRANSFER_ADMISSION_SECONDS``.
DEFAULT_ADMISSION_SECONDS = 30 * 60
MIN_ADMISSION_SECONDS = 60
MAX_ADMISSION_SECONDS = 24 * 60 * 60
EXPIRED_REASON = "the moved conversation was not committed before its admission deadline"
NO_REQUESTER_REASON = (
    "a moved conversation belongs to the user who prepares it, and this caller has no user "
    "identity; an organization API token without a user cannot move a conversation"
)

# The worker streams an envelope in chunks into a buffer of its declared size
# plus one byte, and validates one envelope at a time per process: its memory
# for envelopes stays one envelope's bytes, then its text and parsed tree,
# whatever the store holds or how many transfers arrive at once.
_READ_CHUNK_BYTES = 1024 * 1024
_READ_TIMEOUT_SECONDS = 60
_ENVELOPE_SLOT = threading.BoundedSemaphore(1)
_ENVELOPE_SLOT_WAIT_SECONDS = 120
_CANONICAL_ENCODER = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False)

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


def canonical_digest(value) -> tuple[str, int]:
    """SHA-256 and byte length of ``canonical_json(value)``, streamed: a
    large history is hashed without building a second copy of it."""
    digest = hashlib.sha256()
    size = 0
    for chunk in _CANONICAL_ENCODER.iterencode(value):
        encoded = chunk.encode("utf-8")
        digest.update(encoded)
        size += len(encoded)
    return digest.hexdigest(), size


def admission_seconds() -> int:
    """The configured paused admission window, clamped to its bounds."""
    from django.conf import settings

    configured = int(getattr(settings, "CONVERSATION_TRANSFER_ADMISSION_SECONDS", DEFAULT_ADMISSION_SECONDS))
    return max(MIN_ADMISSION_SECONDS, min(MAX_ADMISSION_SECONDS, configured))


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
    agent, so a transfer reads exactly as its session does. A caller with no
    user identity owns no transfer: ``created_by`` NULL would otherwise match
    every other user-less caller.
    """
    from django.db.models import Q

    from astrolift_agents.models import AgentConversationTransfer
    from astrolift_agents.visibility import agent_tasks, agent_workloads

    if requester_id is None or not isinstance(transfer_id, str) or not transfer_id:
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


def _check_undo_checkpoints(value) -> str:
    """Structural port of calliope-agents ``captureCheckpointTransfer``;
    returns the history's canonical digest.

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
    digest, size = canonical_digest(value)
    if size > MAX_UNDO_BYTES:
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
    return digest


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
        if _check_undo_checkpoints(checkpoints) != digest:
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
    return canonical_digest(snapshot)[0], expected


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


def _delete_envelope(organization, key: str) -> None:
    if not key:
        return
    try:
        _driver(organization).delete(key)
    except Exception:  # noqa: BLE001 - retirement must still settle the arrival
        logger.warning("could not delete transfer envelope %s", key, exc_info=True)


def _delete_envelope_on_commit(transfer) -> None:
    """Delete the stored envelope once the current transaction commits."""
    organization, key = transfer.organization, transfer.envelope_key
    transaction.on_commit(lambda: _delete_envelope(organization, key), robust=True)


def _uploaded_size(transfer) -> int | None:
    """The stored upload's size, or None until the IDE's PUT lands.

    A cloud store lists an object only once its upload has completed, so a
    listed object is final: a short one never grows into the declared size.
    """
    for blob in _driver(transfer.organization).list_keys(transfer.envelope_key):
        if blob.key.endswith(transfer.envelope_key):
            return blob.size_bytes
    return None


def _stored_chunks(driver, url: str, cap: int):
    """Yield at most ``cap`` bytes of the object behind a presigned GET URL."""
    from providers._sdk.blob_store import LocalFsBlobStoreDriver

    if url.startswith("file://"):
        # Only the local development store presigns a filesystem path.
        if not isinstance(driver, LocalFsBlobStoreDriver):
            raise RuntimeError("a file:// envelope URL from a store other than the local development one")
        with open(url.removeprefix("file://"), "rb") as fh:
            while cap > 0 and (chunk := fh.read(min(_READ_CHUNK_BYTES, cap))):
                cap -= len(chunk)
                yield chunk
        return
    import requests

    with requests.get(url, stream=True, timeout=_READ_TIMEOUT_SECONDS) as response:
        response.raise_for_status()
        # The stored bytes exactly: the digest covers what the IDE uploaded,
        # and a Content-Encoding the store replays is never inflated here.
        for raw in response.raw.stream(_READ_CHUNK_BYTES, decode_content=False):
            chunk = raw[:cap]
            cap -= len(chunk)
            yield chunk
            if cap <= 0:
                return


def _read_envelope(transfer) -> bytearray:
    """The uploaded envelope, streamed and checked against its declared size
    and digest.

    Reads at most one byte past the declared size, into a buffer of that
    size, so an object replaced after prepare's size check (the upload URL
    outlives it) is refused without ever being held whole.
    """
    from providers._sdk.blob_store import BlobStoreNotFoundError

    driver = _driver(transfer.organization)
    declared = transfer.envelope_bytes
    try:
        url = driver.presigned_url(transfer.envelope_key, expires_in=URL_TTL_SECONDS)
    except BlobStoreNotFoundError as exc:
        raise ConversationTransferError(
            "validation", "the uploaded envelope is no longer stored", "envelopeSha256"
        ) from exc
    buffer = bytearray(declared + 1)
    size = 0
    digest = hashlib.sha256()
    with memoryview(buffer) as view:
        for chunk in _stored_chunks(driver, url, declared + 1):
            view[size : size + len(chunk)] = chunk
            size += len(chunk)
            digest.update(chunk)
    if size != declared:
        stored = "larger than" if size > declared else f"{size} bytes, not"
        raise ConversationTransferError(
            "validation", f"the uploaded envelope is {stored} the declared {declared} bytes", "envelopeBytes"
        )
    if digest.hexdigest() != transfer.envelope_sha256:
        raise ConversationTransferError(
            "validation", "the uploaded envelope does not match envelopeSha256", "envelopeSha256"
        )
    del buffer[declared:]
    return buffer


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


def _locked(transfer_pk: int):
    """The transfer row, locked for the rest of the transaction."""
    from astrolift_agents.models import AgentConversationTransfer

    return (
        AgentConversationTransfer.objects.select_for_update(of=("self",))
        .select_related("organization", "agent_definition", "environment_spec")
        .get(pk=transfer_pk)
    )


def _stored_transfer(organization, transfer_id: str):
    from astrolift_agents.models import AgentConversationTransfer

    return (
        AgentConversationTransfer.all_objects.filter(organization=organization, transfer_id=transfer_id)
        .select_related("organization")
        .first()
    )


def _cancel_paused_task(task_pk: int, transfer_guid) -> None:
    """Cancel a retired arrival's paused task, unless it has already ended."""
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.agent_stage import _cancel_agent_task_sync

    task = AgentTask.objects.filter(pk=task_pk).only("guid", "status").first()
    if task is None or task.status in AgentTask.TERMINAL_STATUSES:
        return
    outcome = _cancel_agent_task_sync(str(task.guid))
    if not outcome.get("ok"):
        logger.warning("transfer %s: paused task %s not cancelled: %s", transfer_guid, task.guid, outcome)


def _retire(transfer, reason: str, *, by_destination: bool) -> None:
    """Abort the arrival and remove everything it created.

    The envelope deletion and the paused task's cancellation are external:
    they run once the retirement commits, never inside a transaction that
    could still roll it back.
    """
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
    _delete_envelope_on_commit(transfer)
    if transfer.agent_task_id is not None:
        task_pk, guid = transfer.agent_task_id, transfer.guid
        transaction.on_commit(lambda: _cancel_paused_task(task_pk, guid), robust=True)


def _dead_reason(transfer, *, now=None, lock_task: bool = False) -> str:
    """Why an open arrival can no longer commit, or ``""``.

    Its admission deadline passed, or its paused session ended, is being
    cancelled, or is gone. ``lock_task`` holds the task row, so the session
    cannot end between this check and a commit.
    """
    from astrolift_agents.models import AgentTask

    if (now or timezone.now()) >= transfer.admission_deadline:
        return EXPIRED_REASON
    if transfer.agent_task_id is None:
        return "the paused session is gone" if transfer.state == transfer.State.DESTINATION_READY else ""
    tasks = AgentTask.all_objects.filter(pk=transfer.agent_task_id)
    if lock_task:
        tasks = tasks.select_for_update()
    task = tasks.only("status", "deleted_at", "cancel_requested_at").first()
    if task is None or task.deleted_at is not None:
        return "the paused session is gone"
    if task.status in AgentTask.TERMINAL_STATUSES:
        return f"the paused session already ended ({task.status}), so it cannot start this conversation"
    if task.cancel_requested_at is not None:
        return "the paused session is being cancelled, so it cannot start this conversation"
    return ""


def _settle_if_dead(transfer, *, now=None):
    """Retire an open arrival that can no longer commit; the row as it stands."""
    if transfer.state not in transfer.OPEN_STATES or not _dead_reason(transfer, now=now):
        return transfer
    with transaction.atomic():
        locked = _locked(transfer.pk)
        reason = _dead_reason(locked, now=now) if locked.state in locked.OPEN_STATES else ""
        if reason:
            _retire(locked, reason, by_destination=True)
    return locked


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
    from core.run_trigger import request_trigger

    requester_id = getattr(actor, "user_id", None)
    if requester_id is None:
        raise ConversationTransferError("permission_denied", NO_REQUESTER_REASON)
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

    transfer = _stored_transfer(organization, record["transferId"])
    if transfer is None:
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
            admission_deadline=timezone.now() + timedelta(seconds=admission_seconds()),
        )
        transfer.envelope_key = envelope_key(transfer)
        _progress(transfer, "upload", "waiting")
        try:
            with transaction.atomic():
                transfer.save()
        except IntegrityError:
            # A concurrent prepare stored this transferId first; it is
            # checked below like any stored arrival.
            transfer = _stored_transfer(organization, record["transferId"])
            if transfer is None:
                raise
        else:
            return transfer

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
    transfer = _settle_if_dead(transfer)
    worker = {
        "actor": actor,
        "team_id": team_id,
        "undo": capability["undo"],
        "trigger_kind": request_trigger(),
    }
    if transfer.state == transfer.State.VALIDATING:
        # Joins the running validation, or restarts one that failed.
        _start_validation(transfer, **worker)
        return transfer
    if transfer.state != transfer.State.PREPARING or transfer.agent_task_id:
        # A retry after a lost reply: the same acknowledgement, never a reinstall.
        return transfer
    return _receive_upload(transfer, **worker)


def _reject_constant(value: str):
    raise ValueError(f"non-finite JSON number {value!r}")


def _receive_upload(transfer, **worker):
    """Hand a complete upload to the validation worker, or refuse one of the
    wrong size. Only the listing is read here, never the envelope itself."""
    size = _uploaded_size(transfer)
    if size is None:
        return transfer
    refusal = ""
    with transaction.atomic():
        locked = _locked(transfer.pk)
        if locked.state != locked.State.PREPARING or locked.agent_task_id:
            return locked
        if size != locked.envelope_bytes:
            refusal = f"the uploaded envelope is {size} bytes, not the declared {locked.envelope_bytes}; upload it again"
            _progress(locked, "upload", "failed", refusal)
            _delete_envelope_on_commit(locked)
        else:
            locked.state = locked.State.VALIDATING
            _progress(locked, "upload", "done")
            _progress(locked, "validate", "waiting")
        locked.save()
    if refusal:
        raise _invalid(refusal, "envelopeBytes")
    _start_validation(locked, **worker)
    return locked


def validation_workflow_id(transfer) -> str:
    return f"ValidateConversationTransferWorkflow-{transfer.guid}"


def _start_validation(transfer, *, actor, team_id, undo: bool, trigger_kind: str) -> None:
    """Start, or join, the worker that validates the upload and admits the session."""
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import ValidateConversationTransferInput

    try:
        start_workflow(
            "ValidateConversationTransferWorkflow",
            args=[
                ValidateConversationTransferInput(
                    transfer_id=transfer.pk,
                    actor=actor,
                    team_id=team_id,
                    undo=undo,
                    trigger_kind=trigger_kind,
                )
            ],
            workflow_id=validation_workflow_id(transfer),
            replace_running=False,
        )
    except Exception as exc:  # noqa: BLE001 - the next prepare starts it again
        logger.warning("transfer %s: envelope validation did not start", transfer.guid, exc_info=True)
        raise ConversationTransferError(
            "internal", "envelope validation could not start; call prepare again to retry"
        ) from exc


# ---------------------------------------------------------------------------
# Worker half (ValidateConversationTransferWorkflow)
# ---------------------------------------------------------------------------


def validate_uploaded_envelope(
    transfer_pk: int, *, actor, team_id: int | None = None, undo: bool = True, trigger_kind: str = "manual"
) -> str:
    """Stream the upload, validate the envelope, and admit its paused session.

    The worker half of prepare; returns the transfer's state afterwards. A
    refusal retires the arrival with its reason, which the IDE reads as
    ``abortReason``; anything else raises, so the activity retries.
    """
    from astrolift_agents.models import AgentConversationTransfer

    transfer = AgentConversationTransfer.objects.select_related("organization").filter(pk=transfer_pk).first()
    if transfer is None:
        return ""
    transfer = _settle_if_dead(transfer)
    if transfer.state != transfer.State.VALIDATING:
        return transfer.state
    if not _ENVELOPE_SLOT.acquire(timeout=_ENVELOPE_SLOT_WAIT_SECONDS):
        raise RuntimeError("another conversation envelope is still being validated by this worker")
    verdict = None
    refusal = ""
    try:
        verdict = _validated_upload(transfer, undo=undo)
    except ConversationTransferError as exc:
        refusal = f"envelope refused: {exc.message}"
    finally:
        _ENVELOPE_SLOT.release()
    if verdict is None:
        with transaction.atomic():
            locked = _locked(transfer.pk)
            if locked.state == locked.State.VALIDATING:
                _retire(locked, refusal, by_destination=True)
        return locked.state
    snapshot_sha256, expected = verdict
    return _admit(
        transfer.pk,
        snapshot_sha256=snapshot_sha256,
        expected=expected,
        actor=actor,
        team_id=team_id,
        trigger_kind=trigger_kind,
    )


def _validated_upload(transfer, *, undo: bool) -> tuple[str, dict]:
    """``(snapshot digest, expected receipt)`` of the stored envelope."""
    data = _read_envelope(transfer)
    try:
        text = data.decode("utf-8")
        del data  # the raw bytes go before the parse builds its tree
        envelope = json.loads(text, parse_constant=_reject_constant)
        del text
        return validate_envelope(envelope, record=transfer.record, undo=undo)
    except (ValueError, RecursionError) as exc:
        raise _invalid("the uploaded envelope is not valid JSON") from exc


def _admit(
    transfer_pk: int, *, snapshot_sha256: str, expected: dict, actor, team_id, trigger_kind: str
) -> str:
    """Record the validated envelope and dispatch the paused task that installs it."""
    from astrolift_agents.services.agent_dispatch import AgentDispatchError, dispatch_registered_agent

    with transaction.atomic():
        transfer = _locked(transfer_pk)
        if transfer.state != transfer.State.VALIDATING:
            return transfer.state
        if transfer.agent_definition is None:
            _retire(transfer, "the paused session could not start: agent not found", by_destination=True)
            return transfer.state
        transfer.snapshot_sha256 = snapshot_sha256
        transfer.expected_receipt = {"envelopeSha256": transfer.envelope_sha256, **expected}
        _progress(transfer, "validate", "done")
        transfer.save()

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
            trigger_kind=trigger_kind,
            client_request_id=str(request_id),
            conversation_transfer=transfer,
        )
    except AgentDispatchError as exc:
        with transaction.atomic():
            locked = _locked(transfer_pk)
            if locked.state == locked.State.VALIDATING:
                _retire(locked, f"the paused session could not start: {exc.message}", by_destination=True)
        return locked.state

    with transaction.atomic():
        locked = _locked(transfer_pk)
        if locked.state != locked.State.VALIDATING:
            # Retired while its session was being created: the session goes too.
            task_pk, guid = task.pk, locked.guid
            transaction.on_commit(lambda: _cancel_paused_task(task_pk, guid), robust=True)
            return locked.state
        locked.agent_task = task
        locked.state = locked.State.PREPARING
        _progress(locked, "install", "waiting")
        locked.save()
    return locked.state


# ---------------------------------------------------------------------------
# Verify, commit, abort
# ---------------------------------------------------------------------------


def verify_transfer(transfer, *, record, snapshot_sha256: str):
    """Accept only the exact acknowledged arrival (no transport), while its
    paused session can still start it."""
    if transfer.state != transfer.State.DESTINATION_READY:
        raise ConversationTransferError(
            "precondition", f"the transfer is {transfer.state}, not destination_ready", "record"
        )
    if record != transfer.record or snapshot_sha256 != transfer.snapshot_sha256:
        raise ConversationTransferError("precondition", "this is not the acknowledged transfer", "record")
    transfer = _settle_if_dead(transfer)
    if transfer.state != transfer.State.DESTINATION_READY:
        raise ConversationTransferError(
            "precondition", transfer.abort_reason or f"the transfer is {transfer.state}", "record"
        )
    return transfer


def commit_transfer(transfer, *, record, snapshot_sha256: str):
    """Start the session from the installed state, exactly once.

    Refused, and the arrival retired, when its paused session can no longer
    start it: the admission deadline passed, or the task ended (timed out,
    failed, cancelled) while it waited. Nothing is ever committed into a
    dead session.
    """
    if not isinstance(record, dict) or record.get("state") != "committed":
        raise _invalid("record.state must be 'committed'", "record")
    if snapshot_sha256 != transfer.snapshot_sha256 or not _same_immutables(transfer.record, record):
        raise ConversationTransferError("precondition", "this is not the acknowledged transfer", "record")
    with transaction.atomic():
        locked = _locked(transfer.pk)
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
        refusal = _dead_reason(locked, lock_task=True)
        if refusal:
            _retire(locked, refusal, by_destination=True)
        else:
            locked.record = record
            locked.state = locked.State.COMMITTED
            locked.committed_at = timezone.now()
            _progress(locked, "commit", "done")
            locked.save()
            # Once the session starts, a restarted runner must never
            # reinstall over its later edits.
            _delete_envelope_on_commit(locked)
    if refusal:
        raise ConversationTransferError("precondition", refusal, "record")
    return locked


def abort_transfer(transfer, *, record, reason: str = ""):
    """Retire the arrival before commit; idempotent once aborted."""
    if not isinstance(record, dict) or record.get("state") != "aborted":
        raise _invalid("record.state must be 'aborted'", "record")
    if not _same_immutables(transfer.record, record):
        raise ConversationTransferError("precondition", "this is not the stored transfer", "record")
    with transaction.atomic():
        locked = _locked(transfer.pk)
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
# Expiry sweep (ConversationTransferExpiryTickWorkflow)
# ---------------------------------------------------------------------------


def expire_transfers(*, now=None, limit: int = 50) -> dict[str, int]:
    """Retire open arrivals past their admission deadline, or whose paused
    session ended, oldest deadline first.

    Each retirement deletes the stored envelope and cancels the paused task
    once it commits. One row's failure never stops the sweep.
    """
    from django.db.models import Q

    from astrolift_agents.models import AgentConversationTransfer, AgentTask

    now = now or timezone.now()
    due = (
        Q(admission_deadline__lte=now)
        | Q(agent_task__status__in=AgentTask.TERMINAL_STATUSES)
        | Q(agent_task__cancel_requested_at__isnull=False)
        | Q(agent_task__deleted_at__isnull=False)
        | Q(state=AgentConversationTransfer.State.DESTINATION_READY, agent_task__isnull=True)
    )
    candidates = list(
        AgentConversationTransfer.objects.filter(state__in=AgentConversationTransfer.OPEN_STATES)
        .filter(due)
        .order_by("admission_deadline")
        .values_list("pk", flat=True)[:limit]
    )
    summary = {"evaluated": len(candidates), "expired": 0, "ended": 0, "errors": 0}
    for pk in candidates:
        try:
            with transaction.atomic():
                locked = _locked(pk)
                reason = _dead_reason(locked, now=now) if locked.state in locked.OPEN_STATES else ""
                if reason:
                    _retire(locked, reason, by_destination=True)
        except Exception:  # noqa: BLE001 - one transfer must not stop the sweep
            summary["errors"] += 1
            logger.warning("conversation transfer expiry: transfer %s not retired", pk, exc_info=True)
            continue
        if reason:
            summary["expired" if reason == EXPIRED_REASON else "ended"] += 1
    return summary


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
        locked = _locked(transfer.pk)
        if locked.state != locked.State.PREPARING:
            raise ConversationTransferError("conflict", f"the transfer is {locked.state}", "state")
        if dead := _dead_reason(locked):
            # Past its deadline, or its session is ending: the runner exits.
            _retire(locked, dead, by_destination=True)
            return runner_view(locked)
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
