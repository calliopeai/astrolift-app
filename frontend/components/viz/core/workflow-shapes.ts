import type {
  FanoutBranch,
  FanoutStation,
  RunTimeline,
  WorkflowLine,
  WorkflowLoop,
  WorkflowTrain,
} from "./workflow-model";

/**
 * Pure questions the workflow views ask about loops, rounds and fanouts, so
 * each renderer draws the same answer: which loops leave a station, which
 * round a run is on against a loop's bound, when to warn, and whether a join
 * can go.
 */

/** Loops leaving station `index` (a retry leaves and returns to the same station). */
export function loopsAt(line: WorkflowLine, index: number): WorkflowLoop[] {
  return (line.loops ?? []).filter((l) => l.from === index);
}

/**
 * The run's 1-based round on this loop: its attempt for a retry, or one more
 * than the times it has taken the back-edge.
 */
export function loopRound(train: WorkflowTrain, loop: WorkflowLoop): number {
  if (loop.kind === "retry") return train.attempt ?? 1;
  return (train.loopRounds?.[loop.id] ?? 0) + 1;
}

/** Rounds the run may still take on this loop before it fails. */
export function roundsLeft(train: WorkflowTrain, loop: WorkflowLoop): number {
  return Math.max(0, loop.maxRounds - loopRound(train, loop));
}

/** Warn once a run is one round from the bound (or on it). */
export function isNearBound(train: WorkflowTrain, loop: WorkflowLoop): boolean {
  return loopRound(train, loop) >= loop.maxRounds - 1;
}

/** "round 3 of 5" or "attempt 2 of 3", for labels and accessible names. */
export function roundLabel(train: WorkflowTrain, loop: WorkflowLoop): string {
  const word = loop.kind === "retry" ? "attempt" : "round";
  return `${word} ${loopRound(train, loop)} of ${loop.maxRounds}`;
}

const TRIGGER_WORD: Record<WorkflowLoop["trigger"], string> = {
  failed: "fails",
  rejected: "is rejected",
  condition: "meets its condition",
};

/** A loop in words, always with its bound: "Back to Code when Test fails, at most 5 rounds". */
export function loopLabel(line: WorkflowLine, loop: WorkflowLoop): string {
  const from = line.stations[loop.from]?.name ?? "stage";
  const when = loop.condition ?? `${from} ${TRIGGER_WORD[loop.trigger]}`;
  if (loop.kind === "retry")
    return `Retry ${from} when it ${TRIGGER_WORD[loop.trigger]}, at most ${loop.maxRounds} attempts`;
  const to = line.stations[loop.to]?.name ?? "stage";
  return `Back to ${to} when ${when}, at most ${loop.maxRounds} rounds`;
}

export type TimelineStage = RunTimeline["stages"][number];

/** A run's stage executions grouped by round, rounds in order, each in start order. */
export function groupTimelineByRound(
  timeline: RunTimeline
): { round: number; stages: TimelineStage[] }[] {
  const byRound = new Map<number, TimelineStage[]>();
  for (const s of timeline.stages) {
    const r = s.round ?? 1;
    byRound.set(r, [...(byRound.get(r) ?? []), s]);
  }
  return [...byRound.entries()]
    .sort(([a], [b]) => a - b)
    .map(([round, stages]) => ({
      round,
      stages: [...stages].sort((a, b) => a.startedAt - b.startedAt),
    }));
}

export interface JoinState {
  /** Every branch has settled (succeeded or failed), so the join can merge. */
  ready: boolean;
  total: number;
  settled: number;
  succeeded: number;
  failed: number;
  /** Not settled yet (queued or running). */
  pending: number;
}

/** Whether a fanout's join can go, and the branch counts behind the answer. */
export function joinReady(fanout: FanoutStation | FanoutBranch[]): JoinState {
  const branches = Array.isArray(fanout) ? fanout : fanout.branches;
  let succeeded = 0;
  let failed = 0;
  let pending = 0;
  for (const b of branches) {
    if (b.state === "succeeded") succeeded++;
    else if (b.state === "failed") failed++;
    else pending++;
  }
  const settled = succeeded + failed;
  const total = branches.length;
  return { ready: total > 0 && settled === total, total, settled, succeeded, failed, pending };
}
