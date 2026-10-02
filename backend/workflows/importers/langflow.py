"""Langflow flow importer (#985).

Maps a **Langflow** flow export → an Astrolift workflow manifest.

Langflow export shape
---------------------
``{"name", "description", "data": {"nodes": [...], "edges": [...]}}``. Each
node is ``{"id": "<Type>-<rand>", "data": {"type": "<ComponentType>",
"node": {"display_name": ...}}}``; each edge is ``{"source": <id>, "target":
<id>}`` (data flows source → target).

Mapping table (Langflow component → Astrolift stage)
----------------------------------------------------
============================  ==========================  ===================
Langflow component (by type)  Astrolift                   Notes
============================  ==========================  ===================
``*Agent*`` (Agent, CrewAI…)  ``agent_dispatch`` stage    role = display name
``*Tool*`` (search, REPL, …)  → ``skill_refs`` of the     folded into the
                              agent it feeds              downstream agent
``Combine*`` / ``Merge*``     ``aggregation`` stage       merges fan-out
``*Human*`` / ``*Approval*``  ``human_gate`` stage        prompt = label
``*Input*`` (Chat/Text)       workflow entry              info gap (not a
                                                          stage)
``*Output*`` (Chat/Text)      workflow exit               info gap
``Conditional`` / ``IfElse``  —                           unmappable: the
``*Router*`` / ``Loop*``                                  linear stage model
``Condition`` / ``Listen``                                has no branch/loop
                                                          → warning gap
prompt / model / memory /     —                           config of an agent,
vectorstore / everything else                             not a stage → gap
============================  ==========================  ===================

Order comes from a topological sort of the graph; consecutive agent stages →
``chained``, a stage fanning out to several downstream stages → ``fan_out``.
Every construct that did not become a stage surfaces as an ``ImportGap``
(``info`` for benign input/output nodes, ``warning`` for genuine losses).
"""

from __future__ import annotations

import re
from typing import Any

from workflows.importers import _build as build
from workflows.importers._build import ClassifiedNode
from workflows.importers.base import FlowImporter, FlowImportError, FlowImportResult


def _classify(node_type: str) -> str:
    """Map a Langflow component type to a canonical builder category."""
    t = node_type.lower()
    if "agent" in t:
        return build.AGENT
    if "tool" in t:
        return build.TOOL
    if t.startswith(("combine", "merge")) or "aggregat" in t:
        return build.AGGREGATION
    if "human" in t or "approval" in t:
        return build.GATE
    if "input" in t:
        return build.INPUT
    if "output" in t:
        return build.OUTPUT
    if any(k in t for k in ("conditional", "ifelse", "router", "loop", "listen", "notify", "condition")):
        return build.ROUTING
    return build.OTHER


class LangflowImporter(FlowImporter):
    format = "langflow"

    def _analyze(self, payload: dict[str, Any]) -> FlowImportResult:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FlowImportError("not a Langflow export: missing 'data' object")
        raw_nodes = data.get("nodes")
        raw_edges = data.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise FlowImportError("not a Langflow export: 'data.nodes'/'data.edges' must be lists")

        if any(isinstance(node, dict) and "loop" in _node_type(node).lower() for node in raw_nodes):
            from workflows.importers.langflow_collections import import_collection

            return import_collection(payload, raw_nodes, raw_edges)

        nodes: list[ClassifiedNode] = []
        for n in raw_nodes:
            if not isinstance(n, dict) or not n.get("id"):
                continue
            node_type = _node_type(n)
            nodes.append(
                ClassifiedNode(
                    id=str(n["id"]),
                    category=_classify(node_type),
                    label=_label(n),
                    type=node_type,
                )
            )

        edges = [
            (str(e["source"]), str(e["target"]))
            for e in raw_edges
            if isinstance(e, dict) and e.get("source") and e.get("target")
        ]

        name = str(payload.get("name") or "Imported Langflow flow")
        return build.build_result(
            nodes,
            edges,
            name=name,
            description=str(payload.get("description") or ""),
            default_slug="imported-langflow-flow",
        )


def _node_type(node: dict[str, Any]) -> str:
    data = node.get("data")
    return str(data.get("type") or "") if isinstance(data, dict) else ""


def _label(node: dict[str, Any]) -> str:
    """Human label: the component's display_name, else its type, else id."""
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    inner = data.get("node") if isinstance(data.get("node"), dict) else {}
    display = inner.get("display_name") if isinstance(inner, dict) else None
    label = display or _node_type(node) or node.get("id") or "node"
    return re.sub(r"\s+", " ", str(label)).strip()
