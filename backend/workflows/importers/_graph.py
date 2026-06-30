"""Shared node/edge graph helpers for flow importers.

Visual builders express a workflow as a directed graph; Astrolift's stage
model is an ordered sequence. Both Langflow and Flowise need the same two
operations: a topological ordering of the nodes (to derive stage ``order``),
and downstream-reachability between the nodes that *became* stages (to fold
intermediate config nodes — a tool wired through a prompt into an agent — and
to detect fan-out). Kept format-agnostic and pure.
"""

from __future__ import annotations

from collections import defaultdict


def topological_order(node_ids: list[str], edges: list[tuple[str, str]]) -> tuple[list[str], bool]:
    """Kahn's algorithm over ``edges`` (``(source, target)``) restricted to
    ``node_ids``. Returns ``(ordered_ids, has_cycle)``. On a cycle the nodes
    still in the graph are appended in declaration order so the caller can
    fall back to a stable (if approximate) ordering rather than dropping them.
    """
    present = set(node_ids)
    indegree: dict[str, int] = {n: 0 for n in node_ids}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        if src in present and dst in present:
            adjacency[src].append(dst)
            indegree[dst] += 1

    # Preserve declaration order among the ready set for deterministic output.
    ready = [n for n in node_ids if indegree[n] == 0]
    ordered: list[str] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for nxt in adjacency[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)

    if len(ordered) < len(node_ids):
        # Cycle: append the unresolved nodes in declaration order.
        seen = set(ordered)
        ordered.extend(n for n in node_ids if n not in seen)
        return ordered, True
    return ordered, False


def downstream_stage_targets(
    source: str,
    stage_ids: set[str],
    edges: list[tuple[str, str]],
) -> list[str]:
    """Stage nodes reachable from ``source`` *without passing through another
    stage node*. Lets an adapter wire ``agent -> [prompt] -> agent`` as a
    direct stage→stage adjacency while ``stage A -> [tool], A -> B, A -> C``
    surfaces B and C as A's fan-out targets.
    """
    adjacency: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        adjacency[src].append(dst)

    found: list[str] = []
    seen: set[str] = {source}
    frontier = list(adjacency[source])
    while frontier:
        node = frontier.pop(0)
        if node in seen:
            continue
        seen.add(node)
        if node in stage_ids:
            if node not in found:
                found.append(node)
            continue  # don't traverse past a stage boundary
        frontier.extend(adjacency[node])
    return found
