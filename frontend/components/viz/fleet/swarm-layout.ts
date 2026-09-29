import type { FleetAgent, FleetSnapshot } from "../core/fleet-model";
import { mulberry32 } from "../core/semantics";

/**
 * Pure geometry behind the swarm view: where each hive (cluster) sits, how
 * each agent swarms around it, and where a dispatch pulse is along a
 * tendril. Everything that moves is a function of model state:
 *
 *   angular speed  ← load (idle agents do not move)
 *   swarm radius   ← health (failing agents drift out to the edge)
 *   tendril width  ← active runs
 *   pulse          ← a dispatched event, travelling hub → hive → agent
 */

export const VIEW_W = 1000;
export const VIEW_H = 620;
export const HUB = { x: VIEW_W / 2, y: VIEW_H / 2 };

export interface HivePlace {
  id: string;
  name: string;
  x: number;
  y: number;
  /** Resting swarm radius around the hive. */
  r: number;
}

export interface SwarmerSeed {
  id: string;
  clusterId: string;
  /** Starting angle around the hive. */
  phase: number;
  /** Radius jitter so the swarm is not a ring (0.75..1.15). */
  spread: number;
  /** Breathing frequency, rad/s, so agents do not move in lockstep. */
  wobble: number;
}

/** Hives on a ring around the hub, sized to how many agents they carry. */
export function placeHives(snapshot: FleetSnapshot): HivePlace[] {
  const counts = new Map<string, number>();
  for (const a of snapshot.agents) counts.set(a.clusterId, (counts.get(a.clusterId) ?? 0) + 1);
  const n = snapshot.clusters.length;
  if (n === 0) return [];
  const ring = n === 1 ? 0 : Math.min(VIEW_W, VIEW_H) * (n <= 3 ? 0.36 : 0.4);
  return snapshot.clusters.map((c, i) => {
    const angle = -Math.PI / 2 + (i / n) * Math.PI * 2;
    const agents = counts.get(c.id) ?? 0;
    return {
      id: c.id,
      name: c.name,
      x: HUB.x + Math.cos(angle) * ring,
      y: HUB.y + Math.sin(angle) * ring * 0.82,
      r: Math.min(n > 4 ? 80 : 130, 40 + Math.sqrt(agents) * 17),
    };
  });
}

/** Stable per-agent randomness, seeded by the agent id so it survives snapshots. */
export function seedSwarmer(agent: FleetAgent, index: number, siblings: number): SwarmerSeed {
  let h = 2166136261;
  for (let i = 0; i < agent.id.length; i++) h = Math.imul(h ^ agent.id.charCodeAt(i), 16777619);
  const rng = mulberry32(h >>> 0);
  return {
    id: agent.id,
    clusterId: agent.clusterId,
    phase: (index / Math.max(1, siblings)) * Math.PI * 2 + rng() * 0.6,
    spread: 0.75 + rng() * 0.4,
    wobble: 0.6 + rng() * 0.9,
  };
}

/** Radians per second around the hive. Idle agents hold still. */
export function swarmSpeed(agent: FleetAgent): number {
  if (agent.health === "idle") return 0;
  return 0.12 + agent.load * 0.9;
}

/** How far out the agent swarms: failing agents drift to the edge. */
export function swarmRadius(agent: FleetAgent, hive: HivePlace, seed: SwarmerSeed): number {
  const base = hive.r * seed.spread;
  if (agent.health === "failing") return hive.r * 1.55;
  if (agent.health === "idle") return base * 0.7;
  return base;
}

/** The agent's target point at time `t` seconds (reduced motion passes t = 0). */
export function swarmTarget(
  agent: FleetAgent,
  hive: HivePlace,
  seed: SwarmerSeed,
  angle: number,
  t: number
): { x: number; y: number } {
  const breathe =
    agent.health === "idle" ? 0 : Math.sin(t * seed.wobble + seed.phase) * 6 * (0.4 + agent.load);
  const r = swarmRadius(agent, hive, seed) + breathe;
  return { x: hive.x + Math.cos(angle) * r, y: hive.y + Math.sin(angle) * r * 0.86 };
}

/** Tendril width from active runs. */
export function tendrilWidth(activeRuns: number): number {
  return Math.min(4, 0.8 + activeRuns * 0.7);
}

/** A point on the quadratic curve p0 → c → p1 at s (0..1). */
export function quadPoint(
  p0: { x: number; y: number },
  c: { x: number; y: number },
  p1: { x: number; y: number },
  s: number
): { x: number; y: number } {
  const u = 1 - s;
  return {
    x: u * u * p0.x + 2 * u * s * c.x + s * s * p1.x,
    y: u * u * p0.y + 2 * u * s * c.y + s * s * p1.y,
  };
}

/** How long a dispatch pulse takes: first half on the hub tendril, second on the agent's. */
export const PULSE_MS = 1400;

/** Recent dispatches still travelling, with their progress 0..1. */
export function travellingPulses(snapshot: FleetSnapshot, nowMs: number) {
  return snapshot.events
    .filter((e) => e.kind === "dispatched")
    .map((e) => ({ id: e.id, agentId: e.agentId, s: (nowMs - e.at) / PULSE_MS }))
    .filter((p) => p.s >= 0 && p.s <= 1);
}
