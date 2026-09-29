import type { Health } from "../core/semantics";
import { makeTimeline, type RunTimeline } from "../core/workflow-model";

/**
 * Pure logic behind the run replay: where each stage sits on a track scaled
 * to real time, which stage the playhead is in, the stage boundaries the
 * arrow keys step between, and the slowest-stage callout. Times here are ms
 * offsets from the run's start, so the track is 0..total.
 *
 * A run that loops is also placed by round (each round's span on the track,
 * and a mark where a loop sent work back), and a fan-out's branches each get
 * a lane of their own under the main track, so parallel work reads as
 * parallel bars.
 */

export type RunStage = RunTimeline["stages"][number];

export interface PlacedStage {
  stage: RunStage;
  /** Offset of the stage start from the run start, ms. */
  start: number;
  /** Offset of the stage end, ms (a running stage ends at `now`). */
  end: number;
  /** 0 is the main track; 1..n is a fan-out branch's own parallel bar. */
  lane: number;
}

export interface PlacedRound {
  round: number;
  start: number;
  end: number;
  /** Index into `stages` of the execution the loop sent back to, when a loop opened this round. */
  causeIndex: number | null;
}

/** Where a loop sent work back: the start of the execution it caused. */
export interface LoopMark {
  /** Index into `stages` of the execution the loop caused. */
  index: number;
  at: number;
  round: number;
  loopId: string;
  reason: string;
  /** The stage the work went back to. */
  to: string;
}

export interface PlacedRun {
  total: number;
  stages: PlacedStage[];
  /** How many branch lanes sit under the main track. */
  lanes: number;
  /** Rounds in order; one round for a run that never looped. */
  rounds: PlacedRound[];
  marks: LoopMark[];
}

export const STAGE_HEALTH: Record<RunStage["status"], Health> = {
  succeeded: "ok",
  running: "ok",
  failed: "failing",
  waiting: "degraded",
  skipped: "idle",
};

/** The last moment the timeline knows about: its finish, `now`, or its latest stamp. */
export function runEnd(timeline: RunTimeline, now?: number): number {
  if (timeline.finishedAt !== null) return timeline.finishedAt;
  let latest = timeline.startedAt;
  for (const s of timeline.stages) latest = Math.max(latest, s.finishedAt ?? s.startedAt);
  return now === undefined ? latest : Math.max(latest, now);
}

/**
 * Lay every stage on the track. An open stage runs to the next main-track
 * stage's start or the run end; an open branch runs to the run end. Each
 * distinct branch gets its own lane, in the order branches first appear.
 */
export function placeRun(timeline: RunTimeline, now?: number): PlacedRun {
  const end = runEnd(timeline, now);
  const total = Math.max(0, end - timeline.startedAt);
  const laneOf = new Map<string, number>();
  for (const s of timeline.stages)
    if (s.branchId && !laneOf.has(s.branchId)) laneOf.set(s.branchId, laneOf.size + 1);
  const stages = timeline.stages.map((stage, i) => {
    const next = stage.branchId ? undefined : timeline.stages.slice(i + 1).find((s) => !s.branchId);
    const stop = stage.finishedAt ?? (next ? next.startedAt : end);
    const start = clamp(stage.startedAt - timeline.startedAt, 0, total);
    return {
      stage,
      start,
      end: clamp(stop - timeline.startedAt, start, total),
      lane: stage.branchId ? laneOf.get(stage.branchId)! : 0,
    };
  });

  const byRound = new Map<number, PlacedRound>();
  stages.forEach((p, i) => {
    const r = p.stage.round ?? 1;
    const cur = byRound.get(r);
    if (!cur) byRound.set(r, { round: r, start: p.start, end: p.end, causeIndex: null });
    else {
      cur.start = Math.min(cur.start, p.start);
      cur.end = Math.max(cur.end, p.end);
    }
    if (p.stage.causedBy) {
      const round = byRound.get(r)!;
      if (round.causeIndex === null) round.causeIndex = i;
    }
  });
  const rounds = [...byRound.values()].sort((a, b) => a.round - b.round);
  const marks: LoopMark[] = [];
  stages.forEach((p, i) => {
    const c = p.stage.causedBy;
    if (c)
      marks.push({
        index: i,
        at: p.start,
        round: p.stage.round ?? 1,
        loopId: c.loopId,
        reason: c.reason,
        to: p.stage.name,
      });
  });
  return { total, stages, lanes: laneOf.size, rounds, marks };
}

