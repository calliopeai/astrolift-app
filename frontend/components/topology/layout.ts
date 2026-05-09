import type { TopologyNode, TopologyNodeType } from "./types";

// Each tier sits at a fixed x-offset; nodes within a tier stack
// vertically. Deterministic — same inputs always produce the same
// layout, which keeps the visual stable across renders without us
// having to pin positions in storage.
const TIER_BY_TYPE: Record<TopologyNodeType, number> = {
  ingress: 0,
  service: 1,
  workload: 2,
  "managed-service": 3,
  cache: 3,
  external: 4,
};

const COL_WIDTH = 260;
const ROW_HEIGHT = 110;

export interface PositionedNode extends TopologyNode {
  position: { x: number; y: number };
}

/**
 * Layered left-to-right layout:
 *   ingress → services → workloads → managed services / external
 *
 * Override per-node by passing `{ ...node, position }` to the map; the
 * auto-layout only fills missing positions, so manual overrides win.
 */
export function autoLayout(nodes: TopologyNode[]): PositionedNode[] {
  const byTier: Record<number, TopologyNode[]> = {};
  for (const node of nodes) {
    const tier = TIER_BY_TYPE[node.type] ?? 5;
    (byTier[tier] ||= []).push(node);
  }
  return nodes.map((node) => {
    const tier = TIER_BY_TYPE[node.type] ?? 5;
    const tierNodes = byTier[tier];
    const idx = tierNodes.indexOf(node);
    const offset = (tierNodes.length - 1) * ROW_HEIGHT;
    return {
      ...node,
      position: { x: tier * COL_WIDTH, y: idx * ROW_HEIGHT - offset / 2 },
    };
  });
}
