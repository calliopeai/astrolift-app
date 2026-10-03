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

from workflows.back_edges import (
    LoopContractError,
    flowise_output_key,
    validate_loop_plan,
)
from workflows.importers._graph import downstream_stage_targets, topological_order
from workflows.importers.base import FlowImportError, FlowImportResult, ImportGap
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
LOOP = "loop"

_STAGE_CATEGORIES = {AGENT, AGGREGATION, GATE, LOOP}
_STAGE_KIND = {
    AGENT: WorkflowStage.StageKind.AGENT_DISPATCH.value,
    AGGREGATION: WorkflowStage.StageKind.AGGREGATION.value,
    GATE: WorkflowStage.StageKind.HUMAN_GATE.value,
    LOOP: WorkflowStage.StageKind.CHECKPOINT.value,
}


@dataclasses.dataclass(frozen=True)
class ClassifiedNode:
    """One source node reduced to the fields the builder needs."""

    id: str
    category: str  # one of the canonical categories above
    label: str  # human role / display name
    type: str  # raw source type, for gap reporting
    loop_control: dict | None = None


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
            t
            for t in downstream_stage_targets(node.id, stage_ids, edges)
            if by_id[t].category == AGENT
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
        raise FlowImportError(
            "flow graph has an unresolved cycle; configure an explicit bounded return instead of linearizing it"
        )

    ordered_stage_ids = [nid for nid in ordered if nid in stage_ids]
    loops = [node for node in nodes if node.loop_control is not None]
    if loops:
        if len(loops) != 1 or ordered_stage_ids[-1] != loops[0].id:
            raise FlowImportError(
                "only a terminal Flowise Loop on one sequential track has verified scheduler mapping"
            )
        for current, following in zip(ordered_stage_ids, ordered_stage_ids[1:]):
            if downstream_stage_targets(current, stage_ids, edges) != [following]:
                raise FlowImportError(
                    "Flowise bounded-loop stages must form one connected sequential track"
                )
        if len(by_id) != len(nodes) or any(
            source not in by_id or target not in by_id for source, target in edges
        ):
            raise FlowImportError(
                "bounded imported loops require unique nodes and complete edge identities"
            )
        if any(
            len(downstream_stage_targets(node, stage_ids, edges)) > 1
            for node in stage_ids
        ):
            raise FlowImportError(
                "bounded Flowise loops with parallel or conditional routes are not supported"
            )
        if any(node.category == ROUTING for node in nodes):
            raise FlowImportError(
                "conditional routes around a Flowise loop require source scheduler support"
            )
        if any(any(source == node.id for source, _ in edges) for node in loops):
            raise FlowImportError(
                "forward routes after a Flowise loop require source scheduler support"
            )
    output_keys = {node: flowise_output_key(node) for node in ordered_stage_ids}
    if loops and len(set(output_keys.values())) != len(output_keys):
        raise FlowImportError(
            "source node identities do not have unique stable output keys"
        )
    stages: list[WorkflowStageSpec] = []
    pattern = WorkflowDefinition.PatternKind.SINGLE.value
    for order, nid in enumerate(ordered_stage_ids):
        node = by_id[nid]
        fan_targets = downstream_stage_targets(nid, stage_ids, edges)
        fan_out: int | str = "dynamic" if len(fan_targets) > 1 else 0
        if fan_out == "dynamic":
            pattern = WorkflowDefinition.PatternKind.FAN_OUT.value
        back_edge = {}
        if node.loop_control is not None:
            target = node.loop_control["source_target"]
            if target not in ordered_stage_ids[:order]:
                raise FlowImportError(
                    "Flowise loop target must identify an earlier supported stage"
                )
            back_edge = {**node.loop_control, "to": output_keys[target]}
        stages.append(
            WorkflowStageSpec(
                order=order,
                kind=_STAGE_KIND[node.category],
                role=node.label if node.category != AGGREGATION else "",
                output_key=output_keys[nid] if loops else None,
                back_edge=back_edge,
                agent=None,
                skills=sorted(set(tool_skills.get(nid, []))),
                fan_out=fan_out,
                prompt=node.label if node.category == GATE else None,
            )
        )

    if pattern == WorkflowDefinition.PatternKind.SINGLE.value and len(stages) > 1:
        pattern = WorkflowDefinition.PatternKind.CHAINED.value

    if loops:
        try:
            validate_loop_plan(
                [dataclasses.asdict(stage) for stage in stages], pattern_kind=pattern
            )
        except LoopContractError as exc:
            raise FlowImportError(str(exc)) from exc

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
