import { mulberry32 } from "./semantics";
import { joinReady, loopRound, loopsAt } from "./workflow-shapes";

/**
 * The one data model every workflow view draws (transit, isometric, graph,
 * list), plus the run timeline the replay scrubber plays and the gate state
 * the airlock shows.
 *
 * Beyond a straight line of stages, a workflow can loop (a retry onto the
 * same stage, or a bounded back-edge to an earlier one), fan out to parallel
 * branches that a join waits on, hand sub-tasks to a supervisor's workers, or
 * run a nested child workflow. These mirror what the backend runs
 * (astrolift-app#2156): fan_out plus an aggregation stage, on_failure=retry,
 * human gates, and bounded back-edges with max_rounds.
 */

export type StationKind = "stage" | "gate" | "fanout" | "join" | "supervisor" | "workflow";
export type GateState = "waiting" | "approved" | "denied";

interface StationBase {
  id: string;
  name: string;
}

/** A plain stage, or a human gate (approved continues, rejected fails or loops). */
export interface StageStation extends StationBase {
  kind: "stage" | "gate";
}

export type BranchState = "queued" | "running" | "succeeded" | "failed";

export interface FanoutBranch {
  id: string;
  label: string;
  state: BranchState;
}

/**
 * Parallel branches. One run holds a fanout at a time (`runId`); its branches
 * are that run's children, and the next run waits short of the station. With
 * no run holding it, `branches` are the last run's, settled.
 */
export interface FanoutStation extends StationBase {
  kind: "fanout";
  branches: FanoutBranch[];
  /** The branch count is decided per run (from data), not fixed by the definition. */
  dynamic?: boolean;
  runId?: string;
}

/** The aggregation that merges a fanout: it waits until every branch settles. */
export interface JoinStation extends StationBase {
  kind: "join";
  /** Index of the fanout station this join waits on. */
  waitsOn: number;
}

export interface SupervisorWorker {
  id: string;
  label: string;
  busy: boolean;
  /** Recent utilisation, 0..1 (a moving average of busy). */
  load: number;
  /** The run whose sub-task this worker is on. */
  runId?: string;
}

/** A supervisor handing each arriving run's sub-tasks to a pool of workers. */
export interface SupervisorStation extends StationBase {
  kind: "supervisor";
  workers: SupervisorWorker[];
}

/** A stage that runs a whole child workflow, drawn from the line `childLineId`. */
export interface NestedStation extends StationBase {
  kind: "workflow";
  childLineId: string;
}

export type WorkflowStation =
  | StageStation
  | FanoutStation
  | JoinStation
  | SupervisorStation
  | NestedStation;

/**
 * A bounded way back. `to === from` is a retry of the same stage (what the
 * backend runs today as on_failure=retry); `to < from` is a back-edge to an
 * earlier stage (#2156). `maxRounds` bounds it: attempts for a retry, rounds
 * for a back-edge. A run that needs one more than the bound fails.
 */
export interface WorkflowLoop {
  id: string;
  from: number;
  to: number;
  trigger: "rejected" | "failed" | "condition";
  /** For trigger "condition": the condition in words. */
  condition?: string;
  maxRounds: number;
  kind: "retry" | "back-edge";
}

/** A workflow drawn as a line: its stages in order. */
export interface WorkflowLine {
  id: string;
  name: string;
  stations: WorkflowStation[];
  loops?: WorkflowLoop[];
  /** Set on a nested child line: the parent line and its "workflow" station. */
  parent?: { lineId: string; stationId: string };
}

export type TrainState = "moving" | "held" | "failed" | "done";

/**
 * A run on its line. `at` is the station index; `progress` 0..1 toward the next.
 *
 * At a fanout, supervisor or nested station, `progress` is the share of that
 * station's work settled (branches, sub-tasks, child stages), so the run moves
 * only as work finishes. Travelling a loop, `at` stays on the loop's `from`
 * and `loopProgress` 0..1 says how far along the return track it is.
 */
