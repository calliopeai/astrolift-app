"""Framework + adapter coverage for the visual-flow importers (#984/#985/#986).

Pure mapping logic — no DB. Verifies each adapter's node→stage mapping, stage
ordering, fan-out/gate detection, the gap report for unmappable constructs,
and the format registry dispatch."""

from __future__ import annotations

import pytest

from workflows.importers import FlowImportError, available_formats, import_flow
from workflows.importers.registry import get_importer
from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.tests.importer_fixtures import (
    flowise_chained,
    flowise_gate,
    langflow_chained,
    langflow_fan_out,
)

AGENT = WorkflowStage.StageKind.AGENT_DISPATCH.value
GATE = WorkflowStage.StageKind.HUMAN_GATE.value
AGG = WorkflowStage.StageKind.AGGREGATION.value


# ---- registry ---------------------------------------------------------------


def test_registry_lists_both_adapters():
    assert available_formats() == ["flowise", "langflow"]


def test_registry_dispatches_by_format():
    assert get_importer("langflow").format == "langflow"
    assert get_importer("flowise").format == "flowise"


def test_unknown_format_raises_clear_error():
    with pytest.raises(FlowImportError) as exc:
        import_flow("n8n", {"data": {"nodes": [], "edges": []}})
    assert "unknown flow format" in str(exc.value)
    assert "langflow" in str(exc.value) and "flowise" in str(exc.value)


def test_malformed_payload_raises():
    with pytest.raises(FlowImportError):
        import_flow("langflow", {"not": "a flow"})


# ---- Langflow ---------------------------------------------------------------


def test_langflow_chained_maps_ordered_agent_stages():
    result = import_flow("langflow", langflow_chained())
    defn = result.manifest.definition
    stages = result.manifest.stages

    assert defn.pattern == WorkflowDefinition.PatternKind.CHAINED.value
    assert [s.kind for s in stages] == [AGENT, AGENT]
    assert [s.role for s in stages] == ["Implementer", "Reviewer"]
    assert [s.order for s in stages] == [0, 1]
    # The search tool folded into the implementer it feeds.
    assert stages[0].skills == ["web-search"]
    # Input/output nodes are reported (info), not silently dropped.
    codes = {(g.code, g.severity) for g in result.gaps}
    assert ("flow_input", "info") in codes
    assert ("flow_output", "info") in codes


def test_langflow_fan_out_and_aggregation_with_routing_gap():
    result = import_flow("langflow", langflow_fan_out())
    defn = result.manifest.definition
    stages = result.manifest.stages

    assert defn.pattern == WorkflowDefinition.PatternKind.FAN_OUT.value
    # planner fans out to the two workers.
    assert stages[0].role == "Planner"
    assert stages[0].fan_out == "dynamic"
    assert [s.kind for s in stages] == [AGENT, AGENT, AGENT, AGG]
    # The conditional router is unmappable → a warning gap, not a stage.
    routing = [g for g in result.gaps if g.code == "routing_unsupported"]
    assert len(routing) == 1
    assert routing[0].severity == "warning"
    assert routing[0].node_type == "ConditionalRouter"


# ---- Flowise ----------------------------------------------------------------


def test_flowise_chained_maps_agentflow_v2():
    result = import_flow("flowise", flowise_chained())
    stages = result.manifest.stages

    assert result.manifest.definition.pattern == WorkflowDefinition.PatternKind.CHAINED.value
    assert [s.kind for s in stages] == [AGENT, AGENT]
    assert [s.role for s in stages] == ["Researcher", "Writer"]
    assert stages[0].skills == ["search-tool"]
    # start/directReply are entry/exit, reported as info gaps.
    codes = {g.code for g in result.gaps}
    assert "flow_input" in codes and "flow_output" in codes


def test_flowise_gate_maps_human_input_with_memory_gap():
    result = import_flow("flowise", flowise_gate())
    stages = result.manifest.stages

    assert [s.kind for s in stages] == [AGENT, GATE, AGENT]
    gate = stages[1]
    assert gate.role == "Approve Draft"
    assert gate.prompt == "Approve Draft"
    # The memory node has no stage equivalent → warning gap.
    unmapped = [g for g in result.gaps if g.code == "unmapped_node"]
    assert len(unmapped) == 1
    assert unmapped[0].node_type == "bufferMemory"
    assert unmapped[0].severity == "warning"


# ---- protocol parity (gap_report / to_manifest share one pass) --------------


def test_importer_protocol_methods_agree():
    importer = get_importer("flowise")
    payload = flowise_gate()
    manifest = importer.to_manifest(payload)
    gaps = importer.gap_report(payload)
    bundle = importer.import_flow(payload)
    assert bundle.manifest.stages == manifest.stages
    assert bundle.gaps == gaps
