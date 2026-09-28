import { iso, type Point } from "../core/iso";
import type { Health } from "../core/semantics";
import type {
  TrainState,
  WorkflowLine,
  WorkflowSnapshot,
  WorkflowTrain,
} from "../core/workflow-model";

/**
 * Where things sit on the isometric assembly line, kept pure so it is tested
 * apart from the SVG. World units: a line runs along x, lines stack along y.
 */

/** World distance between stations on a line. */
export const STEP = 3.2;
/** World distance between lines. */
export const LANE = 3.4;
/** Machine footprint (stages). */
export const MACHINE = 1.3;
/** Airlock booth footprint along the belt (gates). */
export const BOOTH = 0.7;
/** Belt width across the line. */
export const BELT = 0.9;
/** Crates queue this far apart when several wait at one place. */
export const QUEUE_GAP = 0.6;
/** A segment backlog at or above this turns the next station's intake warning. */
export const BACKLOG_WARN = 3;

export function stationWidth(line: WorkflowLine, j: number): number {
  return line.stations[j]?.kind === "gate" ? BOOTH : MACHINE;
}

export function laneY(index: number): number {
  return index * LANE;
}

export function beltStart(): number {
  return -MACHINE / 2 - 0.5;
}

export function beltEnd(line: WorkflowLine): number {
  return (line.stations.length - 1) * STEP + MACHINE / 2 + 1.6;
}

/**
 * The health a station shows: a failed run there is failing; a gate holding
 * runs, or a stage whose intake is backed up, is degraded; a stage with a run
 * leaving it is working (ok); anything else is idle and stays dark.
 */
export function stationHealth(snapshot: WorkflowSnapshot, line: WorkflowLine, j: number): Health {
  const here = snapshot.trains.filter((t) => t.lineId === line.id && t.at === j);
  if (here.some((t) => t.state === "failed")) return "failing";
  if (line.stations[j].kind === "gate") {
    return here.some((t) => t.state === "held") ? "degraded" : "idle";
  }
  const intake = snapshot.segments.find((s) => s.lineId === line.id && s.from === j - 1);
  if (intake && intake.backlog >= BACKLOG_WARN) return "degraded";
  if (here.some((t) => t.state === "moving")) return "ok";
  return "idle";
}

/** Crate colour by run state; status meaning comes from semantics, not here. */
export const CRATE_HEALTH: Record<TrainState, Health> = {
  moving: "ok",
  held: "degraded",
  failed: "failing",
  done: "idle",
};

export interface CratePlacement {
  x: number;
  y: number;
}

function baseX(train: WorkflowTrain, line: WorkflowLine): number {
  const last = line.stations.length - 1;
  const at = Math.max(0, Math.min(last, train.at));
  const half = stationWidth(line, at) / 2;
  switch (train.state) {
    case "held":
      // Waits outside the closed booth, on the intake side.
      return at * STEP - half - 0.45;
    case "failed":
      // Dropped just past the machine that failed it.
      return at * STEP + half + 0.45;
    case "done":
      return last * STEP + MACHINE / 2 + 0.9;
    case "moving": {
      if (at >= last) return last * STEP + MACHINE / 2 + 0.9;
      const start = at * STEP + half + 0.35;
      const end = (at + 1) * STEP - stationWidth(line, at + 1) / 2 - 0.35;
      return start + (end - start) * Math.max(0, Math.min(1, train.progress));
    }
  }
}

/**
 * World position of every crate. Crates that wait at the same place (held at
 * one gate, failed at one machine, finished) queue instead of overlapping:
 * held ones back up the belt, the others line up after.
 */
export function placeCrates(snapshot: WorkflowSnapshot): Map<string, CratePlacement> {
  const lineIndex = new Map(snapshot.lines.map((l, i) => [l.id, i]));
  const byId = new Map(snapshot.lines.map((l) => [l.id, l]));
  const queued = new Map<string, number>();
  const out = new Map<string, CratePlacement>();
  for (const t of snapshot.trains) {
    const line = byId.get(t.lineId);
    const li = lineIndex.get(t.lineId);
    if (!line || li === undefined) continue;
    let x = baseX(t, line);
    if (t.state !== "moving") {
      const key = `${t.lineId}:${t.at}:${t.state}`;
      const k = queued.get(key) ?? 0;
      queued.set(key, k + 1);
      // Finished crates pile up past the end rather than running off the belt.
      const slot = t.state === "done" ? Math.min(k, 2) : k;
      x += (t.state === "held" ? -1 : 1) * slot * QUEUE_GAP;
    }
    out.set(t.id, { x, y: laneY(li) });
  }
  return out;
}

export interface ViewBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Room left of each line for its name, and below machines for station names. */
const LABEL_LEFT = 120;
const PAD = 24;

/** The screen rectangle that holds every belt, machine and label. */
export function isoViewBox(snapshot: WorkflowSnapshot, unit: number): ViewBox {
  const pts: Point[] = [];
  snapshot.lines.forEach((line, i) => {
    const y = laneY(i);
    const x0 = beltStart();
    const x1 = beltEnd(line);
    pts.push(
      iso(x0, y - BELT, 0, unit),
      iso(x0, y + BELT, 0, unit),
      iso(x1, y - BELT, 0, unit),
      iso(x1, y + BELT, 0, unit),
      iso(x0, y - MACHINE, 2, unit)
    );
  });
  if (pts.length === 0) return { x: 0, y: 0, w: 320, h: 160 };
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const minX = Math.min(...xs) - LABEL_LEFT;
  const minY = Math.min(...ys) - PAD;
  return {
    x: minX,
    y: minY,
    w: Math.max(...xs) - minX + PAD,
    h: Math.max(...ys) - minY + PAD + 16,
  };
}

/** The sentence the picture says, for its aria-label. */
export function summarize(snapshot: WorkflowSnapshot): string {
  const count = (s: TrainState) => snapshot.trains.filter((t) => t.state === s).length;
  const lines = snapshot.lines.length;
  const parts = [
    `${lines} workflow${lines === 1 ? "" : "s"}`,
    `${snapshot.trains.length} runs`,
    `${count("moving")} moving`,
    `${count("held")} held at gates`,
    `${count("failed")} failed`,
  ];
  const backedUp = snapshot.segments.filter((s) => s.backlog >= BACKLOG_WARN).length;
  if (backedUp > 0) parts.push(`${backedUp} backed up`);
  return parts.join(", ");
}

/** Cut a name to `max` characters with an ellipsis; the full name goes in a <title>. */
export function truncate(name: string, max: number): string {
  return name.length <= max ? name : `${name.slice(0, Math.max(1, max - 1))}…`;
}

/** Ease-out used for crate tweens between snapshots. */
export function easeOut(t: number): number {
  const c = Math.max(0, Math.min(1, t));
  return 1 - (1 - c) ** 3;
}
