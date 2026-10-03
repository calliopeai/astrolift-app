"""Finite serial collection plans and the supported Langflow record parser."""

from __future__ import annotations

import json
import math
import re
import string
from typing import Any

MAX_COLLECTION_ITEMS = 50
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}\Z")


class CollectionContractError(ValueError):
    pass


def validate_iteration(value: Any, *, kind: str) -> dict:
    if kind in {"agent_dispatch", "workflow"} and value != {}:
        from workflows.source_ports import SourcePortContractError, validate_source_ports

        try:
            return validate_source_ports(value, kind=kind)
        except SourcePortContractError as exc:
            raise CollectionContractError(str(exc)) from exc
    if kind not in {"collection", "format_record"}:
        if value != {}:
            raise CollectionContractError("iteration is valid only for collection and format_record stages")
        return {}
    if not isinstance(value, dict):
        raise CollectionContractError("iteration must be an object")
    if kind == "format_record":
        if (
            set(value) != {"source_format", "pattern", "separator"}
            or value["source_format"] != "langflow_parser"
        ):
            raise CollectionContractError("unsupported record formatter contract")
        pattern, separator = value["pattern"], value["separator"]
        if (
            not isinstance(pattern, str)
            or len(pattern) > 4096
            or not isinstance(separator, str)
            or len(separator) > 256
        ):
            raise CollectionContractError("record pattern and separator must be bounded strings")
        try:
            fields = list(string.Formatter().parse(pattern))
        except ValueError as exc:
            raise CollectionContractError("invalid record format pattern") from exc
        if any(
            field is not None and (not _KEY.fullmatch(field) or spec or conversion)
            for _, field, spec, conversion in fields
        ):
            raise CollectionContractError("record patterns support plain named fields only")
        return dict(value)
    required = {"max_items", "body_end"}
    allowed = required | {"items", "items_path", "source_format"}
    if (
        not required <= value.keys()
        or value.keys() - allowed
        or ("items" in value) == ("items_path" in value)
    ):
        raise CollectionContractError(
            "collection requires max_items, body_end and exactly one of items or items_path"
        )
    cap = value["max_items"]
    if type(cap) is not int or not 1 <= cap <= MAX_COLLECTION_ITEMS:
        raise CollectionContractError("collection max_items must be an integer between 1 and 50")
    end = value["body_end"]
    if not isinstance(end, str) or not 1 <= len(end) <= 100:
        raise CollectionContractError("collection body_end requires an explicit forward output_key")
    if "source_format" in value and value["source_format"] != "langflow_loop":
        raise CollectionContractError("unsupported imported collection contract")
    if "items_path" in value and (
        not isinstance(value["items_path"], str) or not _PATH.fullmatch(value["items_path"])
    ):
        raise CollectionContractError("collection items_path requires a dotted object field path")
    if "items" in value:
        validate_items(value["items"], cap=cap)
    return dict(value)


def validate_items(value: Any, *, cap: int) -> list[dict]:
    if not isinstance(value, list) or len(value) > cap:
        raise CollectionContractError(
            "collection data is unavailable or exceeds max_items; no items were dispatched"
        )
    if any(not isinstance(item, dict) for item in value):
        raise CollectionContractError("collection requires an ordered list of JSON records")
    bounded_json(value)
    return value


def bounded_json(value: Any) -> None:
    pending = [(value, 0)]
    visited = 0
    while pending:
        member, depth = pending.pop()
        visited += 1
        if depth > 32 or visited > 16384:
            raise CollectionContractError("collection data exceeds its bounded JSON structure")
        if isinstance(member, dict):
            if any(not isinstance(key, str) for key in member):
                raise CollectionContractError("collection records require JSON object keys")
            pending.extend((nested, depth + 1) for nested in member.values())
        elif isinstance(member, list):
            pending.extend((nested, depth + 1) for nested in member)
        elif not (
            member is None
            or type(member) in (str, int, bool)
            or type(member) is float
            and math.isfinite(member)
        ):
            raise CollectionContractError("collection records require finite JSON values")
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise CollectionContractError("collection records require finite JSON values") from exc
    if len(encoded.encode()) > 262144:
        raise CollectionContractError("collection data exceeds its bounded payload size")


