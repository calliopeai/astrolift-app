import re

from astrolift_agents.models import AgentTask, AgentTaskEvent

MAX_EVENT_BYTES = 16 * 1024
MAX_EVENT_BATCH = 64
MAX_TASK_EVENT_BYTES = 16 * 1024 * 1024
MAX_TASK_EVENTS = 100_000
_IDENTITY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}\Z")
_FIELDS = {"sequence", "turn_id", "message_id", "kind", "text"}


class TaskEventError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def validate_task_events(value):
    if not isinstance(value, list) or len(value) > MAX_EVENT_BATCH:
        raise TaskEventError(f"events must be a list of at most {MAX_EVENT_BATCH} entries")
    previous = None
    for event in value:
        if not isinstance(event, dict) or set(event) != _FIELDS:
            raise TaskEventError("each event requires sequence, turn_id, message_id, kind and text")
        sequence = event["sequence"]
        if type(sequence) is not int or not 1 <= sequence <= MAX_TASK_EVENTS:
            raise TaskEventError(f"event sequence must be an integer from 1 to {MAX_TASK_EVENTS}")
        if previous is not None and sequence != previous + 1:
            raise TaskEventError("event batch sequences must be contiguous and increasing")
        previous = sequence
        for key in ("turn_id", "message_id"):
            if not isinstance(event[key], str) or not _IDENTITY.fullmatch(event[key]):
                raise TaskEventError(f"invalid event {key}")
        if event["kind"] not in AgentTaskEvent.Kind.values:
            raise TaskEventError("unknown event kind")
        text = event["text"]
        if not isinstance(text, str):
            raise TaskEventError("event text must be a string")
        if "\x00" in text:
            raise TaskEventError("event text cannot contain NUL characters")
        try:
            size = len(text.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise TaskEventError("event text must be valid UTF-8") from exc
        if size > MAX_EVENT_BYTES:
            raise TaskEventError(f"event text exceeds {MAX_EVENT_BYTES} UTF-8 bytes", 413)
    return value


def prepare_task_events(task, events):
    """Validate under the callback's task row lock, before any callback writes."""
    existing = {
        row.sequence: row
        for row in AgentTaskEvent.all_objects.filter(
            agent_task=task, sequence__in=[event["sequence"] for event in events]
        )
    }
    sequence, size = task.event_sequence, task.event_bytes
    pending = []
    for event in events:
        if event["sequence"] <= task.event_sequence:
            row = existing.get(event["sequence"])
            if (
                row is None
                or row.deleted_at is not None
                or any(getattr(row, key) != event[key] for key in _FIELDS)
            ):
                raise TaskEventError("event sequence conflicts with recorded history", 409)
            continue
        if task.status not in {AgentTask.Status.PROVISIONING, AgentTask.Status.RUNNING}:
            raise TaskEventError("task is not accepting new events", 409)
        if event["sequence"] != sequence + 1:
            raise TaskEventError(f"event sequence gap; expected {sequence + 1}", 409)
        size += len(event["text"].encode("utf-8"))
        if size > MAX_TASK_EVENT_BYTES:
            raise TaskEventError("task event history capacity exceeded", 413)
        sequence = event["sequence"]
        pending.append(AgentTaskEvent(organization_id=task.organization_id, agent_task=task, **event))
    return pending, sequence, size


def commit_task_events(task, prepared):
    rows, sequence, size = prepared
    if not rows:
        return
    if task.status == AgentTask.Status.PROVISIONING:
        task.transition_to(AgentTask.Status.RUNNING)
    AgentTaskEvent.objects.bulk_create(rows)
    task.event_sequence, task.event_bytes = sequence, size
    task.save(update_fields=["event_sequence", "event_bytes", "updated_at", "version"])
