import { mulberry32, type Health } from "./semantics";

/**
 * The one data model every fleet view draws (orbit, heartbeat, hive,
 * manifest, isometric, graph, list). A hook maps GraphQL into this; stories
 * drive it with `makeFleet` + `stepFleet`.
 */

export interface FleetCluster {
  id: string;
  name: string;
  region?: string;
  health: "ok" | "degraded" | "offline";
}

export interface FleetAgent {
  id: string;
  name: string;
  clusterId: string;
  health: Health;
  /** 0..1: how busy the agent is right now. */
  load: number;
  activeRuns: number;
  queued: number;
}

export type FleetEventKind = "dispatched" | "run_started" | "run_finished" | "run_failed";

export interface FleetEvent {
  id: string;
  kind: FleetEventKind;
  agentId: string;
  /** Epoch ms. */
  at: number;
}

export type FleetRunState = "queued" | "holding" | "lifted_off" | "in_flight" | "landed" | "failed";

/** A run as the manifest board shows it. */
export interface FleetRun {
  id: string;
  agentId: string;
  label: string;
  state: FleetRunState;
  /** When a queued run is due to start (T-minus). */
  etaAt?: number;
  startedAt?: number;
  finishedAt?: number;
  /** Why a run is holding (a gate, a quota). */
  holdReason?: string;
}

export interface FleetSnapshot {
  /** Epoch ms the snapshot describes; renderers age events against it. */
  now: number;
  clusters: FleetCluster[];
  agents: FleetAgent[];
  /** Recent events, oldest first. Renderers ignore ones older than they show. */
  events: FleetEvent[];
  runs: FleetRun[];
}

/** Props every fleet renderer takes, so the view switcher can swap them freely. */
export interface FleetViewProps {
  snapshot: FleetSnapshot;
  motion: "full" | "reduced";
  onSelectAgent?: (agentId: string) => void;
  selectedAgentId?: string;
  className?: string;
}

const AGENT_NAMES = [
  "triage",
  "bdr-outreach",
  "release-notes",
  "cost-watch",
  "pr-review",
  "incident-scribe",
  "docs-sync",
  "lead-scout",
  "backup-verify",
  "k8s-janitor",
  "invoice-chaser",
  "support-first-line",
];
const CLUSTER_NAMES = ["prod-east", "prod-west", "edge-eu", "staging", "gpu-pool", "on-prem-slc"];
const RUN_LABELS = [
  "nightly sweep",
  "PR #4821",
  "lead batch",
  "cost report",
  "ticket 1182",
  "release 3.4",
];

export interface MakeFleetOptions {
  clusters?: number;
  agentsPerCluster?: number;
  seed?: number;
  /** Start with this share of agents failing (an incident). */
  failing?: number;
  /** Start everyone idle (a quiet night). */
  idle?: boolean;
  now?: number;
}

export function makeFleet({
  clusters = 3,
  agentsPerCluster = 5,
  seed = 7,
  failing = 0,
  idle = false,
  now = Date.UTC(2026, 8, 28, 14, 0, 0),
}: MakeFleetOptions = {}): FleetSnapshot {
  const rng = mulberry32(seed);
  const cs: FleetCluster[] = Array.from({ length: clusters }, (_, i) => ({
    id: `c${i}`,
    name: CLUSTER_NAMES[i % CLUSTER_NAMES.length] + (i >= CLUSTER_NAMES.length ? `-${i}` : ""),
    region: ["us-east-1", "us-west-2", "eu-west-1"][i % 3],
    health: "ok",
  }));
  const agents: FleetAgent[] = [];
  cs.forEach((c, ci) => {
    for (let j = 0; j < agentsPerCluster; j++) {
      const n = ci * agentsPerCluster + j;
      const load = idle ? 0 : rng();
      const isFailing = rng() < failing;
      agents.push({
        id: `a${n}`,
        name:
          AGENT_NAMES[n % AGENT_NAMES.length] +
          (n >= AGENT_NAMES.length ? `-${Math.floor(n / AGENT_NAMES.length)}` : ""),
        clusterId: c.id,
        health: isFailing
          ? "failing"
          : idle || load < 0.08
            ? "idle"
            : load > 0.85
              ? "degraded"
              : "ok",
        load,
        activeRuns: idle ? 0 : Math.round(load * 3),
        queued: idle ? 0 : Math.floor(rng() * 3),
      });
    }
  });
  if (failing > 0) cs[0].health = "degraded";
  const runs: FleetRun[] = agents.slice(0, 8).map((a, i) => ({
    id: `r${i}`,
    agentId: a.id,
    label: RUN_LABELS[i % RUN_LABELS.length],
    state: (
      [
        "queued",
        "holding",
        "lifted_off",
        "in_flight",
        "in_flight",
        "landed",
        "landed",
        "failed",
      ] as const
    )[i],
    etaAt: now + (i + 1) * 45_000,
    startedAt: i >= 2 ? now - (8 - i) * 60_000 : undefined,
    finishedAt: i >= 5 ? now - (8 - i) * 20_000 : undefined,
    holdReason: i === 1 ? "Waiting on approval" : undefined,
  }));
  return { now, clusters: cs, agents, events: [], runs };
}

/** How long renderers keep an event around. The heartbeat wall fades over this. */
export const EVENT_WINDOW_MS = 12_000;

/**
 * Advance a fleet by `dtMs`: loads drift, some agents start or finish runs,
 * and each of those emits an event. Pure; the rng carries the randomness.
 */
export function stepFleet(prev: FleetSnapshot, rng: () => number, dtMs: number): FleetSnapshot {
  const now = prev.now + dtMs;
  const events = prev.events.filter((e) => now - e.at < EVENT_WINDOW_MS);
  let seq = prev.events.length ? Number(prev.events[prev.events.length - 1].id.slice(1)) + 1 : 0;
  const agents = prev.agents.map((a) => {
    if (a.health === "failing") {
      // Failing agents mostly stay failing; some recover.
      return rng() < 0.08 ? { ...a, health: "ok" as Health, load: 0.2 } : a;
    }
    const drift = (rng() - 0.5) * 0.3;
    const load = Math.max(0, Math.min(1, a.load + drift));
    let { activeRuns } = a;
    if (rng() < load * 0.35) {
      events.push({ id: `e${seq++}`, kind: "dispatched", agentId: a.id, at: now - 300 });
      events.push({ id: `e${seq++}`, kind: "run_started", agentId: a.id, at: now });
      activeRuns += 1;
    }
    if (activeRuns > 0 && rng() < 0.3) {
      const failed = rng() < 0.06;
      events.push({
        id: `e${seq++}`,
        kind: failed ? "run_failed" : "run_finished",
        agentId: a.id,
        at: now,
      });
      activeRuns -= 1;
      if (failed) return { ...a, load, activeRuns, health: "failing" as Health };
    }
    const health: Health =
      load < 0.08 && activeRuns === 0 ? "idle" : load > 0.85 ? "degraded" : "ok";
    return { ...a, load, activeRuns, health };
  });
  return { ...prev, now, agents, events };
}
