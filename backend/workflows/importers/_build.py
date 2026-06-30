"""Shared graph → manifest builder for flow importers.

Both adapters reduce their native export to a list of *classified* nodes —
each tagged with one canonical category — plus the edge list, then hand off
here. This module owns the format-agnostic half: fold tools into the agent
they feed, order stages by topological sort, detect fan-out, and emit the
gap report. Keeping it in one place means a new format only writes a parser +
classifier, never the stage/ordering logic.
"""

from __future__ import annotations

import dataclasses

from django.utils.text import slugify

from workflows.importers._graph import downstream_stage_targets, topological_order
from workflows.importers.base import FlowImportResult, ImportGap
from workflows.manifest import (
    ParsedWorkflowManifest,
    WorkflowDefSpec,
    WorkflowStageSpec,
)
from workflows.models import WorkflowDefinition, WorkflowStage

# Canonical categories every adapter classifies its nodes into.
AGENT = "agent"
AGGREGATION = "aggregation"
GATE = "gate"
TOOL = "tool"
INPUT = "input"
OUTPUT = "output"
ROUTING = "routing"
OTHER = "other"

_STAGE_CATEGORIES = {AGENT, AGGREGATION, GATE}
_STAGE_KIND = {
    AGENT: WorkflowStage.StageKind.AGENT_DISPATCH.value,
    AGGREGATION: WorkflowStage.StageKind.AGGREGATION.value,
    GATE: WorkflowStage.StageKind.HUMAN_GATE.value,
}


@dataclasses.dataclass(frozen=True)
class ClassifiedNode:
    """One source node reduced to the fields the builder needs."""

    id: str
    category: str  # one of the canonical categories above
    label: str  # human role / display name
    type: str  # raw source type, for gap reporting


def build_result(
    nodes: list[ClassifiedNode],
    edges: list[tuple[str, str]],
    *,
    name: str,
    description: str,
    default_slug: str,
) -> FlowImportResult:
    """Assemble a manifest + gap report from classified nodes and edges."""
    by_id = {n.id: n for n in nodes}
    node_ids = [n.id for n in nodes]
    stage_ids = {n.id for n in nodes if n.category in _STAGE_CATEGORIES}
    gaps: list[ImportGap] = []

    # Tools fold into the agent stage(s) they feed.
    tool_skills: dict[str, list[str]] = {sid: [] for sid in stage_ids}
    for node in nodes:
        if node.category != TOOL:
            continue
        targets = [
            t for t in downstream_stage_targets(node.id, stage_ids, edges) if by_id[t].category == AGENT
        ]
        if not targets:
            gaps.append(
                ImportGap(
                    code="orphan_tool",
                    message=f"tool '{node.label}' feeds no agent; dropped",
                    node_id=node.id,
                    node_type=node.type,
                )
            )
            continue
        for tgt in targets:
            tool_skills[tgt].append(slugify(node.label) or "tool")

    # Non-stage, non-tool nodes → gaps.
    for node in nodes:
        if node.category in (INPUT, OUTPUT):
            gaps.append(
                ImportGap(
                    code=f"flow_{node.category}",
                    message=f"{node.category} node mapped to workflow {node.category}, not a stage",
                    node_id=node.id,
                    node_type=node.type,
                    severity="info",
                )
            )
        elif node.category == ROUTING:
            gaps.append(
                ImportGap(
                    code="routing_unsupported",
                    message=(
                        f"routing node '{node.label}' has no equivalent in the linear "
                        "stage model; branch/loop not translated"
                    ),
                    node_id=node.id,
                    node_type=node.type,
                )
            )
        elif node.category == OTHER:
            gaps.append(
                ImportGap(
                    code="unmapped_node",
                    message=f"node type '{node.type}' did not map to a stage; dropped",
                    node_id=node.id,
                    node_type=node.type,
                )
            )

    ordered, has_cycle = topological_order(node_ids, edges)
    if has_cycle:
        gaps.append(
            ImportGap(
                code="graph_cycle",
                message="flow graph has a cycle; stage order is approximate",
            )
        )

    ordered_stage_ids = [nid for nid in ordered if nid in stage_ids]
    stages: list[WorkflowStageSpec] = []
    pattern = WorkflowDefinition.PatternKind.SINGLE.value
    for order, nid in enumerate(ordered_stage_ids):
        node = by_id[nid]
        fan_targets = downstream_stage_targets(nid, stage_ids, edges)
        fan_out: int | str = "dynamic" if len(fan_targets) > 1 else 0
        if fan_out == "dynamic":
            pattern = WorkflowDefinition.PatternKind.FAN_OUT.value
        stages.append(
            WorkflowStageSpec(
                order=order,
                kind=_STAGE_KIND[node.category],
                role=node.label if node.category != AGGREGATION else "",
                agent=None,
                skills=sorted(set(tool_skills.get(nid, []))),
                fan_out=fan_out,
                prompt=node.label if node.category == GATE else None,
            )
        )

    if pattern == WorkflowDefinition.PatternKind.SINGLE.value and len(stages) > 1:
        pattern = WorkflowDefinition.PatternKind.CHAINED.value

    definition = WorkflowDefSpec(
        slug=slugify(name) or default_slug,
        name=name,
        pattern=pattern,
        description=description,
    )
    return FlowImportResult(
        manifest=ParsedWorkflowManifest(definition=definition, stages=stages),
        gaps=gaps,
    )
