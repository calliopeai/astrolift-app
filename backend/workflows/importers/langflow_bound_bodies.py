"""Exact serial source paths with explicitly bound native body implementations."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid

from django.utils.text import slugify

from workflows.back_edges import flowise_output_key, validate_loop_plan
from workflows.collections import bounded_json, validate_iteration
from workflows.importers.base import FlowImportError, FlowImportResult, ImportGap
from workflows.importers.langflow_collections import _BUILTIN_CODE, _handle, _type, _value
from workflows.manifest import ParsedWorkflowManifest, WorkflowDefSpec, WorkflowStageSpec
from workflows.source_ports import LANGFLOW_SOURCE_COMMIT, SOURCE_FORMAT, validate_source_ports

_CODES = {
    **_BUILTIN_CODE,
    "Agent": "14306d4a93732f88a5254c31ea582df779c601673f6cc2d76d3a16f4893c7e09",
    "RunFlow": "ea901208037dfb27d712e359a1cb4b83f7ffee67fa52c56c2a64f22bbe0673ba",
}
_KINDS = {
    "CreateList": "list",
    "CreateListComponent": "list",
    "Loop": "loop",
    "LoopComponent": "loop",
    "Parser": "parser",
    "ParserComponent": "parser",
    "TypeConverter": "converter",
    "TypeConverterComponent": "converter",
    "Agent": "Agent",
    "RunFlow": "RunFlow",
}


def _native_contract(node, binding, input_port, output_port, kind):
    fields = {"target_guid", "source_node_digest", "input_key", "output_path", "output_mode"}
    if not isinstance(binding, dict) or set(binding) != fields:
        raise FlowImportError("native source body requires an explicit complete per-node binding")
    try:
        bounded_json(node)
        fingerprint = hashlib.sha256(
            json.dumps(node, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    except ValueError as exc:
        raise FlowImportError("source native body configuration exceeds its bounded JSON contract") from exc
    if binding["source_node_digest"] != fingerprint:
        raise FlowImportError("source native body changed after its binding was reviewed")
    outputs = node["data"]["node"].get("outputs")
    if (
        not isinstance(outputs, list)
        or not outputs
        or not isinstance(outputs[0], dict)
        or outputs[0].get("name") != output_port
    ):
        raise FlowImportError("source feedback requires an explicit unambiguous first recorded output")
    mode = binding["output_mode"]
    if outputs[0].get("types") != ["Message" if mode == "message" else "Data"]:
        raise FlowImportError("source native output type does not match its declared projection")
    if kind == "RunFlow":
        selected = _value(node, "flow_id_selected")
        try:
            if not isinstance(selected, str) or str(uuid.UUID(selected)) != selected:
                raise ValueError
        except ValueError as exc:
            raise FlowImportError("RunFlow requires an exact canonical source flow identity") from exc
        if (
            input_port.split("~")[-1] != "input_value"
            or _value(node, "session_id") not in (None, "")
            or _value(node, "cache_flow", False) is not False
        ):
            raise FlowImportError("RunFlow supports one explicit text input without shared session state")
        if _value(node, "flow_tweak_data", {}) not in (None, {}):
            raise FlowImportError("RunFlow tool tweaks require source runtime resolution")
    else:
        if _value(node, "api_key") not in (None, ""):
            raise FlowImportError("source credentials must be configured on the native target")
        memory_count = _value(node, "n_messages", 100)
        if type(memory_count) is not int or memory_count != 0 or _value(node, "context_id") not in (None, ""):
            raise FlowImportError("Agent shared memory requires source runtime resolution")
        if output_port != "response":
            raise FlowImportError("Agent structured response is not the first recorded source output")
    ports = {
        "source_format": SOURCE_FORMAT,
        "source_commit": LANGFLOW_SOURCE_COMMIT,
        "source_node_id": node["id"],
        "source_component": kind,
        "source_flow_guid": _value(node, "flow_id_selected") if kind == "RunFlow" else None,
        "source_node_digest": fingerprint,
        "source_input_port": input_port,
        "source_output_port": output_port,
        "target_guid": binding["target_guid"],
        "native_input_key": binding["input_key"],
        "native_output_path": binding["output_path"],
        "output_mode": mode,
    }
    try:
        return validate_source_ports(ports, kind="workflow" if kind == "RunFlow" else "agent_dispatch")
    except ValueError as exc:
        raise FlowImportError(str(exc)) from exc


def import_bound_collection(payload, nodes, edges):
    if len(nodes) > 24 or len(edges) > 25:
        raise FlowImportError("source serial body exceeds its finite component bound")
    if any(
        not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"] for node in nodes
    ):
        raise FlowImportError("source serial body requires unique explicit node identities")
    by_id = {node["id"]: node for node in nodes}
    if len(by_id) != len(nodes):
        raise FlowImportError("source serial body requires unique explicit node identities")
    kinds = {}
    controls = {}
    for key, node in by_id.items():
        kind = _KINDS.get(_type(node))
        if kind is None:
            raise FlowImportError(
                "source branches, model dependencies, tools or nested loop state are unsupported"
            )
        code = _value(node, "code")
        if code is not None and (
            not isinstance(code, str)
            or hashlib.sha256(code.replace("\r\n", "\n").encode()).hexdigest() != _CODES[kind]
        ):
            raise FlowImportError("source component code differs from the verified pinned implementation")
        kinds[key] = kind
        if kind in {"list", "loop", "converter"}:
            if kind in controls:
                raise FlowImportError("source serial body requires one exact component per control role")
            controls[kind] = key
    if not {"list", "loop"} <= controls.keys():
        raise FlowImportError("source serial body requires one collection and one loop")
    bindings = payload.get("astrolift_bindings")
    native_nodes = {key for key, kind in kinds.items() if kind in {"Agent", "RunFlow"}}
    if not isinstance(bindings, dict) or set(bindings) != native_nodes:
        raise FlowImportError("source native body bindings must match every exact native source node")
    identities = set()
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or not isinstance(edge.get("source"), str)
            or not isinstance(edge.get("target"), str)
            or edge["source"] not in by_id
            or edge["target"] not in by_id
        ):
            raise FlowImportError("source serial body has an unavailable edge endpoint")
        item = (edge["source"], _handle(edge, source=True), edge["target"], _handle(edge, source=False))
        if item in identities:
            raise FlowImportError("source serial body has duplicate edges")
        identities.add(item)
    list_id, loop_id = controls["list"], controls["loop"]
    source_edges = [
        edge
        for edge in identities
        if edge[0] == list_id and edge[2:] == (loop_id, "data") and edge[1] in {"list", "dataframe"}
    ]
    if len(source_edges) != 1:
        raise FlowImportError("source serial body requires one exact collection edge")
    expected = {source_edges[0]}
    if "converter" in controls:
        converter = by_id[controls["converter"]]
        if (
            _value(converter, "output_type", "Message") not in {"JSON", "Data"}
            or _value(converter, "auto_parse", False) is not False
        ):
            raise FlowImportError("source done conversion requires explicit JSON without automatic parsing")
        expected.add((loop_id, "done", controls["converter"], "input_data"))
    body_ids = {key for key, kind in kinds.items() if kind in {"parser", "Agent", "RunFlow"}}
    ordered: list[WorkflowStageSpec] = []
    outgoing = [edge for edge in identities if edge[0] == loop_id and edge[1] == "item"]
    seen = set()
    while True:
        if len(outgoing) != 1:
            raise FlowImportError("source body requires one linear bounded path without branch inference")
        incoming = outgoing[0]
        expected.add(incoming)
        key, input_port = incoming[2:]
        if key == loop_id:
            if input_port != "item" or not ordered:
                raise FlowImportError("source body must return to its exact loop feedback input")
            break
        if key not in body_ids or key in seen:
            raise FlowImportError("source body contains an unsupported cycle or control target")
        seen.add(key)
        outgoing = [edge for edge in identities if edge[0] == key]
        if len(outgoing) != 1:
            raise FlowImportError("source body requires one exact output per step")
        output_port = outgoing[0][1]
        node, kind = by_id[key], kinds[key]
        if kind == "parser":
            if (
                input_port != "input_data"
                or output_port != "parsed_text"
                or _value(node, "mode", "Parser") != "Parser"
            ):
                raise FlowImportError("source Parser requires its verified record input and text output")
            iteration = {
                "source_format": "langflow_parser",
                "pattern": _value(node, "pattern", "Text: {text}"),
                "separator": _value(node, "sep", "\n"),
            }
            stage = WorkflowStageSpec(
                order=len(ordered) + 1,
                kind="format_record",
                output_key=flowise_output_key(key),
                iteration=iteration,
            )
        else:
            iteration = _native_contract(node, bindings[key], input_port, output_port, kind)
            stage = WorkflowStageSpec(
                order=len(ordered) + 1,
                kind="workflow" if kind == "RunFlow" else "agent_dispatch",
                output_key=flowise_output_key(key),
                iteration=iteration,
            )
            if kind == "RunFlow":
                stage.workflow = f"guid:{iteration['target_guid']}"
            else:
                stage.agent = f"guid:{iteration['target_guid']}"
        ordered.append(stage)
    if seen != body_ids or identities != expected:
        raise FlowImportError("source graph has unbound dependencies or scheduling outside the serial path")
    texts = _value(by_id[list_id], "texts")
    if not isinstance(texts, list) or any(not isinstance(text, str) or "{{" in text for text in texts):
        raise FlowImportError("source collection requires literal ordered text values")
    if _value(by_id[loop_id], "data") not in (None, "", []):
        raise FlowImportError("source collection data must come from its exact source edge")
    iteration = {
        "source_format": "langflow_loop",
        "max_items": _value(by_id[loop_id], "astrolift_max_items", 50),
        "body_end": ordered[-1].output_key,
        "items": [{"text": text} for text in texts],
    }
    owner = WorkflowStageSpec(
        order=0, kind="collection", output_key=flowise_output_key(loop_id), iteration=iteration
    )
    stages = [owner, *ordered]
    try:
        for stage in stages:
            validate_iteration(stage.iteration, kind=stage.kind)
        validate_loop_plan([dataclasses.asdict(stage) for stage in stages], pattern_kind="chained")
    except ValueError as exc:
        raise FlowImportError(str(exc)) from exc
    name = str(payload.get("name") or "Imported bound serial workflow")
    return FlowImportResult(
        manifest=ParsedWorkflowManifest(
            WorkflowDefSpec(slug=slugify(name) or "imported-bound-serial", name=name, pattern="chained"),
            stages,
        ),
        gaps=[
            ImportGap(
                code="explicit_native_body_binding",
                severity="warning",
                message="The exact source ports and serial scheduling are mapped to explicitly selected native implementations. Native model, tool and instruction equivalence requires operator review; it is not inferred from component labels.",
            )
        ],
    )
