import type { FleetAgent, FleetCluster } from "../core/fleet-model";

/**
 * Honeycomb packing for the hive fleet view. Each cluster is a compact patch
 * of pointy-top hexagons: the first cell is the cluster's dispatcher (the
 * hub), the agents follow. Patches flow left to right and wrap to the width.
 * Pure, so the renderer only draws.
 */

export interface HiveCell {
  /** The agent id, or null for the cluster's hub cell. */
  agentId: string | null;
  clusterId: string;
  x: number;
  y: number;
}

export interface HiveGroup {
  clusterId: string;
  name: string;
  /** Top-left of the label row. */
  x: number;
  y: number;
  width: number;
  hub: HiveCell;
  cells: HiveCell[];
}

export interface HiveLayout {
  r: number;
  groups: HiveGroup[];
  /** Agent id to its cell, for dispatch ripples. */
  byAgent: Map<string, HiveCell>;
  width: number;
  height: number;
}

export const LABEL_H = 16;
const GAP = 14;

/** Hex radius by fleet size: big tiles for a handful, small ones for 500. */
export function hexRadius(agentCount: number): number {
  if (agentCount > 150) return 8;
  if (agentCount > 60) return 11;
  if (agentCount > 20) return 14;
  return 18;
}

/** The six corners of a pointy-top hexagon centred on the origin, as a path. */
export function hexPath(r: number): string {
  const pts = Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 3) * i - Math.PI / 2;
    return `${(r * Math.cos(a)).toFixed(2)},${(r * Math.sin(a)).toFixed(2)}`;
  });
  return `M${pts.join("L")}Z`;
}

export function layoutHive(
  clusters: FleetCluster[],
  agents: FleetAgent[],
  width: number,
  r = hexRadius(agents.length)
): HiveLayout {
  const w = Math.sqrt(3) * r;
  const rowH = 1.5 * r;
  const byCluster = new Map<string, FleetAgent[]>(clusters.map((c) => [c.id, []]));
  for (const a of agents) byCluster.get(a.clusterId)?.push(a);

  const byAgent = new Map<string, HiveCell>();
  const groups: HiveGroup[] = [];
  let cx = 0;
  let cy = 0;
  let shelfH = 0;
  const maxCols = Math.max(2, Math.floor((width - w / 2) / w));

  for (const c of clusters) {
    const members = byCluster.get(c.id) ?? [];
    const n = members.length + 1;
    // Wider than tall reads better under a label; never wider than the view.
    const cols = Math.min(maxCols, Math.max(2, Math.ceil(Math.sqrt(n * 1.6))));
    const rows = Math.ceil(n / cols);
    const patchW = cols * w + (rows > 1 ? w / 2 : 0);
    const patchH = LABEL_H + (rows - 1) * rowH + 2 * r;
    if (cx > 0 && cx + patchW > width) {
      cx = 0;
      cy += shelfH + GAP;
      shelfH = 0;
    }
    const cells: HiveCell[] = [];
    for (let i = 0; i < n; i++) {
      const col = i % cols;
      const row = Math.floor(i / cols);
      cells.push({
        agentId: i === 0 ? null : members[i - 1].id,
        clusterId: c.id,
        x: cx + col * w + (row % 2) * (w / 2) + w / 2,
        y: cy + LABEL_H + row * rowH + r,
      });
    }
    for (const cell of cells) if (cell.agentId) byAgent.set(cell.agentId, cell);
    groups.push({
      clusterId: c.id,
      name: c.name,
      x: cx,
      y: cy,
      width: patchW,
      hub: cells[0],
      cells: cells.slice(1),
    });
    cx += patchW + GAP;
    shelfH = Math.max(shelfH, patchH);
  }

  return { r, groups, byAgent, width, height: Math.max(cy + shelfH, 2 * r) };
}

/** Fill strength for a tile: idle stays near the card, full load is bright. */
export function loadMix(load: number): number {
  const clamped = Math.max(0, Math.min(1, load));
  return Math.round(8 + clamped * 62);
}

/** Cut a label to what fits, with an ellipsis. */
export function truncate(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  return `${text.slice(0, Math.max(1, maxChars - 1))}…`;
}
