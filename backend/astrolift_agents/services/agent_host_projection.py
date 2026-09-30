"""Project the task event log into the pinned AHP 1.0.0 chat actions.

The authority row serializes projection across workers. A snapshot and its
sequence are read under that same lock, so replay cannot miss a concurrent
callback. Callers authorize every task before entering these functions.
"""

import copy
import json

from django.db import transaction

from astrolift_agents.models import (
    AgentHostAction,
    AgentHostAuthority,
    AgentHostProjection,
    AgentTask,
    AgentTaskEvent,
    AgentTaskInputReply,
)

PROTOCOL_VERSION = "1.0.0"
ROOT = "ahp-root://"


def session_uri(task):
    return f"ahp-session:/{task.guid}"


def chat_uri(task):
    return f"{session_uri(task)}/chat"


def summary(task, chat=None):
    status = (chat or {}).get(
        "status",
        8
        if task.status in {"running", "provisioning", "queued"}
        else 2
        if task.status in {"failed", "timed_out"}
        else 1,
    )
    return {
        "resource": session_uri(task),
        "provider": "astrolift",
        "title": (task.agent_definition.name if task.agent_definition_id else f"Agent task {task.guid}"),
        "status": status,
        "createdAt": task.created_at.isoformat(),
        "modifiedAt": task.updated_at.isoformat(),
    }


def new_chat(task):
    info = summary(task)
    return {
        "resource": chat_uri(task),
        "title": info["title"],
        "status": info["status"],
        "modifiedAt": info["modifiedAt"],
        "turns": [],
    }


def _status(chat):
    parts = chat.get("activeTurn", {}).get("responseParts", [])
    blocked = any(
        (p["kind"] == "toolCall" and p["toolCall"]["status"] == "pending-confirmation")
        or (p["kind"] == "inputRequest" and "response" not in p)
        for p in parts
    )
    activity = 24 if blocked else 8 if "activeTurn" in chat else 1
    chat["status"] = (chat["status"] & ~31) | activity


