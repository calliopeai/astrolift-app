import type { Health } from "../core/semantics";
import type {
  WorkflowLine,
  WorkflowLoop,
  WorkflowSnapshot,
  WorkflowStation,
  WorkflowTrain,
} from "../core/workflow-model";
import { isNearBound, joinReady, loopLabel, loopRound, loopsAt } from "../core/workflow-shapes";

import { BACKLOG_WARN } from "./workflow-graph-layout";

/**
 * The rows of the list view's stage table: one per stage of every workflow,
 * in line order, with each loop stated in words on its own row right after
 * the stage it leaves. Pure, so the words are tested once.
 */

export type StageStateWord =
  | "Failed"
  | "Sending back"
  | "Held"
  | "Waiting for approval"
  | "Backed up"
  | "Running"
  | "Finished"
  | "Idle";

export const STATE_WORD_HEALTH: Record<StageStateWord, Health> = {
  Failed: "failing",
  "Sending back": "degraded",
  Held: "degraded",
  "Waiting for approval": "degraded",
  "Backed up": "degraded",
  Running: "ok",
  Finished: "idle",
  Idle: "idle",
};

interface RowBase {
  id: string;
  line: WorkflowLine;
  /** "Quarterly close / Reconcile" for a child line. */
  workflow: string;
  state: StageStateWord;
  /** "3 / 5": the furthest round among the runs here, against its bound. */
  round: { text: string; near: boolean } | null;
}

export interface StageRow extends RowBase {
  type: "stage";
  station: WorkflowStation;
  index: number;
  kind: string;
  runs: WorkflowTrain[];
  /** "2 / 3" on a stage with a retry; the attempt alone elsewhere once past the first. */
  attempts: string | null;
  /** Fan-out, join, supervisor and nested progress in words: "4 / 6 done". */
  progress: string | null;
}

export interface LoopRow extends RowBase {
  type: "loop";
  loop: WorkflowLoop;
  kind: "Loop" | "Retry";
  /** The loop in words: "Test failed goes back to Code, round 3 of 5". */
  words: string;
  /** Runs travelling it now. */
  runs: WorkflowTrain[];
}

export type WorkflowRow = StageRow | LoopRow;

const KIND: Record<WorkflowStation["kind"], string> = {
  stage: "Stage",
  gate: "Gate",
  fanout: "Fan-out",
  join: "Join",
  supervisor: "Supervisor",
  workflow: "Nested workflow",
};

const PAST: Record<WorkflowLoop["trigger"], string> = {
  failed: "failed",
  rejected: "rejected",
  condition: "met its condition",
};

function stateOf(station: WorkflowStation, here: WorkflowTrain[], backlog: number): StageStateWord {
  if (here.some((t) => t.state === "failed")) return "Failed";
  if (here.some((t) => t.onLoopId && t.state === "moving")) return "Sending back";
  if (here.some((t) => t.state === "held")) {
    if (station.kind === "gate") return "Waiting for approval";
    // Runs held short of a busy fanout wait behind the one it is working for.
    if (!here.some((t) => t.state === "moving")) return "Held";
  }
  if (backlog > BACKLOG_WARN) return "Backed up";
  if (here.some((t) => t.state === "moving")) return "Running";
  if (here.some((t) => t.state === "done")) return "Finished";
  return "Idle";
}

/** The furthest round among these runs on the back-edges spanning `index`, against its bound. */
function furthestRound(line: WorkflowLine, runs: WorkflowTrain[], index: number): RowBase["round"] {
  const spans = (line.loops ?? []).filter(
    (l) => l.kind === "back-edge" && l.to < l.from && l.to <= index && index <= l.from
  );
  let best: { n: number; max: number; near: boolean } | null = null;
  for (const t of runs)
    for (const l of spans) {
      const n = loopRound(t, l);
      if (!best || n / l.maxRounds > best.n / best.max)
        best = { n, max: l.maxRounds, near: isNearBound(t, l) };
    }
  if (best) return { text: `${best.n} / ${best.max}`, near: best.near };
  const r = Math.max(1, ...runs.map((t) => t.round ?? 1));
  return r > 1 ? { text: `${r}`, near: false } : null;
}

/**
 * A loop in words. With a run on it: "Test failed goes back to Code, round 3
 * of 5". Otherwise the loop and its bound, and where the furthest run inside
 * it stands.
 */
