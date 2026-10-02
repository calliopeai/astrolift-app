"""Exact source handles map a serial body, never an arbitrary graph cycle."""

import copy
import dataclasses
import json
from pathlib import Path

import pytest

from workflows.importers.base import FlowImportError
from workflows.importers.langflow import LangflowImporter
from workflows.manifest import emit_workflow_manifest, parse_workflow_manifest
from workflows.services.dsl_parser import emit_workflows_dsl, parse_workflows_dsl


def source_collection(texts=None, *, cap=3, done=True):
    def node(identity, kind, **values):
        return {
            "id": identity,
            "data": {
                "type": kind,
                "node": {
                    "template": {key: {"value": value} for key, value in values.items()}
                },
            },
        }

    def edge(source, name, target, field):
        return {
            "source": source,
            "target": target,
            "sourceHandle": json.dumps({"id": source, "name": name}).replace('"', "œ"),
            "targetHandle": json.dumps({"id": target, "fieldName": field}).replace(
                '"', "œ"
            ),
        }

    nodes = [
        node(
            "CreateList-source",
            "CreateList",
            texts=["first", "second"] if texts is None else texts,
        ),
        node("Loop-control", "LoopComponent", astrolift_max_items=cap),
        node(
            "Parser-body",
            "ParserComponent",
            mode="Parser",
            pattern="Item: {text}",
            sep="\n",
        ),
    ]
    edges = [
        edge("CreateList-source", "list", "Loop-control", "data"),
        edge("Loop-control", "item", "Parser-body", "input_data"),
        edge("Parser-body", "parsed_text", "Loop-control", "item"),
    ]
    if done:
        nodes.append(
            node(
                "TypeConverter-done",
                "TypeConverter",
                output_type="JSON",
                auto_parse=False,
            )
        )
        edges.append(edge("Loop-control", "done", "TypeConverter-done", "input_data"))
    return {
        "name": "Imported serial collection",
        "data": {"nodes": nodes, "edges": edges},
    }


@pytest.mark.parametrize("done", [False, True])
@pytest.mark.parametrize("texts", [[], [""], ["first", "second", "third"]])
def test_source_collection_maps_real_item_feedback_and_done_and_roundtrips(done, texts):
    result = LangflowImporter().import_flow(source_collection(texts, done=done))
    assert [stage.kind for stage in result.manifest.stages] == [
        "collection",
        "format_record",
    ]
    owner, body = result.manifest.stages
    assert owner.iteration == {
        "source_format": "langflow_loop",
        "max_items": 3,
        "body_end": body.output_key,
        "items": [{"text": text} for text in texts],
    }
    assert body.iteration == {
        "source_format": "langflow_parser",
        "pattern": "Item: {text}",
        "separator": "\n",
    }
    assert (
        parse_workflow_manifest(emit_workflow_manifest(result.manifest))
        == result.manifest
    )
    stage_rows = [
        {
            **dataclasses.asdict(stage),
            "timeout_seconds": stage.timeout,
            "skill_refs": stage.skills,
            "agent_ref": stage.agent or "",
            "workflow_ref": stage.workflow or "",
            "environment_spec_slug": stage.environment_spec_slug or "",
            "prompt": stage.prompt or "",
        }
        for stage in result.manifest.stages
    ]
    yaml = emit_workflows_dsl(
        [
            {
                "slug": "serial",
                "name": "Serial",
                "pattern_kind": "chained",
                "stages": stage_rows,
            }
        ]
    )
    parsed = parse_workflows_dsl(yaml)
    assert [stage["iteration"] for stage in parsed[0]["stages"]] == [
        owner.iteration,
        body.iteration,
    ]
    assert all(gap.severity == "info" for gap in result.gaps)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_handle",
        "wrong_target",
        "duplicate_edge",
        "extra_body",
        "wrong_feedback",
        "stringify",
        "custom_code",
        "missing_texts",
        "oversize",
        "bad_cap",
        "done_message",
        "conflicting_handle",
    ],
)
def test_unproved_source_scheduling_and_data_are_refused(mutation):
    source = source_collection()
    nodes, edges = source["data"]["nodes"], source["data"]["edges"]
    if mutation == "missing_handle":
        edges[1].pop("sourceHandle")
    elif mutation == "wrong_target":
        edges[1]["target"] = "not-a-source-node"
    elif mutation == "duplicate_edge":
        edges.append(copy.deepcopy(edges[1]))
    elif mutation == "extra_body":
        nodes.append({"id": "Router-extra", "data": {"type": "ConditionalRouter"}})
    elif mutation == "wrong_feedback":
        edges[2]["targetHandle"] = json.dumps(
            {"id": "Loop-control", "fieldName": "data"}
        )
    elif mutation == "stringify":
        nodes[2]["data"]["node"]["template"]["mode"]["value"] = "Stringify"
    elif mutation == "custom_code":
        nodes[1]["data"]["node"]["template"]["code"] = {
            "value": "def altered_loop(): pass"
        }
    elif mutation == "missing_texts":
        nodes[0]["data"]["node"]["template"].pop("texts")
    elif mutation == "oversize":
        nodes[0]["data"]["node"]["template"]["texts"]["value"] = ["item"] * 4
    elif mutation == "bad_cap":
        nodes[1]["data"]["node"]["template"]["astrolift_max_items"]["value"] = True
    elif mutation == "done_message":
        nodes[-1]["data"]["node"]["template"]["output_type"]["value"] = "Message"
    elif mutation == "conflicting_handle":
        edges[1]["data"] = {"sourceHandle": {"id": "Loop-control", "name": "done"}}
    with pytest.raises(FlowImportError):
        LangflowImporter().import_flow(source)


def test_toml_preserves_json_nulls_in_collection_records():
    manifest = LangflowImporter().import_flow(source_collection()).manifest
    manifest.stages[0].iteration["items"] = [{"text": None}]
    assert parse_workflow_manifest(emit_workflow_manifest(manifest)) == manifest


@pytest.mark.parametrize(
    "location", ["node_data", "inner_node", "template", "field", "edge_data"]
)
def test_malformed_source_objects_are_explicit_import_refusals(location):
    payload = source_collection()
    if location == "node_data":
        payload["data"]["nodes"][0]["data"] = []
    elif location == "inner_node":
        payload["data"]["nodes"][0]["data"]["node"] = []
    elif location == "template":
        payload["data"]["nodes"][0]["data"]["node"]["template"] = []
    elif location == "field":
        payload["data"]["nodes"][0]["data"]["node"]["template"]["texts"] = []
    else:
        payload["data"]["edges"][0]["data"] = []
    with pytest.raises(FlowImportError):
        LangflowImporter().import_flow(payload)


def test_published_serial_authoring_example_is_a_real_manifest():
    guide = (
        Path(__file__).resolve().parents[3] / "docs/runbooks/chained-agent-workflows.md"
    ).read_text()
    section = guide.split("## Serial collection bodies", 1)[1]
    source = section.split("```toml\n", 1)[1].split("```", 1)[0]
    parsed = parse_workflow_manifest(source)
    assert [stage.kind for stage in parsed.stages] == [
        "collection",
        "agent_dispatch",
        "human_gate",
    ]
    assert parsed.stages[0].iteration == {
        "max_items": 4,
        "items_path": "items",
        "body_end": "review",
    }
    assert parsed.stages[1].agent == "worker" and parsed.stages[1].max_attempts == 2
    assert parsed.stages[2].timeout == 3600
