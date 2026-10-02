"""Flowise flow importer (#986).

Maps a **Flowise** chatflow/agentflow export → an Astrolift workflow
manifest. Flowise's format evolves (classic chatflow vs. AgentFlow v2), so
the classifier keys off both ``data.name`` and ``data.category`` and falls
through to a gap rather than guessing — "whatever comes out of Flowise".

Flowise export shape
--------------------
``{"nodes": [...], "edges": [...]}`` (sometimes under a top-level
``"flowData"`` string or ``"chatflow"`` object). Each node is ``{"id",
"data": {"name": "toolAgent", "category": "Agents", "label": "Tool
Agent"}}``; each edge is ``{"source": <id>, "target": <id>}``.

Mapping table (Flowise node → Astrolift stage)
----------------------------------------------
==================================  =========================  ==============
Flowise node (name / category)      Astrolift                  Notes
==================================  =========================  ==============
``agentAgentflow`` / ``llmAgentflow``  ``agent_dispatch`` stage  AgentFlow v2
category ``Agents`` / ``Chains``    ``agent_dispatch`` stage   classic flow
``humanInputAgentflow``             ``human_gate`` stage       prompt = label
category ``Tools`` / ``toolAgentflow``  → ``skill_refs``        folded into
                                                              the agent
``startAgentflow``                  workflow entry             info gap
``directReplyAgentflow``            workflow exit              info gap
``conditionAgentflow`` / ``ifElse``  —                         branch/loop →
``loopAgentflow`` v1.2            ``checkpoint`` control   bounded terminal loop
``iteration*``                                              warning gap
``stickyNoteAgentflow``             —                          info gap
chat model / memory / vector store  —                          agent config,
/ embeddings / retriever / etc.                               not a stage →
                                                              warning gap
==================================  =========================  ==============

Ordering, fan-out detection, and gap reporting are shared with the Langflow
adapter via :mod:`workflows.importers._build`.
"""

from __future__ import annotations

from typing import Any

from workflows.importers import _build as build
from workflows.importers._build import ClassifiedNode
from workflows.importers.base import FlowImporter, FlowImportError, FlowImportResult

# AgentFlow v2 node names (``data.name``) → canonical category.
_AGENTFLOW = {
    "agentAgentflow": build.AGENT,
    "llmAgentflow": build.AGENT,
    "toolAgentflow": build.TOOL,
    "humanInputAgentflow": build.GATE,
    "startAgentflow": build.INPUT,
    "directReplyAgentflow": build.OUTPUT,
    "conditionAgentflow": build.ROUTING,
    "conditionAgentAgentflow": build.ROUTING,
    "loopAgentflow": build.LOOP,
    "iterationAgentflow": build.ROUTING,
    "stickyNoteAgentflow": build.OTHER,
}

# Classic chatflow ``data.category`` → canonical category.
_CATEGORY = {
    "agents": build.AGENT,
    "chains": build.AGENT,
    "tools": build.TOOL,
    "multi agents": build.AGENT,
}


def _classify(node_name: str, category: str) -> str:
    """Map a Flowise node to a canonical builder category."""
    if node_name in _AGENTFLOW:
        return _AGENTFLOW[node_name]

    cat = (category or "").lower()
    if cat in _CATEGORY:
        return _CATEGORY[cat]

    name = node_name.lower()
    if "agent" in name:
        return build.AGENT
    if "tool" in name:
        return build.TOOL
    if "ifelse" in name or "condition" in name:
        return build.ROUTING
    if name.startswith("chatprompt") or "humaninput" in name:
        return build.GATE if "human" in name else build.OTHER
    # chat models, memory, vector stores, embeddings, retrievers, parsers…
    return build.OTHER


