import type { AppEdge, AppNode, AppNodeRole, AppSnapshot } from "../../core/app-model";
import type { Health } from "../../core/semantics";

/**
 * The numbers the auto layouts show, derived only from an AppSnapshot. Pure,
 * so every layout reads the same totals and the tests pin the rules.
 *
 * Error thresholds: an edge over 5% failed calls is failing (the model's own
 * rule, see AppEdge.errorRate); over 1% it reads as degraded.
 */

export const FAILING_ERROR_RATE = 0.05;
export const DEGRADED_ERROR_RATE = 0.01;
/** Queue fill above which a queue that is also gaining messages reads as backing up. */
export const QUEUE_BACKING_UP_FILL = 0.5;
/** Points kept in a live sparkline. */
export const SERIES_LENGTH = 30;

export function healthForErrorRate(rate: number, rps: number): Health {
  if (rate > FAILING_ERROR_RATE) return "failing";
  if (rate > DEGRADED_ERROR_RATE) return "degraded";
  return rps > 0 ? "ok" : "idle";
}

/** The worse of two health states (failing > degraded > ok > idle). */
export function worstHealth(a: Health, b: Health): Health {
  const rank: Record<Health, number> = { idle: 0, ok: 1, degraded: 2, failing: 3 };
  return rank[a] >= rank[b] ? a : b;
}

function weighted(edges: AppEdge[]): { rps: number; errorRate: number } {
  const rps = edges.reduce((s, e) => s + e.rps, 0);
  const errors = edges.reduce((s, e) => s + e.rps * e.errorRate, 0);
  return { rps, errorRate: rps > 0 ? errors / rps : 0 };
}

export function edgesInto(snapshot: AppSnapshot, id: string): AppEdge[] {
  return snapshot.edges.filter((e) => e.to === id);
}

export function edgesOutOf(snapshot: AppSnapshot, id: string): AppEdge[] {
  return snapshot.edges.filter((e) => e.from === id);
}

export function nodesWithRole(snapshot: AppSnapshot, ...roles: AppNodeRole[]): AppNode[] {
  return snapshot.nodes.filter((n) => roles.includes(n.role));
}

export interface NodeStats {
  node: AppNode;
  /** Calls per second arriving at the node. */
  inRps: number;
  /** Calls per second the node makes onward. */
  outRps: number;
  /** rps-weighted failed share of calls arriving at the node. */
  errorRate: number;
  /** The node's own health, worsened by the error rate of its inbound calls. */
  health: Health;
}

export function nodeStats(snapshot: AppSnapshot, node: AppNode): NodeStats {
  const inbound = weighted(edgesInto(snapshot, node.id));
  const outRps = edgesOutOf(snapshot, node.id).reduce((s, e) => s + e.rps, 0);
  const fromErrors =
    inbound.rps > 0 ? healthForErrorRate(inbound.errorRate, inbound.rps) : ("idle" as Health);
  return {
    node,
    inRps: inbound.rps,
    outRps,
    errorRate: inbound.errorRate,
    health: worstHealth(node.health, fromErrors === "ok" ? "idle" : fromErrors),
  };
}

export interface AppTotals {
  /** Requests per second arriving from outside (edges leaving ingress/trigger nodes). */
  rps: number;
  /** rps-weighted p50 across services, ms; null when no service reports one. */
  p50: number | null;
  /** Failed share of the outside requests. */
  errorRate: number;
  replicas: { ready: number; desired: number };
  health: Health;
}

export function appTotals(snapshot: AppSnapshot): AppTotals {
  const entry = new Set(nodesWithRole(snapshot, "ingress", "trigger").map((n) => n.id));
  const front = weighted(snapshot.edges.filter((e) => entry.has(e.from)));
  const services = nodesWithRole(snapshot, "service");
  const withP50 = services.filter((s) => s.p50 !== undefined);
  const p50Weight = withP50.reduce((s, n) => s + (n.rps ?? 0), 0);
  const p50 =
    withP50.length === 0
      ? null
      : p50Weight > 0
        ? withP50.reduce((s, n) => s + (n.p50 ?? 0) * (n.rps ?? 0), 0) / p50Weight
        : withP50.reduce((s, n) => s + (n.p50 ?? 0), 0) / withP50.length;
  const replicas = replicaTotals(services);
  let health = healthForErrorRate(front.errorRate, front.rps);
  for (const s of services) health = worstHealth(health, s.health);
  if (replicas.ready < replicas.desired) health = worstHealth(health, "degraded");
  return { rps: front.rps, p50, errorRate: front.errorRate, replicas, health };
}

