"""Pinned Loop v1.2 control contract, not inferred generic graph cycling."""

import pytest

from workflows.importers.base import FlowImportError
from workflows.importers.flowise import FlowiseImporter
from workflows.manifest import emit_workflow_manifest, parse_workflow_manifest
from workflows.services.dsl_parser import emit_workflows_dsl, parse_workflows_dsl


def source_loop(*, cap=2, fallback="Review rounds complete"):
    return {
        "name": "Imported bounded loop",
        "nodes": [
            {"id": "startAgentflow_0", "data": {"name": "startAgentflow"}},
            {
                "id": "humanInputAgentflow_0",
                "data": {
                    "name": "humanInputAgentflow",
                    "label": "Continue this round?",
                },
            },
            {
                "id": "loopAgentflow_0",
                "data": {
                    "name": "loopAgentflow",
                    "version": 1.2,
                    "inputs": {
                        "loopBackToNode": "humanInputAgentflow_0-Continue this round?",
                        "maxLoopCount": cap,
                        "fallbackMessage": fallback,
                        "loopUpdateState": "[]",
                    },
                },
            },
        ],
        "edges": [
            {"source": "startAgentflow_0", "target": "humanInputAgentflow_0"},
            {"source": "humanInputAgentflow_0", "target": "loopAgentflow_0"},
        ],
    }


@pytest.mark.parametrize("fallback", [None, "", "Review rounds complete"])
def test_import_roundtrips_the_actual_bounded_loop_and_fallback(fallback):
    result = FlowiseImporter().import_flow(source_loop(fallback=fallback))
    assert len(result.manifest.stages) == 2
    edge = result.manifest.stages[1].back_edge
    assert edge == {
        "to": "flow_humaninputagentflow_0",
        "when": "always",
        "max_rounds": 2,
        "on_exhausted": "continue",
        "source_format": "flowise_loop_1_2",
        "source_target": "humanInputAgentflow_0",
        "source_label": "Continue this round?",
        "fallback_message": fallback,
    }
    assert (
        parse_workflow_manifest(emit_workflow_manifest(result.manifest))
        == result.manifest
    )
    assert not any(
        gap.code in ("graph_cycle", "routing_unsupported") for gap in result.gaps
    )
    definitions = parse_workflows_dsl("""workflows:
  - slug: imported-bounded-loop
    name: Imported bounded loop
    pattern_kind: chained
    stages:
      - kind: human_gate
        output_key: flow_humaninputagentflow_0
      - kind: checkpoint
        output_key: flow_loopagentflow_0
        back_edge:
          to: flow_humaninputagentflow_0
          when: always
          max_rounds: 2
          on_exhausted: continue
          source_format: flowise_loop_1_2
          source_target: humanInputAgentflow_0
          source_label: Continue this round?
          fallback_message: null
""")
    assert parse_workflows_dsl(emit_workflows_dsl(definitions)) == definitions


@pytest.mark.parametrize("cap", [0, True, -1, 21, 1.5, "1.5", "unbounded"])
def test_unbounded_or_ambiguous_source_caps_are_refused(cap):
    with pytest.raises(FlowImportError, match="bounded integer"):
        FlowiseImporter().import_flow(source_loop(cap=cap))


def test_source_default_and_absent_fallback_are_preserved():
    payload = source_loop()
    inputs = payload["nodes"][2]["data"]["inputs"]
    inputs.pop("maxLoopCount")
    inputs.pop("fallbackMessage")
    edge = FlowiseImporter().import_flow(payload).manifest.stages[1].back_edge
    assert edge["max_rounds"] == 5
    assert "fallback_message" not in edge


@pytest.mark.parametrize(
    "change",
    [
        "state",
        "start_state",
        "fallback_variable",
        "version",
        "missing_target",
        "forward_route",
        "cycle",
    ],
)
def test_unproved_source_scheduler_semantics_are_not_linearized(change):
    payload = source_loop()
    data = payload["nodes"][2]["data"]
    if change == "state":
        data["inputs"]["loopUpdateState"] = '[{"key":"iteration","value":1}]'
    if change == "start_state":
        payload["nodes"][0]["data"]["inputs"] = {
            "startState": [{"key": "iteration", "value": 1}]
        }
    if change == "fallback_variable":
        data["inputs"]["fallbackMessage"] = "{{ $flow.state.iteration }}"
    if change == "version":
        data["version"] = 2
    if change == "missing_target":
        data["inputs"]["loopBackToNode"] = "missing-Unknown"
    if change == "forward_route":
        payload["nodes"].append(
            {"id": "reply", "data": {"name": "directReplyAgentflow"}}
        )
        payload["edges"].append({"source": "loopAgentflow_0", "target": "reply"})
    if change == "cycle":
        payload["edges"].append(
            {"source": "loopAgentflow_0", "target": "humanInputAgentflow_0"}
        )
    with pytest.raises(FlowImportError):
        FlowiseImporter().import_flow(payload)


def test_imported_source_identity_cannot_rebind_to_a_different_target():
    from workflows.back_edges import LoopContractError, validate_back_edge

    edge = FlowiseImporter().import_flow(source_loop()).manifest.stages[1].back_edge
    with pytest.raises(LoopContractError, match="canonical return output key"):
        validate_back_edge({**edge, "to": "different-live-target"}, kind="checkpoint")


def test_source_continuation_is_not_a_native_review_default_or_unresolved_template():
    from workflows.back_edges import LoopContractError, validate_back_edge

    with pytest.raises(LoopContractError, match="supported imported"):
        validate_back_edge(
            {
                "to": "draft",
                "when": "gate_rejected",
                "max_rounds": 2,
                "on_exhausted": "continue",
            },
            kind="human_gate",
        )
    edge = FlowiseImporter().import_flow(source_loop()).manifest.stages[1].back_edge
    with pytest.raises(LoopContractError, match="runtime resolution"):
        validate_back_edge(
            {**edge, "fallback_message": "{{ $flow.state.value }}"}, kind="checkpoint"
        )
