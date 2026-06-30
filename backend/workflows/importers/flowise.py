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
``loopAgentflow`` / ``iteration*``                            warning gap
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
    "loopAgentflow": build.ROUTING,
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
                )
            )

        edges = [
            (str(e["source"]), str(e["target"]))
            for e in raw_edges
            if isinstance(e, dict) and e.get("source") and e.get("target")
        ]

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
