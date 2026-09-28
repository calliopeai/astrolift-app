import type { FleetRun, FleetRunState, FleetSnapshot } from "../core/fleet-model";
import { mulberry32, type Health } from "../core/semantics";

/**
 * Pure logic behind the launch manifest: how a run's status reads, how its
 * clock reads, and (for stories) how the run queue moves. `stepFleet` leaves
 * runs alone, so the Live story composes it with `stepRuns`.
 */

export const RUN_STATE_LABEL: Record<FleetRunState, string> = {
  queued: "Queued",
  holding: "Holding",
  lifted_off: "Lifted off",
  in_flight: "In flight",
  landed: "Landed",
  failed: "Failed",
};

/** Which status colour a run state carries. Queued is waiting, so it stays dark. */
export const RUN_STATE_HEALTH: Record<FleetRunState, Health> = {
  queued: "idle",
  holding: "degraded",
  lifted_off: "ok",
  in_flight: "ok",
  landed: "ok",
  failed: "failing",
};

/** States worth a glow: something is moving, or something broke. Landed and queued stay dark. */
export const RUN_STATE_GLOWS: Record<FleetRunState, boolean> = {
  queued: false,
  holding: false,
  lifted_off: true,
  in_flight: true,
  landed: false,
  failed: true,
};

/** `mm:ss`, or `h:mm:ss` past an hour. Negative spans read as zero. */
export function formatSpan(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mmss = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return h > 0 ? `${h}:${mmss}` : mmss;
}

/** Wall-clock time in UTC, `HH:MM:SS`, so stories render the same everywhere. */
export function formatClock(at: number): string {
  return new Date(at).toISOString().slice(11, 19);
}

/** The T-minus / time column: T-minus while waiting, then the UTC clock time it lifted off or finished. */
export function runClock(run: FleetRun, now: number): string {
  switch (run.state) {
    case "queued":
    case "holding":
      return run.etaAt === undefined ? "T- --:--" : `T-${formatSpan(run.etaAt - now)}`;
    case "lifted_off":
    case "in_flight":
      return run.startedAt === undefined ? "--:--:--" : formatClock(run.startedAt);
    case "landed":
    case "failed":
      return run.finishedAt === undefined ? "--:--:--" : formatClock(run.finishedAt);
  }
}

/** The detail after the status word: why it holds, or how long it has flown. */
export function runDetail(run: FleetRun, now: number): string | undefined {
  if (run.state === "holding") return run.holdReason;
  if (run.state === "in_flight" && run.startedAt !== undefined) {
    return formatSpan(now - run.startedAt);
  }
  return undefined;
}

/** When the run entered its current state, if the model says. */
export function stateSince(run: FleetRun): number | undefined {
  if (run.state === "landed" || run.state === "failed") return run.finishedAt;
  if (run.state === "lifted_off" || run.state === "in_flight") return run.startedAt;
  return undefined;
}

/** Board order: scheduled departure time, so rows stay put while statuses flip. */
export function boardOrder(runs: FleetRun[]): FleetRun[] {
  const key = (r: FleetRun) => r.etaAt ?? r.startedAt ?? r.finishedAt ?? 0;
  return [...runs].sort((a, b) => key(a) - key(b) || a.id.localeCompare(b.id));
}

/** "12 runs: 2 queued, 1 holding, 3 in flight, 4 landed, 2 failed". */
export function summarizeRuns(runs: FleetRun[]): string {
  const counts: Record<FleetRunState, number> = {
    queued: 0,
    holding: 0,
    lifted_off: 0,
    in_flight: 0,
    landed: 0,
    failed: 0,
  };
  for (const r of runs) counts[r.state] += 1;
  const parts = (Object.keys(counts) as FleetRunState[])
    .filter((s) => counts[s] > 0)
    .map((s) => `${counts[s]} ${RUN_STATE_LABEL[s].toLowerCase()}`);
  const head = `${runs.length} run${runs.length === 1 ? "" : "s"}`;
  return parts.length ? `${head}: ${parts.join(", ")}` : `${head} on the board`;
}

const LABELS = [
  "nightly sweep",
  "PR #4821",
  "lead batch",
  "cost report",
  "ticket 1182",
  "release 3.4",
  "backup check",
  "index rebuild",
];
const HOLD_REASONS = ["Waiting on approval", "GPU quota", "Gate: change freeze", "Rate limited"];

/** How long a run shows "Lifted off" before it reads as in flight. */
export const LIFTOFF_MS = 4_000;
/** How long a finished run stays on the board. */
export const ARRIVALS_MS = 60_000;

export interface MakeRunsOptions {
  count: number;
  seed?: number;
  /** Relative weights for each starting state. */
  mix?: Partial<Record<FleetRunState, number>>;
}