export interface WorkflowTrain {
  id: string;
  lineId: string;
  label: string;
  at: number;
  progress: number;
  state: TrainState;
  startedAt: number;
  /** 1-based; one more for every back-edge taken. Absent means 1. */
  round?: number;
  /** 1-based attempt at the current station; a retry adds one. Absent means 1. */
  attempt?: number;
  /** Back-edges taken, per loop id, for the bounds. */
  loopRounds?: Record<string, number>;
  /** Set while the run travels a loop's return track. */
  onLoopId?: string;
  loopProgress?: number;
  /** At a supervisor: the run's sub-tasks and how many are finished. */
  subtasks?: { total: number; done: number };
  /** On a nested child line: the parent run. */
  parentRunId?: string;
  /** At a nested station: the child run on the child line. */
  childRunId?: string;
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
    /** 1-based round this execution belongs to (#2156). Absent means 1. */
    round?: number;
    /** 1-based attempt within the round; a retry adds one. Absent means 1. */
    attempt?: number;
    /** For a fanout's children: which branch this execution is. */
    branchId?: string;
    /** Why this execution happened again: the loop taken and what triggered it. */
    causedBy?: { loopId: string; reason: string };
  }[];
}

const LINES: { name: string; stations: [string, "stage" | "gate"][] }[] = [
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

/**
 * The shaped fixtures, after the classic lines: a loop, a fanout, a
 * supervisor and a nested workflow (plus that workflow's child line). Ids
 * start at `l{base}` so they never collide with the classic lines.
 */
export function shapedLines(base: number): WorkflowLine[] {
  const id = (k: number) => `l${base + k}`;
  const stages = (lineId: string, names: [string, "stage" | "gate"][]): StageStation[] =>
    names.map(([name, kind], j) => ({ id: `${lineId}s${j}`, name, kind }));
  const feature = id(0);
  const research = id(1);
  const support = id(2);
  const close = id(3);
  const reconcile = id(4);
  return [
    {
      id: feature,
      name: "Feature delivery",
      stations: stages(feature, [
        ["Plan", "stage"],
        ["Code", "stage"],
        ["Test", "stage"],
        ["Review", "gate"],
        ["Merge", "stage"],
        ["Deploy", "stage"],
      ]),
      loops: [
        {
          id: `${feature}:test-failed`,
          from: 2,
          to: 1,
          trigger: "failed",
          maxRounds: 5,
          kind: "back-edge",
        },
        {
          id: `${feature}:review-rejected`,
          from: 3,
          to: 1,
          trigger: "rejected",
          maxRounds: 3,
          kind: "back-edge",
        },
        {
          id: `${feature}:deploy-retry`,
          from: 5,
          to: 5,
          trigger: "failed",
          maxRounds: 3,
          kind: "retry",
        },
      ],
    },
    {
      id: research,
      name: "Research digest",
      stations: [
        { id: `${research}s0`, name: "Gather", kind: "stage" },
        { id: `${research}s1`, name: "Fan out", kind: "fanout", dynamic: true, branches: [] },
        { id: `${research}s2`, name: "Summarise", kind: "join", waitsOn: 1 },
        { id: `${research}s3`, name: "Publish", kind: "stage" },
      ],
    },
    {
      id: support,
      name: "Support triage",
      stations: [
        { id: `${support}s0`, name: "Intake", kind: "stage" },
        {
          id: `${support}s1`,
          name: "Supervisor",
          kind: "supervisor",
          workers: Array.from({ length: 5 }, (_, w) => ({
            id: `${support}w${w}`,
            label: `Worker ${w + 1}`,
            busy: false,
            load: 0,
          })),
        },
        { id: `${support}s2`, name: "Resolve", kind: "stage" },
      ],
    },
    {
      id: close,
      name: "Quarterly close",
      stations: [
        { id: `${close}s0`, name: "Collect", kind: "stage" },
        { id: `${close}s1`, name: "Reconcile", kind: "workflow", childLineId: reconcile },
        { id: `${close}s2`, name: "Sign-off", kind: "gate" },
      ],
    },
    {
      id: reconcile,
      name: "Reconcile",
      parent: { lineId: close, stationId: `${close}s1` },
      stations: stages(reconcile, [
        ["Match", "stage"],
        ["Investigate", "stage"],
        ["Adjust", "stage"],
      ]),
    },
  ];
}

export function makeWorkflows({
  lines = 3,
  trainsPerLine = 3,
  seed = 11,
  now = Date.UTC(2026, 8, 28, 14, 0, 0),
  backedUp = false,
  shapes = false,
}: {
  lines?: number;
  trainsPerLine?: number;
  seed?: number;
  now?: number;
  backedUp?: boolean;
  /** Also add the shaped lines (loop, fanout, supervisor, nested) after the classic ones. */
  shapes?: boolean;
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
  if (shapes) addShaped(ls, trains, segments, { trainsPerLine, seed, now });
  return { now, lines: ls, trains, segments };
}

/**
 * Place the shaped lines' first runs where their shape shows (a run on round
 * 2, one travelling back after a reject, a fanout part settled, a supervisor
 * with busy workers, a child run under way), then scatter the rest with the
 * same rules the simulation uses. Its own rng stream, so adding shapes never
 * changes the classic lines.
 */
function addShaped(
  ls: WorkflowLine[],
  trains: WorkflowTrain[],
  segments: WorkflowSegment[],
  { trainsPerLine, seed, now }: { trainsPerLine: number; seed: number; now: number }
) {
  const rng = mulberry32(seed + 7919);
  const shaped = shapedLines(ls.length);
  ls.push(...shaped);
  const ctx: SimContext = { rng, lines: new Map(shaped.map((l) => [l.id, l])) };
  const [feature, research, support, close] = shaped;
  const fresh = (l: WorkflowLine, k: number): WorkflowTrain => ({
    id: `${l.id}t${k}`,
    lineId: l.id,
    label: `#${1200 + trains.length * 7}`,
    at: 0,
    progress: 0,
    state: "moving",
    startedAt: now - Math.floor(rng() * 600_000),
    round: 1,
    attempt: 1,
  });
  for (const l of [feature, research, support, close]) {
    for (let k = 0; k < trainsPerLine; k++) {
      const t = fresh(l, k);
      if (k === 0 && l === feature) {
        const testLoop = l.loops![0];
        trains.push({ ...t, at: 2, progress: 0.4, round: 2, loopRounds: { [testLoop.id]: 1 } });
      } else if (k === 1 && l === feature) {
        const reviewLoop = l.loops![1];
        trains.push({ ...t, at: 3, onLoopId: reviewLoop.id, loopProgress: 0.45 });
      } else if (k === 0 && l === research) {
        const [placed] = arrive(t, l, 1, ctx);
        const fan = l.stations[1] as FanoutStation;
        const states: BranchState[] = [
          "succeeded",
          "running",
          "succeeded",
          "running",
          "failed",
          "running",
        ];
        fan.branches = states.map((state, b) => ({
          id: `${t.id}b${b}`,
          label: `Source ${b + 1}`,
          state,
        }));
        trains.push({ ...placed, progress: settledShare(fan) });
      } else if (k === 0 && l === support) {
        const sup = l.stations[1] as SupervisorStation;
        sup.workers[0] = { ...sup.workers[0], busy: true, load: 0.8, runId: t.id };
        sup.workers[1] = { ...sup.workers[1], busy: true, load: 0.6, runId: t.id };
        sup.workers[2] = { ...sup.workers[2], load: 0.3 };
        trains.push({
          ...t,
          at: 1,
          subtasks: { total: 5, done: 2 },
          progress: (2 / 5) * WAIT_SPAN,
        });
      } else if (k === 0 && l === close) {
        const [parent, child] = arrive(t, l, 1, ctx);
        const moved = { ...child, at: 1, progress: 0.5 };
        trains.push({ ...parent, progress: childShare(moved, ctx) }, moved);
      } else {
        const at = Math.floor(rng() * (l.stations.length - 1));
        const placed = arrive(t, l, at, ctx);
        const [head, ...rest] = placed;
        const plain = l.stations[at].kind === "stage" || l.stations[at].kind === "join";
        trains.push(
          plain && head.state === "moving" ? { ...head, progress: rng() } : head,
          ...rest
        );
      }
    }
  }
  for (const l of shaped) {
    for (let s = 0; s < l.stations.length - 1; s++)
      segments.push({ lineId: l.id, from: s, rate: rng(), backlog: Math.floor(rng() * 2) });
  }
}

/** Advance runs along their lines. Trains wait at gates until approved. */
export function stepWorkflows(
  prev: WorkflowSnapshot,
  rng: () => number,
  dtMs: number
): WorkflowSnapshot {
  const lines = new Map(prev.lines.map((l) => [l.id, l]));
  // Stations with live state (fanout branches, supervisor workers) are copied
  // so the step stays pure; every other line keeps its identity.
  const work = new Map<string, WorkflowLine>();
  for (const l of prev.lines) {
    if (!isShaped(l)) continue;
    const live = l.stations.some((s) => s.kind === "fanout" || s.kind === "supervisor");
    work.set(l.id, live ? { ...l, stations: l.stations.map(cloneStation) } : l);
  }
  const ctx: SimContext = { rng, lines: work };
  const finished = work.size
    ? settleStations(work, prev.trains, ctx, dtMs)
    : new Map<string, number>();
  const byId = new Map(prev.trains.map((t) => [t.id, t]));
  const dropped = new Set<string>();
  const out: WorkflowTrain[] = [];
  for (const t of prev.trains) {
    const shaped = work.get(t.lineId);
    if (shaped)
      out.push(...stepShaped(t, shaped, { ...ctx, byId, dropped, finished, dtMs, now: prev.now }));
    else out.push(stepClassic(t, lines.get(t.lineId)!, rng, dtMs, prev.now));
  }
  const trains = dropped.size ? out.filter((t) => !dropped.has(t.id)) : out;
  const segments = prev.segments.map((s) => ({
    ...s,
    rate: Math.max(0, Math.min(1, s.rate + (rng() - 0.5) * 0.2)),
  }));
  const nextLines = work.size ? prev.lines.map((l) => work.get(l.id) ?? l) : prev.lines;
  return { ...prev, now: prev.now + dtMs, lines: nextLines, trains, segments };
}

function stepClassic(
  t: WorkflowTrain,
  line: WorkflowLine,
  rng: () => number,
  dtMs: number,
  now: number
): WorkflowTrain {
  if (t.state === "done" || t.state === "failed") {
    // Finished runs leave and a new run enters at the first station.
    return rng() < 0.2 ? { ...t, at: 0, progress: 0, state: "moving" as const, startedAt: now } : t;
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
    if (line.stations[at].kind === "gate") return { ...t, at, progress: 0, state: "held" as const };
    if (rng() < 0.03) return { ...t, at, progress: 0, state: "failed" as const };
  }
  return { ...t, at, progress };
}

/* ---------- The shaped simulation: loops, fanout, supervisor, nested ---------- */

/** Chance a stage with a "failed" loop leaving it fails (a flaky test, a bad deploy). */
const LOOP_FAIL_P = 0.3;
/** Chance a gate with a "rejected" loop sends the work back instead of approving it. */
const REJECT_P = 0.35;
/** Chance a plain stage or gate fails the run outright. */
const PLAIN_FAIL_P = 0.02;
/** Loop travel speed, return tracks per second. */
const LOOP_SPEED = 0.5;
/**
 * How far along its segment a run waiting on work (branches, sub-tasks, a
 * child run) can get: the rest is the hop that happens once the work is done.
 */
const WAIT_SPAN = 0.9;

interface SimContext {
  rng: () => number;
  /** The step's working lines (live stations are copies, safe to write). */
  lines: Map<string, WorkflowLine>;
}

interface StepContext extends SimContext {
  byId: Map<string, WorkflowTrain>;
  /** Child runs a parent has consumed this step; removed at the end. */
  dropped: Set<string>;
  /** Sub-tasks finished this step by supervisor workers, per run. */
  finished: Map<string, number>;
  dtMs: number;
  now: number;
}

function isShaped(l: WorkflowLine): boolean {
  return (
    !!l.loops?.length ||
    !!l.parent ||
    l.stations.some((s) => s.kind !== "stage" && s.kind !== "gate")
  );
}

function cloneStation(s: WorkflowStation): WorkflowStation {
  if (s.kind === "fanout") return { ...s, branches: s.branches.map((b) => ({ ...b })) };
  if (s.kind === "supervisor") return { ...s, workers: s.workers.map((w) => ({ ...w })) };
  return s;
}

function settledShare(fan: FanoutStation): number {
  const r = joinReady(fan);
  return r.total ? (r.settled / r.total) * WAIT_SPAN : 0;
}

function childShare(child: WorkflowTrain, ctx: SimContext): number {
  const n = ctx.lines.get(child.lineId)?.stations.length ?? 1;
  if (child.state === "done") return WAIT_SPAN;
  return n > 1 ? ((child.at + child.progress) / (n - 1)) * WAIT_SPAN : 0;
}

/**
 * The stations' own work, before runs move: fanout branches settle at their
 * own pace, and supervisor workers finish a sub-task or pick up the next one.
 */
function settleStations(
  work: Map<string, WorkflowLine>,
  trains: WorkflowTrain[],
  { rng }: SimContext,
  dtMs: number
): Map<string, number> {
  const finished = new Map<string, number>();
  const pace = dtMs / 1000;
  for (const line of work.values()) {
    line.stations.forEach((st, idx) => {
      if (st.kind === "fanout" && st.runId) {
        for (const b of st.branches) {
          if (b.state === "queued") b.state = "running";
          else if (b.state === "running" && rng() < 0.15 * pace)
            b.state = rng() < 0.9 ? "succeeded" : "failed";
        }
      }
      if (st.kind === "supervisor") {
        const here = trains.filter(
          (t) => t.lineId === line.id && t.at === idx && t.state === "moving" && t.subtasks
        );
        const present = new Set(here.map((t) => t.id));
        for (const w of st.workers) {
          if (w.busy && (!w.runId || !present.has(w.runId))) Object.assign(w, idleWorker);
          else if (w.busy && rng() < 0.3 * pace) {
            finished.set(w.runId!, (finished.get(w.runId!) ?? 0) + 1);
            Object.assign(w, idleWorker);
          }
        }
        for (const w of st.workers) {
          if (!w.busy) {
            const next = here.find((t) => {
              const inFlight = st.workers.filter((x) => x.runId === t.id).length;
              return (
                t.subtasks!.total - t.subtasks!.done - (finished.get(t.id) ?? 0) - inFlight > 0
              );
            });
            if (next) Object.assign(w, { busy: true, runId: next.id });
          }
          w.load = w.load * 0.6 + (w.busy ? 0.4 : 0);
        }
      }
    });
  }
  return finished;
}

const idleWorker = { busy: false, runId: undefined };

/** A run reaches station `at`: gates hold it, fanouts, supervisors and nested stations start their work. */
function arrive(
  t: WorkflowTrain,
  line: WorkflowLine,
  at: number,
  ctx: SimContext
): WorkflowTrain[] {
  const base: WorkflowTrain = {
    ...t,
    at,
    progress: 0,
    state: "moving",
    attempt: 1,
    subtasks: undefined,
    childRunId: undefined,
  };
  const st = line.stations[at];
  if (st.kind === "gate") return [{ ...base, state: "held" }];
  if (at === line.stations.length - 1) return [settleTerminus(base, line, ctx)];
  if (st.kind === "fanout") return [claimFanout(base, st, ctx)];
  if (st.kind === "supervisor")
    return [{ ...base, subtasks: { total: 3 + Math.floor(ctx.rng() * 4), done: 0 } }];
  if (st.kind === "workflow") {
    const childLine = ctx.lines.get(st.childLineId);
    if (!childLine) return [base];
    const child: WorkflowTrain = {
      id: `${t.id}/child`,
      lineId: childLine.id,
      label: t.label,
      at: 0,
      progress: 0,
      state: "moving",
      startedAt: t.startedAt,
      round: 1,
      attempt: 1,
      parentRunId: t.id,
    };
    return [{ ...base, childRunId: child.id }, child];
  }
  return [base];
}

/** One run holds a fanout at a time; the next waits short of it. */
function claimFanout(t: WorkflowTrain, st: FanoutStation, { rng }: SimContext): WorkflowTrain {
  if (st.runId && st.runId !== t.id) return { ...t, state: "held" };
  if (st.runId !== t.id) {
    const n = st.dynamic ? 4 + Math.floor(rng() * 4) : Math.max(1, st.branches.length);
    st.runId = t.id;
    st.branches = Array.from({ length: n }, (_, b) => ({
      id: `${t.id}b${b}`,
      label: st.branches[b]?.label && !st.dynamic ? st.branches[b].label : `Source ${b + 1}`,
      state: "queued" as const,
    }));
  }
  return { ...t, state: "moving", progress: 0 };
}

/** Take a loop if its bound allows; a run that needs one more round than the bound fails. */
function takeLoop(t: WorkflowTrain, loop: WorkflowLoop): WorkflowTrain {
  if (loopRound(t, loop) >= loop.maxRounds) return { ...t, state: "failed" };
  return { ...t, state: "moving", onLoopId: loop.id, loopProgress: 0 };
}

function loopFor(line: WorkflowLine, at: number, trigger: WorkflowLoop["trigger"]) {
  return loopsAt(line, at).find(
    (l) => l.trigger === trigger || (trigger === "failed" && l.trigger === "condition")
  );
}

/** The last station's work: done, or a failure that retries (bounded) or fails the run. */
function settleTerminus(t: WorkflowTrain, line: WorkflowLine, { rng }: SimContext): WorkflowTrain {
  const loop = loopFor(line, t.at, "failed");
  if (rng() < (loop ? LOOP_FAIL_P : PLAIN_FAIL_P))
    return loop ? takeLoop(t, loop) : { ...t, state: "failed" };
  return { ...t, progress: 0, state: "done" };
}

function stepShaped(t: WorkflowTrain, line: WorkflowLine, ctx: StepContext): WorkflowTrain[] {
  const { rng } = ctx;
  const last = line.stations.length - 1;
  if (t.state === "done" || t.state === "failed") {
    // A child run waits for its parent to collect it.
    if (t.parentRunId) return [t];
    if (rng() >= 0.2) return [t];
    return arrive(
      {
        id: t.id,
        lineId: t.lineId,
        label: t.label,
        at: 0,
        progress: 0,
        state: "moving",
        startedAt: ctx.now,
        round: 1,
        attempt: 1,
      },
      line,
      0,
      ctx
    );
  }

  if (t.onLoopId) {
    const loopProgress = (t.loopProgress ?? 0) + (ctx.dtMs / 1000) * LOOP_SPEED;
    const loop = line.loops?.find((l) => l.id === t.onLoopId);
    if (!loop) return [{ ...t, onLoopId: undefined, loopProgress: undefined }];
    if (loopProgress < 1) return [{ ...t, loopProgress }];
    const back = { ...t, onLoopId: undefined, loopProgress: undefined };
    if (loop.kind === "retry")
      return [{ ...back, at: loop.from, progress: 0, attempt: (t.attempt ?? 1) + 1 }];
    return arrive(
      {
        ...back,
        round: (t.round ?? 1) + 1,
        loopRounds: { ...t.loopRounds, [loop.id]: (t.loopRounds?.[loop.id] ?? 0) + 1 },
      },
      line,
      loop.to,
      ctx
    );
  }

  const st = line.stations[t.at];
  if (t.state === "held") {
    if (st.kind === "fanout") return [claimFanout(t, st, ctx)];
    if (rng() >= 0.2) return [t];
    const loop = loopFor(line, t.at, "rejected");
    if (rng() < (loop ? REJECT_P : PLAIN_FAIL_P * 5))
      return [loop ? takeLoop(t, loop) : { ...t, state: "failed" }];
    // Approved. A gate at the terminus is the run's last step.
    if (t.at === last) return [{ ...t, state: "done" }];
    return [{ ...t, state: "moving", progress: 0.05 }];
  }

  // Back at the terminus after a retry: run its work again.
  if (t.at === last) return [settleTerminus(t, line, ctx)];

  if (st.kind === "fanout") {
    if (st.runId !== t.id) return [claimFanout(t, st, ctx)];
    const r = joinReady(st);
    if (!r.ready) return [{ ...t, progress: settledShare(st) }];
    st.runId = undefined;
    if (r.failed === r.total) return [{ ...t, progress: WAIT_SPAN, state: "failed" }];
    return arrive(t, line, t.at + 1, ctx);
  }
  if (st.kind === "supervisor" && t.subtasks) {
    const done = Math.min(t.subtasks.total, t.subtasks.done + (ctx.finished.get(t.id) ?? 0));
    if (done >= t.subtasks.total) return arrive(t, line, t.at + 1, ctx);
    return [
      { ...t, subtasks: { ...t.subtasks, done }, progress: (done / t.subtasks.total) * WAIT_SPAN },
    ];
  }
  if (st.kind === "workflow") {
    const child = t.childRunId ? ctx.byId.get(t.childRunId) : undefined;
    if (!child) return arrive(t, line, t.at, ctx);
    if (child.state === "failed") {
      ctx.dropped.add(child.id);
      return [{ ...t, childRunId: undefined, state: "failed" }];
    }
    if (child.state === "done") {
      ctx.dropped.add(child.id);
      return arrive(t, line, t.at + 1, ctx);
    }
    return [{ ...t, progress: childShare(child, ctx) }];
  }

  const progress = t.progress + (ctx.dtMs / 1000) * (0.15 + rng() * 0.25);
  if (progress < 1) return [{ ...t, progress }];
  // Leaving a stage: its work is settled here, so a failure can loop back.
  if (st.kind === "stage" || st.kind === "join") {
    const loop = loopFor(line, t.at, "failed");
    if (rng() < (loop ? LOOP_FAIL_P : PLAIN_FAIL_P))
      return [
        loop ? takeLoop({ ...t, progress: 0 }, loop) : { ...t, progress: 0, state: "failed" },
      ];
  }
  return arrive(t, line, t.at + 1, ctx);
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

/**
 * A Feature delivery run that went round three times before it finished:
 * Test failed twice (back to Code each time), then Review rejected once (back
 * to Code again). Round 4 passed Test and Review, merged and deployed.
 */
export function makeFeatureTimeline({
  now = Date.UTC(2026, 8, 28, 14, 0, 0),
  lineId = "l3",
}: { now?: number; lineId?: string } = {}): RunTimeline {
  const testLoop = `${lineId}:test-failed`;
  const reviewLoop = `${lineId}:review-rejected`;
  type Step = {
    name: string;
    kind: StationKind;
    ms: number;
    round: number;
    status?: "failed";
    decidedBy?: string;
    causedBy?: { loopId: string; reason: string };
  };
  const plan: Step[] = [
    { name: "Plan", kind: "stage", ms: 180_000, round: 1 },
    { name: "Code", kind: "stage", ms: 620_000, round: 1 },
    { name: "Test", kind: "stage", ms: 240_000, round: 1, status: "failed" },
    {
      name: "Code",
      kind: "stage",
      ms: 310_000,
      round: 2,
      causedBy: { loopId: testLoop, reason: "Test failed: 3 specs in checkout" },
    },
    { name: "Test", kind: "stage", ms: 236_000, round: 2, status: "failed" },
    {
      name: "Code",
      kind: "stage",
      ms: 190_000,
      round: 3,
      causedBy: { loopId: testLoop, reason: "Test failed: 1 spec in checkout" },
    },
    { name: "Test", kind: "stage", ms: 244_000, round: 3 },
    {
      name: "Review",
      kind: "gate",
      ms: 1_260_000,
      round: 3,
      status: "failed",
      decidedBy: "reviewer@example.com",
    },
    {
      name: "Code",
      kind: "stage",
      ms: 420_000,
      round: 4,
      causedBy: { loopId: reviewLoop, reason: "Review rejected: needs a migration" },
    },
    { name: "Test", kind: "stage", ms: 238_000, round: 4 },
    {
      name: "Review",
      kind: "gate",
      ms: 540_000,
      round: 4,
      decidedBy: "reviewer@example.com",
    },
    { name: "Merge", kind: "stage", ms: 35_000, round: 4 },
    { name: "Deploy", kind: "stage", ms: 160_000, round: 4 },
  ];
  let t = now - plan.reduce((sum, p) => sum + p.ms, 0);
  const startedAt = t;
  const stages: RunTimeline["stages"] = plan.map((p, i) => {
    const s = t;
    t += p.ms;
    return {
      id: `st${i}`,
      name: p.name,
      kind: p.kind,
      startedAt: s,
      finishedAt: t,
      status: p.status ?? "succeeded",
      decidedBy: p.decidedBy,
      round: p.round,
      attempt: 1,
      causedBy: p.causedBy,
    };
  });
  return {
    runId: "run-5310",
    label: "Feature delivery · checkout redesign · #5310",
    startedAt,
    finishedAt: t,
    stages,
  };
}
