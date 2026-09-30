import json
import math
import re

from django.db import transaction

from astrolift_agents.models import AgentTask, AgentTaskEvent, AgentTaskInputReply

MAX_REQUEST_BYTES = 16 * 1024
_QUESTION_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}\Z")


class TaskInputRequestError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _text(value, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise TaskInputRequestError(f"request strings must be nonempty and at most {maximum} characters")


def _json(value, depth=0):
    if depth > 12:
        raise TaskInputRequestError("request JSON is nested too deeply")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or "\x00" in key:
                raise TaskInputRequestError("request JSON keys must be strings without NUL")
            _json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _json(item, depth + 1)
    elif isinstance(value, str):
        if "\x00" in value:
            raise TaskInputRequestError("request JSON cannot contain NUL")
    elif value is not None and type(value) not in {bool, int, float}:
        raise TaskInputRequestError("invalid request JSON value")
    elif type(value) is float and not math.isfinite(value):
        raise TaskInputRequestError("request JSON numbers must be finite")


def request_bytes(value):
    _json(value)
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise TaskInputRequestError("request JSON must be valid UTF-8") from exc
    if size > MAX_REQUEST_BYTES:
        raise TaskInputRequestError(f"request JSON exceeds {MAX_REQUEST_BYTES} bytes", 413)
    return size


def validate_request(value, event_kind):
    if value is None:
        return None
    request_bytes(value)
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise TaskInputRequestError("request requires protocol version 1")
    if event_kind == AgentTaskEvent.Kind.APPROVAL_REQUIRED:
        if set(value) != {"version", "kind", "tool"} or value["kind"] != "approval":
            raise TaskInputRequestError("approval request requires kind and tool")
        tool = value["tool"]
        if (
            not isinstance(tool, dict)
            or set(tool) != {"name", "input"}
            or not isinstance(tool["input"], dict)
        ):
            raise TaskInputRequestError("approval tool requires name and input object")
        _text(tool["name"], 128)
    elif event_kind == AgentTaskEvent.Kind.INPUT_REQUIRED:
        if set(value) != {"version", "kind", "questions"} or value["kind"] != "question":
            raise TaskInputRequestError("question request requires kind and questions")
        questions = value["questions"]
        if not isinstance(questions, list) or not 1 <= len(questions) <= 4:
            raise TaskInputRequestError("request requires one to four questions")
        ids = set()
        for question in questions:
            if not isinstance(question, dict) or set(question) != {"id", "text", "options", "multiple"}:
                raise TaskInputRequestError("question requires id, text, options and multiple")
            key = question["id"]
            if not isinstance(key, str) or not _QUESTION_ID.fullmatch(key) or key in ids:
                raise TaskInputRequestError("question IDs must be valid and unique")
            ids.add(key)
            _text(question["text"])
            if type(question["multiple"]) is not bool:
                raise TaskInputRequestError("question multiple must be boolean")
            options = question["options"]
            if not isinstance(options, list) or len(options) > 8:
                raise TaskInputRequestError("question permits at most eight options")
            labels = set()
            for option in options:
                if not isinstance(option, dict) or set(option) != {"label", "description"}:
                    raise TaskInputRequestError("option requires label and description")
                _text(option["label"], 200)
                _text(option["description"])
                if option["label"] in labels:
                    raise TaskInputRequestError("option labels must be unique")
                labels.add(option["label"])
    else:
        raise TaskInputRequestError("only attention events can carry a request")
    return value


def validate_response(request, response):
    request_bytes(response)
    if not isinstance(response, dict):
        raise TaskInputRequestError("response must be an object")
    if request["kind"] == "approval":
        if (
            not {"decision"} <= set(response) <= {"decision", "reason"}
            or not isinstance(response["decision"], str)
            or response["decision"] not in {"allow", "deny"}
        ):
            raise TaskInputRequestError("approval response requires an allow or deny decision")
        if "reason" in response:
            _text(response["reason"])
    else:
        answers = response.get("answers")
        if set(response) != {"answers"} or not isinstance(answers, dict):
            raise TaskInputRequestError("question response requires answers")
        if set(answers) != {question["id"] for question in request["questions"]}:
            raise TaskInputRequestError("answers must match every requested question ID")
        for question in request["questions"]:
            answer = answers[question["id"]]
            if question["multiple"]:
                if not isinstance(answer, list) or not 1 <= len(answer) <= 8:
                    raise TaskInputRequestError("multiple-choice answer requires one to eight values")
                for item in answer:
                    _text(item)
                if len(set(answer)) != len(answer):
                    raise TaskInputRequestError("answer choices must be unique")
            else:
                _text(answer)
    return response


def find_request(task, sequence):
    if type(sequence) is not int or not 1 <= sequence <= 100_000:
        raise TaskInputRequestError("input request sequence must be a positive integer")
    event = AgentTaskEvent.objects.filter(
        agent_task=task,
        organization_id=task.organization_id,
        sequence=sequence,
        kind__in=[AgentTaskEvent.Kind.INPUT_REQUIRED, AgentTaskEvent.Kind.APPROVAL_REQUIRED],
        request__isnull=False,
    ).first()
    if event is None:
        raise TaskInputRequestError("input request not found", 404)
    return event


def callback_input_response(task, sequence):
    request = find_request(task, sequence)
    if task.status != AgentTask.Status.RUNNING:
        return None
    reply = AgentTaskInputReply.objects.filter(
        request_event=request, organization_id=task.organization_id
    ).first()
    return (
        {
            "id": str(reply.guid),
            "request_sequence": sequence,
            "response": reply.response,
        }
        if reply
        else None
    )


@transaction.atomic
def reply_to_request(*, task, sequence, response, author=None, author_label="", _policy_checked=False):
    from astrolift_agents.services.agent_task_events import MAX_TASK_EVENT_BYTES

    task = AgentTask.objects.select_for_update().get(pk=task.pk, organization_id=task.organization_id)
    event = find_request(task, sequence)
    response = validate_response(event.request, response)
    previous = AgentTaskInputReply.all_objects.filter(request_event=event).first()
    if previous:
        if previous.deleted_at is not None or previous.response != response:
            raise TaskInputRequestError("input request already has a different response", 409)
        return previous
    if task.status != AgentTask.Status.RUNNING:
        raise TaskInputRequestError("task is not accepting input", 409)
    if task.dispatch_target:
        from astrolift_agents.services.task_timeout import task_timeout_reason

        if reason := task_timeout_reason(task):
            raise TaskInputRequestError(reason, 409)
    if AgentTaskEvent.objects.filter(
        agent_task=task,
        turn_id=event.turn_id,
        message_id=event.message_id,
        kind=AgentTaskEvent.Kind.INPUT_RESOLVED,
        sequence__gt=event.sequence,
    ).exists():
        raise TaskInputRequestError("input request is already resolved", 409)
    if (
        event.kind == AgentTaskEvent.Kind.APPROVAL_REQUIRED
        and response["decision"] == "allow"
        and not _policy_checked
    ):
        from astrolift_agents.services.agent_host_policy import AgentHostPolicyError, guard_approval

        try:
            guard_approval(task, event, author)
        except AgentHostPolicyError as exc:
            raise TaskInputRequestError(str(exc), 403) from exc
    size = request_bytes(response)
    if task.event_bytes + size > MAX_TASK_EVENT_BYTES:
        raise TaskInputRequestError("task event history capacity exceeded", 413)
    reply = AgentTaskInputReply.objects.create(
        organization_id=task.organization_id,
        request_event=event,
        response=response,
        author=author,
        author_label=author_label[:255],
    )
    task.event_bytes += size
    task.save(update_fields=["event_bytes", "updated_at", "version"])
    return reply