/** The round the playhead is in: the last round started by `t`. */
export function roundAt(run: PlacedRun, t: number): PlacedRound | null {
  let found: PlacedRound | null = null;
  for (const r of run.rounds) if (r.start <= t) found = r;
  return found;
}

/** A branch bar's state at playhead `t`, from its own times (branches run side by side). */
export function laneState(p: PlacedStage, t: number): "ahead" | "active" | "settled" | "skipped" {
  if (p.stage.status === "skipped") return "skipped";
  if (t < p.start) return "ahead";
  // A branch still running stays lit at the live edge.
  return t >= p.end && p.stage.finishedAt !== null ? "settled" : "active";
}

/**
 * The main-track stage the playhead is in or has most recently entered,
 * ignoring skipped stages (they never ran, so the playhead never lights them)
 * and branch bars (they run beside the main track, under their fan-out). -1
 * before the first stage starts.
 */
export function stageIndexAt(run: PlacedRun, t: number): number {
  let idx = -1;
  run.stages.forEach((p, i) => {
    if (p.lane === 0 && p.stage.status !== "skipped" && p.start <= t) idx = i;
  });
  return idx;
}

/** Every distinct stage start and end, plus the run's ends, in order. */
export function boundaries(run: PlacedRun): number[] {
  const set = new Set<number>([0, run.total]);
  for (const p of run.stages) {
    set.add(p.start);
    set.add(p.end);
  }
  return [...set].sort((a, b) => a - b);
}

/** The next (dir 1) or previous (dir -1) boundary from `t`; stays put at the ends. */
export function stepBoundary(run: PlacedRun, t: number, dir: 1 | -1): number {
  const b = boundaries(run);
  const eps = 1;
  if (dir === 1) return b.find((x) => x > t + eps) ?? run.total;
  for (let i = b.length - 1; i >= 0; i--) if (b[i] < t - eps) return b[i];
  return 0;
}

/** The longest main-track stage that actually ran, and its share of the run. */
export function slowestStage(
  run: PlacedRun
): { stage: RunStage; ms: number; share: number } | null {
  let best: PlacedStage | null = null;
  for (const p of run.stages) {
    if (p.stage.status === "skipped" || p.lane > 0) continue;
    if (!best || p.end - p.start > best.end - best.start) best = p;
  }
  if (!best || run.total <= 0) return null;
  const ms = best.end - best.start;
  return { stage: best.stage, ms, share: ms / run.total };
}

/** "6m 20s", "41s", "1h 2m". */
export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  if (m > 0) return sec > 0 ? `${m}m ${sec}s` : `${m}m`;
  return `${sec}s`;
}

/** A clock reading for the playhead: "04:12" or "1:02:09". */
export function formatClock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** A run still going, for the Live story: the timeline so far and the plan it follows. */
export interface LiveRun {
  timeline: RunTimeline;
  now: number;
  /** Planned duration of each stage, ms, in order. */
  plan: number[];
  names: [string, RunStage["kind"]][];
}

/** Start the reference release run from its first stage. */
export function makeLiveRun(now = Date.UTC(2026, 8, 28, 14, 0, 0)): LiveRun {
  const ref = makeTimeline({ now });
  const plan = ref.stages.map((s) => (s.finishedAt ?? s.startedAt) - s.startedAt);
  const names = ref.stages.map((s) => [s.name, s.kind] as [string, RunStage["kind"]]);
  return {
    now,
    plan,
    names,
    timeline: {
      runId: ref.runId,
      label: ref.label,
      startedAt: now,
      finishedAt: null,
      stages: [
        {
          id: "st0",
          name: names[0][0],
          kind: names[0][1],
          startedAt: now,
          finishedAt: null,
          status: "running",
        },
      ],
    },
  };
}

