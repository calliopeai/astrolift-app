import {
  EVENT_WINDOW_MS,
  type FleetAgent,
  type FleetCluster,
  type FleetEvent,
  type FleetSnapshot,
} from "../core/fleet-model";

/**
 * Pure layout for the heartbeat wall: which events are still on screen, where
 * each one sits on its row, and what the whole fleet adds up to. The renderer
 * only draws what this returns.
 */

export interface HeartbeatDot {
  event: FleetEvent;
  /** ms since the event, against snapshot.now. */
  age: number;
  /** Horizontal position as a percent of the track: 100 = now, 0 = a window ago. */
  x: number;
  /** Still-frame opacity for reduced motion: 1 fresh, 0 a window old. */
  opacity: number;
}

export interface HeartbeatRow {
  agent: FleetAgent;
  dots: HeartbeatDot[];
  /** The agent is doing work right now, so its row glows. */
  busy: boolean;
}

export interface HeartbeatGroup {
  cluster: FleetCluster;
  rows: HeartbeatRow[];
}

/** An agent is busy when it has runs in flight or real load; idle rows stay dark. */
export function isBusy(agent: FleetAgent): boolean {
  return agent.health !== "idle" && (agent.activeRuns > 0 || agent.load >= 0.08);
}

export function placeEvent(
  event: FleetEvent,
  now: number,
  windowMs = EVENT_WINDOW_MS
): HeartbeatDot | null {
  const age = now - event.at;
  // Events from the future (clock skew) sit at "now"; expired ones drop off.
  const clamped = Math.max(0, age);
  if (clamped >= windowMs) return null;
  const fraction = clamped / windowMs;
  return { event, age: clamped, x: 100 - fraction * 100, opacity: 1 - fraction };
}

export function layoutHeartbeat(
  snapshot: FleetSnapshot,
  windowMs = EVENT_WINDOW_MS
): HeartbeatGroup[] {
  const byAgent = new Map<string, HeartbeatDot[]>();
  for (const event of snapshot.events) {
    const dot = placeEvent(event, snapshot.now, windowMs);
    if (!dot) continue;
    const list = byAgent.get(event.agentId);
    if (list) list.push(dot);
    else byAgent.set(event.agentId, [dot]);
  }
  const rowsByCluster = new Map<string, HeartbeatRow[]>();
  for (const agent of snapshot.agents) {
    const row: HeartbeatRow = { agent, dots: byAgent.get(agent.id) ?? [], busy: isBusy(agent) };
    const list = rowsByCluster.get(agent.clusterId);
    if (list) list.push(row);
    else rowsByCluster.set(agent.clusterId, [row]);
  }
  return snapshot.clusters
    .map((cluster) => ({ cluster, rows: rowsByCluster.get(cluster.id) ?? [] }))
    .filter((g) => g.rows.length > 0);
}

/** The sentence a screen reader hears for the whole wall. */
export function summarizeHeartbeat(snapshot: FleetSnapshot, windowMs = EVENT_WINDOW_MS): string {
  const agents = snapshot.agents.length;
  const failing = snapshot.agents.filter((a) => a.health === "failing").length;
  const busy = snapshot.agents.filter(isBusy).length;
  const recent = snapshot.events.filter((e) => placeEvent(e, snapshot.now, windowMs)).length;
  return `Heartbeat: ${agents} agents, ${failing} failing, ${busy} busy, ${recent} events in the last ${Math.round(windowMs / 1000)}s`;
}

/** Row tint strength: busier rows glow brighter, capped so text stays legible. */
export function rowGlowPercent(agent: FleetAgent): number {
  if (!isBusy(agent)) return 0;
  return Math.round(6 + Math.max(0, Math.min(1, agent.load)) * 14);
}
