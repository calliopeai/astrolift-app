"""Verified CreateList → serial Loop → Parser feedback mapping.

Contract source: Langflow f9b283243d2fdd8502cb4ffd606c3058cff5017e,
components/processing/create_list.py, parser.py and flow_controls/loop.py.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

from django.utils.text import slugify

from workflows.back_edges import flowise_output_key, validate_loop_plan
from workflows.collections import validate_iteration
from workflows.importers.base import FlowImportError, FlowImportResult, ImportGap
from workflows.manifest import (
    ParsedWorkflowManifest,
    WorkflowDefSpec,
    WorkflowStageSpec,
)

_BUILTIN_CODE = {
    "list": "5657383579615ce5781924b307013d60e7662b45bca7e3f8db021813eefef73f",
    "parser": "7ab5fd0e0a106ccda5d614ae4c1bddf1120f16bb3c953c0a3b9d8e1ed8a56ec2",
    "loop": "2712a1de7210b3f15506893853fefc69ae66f7d802fe633869a27b80fe453704",
    "converter": "6ce26e994c2d921fd3b7a2a2dbe69acf60caaae136660c85422f6c5b6ade12cb",
}


def _type(node):
    data = node.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        raise FlowImportError("Langflow components require an explicit type object")
    return data["type"]


def _value(node, name, default=None):
    data = node.get("data")
    inner = data.get("node") if isinstance(data, dict) else None
    template = inner.get("template") if isinstance(inner, dict) else None
    if not isinstance(template, dict):
        raise FlowImportError("Langflow components require an explicit template object")
    field = template.get(name, {})
    if not isinstance(field, dict):
        raise FlowImportError(
            "Langflow component template fields must be explicit objects"
        )
    return field.get("value", default)


def _handle(edge, *, source):
    key = "sourceHandle" if source else "targetHandle"
    field = "name" if source else "fieldName"
    endpoint = edge.get("source" if source else "target")
    data = edge.get("data")
    if data is not None and not isinstance(data, dict):
        raise FlowImportError("Langflow edge data must be an object")
    values = [edge.get(key), (data or {}).get(key)]
    identities = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            try:
                value = json.loads(value.replace("œ", '"'))
            except ValueError as exc:
                raise FlowImportError("Langflow loop handle is malformed") from exc
        if (
            not isinstance(value, dict)
            or value.get("id") != endpoint
            or not isinstance(value.get(field), str)
        ):
            raise FlowImportError(
                "Langflow loop requires exact node and input/output handle identities"
            )
        identities.append(value[field])
    if not identities or len(set(identities)) != 1:
        raise FlowImportError("Langflow loop handles are unavailable or ambiguous")
    return identities[0]


def import_collection(payload, nodes, edges) -> FlowImportResult:
    by_id = {node.get("id"): node for node in nodes if isinstance(node, dict)}
    if (
        len(by_id) != len(nodes)
        or None in by_id
        or any(not isinstance(key, str) or not key for key in by_id)
    ):
        raise FlowImportError("Langflow loop requires unique source node identities")
    kinds = {}
    for node in nodes:
        kind = _type(node)
        if kind not in {
            "CreateList",
            "CreateListComponent",
            "Loop",
            "LoopComponent",
            "Parser",
            "ParserComponent",
            "TypeConverter",
            "TypeConverterComponent",
        }:
            raise FlowImportError(
                "Langflow serial import supports CreateList, one Loop and one Parser body; other body/state/router semantics require native configuration"
            )
        canonical = (
            "list"
            if kind.startswith("CreateList")
            else "loop"
            if kind.startswith("Loop")
            else "parser"
            if kind.startswith("Parser")
            else "converter"
        )
        if canonical in kinds:
            raise FlowImportError(
                "Langflow serial import requires one exact component per role"
            )
        code = _value(node, "code")
        if code is not None and (
            not isinstance(code, str)
            or hashlib.sha256(code.replace("\r\n", "\n").encode()).hexdigest()
            != _BUILTIN_CODE[canonical]
        ):
            raise FlowImportError(
                "custom or older Langflow component code has no verified scheduler mapping"
            )
        kinds[canonical] = node["id"]
    if not {"list", "loop", "parser"} <= kinds.keys():
        raise FlowImportError(
            "Langflow Loop requires a supported collection source and declared Parser feedback body"
        )
    identities = set()
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or edge.get("source") not in by_id
            or edge.get("target") not in by_id
        ):
            raise FlowImportError("Langflow loop edge target is unavailable")
        identity = (
            edge["source"],
            _handle(edge, source=True),
            edge["target"],
            _handle(edge, source=False),
        )
        if identity in identities:
            raise FlowImportError("Langflow loop has duplicate edges")
        identities.add(identity)
    list_id, loop_id, parser_id = (kinds[key] for key in ("list", "loop", "parser"))
    expected = {
        (loop_id, "item", parser_id, "input_data"),
        (parser_id, "parsed_text", loop_id, "item"),
    }
    source_edge = [
        (a, b, c, d)
        for a, b, c, d in identities
        if a == list_id and c == loop_id and d == "data" and b in {"list", "dataframe"}
    ]
    if len(source_edge) != 1:
        raise FlowImportError(
            "Langflow collection requires one CreateList data binding"
        )
    expected.add(source_edge[0])
    if "converter" in kinds:
        converter = by_id[kinds["converter"]]
        if (
            _value(converter, "output_type", "Message") not in {"JSON", "Data"}
            or _value(converter, "auto_parse", False) is not False
        ):
            raise FlowImportError(
                "Langflow done output supports only the explicit JSON conversion without auto parsing"
            )
        expected.add((loop_id, "done", kinds["converter"], "input_data"))
    if identities != expected:
        raise FlowImportError(
            "Langflow loop body or done scheduling is unsupported; no graph was flattened"
        )
    source, loop, parser = (by_id[key] for key in (list_id, loop_id, parser_id))
    texts = _value(source, "texts")
    if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
        raise FlowImportError(
            "Langflow CreateList requires an explicit ordered list of text values"
        )
    if _value(parser, "mode", "Parser") != "Parser":
        raise FlowImportError(
            "Langflow Parser Stringify mode has no verified native mapping"
        )
    if any(isinstance(text, str) and "{{" in text for text in texts):
        raise FlowImportError(
            "Langflow unresolved input variables require source runtime resolution"
        )
    if _value(loop, "data") not in (None, "", []):
        raise FlowImportError("Langflow loop data must come from its exact source edge")
    parser_key = flowise_output_key(parser_id)
    collection = {
        "source_format": "langflow_loop",
        "max_items": _value(loop, "astrolift_max_items", 50),
        "body_end": parser_key,
        "items": [{"text": text} for text in texts],
    }
    formatter = {
        "source_format": "langflow_parser",
        "pattern": _value(parser, "pattern", "Text: {text}"),
        "separator": _value(parser, "sep", "\n"),
    }
    try:
        validate_iteration(collection, kind="collection")
        validate_iteration(formatter, kind="format_record")
        stages = [
            WorkflowStageSpec(
                order=0,
                kind="collection",
                output_key=flowise_output_key(loop_id),
                role="Serial collection",
                iteration=collection,
            ),
            WorkflowStageSpec(
                order=1,
                kind="format_record",
                output_key=parser_key,
                role="Record parser",
                iteration=formatter,
            ),
        ]
        validate_loop_plan(
            [dataclasses.asdict(stage) for stage in stages], pattern_kind="chained"
        )
    except ValueError as exc:
        raise FlowImportError(str(exc)) from exc
    name = str(payload.get("name") or "Imported Langflow collection")
    return FlowImportResult(
        manifest=ParsedWorkflowManifest(
            definition=WorkflowDefSpec(
                slug=slugify(name) or "imported-langflow-collection",
                name=name,
                pattern="chained",
                description=str(payload.get("description") or ""),
            ),
            stages=stages,
        ),
        gaps=[
            ImportGap(
                code="bounded_serial_collection",
                message="CreateList records run through the declared Parser body serially. Done is the ordered results table; max_items is an explicit platform safety bound. Custom code, state, routers and other bodies are not translated.",
                node_id=loop_id,
                node_type=_type(loop),
                severity="info",
            )
        ],
    )