class FlowiseImporter(FlowImporter):
    format = "flowise"

    def _analyze(self, payload: dict[str, Any]) -> FlowImportResult:
        graph = _unwrap_graph(payload)
        raw_nodes = graph.get("nodes")
        raw_edges = graph.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise FlowImportError("not a Flowise export: 'nodes'/'edges' must be lists")

        nodes: list[ClassifiedNode] = []
        for n in raw_nodes:
            if not isinstance(n, dict) or not n.get("id"):
                continue
            data = n.get("data") if isinstance(n.get("data"), dict) else {}
            node_name = str(data.get("name") or "")
            category = str(data.get("category") or "")
            label = str(data.get("label") or node_name or n["id"])
            nodes.append(
                ClassifiedNode(
                    id=str(n["id"]),
                    category=_classify(node_name, category),
                    label=label,
                    type=node_name or category or "unknown",
                    loop_control=_loop_control(data)
                    if node_name == "loopAgentflow"
                    else None,
                )
            )

        edges = [
            (str(e["source"]), str(e["target"]))
            for e in raw_edges
            if isinstance(e, dict) and e.get("source") and e.get("target")
        ]

        if any(node.loop_control for node in nodes) and any(
            isinstance(raw, dict)
            and isinstance(raw.get("data"), dict)
            and raw["data"].get("name") == "startAgentflow"
            and isinstance(raw["data"].get("inputs"), dict)
            and raw["data"]["inputs"].get("startState")
            for raw in raw_nodes
        ):
            raise FlowImportError(
                "Flowise loop runtime state requires source scheduler support"
            )
        name = str(payload.get("name") or graph.get("name") or "Imported Flowise flow")
        return build.build_result(
            nodes,
            edges,
            name=name,
            description=str(payload.get("description") or ""),
            default_slug="imported-flowise-flow",
        )


def _unwrap_graph(payload: dict[str, Any]) -> dict[str, Any]:
    """Find the ``{nodes, edges}`` graph. Flowise exports it inline, nested
    under ``flowData`` (a JSON string), or under ``chatflow``/``graph``."""
    if isinstance(payload.get("nodes"), list):
        return payload
    flow_data = payload.get("flowData")
    if isinstance(flow_data, str):
        import json

        try:
            parsed = json.loads(flow_data)
        except json.JSONDecodeError as exc:
            raise FlowImportError(f"flowData is not valid JSON: {exc}") from exc
        if isinstance(parsed, dict):
            return parsed
    for key in ("chatflow", "graph"):
        nested = payload.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("nodes"), list):
            return nested
    raise FlowImportError("not a Flowise export: no 'nodes'/'edges' graph found")


def _loop_control(data: dict) -> dict:
    """Loop v1.2 output/counter contract, without state-update or scheduler guesses."""
    if data.get("version") != 1.2:
        raise FlowImportError(
            "Flowise Loop mapping supports the verified node version 1.2"
        )
    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        raise FlowImportError("Flowise Loop requires explicit input configuration")
    state = inputs.get("loopUpdateState")
    if state:
        if isinstance(state, str):
            import json

            try:
                state = json.loads(state)
            except ValueError as exc:
                raise FlowImportError(
                    "Flowise loop state updates are not supported"
                ) from exc
        if state != []:
            raise FlowImportError("Flowise loop state updates are not supported")
    target = inputs.get("loopBackToNode")
    if not isinstance(target, str) or not target:
        raise FlowImportError("Flowise Loop requires an exact return node")
    # Verified at Flowise 9291856d1ea4a4ceea9f8fef8ce14f4f6c81e8eb:
    # packages/components/nodes/agentflow/Loop/Loop.ts and server buildAgentflow.ts.
    # The source component extracts the node ID and first label segment.
    source_parts = target.split("-")
    source_target = source_parts[0]
    source_label = source_parts[1] if len(source_parts) > 1 else "undefined"
    cap = inputs.get("maxLoopCount", 5)
    if cap in (None, ""):
        cap = 5
    if isinstance(cap, str) and cap.isascii() and cap.isdigit():
        cap = int(cap)
    if type(cap) is not int or not 1 <= cap <= 20:
        raise FlowImportError(
            "Flowise Loop maxLoopCount must be a bounded integer between 1 and 20"
        )
    edge = {
        "when": "always",
        "max_rounds": cap,
        "on_exhausted": "continue",
        "source_format": "flowise_loop_1_2",
        "source_target": source_target,
        "source_label": source_label,
    }
    if "fallbackMessage" in inputs:
        fallback = inputs["fallbackMessage"]
        if fallback is not None and (
            not isinstance(fallback, str) or len(fallback) > 4096
        ):
            raise FlowImportError(
                "Flowise Loop fallbackMessage must be a bounded string or null"
            )
        if isinstance(fallback, str) and "{{" in fallback:
            raise FlowImportError(
                "Flowise Loop fallback variables require source runtime resolution"
            )
        edge["fallback_message"] = fallback
    return edge
