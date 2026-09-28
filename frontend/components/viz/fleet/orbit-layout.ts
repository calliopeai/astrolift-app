import type { FleetCluster, FleetSnapshot } from "../core/fleet-model";
import type { Health } from "../core/semantics";

/**
 * Geometry for the orbit fleet view, kept pure so it can be tested and so the
 * animation loop only reads numbers. Clusters are planets on a grid; each
 * agent is a satellite on one of up to three rings, and a failing agent drops
 * to a low, tight decay orbit just above the planet.
 */

/** Width and height of one planet's cell, in viewBox units. */
export const CELL_W = 260;
export const CELL_H = 290;
export const PLANET_R = 34;
/** Radii of the healthy rings, innermost first. */
export const RING_R = [62, 86, 110] as const;
/** The decay orbit a failing agent sinks to. */
export const DECAY_R = PLANET_R + 12;
/** Radians per second at full load; an idle agent does not move. */
export const MAX_OMEGA = 0.9;
/** How long a launch trail stays drawn under full motion (the rise lasts 1.4s). */
export const LAUNCH_MS = 2_000;

export interface OrbitSatellite {
  id: string;
  name: string;
  health: Health;
  load: number;
  activeRuns: number;
  queued: number;
  /** Position within its cluster, stable across snapshots. */
  order: number;
  /** Planet centre. */
  cx: number;
  cy: number;
  /** Orbit radius. */
  r: number;
  /** Where it sits when still: evenly spaced on its ring. */
  phase: number;
  /** Radians per second, proportional to load. */
  omega: number;
}

export interface OrbitPlanet {
  cluster: FleetCluster;
  cx: number;
  cy: number;
  /** How many healthy rings are drawn (1-3). */
  rings: number;
  hasDecay: boolean;
  /** Mean agent load, 0..1: how bright the atmosphere is. */
  meanLoad: number;
  agents: number;
  busy: number;
  failing: number;
  queued: number;
}

export interface OrbitLaunch {
  id: string;
  agentId: string;
  /** Where the trail leaves the planet's upper limb. */
  x: number;
  y: number;
}

export interface OrbitLayout {
  width: number;
  height: number;
  cols: number;
  planets: OrbitPlanet[];
  satellites: OrbitSatellite[];
}

/** How many rings a cluster of `n` agents needs. */
export function ringCount(n: number): number {
  if (n <= 10) return 1;
  if (n <= 26) return 2;
  return 3;
}

/**
 * Deal `n` agents onto `rings` rings in proportion to each ring's
 * circumference, so outer rings hold more and spacing stays even.
 */
export function ringSlots(n: number, rings: number): number[] {
  const radii = RING_R.slice(0, rings);
  const total = radii.reduce((s, r) => s + r, 0);
  const slots = radii.map((r) => Math.floor((n * r) / total));
  let left = n - slots.reduce((s, c) => s + c, 0);
  for (let i = rings - 1; left > 0; i = (i - 1 + rings) % rings, left--) slots[i]++;
  return slots;
}

/** Load drives speed; idle agents are parked. */
export function angularSpeed(health: Health, load: number): number {
  if (health === "idle") return 0;
  return Math.max(0, Math.min(1, load)) * MAX_OMEGA;
}

