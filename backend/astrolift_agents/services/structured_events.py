"""Validate the negotiated runner event protocol, independently of AHP."""

import re

from astrolift_agents.services.agent_task_requests import TaskInputRequestError, request_bytes

VERSION = 1
FIELD = "structured_event_protocol_version"
ATTENTION = {"approval_required", "input_required", "input_resolved"}
IDENTITY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}\Z")


def _text(limit, empty=False):
    return lambda value: isinstance(value, str) and (empty or bool(value)) and len(value) <= limit


def _duration(value):
    return type(value) is int and 0 <= value <= 7 * 24 * 60 * 60 * 1000


def _identity(value):
    return isinstance(value, str) and bool(IDENTITY.fullmatch(value))


SCHEMA = {
    "turn_started": (
        {"trigger": lambda v: v in ("task", "follow_up")},
        {"input_ids": lambda v: isinstance(v, list) and 1 <= len(v) <= 20 and all(_text(128)(i) for i in v)},
    ),
    "turn_completed": ({"stop_reason": _text(128, True), "duration_ms": _duration}, {}),
    "turn_failed": ({"error": _text(500), "duration_ms": _duration}, {}),
    "turn_cancelled": ({"reason": lambda v: v == "stop_requested", "duration_ms": _duration}, {}),
    "stop_requested": ({}, {}),
    "tool_call_started": ({"name": _text(128)}, {"parent_call_id": _identity}),
    "tool_call_input": ({"input": lambda v: isinstance(v, dict), "truncated": lambda v: type(v) is bool}, {}),
    "tool_call_result": ({"output": _text(2000, True), "truncated": lambda v: type(v) is bool}, {}),
    "tool_call_completed": ({}, {"duration_ms": _duration}),
    "tool_call_failed": ({}, {"duration_ms": _duration, "error": _text(500)}),
    "approval_required": ({}, {"call_id": _identity}),
    "input_required": ({}, {"call_id": _identity}),
    "input_resolved": (
        {
            "outcome": lambda v: isinstance(v, str)
            and v in ("allowed", "denied", "answered", "withdrawn", "failed")
        },
        {},
    ),
}


def validate(kind, data, text):
    if data is None:
        if kind in SCHEMA and kind not in ATTENTION:
            raise TaskInputRequestError("structured events require data")
        return
    if kind not in SCHEMA:
        raise TaskInputRequestError("this event kind does not accept structured data")
    required, optional = SCHEMA[kind]
    if not isinstance(data, dict) or not set(required) <= set(data) <= set(required) | set(optional):
        raise TaskInputRequestError("invalid structured event data")
    if kind not in ATTENTION and text:
        raise TaskInputRequestError("structured lifecycle event text must be empty")
    request_bytes(data)
    for key, check in {**required, **optional}.items():
        if key in data and not check(data[key]):
            raise TaskInputRequestError(f"invalid structured event field {key}")
    if kind == "tool_call_input" and request_bytes(data["input"]) > 8 * 1024:
        raise TaskInputRequestError("structured tool input exceeds 8192 bytes", 413)
