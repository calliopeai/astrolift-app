"""Bounded, ordered backlog snapshots carried outside the task event stream."""

import json
import re

from django.utils import timezone

from astrolift_agents.models import AgentTask
from astrolift_agents.services.agent_task_events import TaskEventError

MAX_BACKLOG_BYTES = 128 * 1024
MAX_BACKLOG_ITEMS = 256
HARNESSES = {"claude-code-cli", "codex-cli"}
STATUSES = {"pending", "in_progress", "completed"}


def _text(value, limit, *, empty=False):
    return (
        isinstance(value, str)
        and len(value) <= limit
        and (empty or bool(value.strip()))
        and "\x00" not in value
    )


def validate_backlog(value):
    if not isinstance(value, dict) or set(value) != {"revision", "harness", "session_id", "items"}:
        raise TaskEventError("invalid backlog envelope")
    if type(value["revision"]) is not int or not 1 <= value["revision"] <= 2147483647:
        raise TaskEventError("invalid backlog revision")
    if not isinstance(value["harness"], str) or value["harness"] not in HARNESSES:
        raise TaskEventError("unsupported backlog harness")
    if not isinstance(value["session_id"], str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", value["session_id"]
    ):
        raise TaskEventError("invalid backlog session identity")
    items = value["items"]
    if not isinstance(items, list) or len(items) > MAX_BACKLOG_ITEMS:
        raise TaskEventError("invalid backlog item list")
    identities = set()
    for item in items:
        if not isinstance(item, dict) or not {"id", "text", "status"} <= set(item) <= {
            "id",
            "text",
            "status",
            "active_form",
            "details",
        }:
            raise TaskEventError("invalid backlog item")
        if not _text(item["id"], 128) or item["id"] in identities or not _text(item["text"], 8192):
            raise TaskEventError("invalid or duplicate backlog item identity/text")
        identities.add(item["id"])
        if not isinstance(item["status"], str) or item["status"] not in STATUSES:
            raise TaskEventError("invalid backlog status")
        for field, limit in (("active_form", 8192), ("details", 16384)):
            if item.get(field) is not None and not _text(item[field], limit, empty=True):
                raise TaskEventError(f"invalid backlog {field}")
    try:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise TaskEventError("backlog must be valid UTF-8") from exc
    if size > MAX_BACKLOG_BYTES:
        raise TaskEventError("backlog exceeds 128 KiB", 413)
    return value


def prepare_backlog(task, value):
    previous = task.backlog_snapshot
    if previous is not None:
        original = {key: previous[key] for key in value}
        if value["revision"] == previous["revision"] and value == original:
            return None
        if value["revision"] <= previous["revision"]:
            raise TaskEventError("backlog revision conflicts with recorded state", 409)
        if (value["harness"], value["session_id"]) != (previous["harness"], previous["session_id"]):
            raise TaskEventError("backlog session identity changed", 409)
    if task.status not in {AgentTask.Status.PROVISIONING, AgentTask.Status.RUNNING}:
        raise TaskEventError("task is not accepting backlog updates", 409)
    return {**value, "updated_at": timezone.now().isoformat()}


def commit_backlog(task, snapshot):
    if snapshot is not None:
        task.backlog_snapshot = snapshot
        task.save(update_fields=["backlog_snapshot", "updated_at", "version"])