def reduce_chat(state, action):
    """Subset of the upstream reducer emitted by this host; conformance-tested."""
    chat = copy.deepcopy(state)
    kind = action["type"]
    if kind == "chat/turnStarted":
        chat["modifiedAt"] = action["startedAt"]
        chat["status"] &= ~32
        chat["activeTurn"] = {
            "id": action["turnId"],
            "startedAt": action["startedAt"],
            "message": action["message"],
            "responseParts": [],
        }
    elif kind in {"chat/turnComplete", "chat/turnCancelled", "chat/error"}:
        if chat.get("activeTurn", {}).get("id") != action["turnId"]:
            return chat
        turn = chat.pop("activeTurn", None)
        if turn:
            turn["state"] = (
                "complete"
                if kind == "chat/turnComplete"
                else "cancelled"
                if kind == "chat/turnCancelled"
                else "error"
            )
            turn["duration"] = action["duration"]
            from datetime import datetime, timedelta

            chat["modifiedAt"] = (
                (
                    datetime.fromisoformat(turn["startedAt"].replace("Z", "+00:00"))
                    + timedelta(milliseconds=action["duration"])
                )
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
            for part in turn["responseParts"]:
                tool = part.get("toolCall")
                if tool and tool["status"] not in {"cancelled", "completed"}:
                    part["toolCall"] = {
                        key: tool[key]
                        for key in (
                            "toolCallId",
                            "toolName",
                            "displayName",
                            "intention",
                            "contributor",
                            "_meta",
                            "invocationMessage",
                            "toolInput",
                        )
                        if key in tool
                    }
                    part["toolCall"].setdefault("invocationMessage", "")
                    part["toolCall"].update(status="cancelled", reason="skipped")
            if kind == "chat/error":
                turn["error"] = action["error"]
            chat["turns"].append(turn)
    elif kind == "chat/pendingMessageSet":
        if action["kind"] == "steering":
            chat["steeringMessage"] = {"id": action["id"], "message": action["message"]}
    elif "activeTurn" in chat:
        if "turnId" in action and chat["activeTurn"]["id"] != action["turnId"]:
            return chat
        parts = chat["activeTurn"]["responseParts"]
        if kind == "chat/responsePart":
            parts.append(action["part"])
        elif kind == "chat/delta":
            for part in parts:
                if part.get("id") == action["partId"]:
                    part["content"] += action["content"]
        elif kind == "chat/toolCallStart":
            parts.append(
                {
                    "kind": "toolCall",
                    "toolCall": {
                        "toolCallId": action["toolCallId"],
                        "toolName": action["toolName"],
                        "displayName": action["displayName"],
                        "status": "streaming",
                    },
                }
            )
        elif kind in {"chat/toolCallReady", "chat/toolCallConfirmed"}:
            for part in parts:
                tool = part.get("toolCall", {})
                if tool.get("toolCallId") != action["toolCallId"]:
                    continue
                if kind == "chat/toolCallReady":
                    tool.update(
                        {
                            key: action[key]
                            for key in ("toolInput", "invocationMessage", "riskAssessment")
                            if key in action
                        }
                    )
                    tool.pop("confirmed", None)
                    tool["status"] = "running" if action.get("confirmed") else "pending-confirmation"
                    if action.get("confirmed"):
                        tool["confirmed"] = action["confirmed"]
                elif tool["status"] != "pending-confirmation":
                    continue
                else:
                    replacement = {
                        key: tool[key]
                        for key in (
                            "toolCallId",
                            "toolName",
                            "displayName",
                            "intention",
                            "contributor",
                            "_meta",
                            "invocationMessage",
                            "toolInput",
                        )
                        if key in tool
                    }
                    if action["approved"]:
                        replacement.update(status="running", confirmed=action["confirmed"])
                    else:
                        replacement.update(status="cancelled", reason=action["reason"])
                        if "reasonMessage" in action:
                            replacement["reasonMessage"] = action["reasonMessage"]
                    part["toolCall"] = replacement
        elif kind == "chat/toolCallComplete":
            for part in parts:
                tool = part.get("toolCall", {})
                if tool.get("toolCallId") == action["toolCallId"] and tool.get("status") in {
                    "running",
                    "pending-confirmation",
                }:
                    replacement = {
                        key: tool[key]
                        for key in (
                            "toolCallId",
                            "toolName",
                            "displayName",
                            "intention",
                            "contributor",
                            "_meta",
                            "invocationMessage",
                            "toolInput",
                        )
                        if key in tool
                    }
                    replacement.update(action["result"])
                    replacement.update(status="completed", confirmed=tool.get("confirmed", "not-needed"))
                    part["toolCall"] = replacement
        elif kind == "chat/inputRequested":
            parts.append({"kind": "inputRequest", "request": action["request"]})
        elif kind == "chat/inputCompleted":
            for part in parts:
                if part.get("request", {}).get("id") == action["requestId"] and "response" not in part:
                    part["response"] = action["response"]
                    if "answers" in action:
                        part["request"]["answers"] = {
                            **part["request"].get("answers", {}),
                            **action["answers"],
                        }
    _status(chat)
    if kind == "chat/error":
        chat["status"] = (chat["status"] & ~31) | 2
    return chat


def append_action(authority, task, channel, action, *, origin=None, actor=None, rejection=""):
    authority.server_sequence += 1
    row = AgentHostAction.objects.create(
        authority=authority,
        agent_task=task,
        server_sequence=authority.server_sequence,
        channel=channel,
        action=action,
        origin=origin,
        actor=actor,
        rejection_reason=rejection[:1000],
        client_id=origin["clientId"] if origin else "",
        client_sequence=origin["clientSeq"] if origin else None,
    )
    return row


def envelope(row):
    data = {"channel": row.channel, "action": row.action, "serverSeq": row.server_sequence}
    if row.origin is not None:
        data["origin"] = row.origin
    if row.rejection_reason:
        data["rejectionReason"] = row.rejection_reason
    return data


def _emit(authority, task, projection, action, *, origin=None, actor=None):
    projection.chat = reduce_chat(projection.chat, action)
    return append_action(authority, task, chat_uri(task), action, origin=origin, actor=actor)


def _turn(authority, task, projection, event):
    current = projection.chat.get("activeTurn")
    if current and current["id"] == event.turn_id:
        return
    if current:
        _emit(
            authority, task, projection, {"type": "chat/turnComplete", "turnId": current["id"], "duration": 0}
        )
    initial = (task.dispatch_input or {}).get("prompt", "") if not projection.chat["turns"] else ""
    _emit(
        authority,
        task,
        projection,
        {
            "type": "chat/turnStarted",
            "turnId": event.turn_id,
            "startedAt": event.created_at.isoformat(),
            "message": {"text": initial if isinstance(initial, str) else "", "origin": {"kind": "agent"}},
        },
    )


def _question(request, identity, text):
    questions = []
    for q in request["questions"]:
        question = {"id": q["id"], "message": q["text"], "required": True}
        if q["options"]:
            question.update(
                kind="multi-select" if q["multiple"] else "single-select",
                allowFreeformInput=True,
                options=[
                    {"id": o["label"], "label": o["label"], "description": o["description"]}
                    for o in q["options"]
                ],
            )
        else:
            question["kind"] = "text"
        questions.append(question)
    return {"id": identity, "message": text, "questions": questions}


def project_locked(authority, task):
    projection, _ = AgentHostProjection.objects.get_or_create(
        agent_task=task, defaults={"chat": new_chat(task)}
    )
    if projection.task_version == task.version:
        return projection
    events = AgentTaskEvent.objects.filter(agent_task=task, sequence__gt=projection.event_sequence).order_by(
        "sequence"
    )
    for event in events:
        if event.sequence != projection.event_sequence + 1:
            raise ValueError("task event history is incomplete")
        if event.kind != "message_end":
            _turn(authority, task, projection, event)
        identity = (event.data or {}).get("call_id") or f"request-{event.sequence}"
        data = event.data or {}
        if event.kind in {"turn_completed", "turn_failed", "turn_cancelled"}:
            action = {
                "type": {
                    "turn_completed": "chat/turnComplete",
                    "turn_failed": "chat/error",
                    "turn_cancelled": "chat/turnCancelled",
                }[event.kind],
                "turnId": event.turn_id,
                "duration": data["duration_ms"],
            }
            if event.kind == "turn_failed":
                action["error"] = {"errorType": "runtime", "message": data["error"]}
            _emit(authority, task, projection, action)
        elif event.kind == "tool_call_started":
            projection.runtime_state[event.message_id] = {"name": data["name"]}
            _emit(
                authority,
                task,
                projection,
                {
                    "type": "chat/toolCallStart",
                    "turnId": event.turn_id,
                    "toolCallId": event.message_id,
                    "toolName": data["name"],
                    "displayName": data["name"],
                },
            )
        elif event.kind == "tool_call_input":
            info = projection.runtime_state.setdefault(event.message_id, {})
            info["input"] = data["input"]
            _emit(
                authority,
                task,
                projection,
                {
                    "type": "chat/toolCallReady",
                    "turnId": event.turn_id,
                    "toolCallId": event.message_id,
                    "invocationMessage": info.get("name", "Tool call"),
                    "toolInput": json.dumps(data["input"]),
                    "confirmed": "not-needed",
                },
            )
        elif event.kind == "tool_call_result":
            projection.runtime_state.setdefault(event.message_id, {})["output"] = data["output"]
        elif event.kind in {"tool_call_completed", "tool_call_failed"}:
            info = projection.runtime_state.get(event.message_id, {})
            # Some harnesses omit input; ready the observed call without
            # inventing arguments so its real result can be represented.
            parts = projection.chat.get("activeTurn", {}).get("responseParts", [])
            observed = next(
                (p["toolCall"] for p in parts if p.get("toolCall", {}).get("toolCallId") == event.message_id),
                None,
            )
            if observed and observed["status"] == "streaming":
                _emit(
                    authority,
                    task,
                    projection,
                    {
                        "type": "chat/toolCallReady",
                        "turnId": event.turn_id,
                        "toolCallId": event.message_id,
                        "invocationMessage": info.get("name", "Tool call"),
                        "confirmed": "not-needed",
                    },
                )
            result = {
                "success": event.kind == "tool_call_completed",
                "pastTenseMessage": info.get("name", "Tool call"),
                "content": [{"type": "text", "text": info["output"]}] if "output" in info else [],
            }
            if event.kind == "tool_call_failed":
                result["error"] = {"message": data.get("error", "Tool failed")}
            _emit(
                authority,
                task,
                projection,
                {
                    "type": "chat/toolCallComplete",
                    "turnId": event.turn_id,
                    "toolCallId": event.message_id,
                    "result": result,
                },
            )
        elif event.kind in {"assistant_delta", "terminal_delta"}:
            parts = projection.chat["activeTurn"]["responseParts"]
            if not any(part.get("id") == event.message_id for part in parts):
                _emit(
                    authority,
                    task,
                    projection,
                    {
                        "type": "chat/responsePart",
                        "turnId": event.turn_id,
                        "part": {"kind": "markdown", "id": event.message_id, "content": ""},
                    },
                )
            _emit(
                authority,
                task,
                projection,
                {
                    "type": "chat/delta",
                    "turnId": event.turn_id,
                    "partId": event.message_id,
                    "content": event.text,
                },
            )
        elif event.kind == "approval_required" and event.request:
            tool = event.request["tool"]
            from astrolift_agents.services.agent_host_policy import AgentHostPolicyError, evaluate
            from astrolift_agents.services.agent_task_requests import reply_to_request

            recorded = AgentTaskInputReply.objects.filter(request_event=event).first()
            try:
                verdict = (
                    {
                        "decision": recorded.response["decision"],
                        "reason": "Resolved by an authenticated controller",
                        "configured": False,
                    }
                    if recorded
                    else evaluate(task, event)
                )
            except AgentHostPolicyError as exc:
                verdict = {"decision": "ask", "reason": str(exc), "configured": True, "unavailable": True}
            projection.policy_decisions[str(event.sequence)] = verdict
            if not any(
                p.get("toolCall", {}).get("toolCallId") == identity
                for p in projection.chat["activeTurn"]["responseParts"]
            ):
                _emit(
                    authority,
                    task,
                    projection,
                    {
                        "type": "chat/toolCallStart",
                        "turnId": event.turn_id,
                        "toolCallId": identity,
                        "toolName": tool["name"],
                        "displayName": tool["name"],
                    },
                )
            _emit(
                authority,
                task,
                projection,
                {
                    "type": "chat/toolCallReady",
                    "turnId": event.turn_id,
                    "toolCallId": identity,
                    "invocationMessage": event.text,
                    "toolInput": json.dumps(tool["input"]),
                    "riskAssessment": {
                        "kind": "judge",
                        "status": "complete",
                        "reason": verdict["reason"],
                        "safety": {"allow": 1, "deny": 0, "ask": 0.5}[verdict["decision"]],
                    },
                },
            )
            if recorded or (
                verdict["configured"]
                and verdict["decision"] in {"allow", "deny"}
                and task.status == "running"
            ):
                if not recorded:
                    reply_to_request(
                        task=task,
                        sequence=event.sequence,
                        response={
                            "decision": verdict["decision"],
                            **({"reason": verdict["reason"]} if verdict["reason"] else {}),
                        },
                        author_label="Zentinelle",
                        _policy_checked=True,
                    )
                action = {
                    "type": "chat/toolCallConfirmed",
                    "turnId": event.turn_id,
                    "toolCallId": identity,
                    "approved": verdict["decision"] == "allow",
                }
                action.update(
                    {"confirmed": "user-action" if recorded else "not-needed"}
                    if action["approved"]
                    else {"reason": "denied", "reasonMessage": verdict["reason"]}
                )
                _emit(authority, task, projection, action)
        elif event.kind == "input_required" and event.request:
            _emit(
                authority,
                task,
                projection,
                {"type": "chat/inputRequested", "request": _question(event.request, identity, event.text)},
            )
        elif event.kind == "input_resolved":
            request = (
                AgentTaskEvent.objects.filter(
                    agent_task=task,
                    turn_id=event.turn_id,
                    message_id=event.message_id,
                    sequence__lt=event.sequence,
                    request__isnull=False,
                )
                .order_by("-sequence")
                .first()
            )
            if request:
                reply = AgentTaskInputReply.objects.filter(request_event=request).first()
                if request.kind == "approval_required":
                    allowed = reply is not None and reply.response.get("decision") == "allow"
                    action = {
                        "type": "chat/toolCallConfirmed",
                        "turnId": event.turn_id,
                        "toolCallId": (request.data or {}).get("call_id") or f"request-{request.sequence}",
                        "approved": allowed,
                    }
                    action.update(
                        {"confirmed": "user-action"}
                        if allowed
                        else {"reason": "skipped", "reasonMessage": event.text or "Resolved elsewhere"}
                    )
                else:
                    action = {
                        "type": "chat/inputCompleted",
                        "requestId": f"request-{request.sequence}",
                        "response": "accept" if reply else "cancel",
                    }
                _emit(authority, task, projection, action)
        projection.event_sequence = event.sequence
    current = projection.chat.get("activeTurn")
    if (
        not current
        and not projection.chat["turns"]
        and task.status in {"completed", "failed", "timed_out", "cancelled"}
    ):
        _emit(
            authority,
            task,
            projection,
            {
                "type": "chat/turnStarted",
                "turnId": "task-result",
                "startedAt": task.created_at.isoformat(),
                "message": {"text": "", "origin": {"kind": "agent"}},
            },
        )
        current = projection.chat["activeTurn"]
    if current and task.status in {"completed", "failed", "timed_out", "cancelled"}:
        if task.status == "completed" and task.result is not None:
            text = task.result if isinstance(task.result, str) else json.dumps(task.result)
            _emit(
                authority,
                task,
                projection,
                {
                    "type": "chat/responsePart",
                    "turnId": current["id"],
                    "part": {"kind": "markdown", "id": "task-result", "content": text},
                },
            )
        if task.status in {"failed", "timed_out"}:
            action = {
                "type": "chat/error",
                "turnId": current["id"],
                "duration": 0,
                "error": {"errorType": task.status, "message": json.dumps(task.failure or task.status)},
            }
        else:
            action = {
                "type": "chat/turnComplete" if task.status == "completed" else "chat/turnCancelled",
                "turnId": current["id"],
                "duration": 0,
            }
            if task.status == "completed":
                action["duration"] = 0
        _emit(authority, task, projection, action)
    info = {key: projection.chat[key] for key in ("title", "status", "modifiedAt")}
    append_action(
        authority,
        task,
        session_uri(task),
        {"type": "session/chatUpdated", "chat": chat_uri(task), "changes": info},
    )
    projection.task_version = task.version
    projection.save()
    return projection


@transaction.atomic
def snapshots(tasks):
    if not tasks:
        return 0, {}
    org_id = tasks[0].organization_id
    if any(task.organization_id != org_id for task in tasks):
        raise ValueError("one authority cannot span organizations")
    AgentHostAuthority.objects.get_or_create(organization_id=org_id)
    authority = AgentHostAuthority.objects.select_for_update().get(organization_id=org_id)
    states = {}
    for row in tasks:
        task = AgentTask.objects.get(pk=row.pk, organization_id=org_id)
        projection = project_locked(authority, task)
        chat = projection.chat
        states[chat_uri(task)] = chat
        states[session_uri(task)] = {
            **summary(task, chat),
            "lifecycle": "ready",
            "activeClients": [],
            "chats": [{key: chat[key] for key in ("resource", "title", "status", "modifiedAt")}],
            "defaultChat": chat_uri(task),
        }
    authority.save()
    return authority.server_sequence, states
