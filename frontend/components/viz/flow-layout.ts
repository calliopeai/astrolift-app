export interface RankLayoutOptions {
  colWidth?: number;
  rowHeight?: number;
}

interface RankEdge {
  source: string;
  target: string;
}

/**
 * Longest-path layered DAG layout: left-to-right by dependency depth.
 *
 * Rank 0 holds the roots (no incoming edge); every other node sits one column
 * right of its deepest predecessor. Nodes sharing a rank stack vertically,
 * centred. Deterministic, so the graph is stable across renders without pinning
 * positions in storage. Cycles are broken defensively (the iteration is capped)
 * so a malformed `needs` can never hang the layout.
 */
export function rankLayout(
  nodeIds: string[],
  edges: RankEdge[],
  { colWidth = 240, rowHeight = 96 }: RankLayoutOptions = {},
): Map<string, { x: number; y: number }> {
  const ids = new Set(nodeIds);
  const preds = new Map<string, string[]>();
  for (const id of nodeIds) preds.set(id, []);
  for (const e of edges) {
    // Ignore edges that reference unknown nodes.
    if (ids.has(e.source) && ids.has(e.target)) preds.get(e.target)!.push(e.source);
  }

  // Relax ranks until stable: rank(n) = max(rank(pred)) + 1. Capped at
  // nodeIds.length passes — a DAG converges within that; a cycle stops early.
  const rank = new Map<string, number>();
  for (const id of nodeIds) rank.set(id, 0);
  for (let pass = 0; pass < nodeIds.length; pass++) {
    let changed = false;
    for (const id of nodeIds) {
      const parents = preds.get(id)!;
      if (parents.length === 0) continue;
      const next = Math.max(...parents.map((p) => rank.get(p)! + 1));
      if (next > rank.get(id)!) {
        rank.set(id, next);
        changed = true;
      }
    }
    if (!changed) break;
  }

  // Bucket by rank in input order, then position centred within each column.
  const byRank = new Map<number, string[]>();
  for (const id of nodeIds) {
    const r = rank.get(id)!;
    (byRank.get(r) ?? byRank.set(r, []).get(r)!).push(id);
  }

  const pos = new Map<string, { x: number; y: number }>();
  for (const [r, column] of byRank) {
    const offset = ((column.length - 1) * rowHeight) / 2;
    column.forEach((id, i) => {
      pos.set(id, { x: r * colWidth, y: i * rowHeight - offset });
    });
  }
  return pos;
}