export function replicaTotals(nodes: AppNode[]): { ready: number; desired: number } {
  return nodes.reduce(
    (acc, n) => ({
      ready: acc.ready + (n.replicas?.ready ?? 0),
      desired: acc.desired + (n.replicas?.desired ?? 0),
    }),
    { ready: 0, desired: 0 }
  );
}

export interface QueueStats {
  node: AppNode;
  /** Messages per second enqueued. */
  inRps: number;
  /** Messages per second consumed. */
  outRps: number;
  /** Enqueued minus consumed: above zero the consumers are falling behind. */
  net: number;
  /** 0..1 fill of the queue (the node's load). */
  fill: number;
  /** A queue gaining messages while past half full is held work: it reads as degraded. */
  backingUp: boolean;
  health: Health;
}

export function queueStats(snapshot: AppSnapshot): QueueStats | null {
  const q = nodesWithRole(snapshot, "queue")[0];
  if (!q) return null;
  const inbound = weighted(edgesInto(snapshot, q.id));
  const outRps = edgesOutOf(snapshot, q.id).reduce((s, e) => s + e.rps, 0);
  const net = inbound.rps - outRps;
  const backingUp = net > 0 && q.load > QUEUE_BACKING_UP_FILL;
  let health = worstHealth(q.health, healthForErrorRate(inbound.errorRate, inbound.rps));
  if (backingUp) health = worstHealth(health, "degraded");
  return { node: q, inRps: inbound.rps, outRps, net, fill: q.load, backingUp, health };
}

export interface HotSpot extends NodeStats {
  /** Heat used for ranking: error rate dominates, latency breaks ties. */
  score: number;
}

/**
 * Nodes ranked by how much they need a look: inbound error rate first
 * (scaled so 1% of errors outweighs 100 ms), then p50 latency.
 */
export function hotSpots(snapshot: AppSnapshot, limit = 5): HotSpot[] {
  return snapshot.nodes
    .filter((n) => n.role !== "ingress" && n.role !== "trigger" && n.role !== "schedule")
    .map((n) => {
      const s = nodeStats(snapshot, n);
      const failingBoost = n.health === "failing" ? 1000 : 0;
      return { ...s, score: failingBoost + s.errorRate * 10_000 + (n.p50 ?? 0) };
    })
    .sort((a, b) => b.score - a.score || a.node.name.localeCompare(b.node.name))
    .slice(0, limit);
}

export interface EdgeRow {
  edge: AppEdge;
  fromName: string;
  toName: string;
  health: Health;
  /** 0..1 of the busiest edge, for bar widths and flow speed. */
  share: number;
}

export function edgeRows(snapshot: AppSnapshot): EdgeRow[] {
  const name = new Map(snapshot.nodes.map((n) => [n.id, n.name]));
  const max = maxEdgeRps(snapshot);
  return [...snapshot.edges]
    .sort((a, b) => b.rps - a.rps)
    .map((edge) => ({
      edge,
      fromName: name.get(edge.from) ?? edge.from,
      toName: name.get(edge.to) ?? edge.to,
      health: healthForErrorRate(edge.errorRate, edge.rps),
      share: max > 0 ? edge.rps / max : 0,
    }));
}

export function maxEdgeRps(snapshot: AppSnapshot): number {
  return snapshot.edges.reduce((m, e) => Math.max(m, e.rps), 0);
}

/** Append to a rolling series, keeping the last `max` points. */
export function pushSeries(prev: number[], value: number, max = SERIES_LENGTH): number[] {
  const next = [...prev, value];
  return next.length > max ? next.slice(next.length - max) : next;
}

export function fmtRps(n: number): string {
  if (n >= 10_000) return `${(n / 1000).toFixed(0)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

export function fmtPct(rate: number): string {
  const pct = rate * 100;
  return `${pct < 10 ? pct.toFixed(1) : Math.round(pct)}%`;
}

export function fmtMs(ms: number | null | undefined): string {
  return ms === null || ms === undefined ? "n/a" : `${Math.round(ms)} ms`;
}