def collection_items(config: dict, previous: Any) -> list[dict]:
    value = config.get("items")
    if "items_path" in config:
        value = previous
        for field in config["items_path"].split("."):
            if not isinstance(value, dict) or field not in value:
                raise CollectionContractError(
                    "collection input field is unavailable; no items were dispatched"
                )
            value = value[field]
    return validate_items(value, cap=config["max_items"])


def collection_ranges(stages: list[dict]) -> dict[int, int]:
    keys = {stage.get("output_key"): index for index, stage in enumerate(stages) if stage.get("output_key")}
    ranges: dict[int, int] = {}
    occupied: set[int] = set()
    for index, stage in enumerate(stages):
        config = validate_iteration(stage.get("iteration", {}), kind=stage["kind"])
        if stage["kind"] != "collection":
            continue
        end = keys.get(config["body_end"])
        if not stage.get("output_key") or end is None or end <= index:
            raise CollectionContractError("collection requires an explicit forward body range")
        body = set(range(index + 1, end + 1))
        if body & occupied or index in occupied:
            raise CollectionContractError("nested or overlapping collection ranges are not supported")
        occupied |= body
        ranges[index] = end
        for member in stages[index + 1 : end + 1]:
            if member["kind"] not in {
                "agent_dispatch",
                "workflow",
                "checkpoint",
                "human_gate",
                "format_record",
            }:
                raise CollectionContractError("collection body kind is not supported")
            if (
                member.get("fan_out_dynamic")
                or member.get("fan_out_count")
                or member.get("fan_out") not in (None, 0)
            ):
                raise CollectionContractError("collection bodies execute serially; fan-out is not supported")
    for index, stage in enumerate(stages):
        edge = stage.get("back_edge") or {}
        if not edge:
            continue
        target = keys.get(edge.get("to"))
        for start, end in ranges.items():
            if (start < index <= end) != (target is not None and start < target <= end):
                raise CollectionContractError("return edges cannot enter or leave a collection body")
    return ranges


def format_record(config: dict, value: Any, *, timestamp: str) -> dict:
    validate_iteration(config, kind="format_record")
    if not isinstance(value, dict):
        raise CollectionContractError("record formatter input is unavailable")
    validate_items([value], cap=1)

    class DefaultFields(dict):
        def __missing__(self, key):
            return ""

    text = config["pattern"].format_map(DefaultFields(value))
    if len(text.encode()) > 65536:
        raise CollectionContractError("record formatter output exceeds its bounded payload size")
    return {"text": text, "timestamp": timestamp}


def collection_binding(output: Any) -> dict | None:
    """Read only a complete bounded runtime binding; malformed history is unavailable."""
    value = output.get("collection") if isinstance(output, dict) else None
    if not isinstance(value, dict) or set(value) != {
        "owner_order",
        "body_stage_ids",
        "item_count",
        "max_items",
    }:
        return None
    order, count, cap, ids = (
        value[key] for key in ("owner_order", "item_count", "max_items", "body_stage_ids")
    )
    if (
        type(order) is not int
        or not 0 <= order <= 32767
        or type(cap) is not int
        or not 1 <= cap <= MAX_COLLECTION_ITEMS
        or type(count) is not int
        or not 0 <= count <= cap
        or not isinstance(ids, list)
        or not 1 <= len(ids) <= 500
        or any(
            not isinstance(identity, str)
            or not 1 <= len(identity) <= 20
            or not identity.isascii()
            or not identity.isdigit()
            or int(identity) < 1
            for identity in ids
        )
        or len(set(ids)) != len(ids)
    ):
        return None
    return value