/**
 * A Research digest run that fanned out to six sources in parallel (one
 * failed, the slowest took seven minutes), then summarised and published.
 */
export function makeFanoutTimeline({
  now = Date.UTC(2026, 8, 28, 14, 0, 0),
}: { now?: number } = {}): RunTimeline {
  const gather = 120_000;
  const branches: [number, number, "succeeded" | "failed"][] = [
    [0, 140_000, "succeeded"],
    [2_000, 260_000, "succeeded"],
    [3_000, 180_000, "succeeded"],
    [4_000, 420_000, "succeeded"],
    [6_000, 90_000, "failed"],
    [8_000, 310_000, "succeeded"],
  ];
  const fanMs = Math.max(...branches.map(([off, d]) => off + d));
  const summarise = 150_000;
  const publish = 40_000;
  const startedAt = now - (gather + fanMs + summarise + publish);
  const fanAt = startedAt + gather;
  const joinAt = fanAt + fanMs;
  const stages: RunTimeline["stages"] = [
    {
      id: "st0",
      name: "Gather",
      kind: "stage",
      startedAt,
      finishedAt: fanAt,
      status: "succeeded",
    },
    {
      id: "st1",
      name: "Fan out",
      kind: "fanout",
      startedAt: fanAt,
      finishedAt: joinAt,
      status: "succeeded",
    },
    ...branches.map(([off, d, status], b) => ({
      id: `st1b${b}`,
      name: `Source ${b + 1}`,
      kind: "stage" as const,
      startedAt: fanAt + off,
      finishedAt: fanAt + off + d,
      status,
      branchId: `b${b}`,
    })),
    {
      id: "st2",
      name: "Summarise",
      kind: "join",
      startedAt: joinAt,
      finishedAt: joinAt + summarise,
      status: "succeeded",
    },
    {
      id: "st3",
      name: "Publish",
      kind: "stage",
      startedAt: joinAt + summarise,
      finishedAt: now,
      status: "succeeded",
    },
  ];
  return {
    runId: "run-6120",
    label: "Research digest · weekly sources · #6120",
    startedAt,
    finishedAt: now,
    stages,
  };
}

/** Real time is compressed so a 15-minute run plays out in a story. */
const LIVE_SPEED = 40;

/**
 * Advance a live run: the open stage finishes once its planned time is up,
 * the next one opens, and a gate holds (waiting) until someone approves it.
 */
export function stepLiveRun(prev: LiveRun, rng: () => number, dtMs: number): LiveRun {
  if (prev.timeline.finishedAt !== null) return prev;
  const now = prev.now + dtMs * LIVE_SPEED;
  const stages = [...prev.timeline.stages];
  const i = stages.length - 1;
  const open = stages[i];
  let done = false;
  if (open.status === "waiting") {
    if (rng() < 0.3)
      stages[i] = { ...open, status: "succeeded", finishedAt: now, decidedBy: "ops@example.com" };
    done = stages[i].status === "succeeded";
  } else if (now - open.startedAt >= prev.plan[i]) {
    stages[i] = { ...open, status: "succeeded", finishedAt: open.startedAt + prev.plan[i] };
    done = true;
  }
  let finishedAt: number | null = null;
  if (done) {
    const at = stages[i].finishedAt!;
    const next = prev.names[i + 1];
    if (next) {
      stages.push({
        id: `st${i + 1}`,
        name: next[0],
        kind: next[1],
        startedAt: at,
        finishedAt: null,
        status: next[1] === "gate" ? "waiting" : "running",
      });
    } else finishedAt = at;
  }
  return { ...prev, now, timeline: { ...prev.timeline, stages, finishedAt } };
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}
