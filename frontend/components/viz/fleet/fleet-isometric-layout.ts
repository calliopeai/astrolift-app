import type { FleetAgent, FleetCluster, FleetSnapshot } from "../core/fleet-model";
import { depth, iso, type Point } from "../core/iso";

/**
 * Pure geometry for the isometric fleet: where each cluster's platform and
 * each agent's box sit in world units, how tall a box is for a load, and the
 * screen bounds the SVG viewBox needs. No React, so it is tested directly.
 */

/** World units: one grid cell per agent. */
export const CELL = 1;
/** A box's footprint inside its cell. */
export const BOX = 0.62;
/** Padding between a platform's edge and its first row of boxes. */
export const PLATFORM_PAD = 0.35;
/** The slab's own thickness, drawn below z = 0. */
export const SLAB = 0.28;
/** Space between platforms on the ground. */
export const PLATFORM_GAP = 1.4;
/** Idle boxes stay this low. */
export const IDLE_HEIGHT = 0.1;
/** Height of a box at load 0 and load 1 (world units). */
export const MIN_HEIGHT = 0.22;
export const MAX_HEIGHT = 2.2;
/** Screen room kept above the tallest box for a rising spark. */
export const SPARK_HEADROOM = 52;

export interface IsoPlatform {
  cluster: FleetCluster;
  x: number;
  y: number;
  w: number;
  d: number;
  /** Agents on this platform, in painter's order (far first). */
  boxes: IsoAgentBox[];
}

export interface IsoAgentBox {
  agent: FleetAgent;
  /** World position of the box's far corner. */
  x: number;
  y: number;
}

/** Box height for an agent: idle is low, otherwise height is load. */
export function boxHeight(agent: Pick<FleetAgent, "health" | "load">): number {
  if (agent.health === "idle") return IDLE_HEIGHT;
  const load = Math.max(0, Math.min(1, agent.load));
  return MIN_HEIGHT + load * (MAX_HEIGHT - MIN_HEIGHT);
}

/** Columns for a square-ish grid of `n` items. */
export function gridCols(n: number): number {
  return Math.max(1, Math.ceil(Math.sqrt(n)));
}

/**
 * Lay clusters out as platforms on a grid of platforms, each holding its
 * agents on a grid of cells. Every platform gets the same pitch so the fleet
 * reads as one site; platforms come out far-first.
 */
export function layoutFleet(snapshot: Pick<FleetSnapshot, "clusters" | "agents">): IsoPlatform[] {
  const byCluster = new Map<string, FleetAgent[]>();
  for (const c of snapshot.clusters) byCluster.set(c.id, []);
  for (const a of snapshot.agents) byCluster.get(a.clusterId)?.push(a);

  const largest = Math.max(1, ...Array.from(byCluster.values(), (list) => list.length));
  const cols = gridCols(largest);
  const rows = Math.ceil(largest / cols);
  const w = cols * CELL + PLATFORM_PAD * 2;
  const d = rows * CELL + PLATFORM_PAD * 2;
  const platformCols = gridCols(snapshot.clusters.length);

  const platforms = snapshot.clusters.map((cluster, i) => {
    const px = (i % platformCols) * (w + PLATFORM_GAP);
    const py = Math.floor(i / platformCols) * (d + PLATFORM_GAP);
    const agents = byCluster.get(cluster.id) ?? [];
    const inset = PLATFORM_PAD + (CELL - BOX) / 2;
    const boxes = agents
      .map((agent, j) => ({
        agent,
        x: px + inset + (j % cols) * CELL,
        y: py + inset + Math.floor(j / cols) * CELL,
      }))
      .sort((a, b) => depth(a.x, a.y) - depth(b.x, b.y));
    return { cluster, x: px, y: py, w, d, boxes };
  });
  return platforms.sort((a, b) => depth(a.x, a.y) - depth(b.x, b.y));
}

export interface ScreenBounds {
  minX: number;
  minY: number;
  width: number;
  height: number;
}

/** Screen bounds of every platform at full box height, plus label and spark room. */
export function fleetBounds(platforms: IsoPlatform[], unit: number): ScreenBounds {
  const pts: Point[] = [];
  for (const p of platforms) {
    const x1 = p.x + p.w;
    const y1 = p.y + p.d;
    pts.push(
      iso(p.x, p.y, MAX_HEIGHT, unit),
      iso(x1, p.y, MAX_HEIGHT, unit),
      iso(p.x, y1, MAX_HEIGHT, unit),
      iso(p.x, y1, -SLAB, unit),
      iso(x1, p.y, -SLAB, unit),
      iso(x1, y1, -SLAB, unit)
    );
  }
  if (pts.length === 0) return { minX: 0, minY: 0, width: 1, height: 1 };
  const xs = pts.map((pt) => pt[0]);
  const ys = pts.map((pt) => pt[1]);
  const pad = unit;
  const minX = Math.min(...xs) - pad;
  const minY = Math.min(...ys) - SPARK_HEADROOM;
  // Cluster labels hang below each platform's front corner.
  const maxY = Math.max(...ys) + pad * 1.4;
  return { minX, minY, width: Math.max(...xs) + pad - minX, height: maxY - minY };
}

export interface FleetSummary {
  agents: number;
  clusters: number;
  failing: number;
  degraded: number;
  busy: number;
  idle: number;
}

export function summarizeFleet(snapshot: Pick<FleetSnapshot, "clusters" | "agents">): FleetSummary {
  const count = (pred: (a: FleetAgent) => boolean) => snapshot.agents.filter(pred).length;
  return {
    agents: snapshot.agents.length,
    clusters: snapshot.clusters.length,
    failing: count((a) => a.health === "failing"),
    degraded: count((a) => a.health === "degraded"),
    busy: count((a) => a.activeRuns > 0),
    idle: count((a) => a.health === "idle"),
  };
}

/** The root aria-label: the state in one sentence. */
export function fleetAriaLabel(s: FleetSummary): string {
  const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  return (
    `Isometric fleet: ${plural(s.agents, "agent")} across ${plural(s.clusters, "cluster")}, ` +
    `${s.failing} failing, ${s.degraded} degraded, ${s.busy} busy, ${s.idle} idle`
  );
}

/** Cut a label to `max` characters with an ellipsis; the full text goes in a <title>. */
export function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`;
}

/** Agent ids with a run_started event within `windowMs` of the snapshot. */
export function recentStarts(
  snapshot: Pick<FleetSnapshot, "events" | "now">,
  windowMs: number
): { id: string; agentId: string }[] {
  return snapshot.events
    .filter((e) => e.kind === "run_started" && snapshot.now - e.at < windowMs)
    .map((e) => ({ id: e.id, agentId: e.agentId }));
}