export function layoutOrbit(snapshot: FleetSnapshot, maxCols = 3): OrbitLayout {
  const n = snapshot.clusters.length;
  const cols = Math.max(1, Math.min(n, maxCols));
  const rows = Math.max(1, Math.ceil(n / cols));
  const byCluster = new Map<string, FleetSnapshot["agents"]>();
  for (const a of snapshot.agents) {
    const list = byCluster.get(a.clusterId) ?? [];
    list.push(a);
    byCluster.set(a.clusterId, list);
  }

  const planets: OrbitPlanet[] = [];
  const satellites: OrbitSatellite[] = [];
  snapshot.clusters.forEach((cluster, i) => {
    const cx = (i % cols) * CELL_W + CELL_W / 2;
    const cy = Math.floor(i / cols) * CELL_H + RING_R[2] + 16;
    const agents = byCluster.get(cluster.id) ?? [];
    const rings = ringCount(agents.length);
    const slots = ringSlots(agents.length, rings);

    // Ring by the agent's stable position in its cluster, so one agent failing
    // leaves a gap instead of reshuffling everyone else.
    let ring = 0;
    let slot = 0;
    const decay = agents.filter((a) => a.health === "failing");
    agents.forEach((a, order) => {
      while (ring < rings - 1 && slot >= slots[ring]) {
        ring++;
        slot = 0;
      }
      const count = Math.max(1, slots[ring]);
      const failing = a.health === "failing";
      const phase = failing
        ? -Math.PI / 2 + (decay.indexOf(a) * 2 * Math.PI) / decay.length
        : // Offset each ring a little so rings do not line up in spokes.
          -Math.PI / 2 + ((slot + ring * 0.37) * 2 * Math.PI) / count;
      satellites.push({
        id: a.id,
        name: a.name,
        health: a.health,
        load: a.load,
        activeRuns: a.activeRuns,
        queued: a.queued,
        order,
        cx,
        cy,
        r: failing ? DECAY_R : RING_R[ring],
        phase,
        omega: angularSpeed(a.health, a.load),
      });
      slot++;
    });

    planets.push({
      cluster,
      cx,
      cy,
      rings,
      hasDecay: decay.length > 0,
      meanLoad: agents.length ? agents.reduce((s, a) => s + a.load, 0) / agents.length : 0,
      agents: agents.length,
      busy: agents.filter((a) => a.activeRuns > 0).length,
      failing: decay.length,
      queued: agents.reduce((s, a) => s + a.queued, 0),
    });
  });

  return { width: cols * CELL_W, height: rows * CELL_H, cols, planets, satellites };
}

/**
 * Launches to draw: run_started events younger than `windowMs`, placed on the
 * upper limb of the agent's planet. Under reduced motion the caller passes a
 * longer window and keeps one per agent, as a still spark.
 */
export function recentLaunches(
  snapshot: FleetSnapshot,
  layout: OrbitLayout,
  windowMs: number,
  onePerAgent = false
): OrbitLaunch[] {
  const sats = new Map(layout.satellites.map((s) => [s.id, s]));
  const seen = new Set<string>();
  const out: OrbitLaunch[] = [];
  for (let i = snapshot.events.length - 1; i >= 0; i--) {
    const e = snapshot.events[i];
    if (e.kind !== "run_started") continue;
    if (snapshot.now - e.at >= windowMs || e.at > snapshot.now) continue;
    const sat = sats.get(e.agentId);
    if (!sat) continue;
    if (onePerAgent && seen.has(e.agentId)) continue;
    seen.add(e.agentId);
    // Spread launch points across the limb by agent, golden-ratio stepped.
    const t = ((sat.order * 0.618034 + 0.25) % 1) - 0.5;
    const dx = t * 1.3 * PLANET_R;
    out.push({
      id: e.id,
      agentId: e.agentId,
      x: sat.cx + dx,
      y: sat.cy - Math.sqrt(PLANET_R * PLANET_R - dx * dx),
    });
  }
  return out.reverse();
}

/** The whole state in one sentence, for the root aria-label. */
export function summarizeFleet(snapshot: FleetSnapshot): string {
  const { agents, clusters } = snapshot;
  const count = (h: Health) => agents.filter((a) => a.health === h).length;
  const busy = agents.filter((a) => a.activeRuns > 0).length;
  const parts = [
    `${agents.length} agent${agents.length === 1 ? "" : "s"} across ${clusters.length} cluster${clusters.length === 1 ? "" : "s"}`,
    `${count("failing")} failing`,
    `${count("degraded")} degraded`,
    `${busy} busy`,
    `${count("idle")} idle`,
  ];
  const down = clusters.filter((c) => c.health !== "ok").length;
  if (down) parts.push(`${down} cluster${down === 1 ? "" : "s"} not healthy`);
  return parts.join(", ");
}
