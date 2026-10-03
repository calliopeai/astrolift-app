"""Finite native body contracts for explicitly bound Langflow source ports."""

from __future__ import annotations

import re
import uuid
from typing import Any

LANGFLOW_SOURCE_COMMIT = "f9b283243d2fdd8502cb4ffd606c3058cff5017e"
SOURCE_FORMAT = "langflow_native_body_v1"
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}\Z")
_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class SourcePortContractError(ValueError):
    pass


def validate_source_ports(value: Any, *, kind: str) -> dict:
    required = {
        "source_format",
        "source_commit",
        "source_node_id",
        "source_component",
        "source_flow_guid",
        "source_node_digest",
        "source_input_port",
        "source_output_port",
        "target_guid",
        "native_input_key",
        "native_output_path",
        "output_mode",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise SourcePortContractError("imported native body requires its complete source-port contract")
    component = value["source_component"]
    if (
        value["source_format"] != SOURCE_FORMAT
        or value["source_commit"] != LANGFLOW_SOURCE_COMMIT
        or not isinstance(component, str)
        or (component, kind) not in {("Agent", "agent_dispatch"), ("RunFlow", "workflow")}
    ):
        raise SourcePortContractError("unsupported imported native body source or stage kind")
    if (
        not isinstance(value["source_node_id"], str)
        or not 1 <= len(value["source_node_id"]) <= 200
        or not isinstance(value["source_node_digest"], str)
        or not _SHA256.fullmatch(value["source_node_digest"])
    ):
        raise SourcePortContractError("imported native body requires an exact bounded source identity")
    try:
        guid = str(uuid.UUID(value["target_guid"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SourcePortContractError("imported native body target must be a canonical GUID") from exc
    if value["target_guid"] != guid:
        raise SourcePortContractError("imported native body target must be a canonical GUID")
    for key in ("source_input_port", "source_output_port"):
        if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 200:
            raise SourcePortContractError("imported native body requires explicit source ports")
    if component == "Agent" and (
        value["source_input_port"] != "input_value"
        or value["source_output_port"] != "response"
        or value["output_mode"] != "message"
    ):
        raise SourcePortContractError("unsupported Agent input or output port semantics")
    if component == "RunFlow" and any(
        len(value[key].split("~")) != 2 or not all(value[key].split("~"))
        for key in ("source_input_port", "source_output_port")
    ):
        raise SourcePortContractError("RunFlow requires exact component~port source identities")
    if component == "RunFlow":
        if value["source_input_port"].split("~")[-1] != "input_value":
            raise SourcePortContractError("RunFlow supports only its explicit text input port")
        try:
            flow_guid = str(uuid.UUID(value["source_flow_guid"]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise SourcePortContractError("RunFlow requires its exact canonical source flow GUID") from exc
        if flow_guid != value["source_flow_guid"]:
            raise SourcePortContractError("RunFlow requires its exact canonical source flow GUID")
    elif value["source_flow_guid"] is not None:
        raise SourcePortContractError("Agent source bodies do not select a source flow GUID")
    if (
        not isinstance(value["native_input_key"], str)
        or not _KEY.fullmatch(value["native_input_key"])
        or value["native_input_key"].startswith("_astrolift_")
        or not isinstance(value["native_output_path"], str)
        or value["native_output_path"]
        and not _PATH.fullmatch(value["native_output_path"])
        or not isinstance(value["output_mode"], str)
        or value["output_mode"] not in {"message", "data"}
    ):
        raise SourcePortContractError("imported native body requires bounded native port projections")
    return dict(value)


def imported_input(value: dict, previous: Any) -> dict:
    if (
        not isinstance(previous, dict)
        or not isinstance(previous.get("text", ""), str)
        or len(previous.get("text", "").encode()) > 65536
    ):
        raise SourcePortContractError("imported text input is unavailable")
    return {value["native_input_key"]: previous.get("text", "")}


def imported_output(value: dict, result: Any, *, timestamp: str) -> dict:
    from workflows.collections import bounded_json

    selected = result
    if value["native_output_path"]:
        for key in value["native_output_path"].split("."):
            if not isinstance(selected, dict) or key not in selected:
                raise SourcePortContractError("imported native output port is unavailable")
            selected = selected[key]
    if value["output_mode"] == "message":
        if not isinstance(selected, str) or len(selected.encode()) > 65536:
            raise SourcePortContractError("imported message output is unavailable or exceeds its bound")
        return {"text": selected, "timestamp": timestamp}
    if not isinstance(selected, dict):
        raise SourcePortContractError("imported data output port requires a JSON record")
    try:
        bounded_json(selected)
    except ValueError as exc:
        raise SourcePortContractError("imported data output exceeds its finite JSON bound") from exc
    return selected
