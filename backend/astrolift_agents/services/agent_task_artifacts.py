"""Evidence a run attaches to its terminal callback (#2488).

The callback's ``artifacts`` list is checked whole before anything is
written, so a refused artifact refuses the result it came with. See
docs/runbooks/agent-task-artifacts.md for the wire contract.
"""

import hashlib
import json
import re

from astrolift_agents.models import AgentTaskArtifact

MAX_ARTIFACTS = 16
MAX_ARTIFACT_BYTES = 512 * 1024
MAX_TASK_ARTIFACT_BYTES = 1024 * 1024
_FIELDS = frozenset({"name", "kind", "media_type", "content", "sha256"})
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
# RFC 6838 type and subtype names, then optional parameters in printable ASCII.
_TOKEN = r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}"
_MEDIA_TYPE = re.compile(rf"{_TOKEN}/{_TOKEN}(?:;[\x20-\x7e]*)?\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class TaskArtifactError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _reject_nonfinite(value):
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


def validate_task_artifacts(value) -> list[dict]:
    """Return the artifacts ready to store, or raise for the first refusal."""
    if not isinstance(value, list) or len(value) > MAX_ARTIFACTS:
        raise TaskArtifactError(f"artifacts must be a list of at most {MAX_ARTIFACTS} entries")
    artifacts, names, total = [], set(), 0
    for item in value:
        if not isinstance(item, dict) or set(item) != _FIELDS:
            raise TaskArtifactError(
                "each artifact requires exactly name, kind, media_type, content and sha256"
            )
        name = item["name"]
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise TaskArtifactError(
                "artifact names are 1-128 letters, digits, '.', '_' or '-', starting with a letter or digit"
            )
        if name in names:
            raise TaskArtifactError(f"artifact {name} is reported more than once")
        names.add(name)
        kind = item["kind"]
        if kind not in AgentTaskArtifact.Kind.values:
            raise TaskArtifactError(
                f"artifact {name} kind must be junit, coverage, conformance-json or other"
            )
        media_type = item["media_type"]
        if not isinstance(media_type, str) or len(media_type) > 255 or not _MEDIA_TYPE.fullmatch(media_type):
            raise TaskArtifactError(f"artifact {name} media_type must be a type/subtype media type")
        content = item["content"]
        if not isinstance(content, str):
            raise TaskArtifactError(f"artifact {name} content must be a string")
        if "\x00" in content:
            raise TaskArtifactError(f"artifact {name} content cannot contain NUL characters")
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError:
            raise TaskArtifactError(f"artifact {name} content must be valid UTF-8") from None
        if len(data) > MAX_ARTIFACT_BYTES:
            raise TaskArtifactError(f"artifact {name} exceeds {MAX_ARTIFACT_BYTES} UTF-8 bytes", 413)
        total += len(data)
        if total > MAX_TASK_ARTIFACT_BYTES:
            raise TaskArtifactError(f"artifacts exceed {MAX_TASK_ARTIFACT_BYTES} UTF-8 bytes in total", 413)
        sha256 = item["sha256"]
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise TaskArtifactError(f"artifact {name} sha256 must be 64 lowercase hexadecimal characters")
        if sha256 != hashlib.sha256(data).hexdigest():
            raise TaskArtifactError(f"artifact {name} sha256 does not match its content")
        if kind == AgentTaskArtifact.Kind.CONFORMANCE_JSON:
            try:
                json.loads(content, parse_constant=_reject_nonfinite)
            except (ValueError, RecursionError):
                raise TaskArtifactError(f"artifact {name} of kind conformance-json must be JSON") from None
        artifacts.append(
            {
                "name": name,
                "kind": kind,
                "media_type": media_type,
                "content": content,
                "size_bytes": len(data),
                "sha256": sha256,
            }
        )
    return artifacts


def record_task_artifacts(task, artifacts) -> None:
    """Store validated artifacts inside the callback's locked terminal transaction."""
    AgentTaskArtifact.objects.bulk_create(
        [
            AgentTaskArtifact(organization_id=task.organization_id, agent_task=task, **artifact)
            for artifact in artifacts
        ]
    )
