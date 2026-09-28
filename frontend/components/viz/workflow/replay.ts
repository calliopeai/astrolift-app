import type { Health } from "../core/semantics";
import { makeTimeline, type RunTimeline } from "../core/workflow-model";

/**
 * Pure logic behind the run replay: where each stage sits on a track scaled
 * to real time, which stage the playhead is in, the stage boundaries the
 * arrow keys step between, and the slowest-stage callout. Times here are ms
 * offsets from the run's start, so the track is 0..total.
 */

export type RunStage = RunTimeline["stages"][number];

export interface PlacedStage {
  stage: RunStage;
  /** Offset of the stage start from the run start, ms. */
  start: number;
  /** Offset of the stage end, ms (a running stage ends at `now`). */
  end: number;
}

export interface PlacedRun {
  total: number;
  stages: PlacedStage[];
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

/** Lay every stage on the track. An open stage runs to the next stage's start or the run end. */
export function placeRun(timeline: RunTimeline, now?: number): PlacedRun {
  const end = runEnd(timeline, now);
  const total = Math.max(0, end - timeline.startedAt);
  const stages = timeline.stages.map((stage, i) => {
    const next = timeline.stages[i + 1];
    const stop = stage.finishedAt ?? (next ? next.startedAt : end);
    const start = clamp(stage.startedAt - timeline.startedAt, 0, total);
    return { stage, start, end: clamp(stop - timeline.startedAt, start, total) };
  });
  return { total, stages };
}

/**
 * The stage the playhead is in or has most recently entered, ignoring skipped
 * stages (they never ran, so the playhead never lights them). -1 before the
 * first stage starts.
 */
export function stageIndexAt(run: PlacedRun, t: number): number {
  let idx = -1;
  run.stages.forEach((p, i) => {
    if (p.stage.status !== "skipped" && p.start <= t) idx = i;
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

/** The longest stage that actually ran, and its share of the run. */
export function slowestStage(
  run: PlacedRun
): { stage: RunStage; ms: number; share: number } | null {
  let best: PlacedStage | null = null;
  for (const p of run.stages) {
    if (p.stage.status === "skipped") continue;
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