export function loopWords(line: WorkflowLine, loop: WorkflowLoop, trains: WorkflowTrain[]): string {
  const from = line.stations[loop.from]?.name ?? "stage";
  const to = line.stations[loop.to]?.name ?? "stage";
  const what = loop.condition ?? PAST[loop.trigger];
  const on = trains.filter((t) => t.onLoopId === loop.id);
  const lead = (ts: WorkflowTrain[]) =>
    ts.reduce<WorkflowTrain | null>(
      (a, t) => (!a || loopRound(t, loop) > loopRound(a, loop) ? t : a),
      null
    );
  if (loop.kind === "retry" || loop.from === loop.to) {
    const t = lead(on);
    if (t)
      return `${from} ${what}, retrying ${from}, attempt ${loopRound(t, loop) + 1} of ${loop.maxRounds}`;
    return loopLabel(line, loop);
  }
  const t = lead(on);
  if (t)
    return `${from} ${what} goes back to ${to}, round ${loopRound(t, loop) + 1} of ${loop.maxRounds}`;
  const inside = lead(
    trains.filter(
      (x) => !x.onLoopId && x.at >= loop.to && x.at <= loop.from && loopRound(x, loop) > 1
    )
  );
  const base = loopLabel(line, loop);
  return inside
    ? `${base}; ${inside.label} is on round ${loopRound(inside, loop)} of ${loop.maxRounds}`
    : base;
}

function progressOf(
  line: WorkflowLine,
  station: WorkflowStation,
  here: WorkflowTrain[],
  snapshot: WorkflowSnapshot,
  lines: Map<string, WorkflowLine>
): string | null {
  if (station.kind === "fanout") {
    const j = joinReady(station);
    if (!j.total) return station.dynamic ? "Branches decided per run" : "No branches yet";
    return `${j.settled} / ${j.total} done${j.failed ? `, ${j.failed} failed` : ""}`;
  }
  if (station.kind === "join") {
    const fan = line.stations[station.waitsOn];
    if (fan?.kind !== "fanout") return null;
    const j = joinReady(fan);
    return j.total
      ? `Waits on ${j.total} branches, ${j.ready ? "all settled" : `${j.pending} to go`}`
      : `Waits on ${fan.name}`;
  }
  if (station.kind === "supervisor") {
    const busy = station.workers.filter((w) => w.busy).length;
    const lead = here.find((t) => t.subtasks)?.subtasks;
    const tasks = lead ? `${lead.done} / ${lead.total} sub-tasks, ` : "";
    return `${tasks}${busy} of ${station.workers.length} workers busy`;
  }
  if (station.kind === "workflow") {
    const child = lines.get(station.childLineId);
    const run = here
      .map((t) => (t.childRunId ? snapshot.trains.find((c) => c.id === t.childRunId) : undefined))
      .find(Boolean);
    if (!child) return null;
    if (!run) return `Runs ${child.name}`;
    return `Child run at ${child.stations[run.at]?.name ?? "start"}, ${run.at + 1} / ${child.stations.length}`;
  }
  return null;
}

/** Every stage row and loop row, workflow by workflow; a child line follows its parent. */
export function workflowRows(snapshot: WorkflowSnapshot): WorkflowRow[] {
  const lines = new Map(snapshot.lines.map((l) => [l.id, l]));
  const ordered: WorkflowLine[] = [];
  const children = snapshot.lines.filter((l) => l.parent && lines.has(l.parent.lineId));
  for (const l of snapshot.lines) {
    if (children.includes(l)) continue;
    ordered.push(l, ...children.filter((c) => c.parent!.lineId === l.id));
  }
  const rows: WorkflowRow[] = [];
  for (const line of ordered) {
    const parent = line.parent ? lines.get(line.parent.lineId) : undefined;
    const workflow = parent ? `${parent.name} / ${line.name}` : line.name;
    const trains = snapshot.trains.filter((t) => t.lineId === line.id);
    const segs = snapshot.segments.filter((s) => s.lineId === line.id);
    line.stations.forEach((station, index) => {
      const here = trains.filter((t) => t.at === index);
      const backlog = segs.find((s) => s.from === index - 1)?.backlog ?? 0;
      const retry = loopsAt(line, index).find((l) => l.kind === "retry" || l.to === l.from);
      const attempt = Math.max(0, ...here.map((t) => t.attempt ?? 1));
      rows.push({
        type: "stage",
        id: station.id,
        line,
        workflow,
        station,
        index,
        kind: KIND[station.kind],
        state: stateOf(station, here, backlog),
        runs: here,
        round: furthestRound(line, here, index),
        attempts: retry
          ? `${Math.max(1, attempt)} / ${retry.maxRounds}`
          : attempt > 1
            ? `${attempt}`
            : null,
        progress: progressOf(line, station, here, snapshot, lines),
      });
      for (const loop of loopsAt(line, index)) {
        const on = trains.filter((t) => t.onLoopId === loop.id);
        const lead = on.reduce<WorkflowTrain | null>(
          (a, t) => (!a || loopRound(t, loop) > loopRound(a, loop) ? t : a),
          null
        );
        const next = lead ? loopRound(lead, loop) + 1 : null;
        rows.push({
          type: "loop",
          id: loop.id,
          line,
          workflow,
          loop,
          kind: loop.kind === "retry" || loop.to === loop.from ? "Retry" : "Loop",
          words: loopWords(line, loop, trains),
          state: on.length ? "Sending back" : "Idle",
          runs: on,
          round:
            next === null
              ? null
              : { text: `${next} / ${loop.maxRounds}`, near: next >= loop.maxRounds - 1 },
        });
      }
    });
  }
  return rows;
}
