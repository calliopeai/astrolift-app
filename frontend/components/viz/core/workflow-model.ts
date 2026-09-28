import { mulberry32 } from "./semantics";

/**
 * The one data model every workflow view draws (transit, isometric, graph,
 * list), plus the run timeline the replay scrubber plays and the gate state
 * the airlock shows.
 */

export type StationKind = "stage" | "gate";
export type GateState = "waiting" | "approved" | "denied";

export interface WorkflowStation {
  id: string;
  name: string;
  kind: StationKind;
}

/** A workflow drawn as a line: its stages in order. */
export interface WorkflowLine {
  id: string;
  name: string;
  stations: WorkflowStation[];
}

export type TrainState = "moving" | "held" | "failed" | "done";

/** A run on its line. `at` is the station index; `progress` 0..1 toward the next. */
export interface WorkflowTrain {
  id: string;
  lineId: string;
  label: string;
  at: number;
  progress: number;
  state: TrainState;
  startedAt: number;
}

/** Throughput between station `from` and `from + 1`. */
export interface WorkflowSegment {
  lineId: string;
  from: number;
  /** 0..1 relative throughput; drives particle density and speed. */
  rate: number;
  /** Runs waiting to enter the next station; a backlog turns the edge warning. */
  backlog: number;
}

export interface WorkflowSnapshot {
  now: number;
  lines: WorkflowLine[];
  trains: WorkflowTrain[];
  segments: WorkflowSegment[];
}

export interface WorkflowViewProps {
  snapshot: WorkflowSnapshot;
  motion: "full" | "reduced";
  /** Particles along segments (a user preference). */
  flowParticles?: boolean;
  onSelectRun?: (trainId: string) => void;
  onSelectStation?: (lineId: string, stationId: string) => void;
  className?: string;
}

/** One finished (or running) run, for the replay scrubber. */
export interface RunTimeline {
  runId: string;
  label: string;
  startedAt: number;
  finishedAt: number | null;
  stages: {
    id: string;
    name: string;
    kind: StationKind;
    startedAt: number;
    finishedAt: number | null;
    status: "succeeded" | "failed" | "skipped" | "running" | "waiting";
    /** For gates: who decided, and when. */
    decidedBy?: string;
  }[];
}

const LINES: { name: string; stations: [string, StationKind][] }[] = [
  {
    name: "Release",
    stations: [
      ["Build", "stage"],
      ["Test", "stage"],
      ["Scan", "stage"],
      ["Approve", "gate"],
      ["Canary", "stage"],
      ["Rollout", "stage"],
    ],
  },
  {
    name: "Lead outreach",
    stations: [
      ["Scout", "stage"],
      ["Enrich", "stage"],
      ["Draft", "stage"],
      ["Review", "gate"],
      ["Send", "stage"],
    ],
  },
  {
    name: "Nightly data",
    stations: [
      ["Extract", "stage"],
      ["Transform", "stage"],
      ["Load", "stage"],
      ["Verify", "stage"],
    ],
  },
];

export function makeWorkflows({
  lines = 3,
  trainsPerLine = 3,
  seed = 11,
  now = Date.UTC(2026, 8, 28, 14, 0, 0),
  backedUp = false,
}: {
  lines?: number;
  trainsPerLine?: number;
  seed?: number;
  now?: number;
  backedUp?: boolean;
} = {}): WorkflowSnapshot {
  const rng = mulberry32(seed);
  const ls: WorkflowLine[] = Array.from({ length: lines }, (_, i) => {
    const t = LINES[i % LINES.length];
    return {
      id: `l${i}`,
      name: t.name + (i >= LINES.length ? ` ${Math.floor(i / LINES.length) + 1}` : ""),
      stations: t.stations.map(([name, kind], j) => ({ id: `l${i}s${j}`, name, kind })),
    };
  });
  const trains: WorkflowTrain[] = [];
  const segments: WorkflowSegment[] = [];
  ls.forEach((l) => {
    for (let k = 0; k < trainsPerLine; k++) {
      const at = Math.floor(rng() * (l.stations.length - 1));
      const atGate = l.stations[at].kind === "gate";
      trains.push({
        id: `${l.id}t${k}`,
        lineId: l.id,
        label: `#${1200 + trains.length * 7}`,
        at,
        progress: atGate ? 0 : rng(),
        state: atGate ? "held" : "moving",
        startedAt: now - Math.floor(rng() * 600_000),
      });
    }
    for (let s = 0; s < l.stations.length - 1; s++) {
      segments.push({
        lineId: l.id,
        from: s,
        rate: rng(),
        backlog: backedUp && s === l.stations.length - 3 ? 6 : Math.floor(rng() * 2),
      });
    }
  });
  return { now, lines: ls, trains, segments };
}

/** Advance runs along their lines. Trains wait at gates until approved. */
export function stepWorkflows(
  prev: WorkflowSnapshot,
  rng: () => number,
  dtMs: number
): WorkflowSnapshot {
  const lines = new Map(prev.lines.map((l) => [l.id, l]));
  const trains = prev.trains.map((t) => {
    const line = lines.get(t.lineId)!;
    if (t.state === "done" || t.state === "failed") {
      // Finished runs leave and a new run enters at the first station.
      return rng() < 0.2
        ? { ...t, at: 0, progress: 0, state: "moving" as const, startedAt: prev.now }
        : t;
    }
    if (t.state === "held")
      return rng() < 0.15 ? { ...t, state: "moving" as const, progress: 0.05 } : t;
    let progress = t.progress + (dtMs / 1000) * (0.15 + rng() * 0.25);
    let at = t.at;
    if (progress >= 1) {
      at += 1;
      progress = 0;
      if (at >= line.stations.length - 1)
        return { ...t, at: line.stations.length - 1, progress: 0, state: "done" as const };
      if (line.stations[at].kind === "gate")
        return { ...t, at, progress: 0, state: "held" as const };
      if (rng() < 0.03) return { ...t, at, progress: 0, state: "failed" as const };
    }
    return { ...t, at, progress };
  });
  const segments = prev.segments.map((s) => ({
    ...s,
    rate: Math.max(0, Math.min(1, s.rate + (rng() - 0.5) * 0.2)),
  }));
  return { ...prev, now: prev.now + dtMs, trains, segments };
}

export function makeTimeline({
  now = Date.UTC(2026, 8, 28, 14, 0, 0),
  failedAt,
}: { now?: number; failedAt?: number } = {}): RunTimeline {
  const plan: [string, StationKind, number][] = [
    ["Build", "stage", 94_000],
    ["Test", "stage", 212_000],
    ["Scan", "stage", 41_000],
    ["Approve", "gate", 380_000],
    ["Canary", "stage", 120_000],
    ["Rollout", "stage", 66_000],
  ];
  let t = now - plan.reduce((sum, [, , d]) => sum + d, 0);
  const startedAt = t;
  const stages: RunTimeline["stages"] = plan.map(([name, kind, d], i) => {
    const s = t;
    t += d;
    const failed = failedAt === i;
    const after = failedAt !== undefined && i > failedAt;
    return {
      id: `st${i}`,
      name,
      kind,
      startedAt: s,
      finishedAt: after ? null : t,
      status: after ? "skipped" : failed ? "failed" : "succeeded",
      decidedBy: kind === "gate" && !after ? "reviewer@example.com" : undefined,
    };
  });
  return { runId: "run-4821", label: "Release 3.4 · #4821", startedAt, finishedAt: t, stages };
}
