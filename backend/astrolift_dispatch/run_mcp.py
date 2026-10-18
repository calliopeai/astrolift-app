"""The run's own MCP server (#2434): talk to people as the run.

``/api/dispatch/v1/agents/<task-id>/mcp/`` speaks MCP Streamable HTTP in JSON
response mode, stateless (no session), and is authorised by the run's own
callback key: the same identity its callbacks use, bound to that one run.
It offers only what a run needs to reach people:

* ``astrolift_message_user`` sends a message (the REST ``POST .../messages/``).
* ``astrolift_get_message_replies`` collects queued answers (``GET .../messages/``).

The person-facing MCP gateway (``/api/mcp/v1/``, API tokens) is a different
surface and does not carry these tools: a person's token is not the run.
"""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from astrolift_agents.mcp_contract import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from astrolift_dispatch.views import (
    _require_agent_callback_auth,
    _run_task_for_messages,
    collect_run_replies,
    send_run_message,
)

_ADDRESS = {"type": "array", "items": {"type": "string"}, "maxItems": 50}

RUN_TOOLS = [
    {
        "name": "astrolift_message_user",
        "description": (
            "Send a message to people who can read this agent: an update, a question (with options), "
            "an approval (naming the run's approval request), a result or an alert. Without `to` or "
            "`teams` it goes to the person who launched the run. Anyone outside the agent's audience "
            "refuses the whole send. Answers come back on the run's input channel while it runs."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["body"],
            "properties": {
                "kind": {"type": "string", "enum": ["update", "question", "approval", "result", "alert"]},
                "title": {"type": "string", "maxLength": 200},
                "body": {"type": "string", "maxLength": 8000, "description": "Markdown."},
                "to": {**_ADDRESS, "description": "Email addresses."},
                "teams": {**_ADDRESS, "description": "Team slugs."},
                "options": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "label"],
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                },
                "attachments": {
                    "type": "array",
                    "maxItems": 10,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "ref"],
                        "properties": {
                            "kind": {"type": "string", "enum": ["artifact", "url", "citation"]},
                            "ref": {"type": "string"},
                            "label": {"type": "string"},
                        },
                    },
                },
                "thread_id": {"type": "string", "format": "uuid"},
                "reply_to": {"type": "string", "format": "uuid"},
                "request_sequence": {"type": "integer", "minimum": 1},
                "expires_in_seconds": {"type": "integer", "minimum": 60, "maximum": 2592000},
                "client_request_id": {"type": "string", "format": "uuid"},
            },
        },
    },
    {
        "name": "astrolift_get_message_replies",
        "description": (
            "Collect answers people gave to this agent's messages after the run that sent them had "
            "ended. Each answer is handed out once."
        ),
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
]


def _rpc(request_id, *, result=None, error=None, status=200, protocol=PROTOCOL_VERSION) -> JsonResponse:
    body = {"jsonrpc": "2.0", "id": request_id}
    body.update({"error": error} if error is not None else {"result": result})
    response = JsonResponse(body, status=status)
    response["MCP-Protocol-Version"] = protocol
    return response


def _tool_result(payload: dict, *, is_error: bool) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "structuredContent": payload,
        "isError": is_error,
    }


@csrf_exempt
@require_http_methods(["POST", "DELETE"])
@_require_agent_callback_auth
def agent_run_mcp(request: HttpRequest, task_id: str) -> HttpResponse:
    task, refusal = _run_task_for_messages(request, task_id)
    if refusal is not None:
        return refusal
    protocol = str(request.headers.get("MCP-Protocol-Version") or "2025-03-26").strip()
    if protocol not in SUPPORTED_PROTOCOL_VERSIONS:
        return _rpc(None, error={"code": -32022, "message": "unsupported MCP protocol version"}, status=400)
    if request.method == "DELETE":
        return HttpResponse(status=204)
    if len(request.body) > 64 * 1024:
        return _rpc(None, error={"code": -32600, "message": "request body is too large"}, status=413)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _rpc(None, error={"code": -32700, "message": "invalid JSON"}, protocol=protocol)
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        return _rpc(None, error={"code": -32600, "message": "invalid request"}, protocol=protocol)
    request_id, method = body.get("id"), body.get("method")
    params = body.get("params") if isinstance(body.get("params"), dict) else {}

    if method == "notifications/initialized":
        return HttpResponse(status=202)
    if method == "initialize":
        requested = str(params.get("protocolVersion") or "")
        negotiated = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return _rpc(
            request_id,
            result={
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": f"{SERVER_NAME}-run", "version": SERVER_VERSION},
                "instructions": "Message the people who can read this agent, and collect their answers.",
            },
            protocol=negotiated,
        )
    if method == "ping":
        return _rpc(request_id, result={}, protocol=protocol)
    if method == "tools/list":
        return _rpc(request_id, result={"tools": RUN_TOOLS}, protocol=protocol)
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _rpc(
                request_id, result=_tool_result({"error": "arguments must be an object"}, is_error=True)
            )
        if name == "astrolift_message_user":
            payload, status = send_run_message(task, args)
            return _rpc(request_id, result=_tool_result(payload, is_error=status >= 400), protocol=protocol)
        if name == "astrolift_get_message_replies":
            return _rpc(
                request_id, result=_tool_result(collect_run_replies(task), is_error=False), protocol=protocol
            )
        return _rpc(
            request_id,
            result=_tool_result({"error": f"unknown tool {name!r}"}, is_error=True),
            protocol=protocol,
        )
    return _rpc(
        request_id, error={"code": -32601, "message": f"method not found: {method}"}, protocol=protocol
    )