const DEFAULT_MIX: Record<FleetRunState, number> = {
  queued: 3,
  holding: 1,
  lifted_off: 1,
  in_flight: 3,
  landed: 2,
  failed: 0.5,
};

function pickState(rng: () => number, mix: Record<FleetRunState, number>): FleetRunState {
  const states = Object.keys(mix) as FleetRunState[];
  const total = states.reduce((n, s) => n + mix[s], 0);
  let x = rng() * total;
  for (const s of states) {
    x -= mix[s];
    if (x < 0) return s;
  }
  return states[states.length - 1];
}

/** A fresh run queue for a snapshot's agents, deterministic for a seed. */
export function makeRuns(
  snapshot: FleetSnapshot,
  { count, seed = 3, mix }: MakeRunsOptions
): FleetRun[] {
  const rng = mulberry32(seed);
  const weights = mix ? { ...zeroMix(), ...mix } : DEFAULT_MIX;
  const { now, agents } = snapshot;
  if (agents.length === 0) return [];
  return Array.from({ length: count }, (_, i) => {
    const state = pickState(rng, weights);
    const agent = agents[Math.floor(rng() * agents.length)];
    const run: FleetRun = {
      id: `r${i}`,
      agentId: agent.id,
      label: LABELS[i % LABELS.length],
      state,
    };
    if (state === "queued" || state === "holding") {
      run.etaAt = now + 10_000 + Math.floor(rng() * 240_000);
    } else {
      run.startedAt = now - 5_000 - Math.floor(rng() * 600_000);
      run.etaAt = run.startedAt;
    }
    if (state === "lifted_off") run.startedAt = now - Math.floor(rng() * LIFTOFF_MS);
    if (state === "landed" || state === "failed") {
      run.finishedAt = now - Math.floor(rng() * ARRIVALS_MS * 0.8);
      run.startedAt = Math.min(run.startedAt ?? now, run.finishedAt - 1_000);
      run.etaAt = run.startedAt;
    }
    if (state === "holding") run.holdReason = HOLD_REASONS[i % HOLD_REASONS.length];
    return run;
  });
}

function zeroMix(): Record<FleetRunState, number> {
  return { queued: 0, holding: 0, lifted_off: 0, in_flight: 0, landed: 0, failed: 0 };
}

/**
 * Move the run queue to `snapshot.now` (call after `stepFleet`): queued runs
 * lift off when their T-minus reaches zero, some hold and release, flights
 * land or fail (failing agents fail more), finished runs roll off the board
 * and new ones are queued so the board keeps its length.
 */
export function stepRuns(snapshot: FleetSnapshot, rng: () => number): FleetSnapshot {
  const { now, agents } = snapshot;
  const failing = new Set(agents.filter((a) => a.health === "failing").map((a) => a.id));
  let seq = snapshot.runs.reduce((n, r) => Math.max(n, Number(r.id.slice(1)) || 0), -1) + 1;
  const runs: FleetRun[] = [];
  for (const r of snapshot.runs) {
    switch (r.state) {
      case "queued":
        if (r.etaAt !== undefined && now >= r.etaAt) {
          runs.push({ ...r, state: "lifted_off", startedAt: now });
        } else if (rng() < 0.03) {
          runs.push({
            ...r,
            state: "holding",
            holdReason: HOLD_REASONS[seq % HOLD_REASONS.length],
          });
        } else runs.push(r);
        break;
      case "holding":
        if (rng() < 0.12) {
          runs.push({
            ...r,
            state: "queued",
            holdReason: undefined,
            etaAt: Math.max(r.etaAt ?? now, now + 5_000),
          });
        } else runs.push(r);
        break;
      case "lifted_off":
        runs.push(
          r.startedAt !== undefined && now - r.startedAt >= LIFTOFF_MS
            ? { ...r, state: "in_flight" }
            : r
        );
        break;
      case "in_flight": {
        const failP = failing.has(r.agentId) ? 0.5 : 0.04;
        if (rng() < 0.1) {
          runs.push({ ...r, state: rng() < failP ? "failed" : "landed", finishedAt: now });
        } else runs.push(r);
        break;
      }
      case "landed":
      case "failed":
        if (r.finishedAt === undefined || now - r.finishedAt < ARRIVALS_MS) runs.push(r);
        break;
    }
  }
  while (runs.length < snapshot.runs.length && agents.length > 0) {
    const agent = agents[Math.floor(rng() * agents.length)];
    runs.push({
      id: `r${seq}`,
      agentId: agent.id,
      label: LABELS[seq % LABELS.length],
      state: "queued",
      etaAt: now + 15_000 + Math.floor(rng() * 90_000),
    });
    seq += 1;
  }
  return { ...snapshot, runs };
}
