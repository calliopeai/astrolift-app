/**
 * The org's fleet as the viz draws it (components/viz FleetSnapshot), for the
 * agent Overview's "In the fleet" panel: every agent from `agentFleet`, this
 * agent's recent runs as its manifest, and nothing invented. Pure.
 *
 * The fleet read carries no cluster per agent, so the groups the views draw
 * are the agents' projects ("No project" for the rest), each marked healthy:
 * the read has no group health to show.
 */
import type {
  FleetAgent,
  FleetCluster,
  FleetRun,
  FleetRunState,
  FleetSnapshot,
} from "@/components/viz/core/fleet-model";
import type { Health } from "@/components/viz/core/semantics";

export interface FleetAgentRow {
  id: string;
  name: string;
  projectSlug?: string | null;
  runningCount: number;
  runPaused: boolean;
  lastRunStatus?: string | null;
}

export interface FleetTaskRow {
  id: string;
  status: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
}

const FAILED = new Set(["failed", "timed_out", "error"]);
const NO_PROJECT = "__none";

/** What the agent's fleet dot says: failing, running, or idle. Paused reads idle. */
export function agentHealth(a: FleetAgentRow): Health {
  if (a.lastRunStatus && FAILED.has(a.lastRunStatus.toLowerCase())) return "failing";
  if (a.runningCount > 0) return "ok";
  return "idle";
}

const RUN_STATE: Record<string, FleetRunState> = {
  queued: "queued",
  pending: "queued",
  running: "in_flight",
  completed: "landed",
  succeeded: "landed",
  failed: "failed",
  timed_out: "failed",
};

const ms = (iso: string | null) => (iso ? new Date(iso).getTime() : undefined);

export function agentFleetSnapshot(
  agents: FleetAgentRow[],
  agentId: string,
  tasks: FleetTaskRow[],
  now: number
): FleetSnapshot {
  const groups = new Map<string, FleetCluster>();
  const fleet: FleetAgent[] = agents.map((a) => {
    const key = a.projectSlug || NO_PROJECT;
    if (!groups.has(key))
      groups.set(key, { id: key, name: a.projectSlug || "No project", health: "ok" });
    return {
      id: a.id,
      name: a.name,
      clusterId: key,
      health: agentHealth(a),
      load: Math.min(1, a.runningCount / 3),
      activeRuns: a.runningCount,
      queued: 0,
    };
  });
  const runs: FleetRun[] = tasks
    .filter((t) => RUN_STATE[t.status.toLowerCase()])
    .map((t) => ({
      id: t.id,
      agentId,
      label: t.id.slice(0, 8),
      state: RUN_STATE[t.status.toLowerCase()]!,
      startedAt: ms(t.startedAt),
      finishedAt: ms(t.finishedAt),
    }));
  return { now, clusters: [...groups.values()], agents: fleet, events: [], runs };
}
