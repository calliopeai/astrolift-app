import { iso, type Point } from "../core/iso";
import { HEALTH_LABEL, type Health } from "../core/semantics";
import type {
  BranchState,
  SupervisorWorker,
  TrainState,
  WorkflowLine,
  WorkflowLoop,
  WorkflowSnapshot,
  WorkflowStation,
  WorkflowTrain,
} from "../core/workflow-model";
import { joinReady, loopLabel, loopRound, loopsAt, roundLabel } from "../core/workflow-shapes";

/**
 * Where things sit on the isometric assembly line, kept pure so it is tested
 * apart from the SVG. World units: a line runs along x, lines stack along y,
 * z is up.
 *
 * Shapes: a back-edge loop builds a tower over the stations it spans, one
 * deck per round, and a run on round r rides deck r - 1 (round 1 is the
 * belt), so a run that went round four times sits visibly higher. A run sent
 * back climbs a return ramp from the station it left to the earlier one, one
 * level up. A retry is a hoop over its own stage. A fanout's branches ride
 * raised rails to their join; a supervisor's workers orbit its hub on a
 * tile; a nested workflow opens into a sub-platform over its block.
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
/** Belt thickness; crates on the belt stand on it. */
export const BELT_H = 0.12;
/** Crates queue this far apart when several wait at one place. */
export const QUEUE_GAP = 0.6;
/** A segment backlog at or above this turns the next station's intake warning. */
export const BACKLOG_WARN = 3;

/** Height of one round's deck above the last. */
export const LEVEL = 1.3;
/** Deck thickness. */
export const DECK = 0.08;
/** How high a retry hoop arches over its stage. */
export const HOOP = 1.5;
/** Height of a fanout's rails. */
export const RAIL_Z = 0.8;
/** Furthest a rail swings out from the belt, across the line. */
export const RAIL_SPREAD = 1.15;
/** Radius of a supervisor's worker orbit. */
export const ORBIT = 1.25;
/** Side of the supervisor's tile. */
export const TILE = 3.1;
/** Height of an open nested workflow's sub-platform. */
export const PLATFORM_Z = 2;
/** Distance between child stages on a sub-platform, at most. */
export const SUB_STEP = 1.1;
/** Extra room after a fanout, so its rails have length to spread. */
export const FAN_EXTRA = 2.4;
/** Extra room between lanes when either draws a shape (rails, a tile, a tower, a platform). */
export const WIDE_GAP = 1.2;

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

export function stationWidth(line: WorkflowLine, j: number): number {
  return line.stations[j]?.kind === "gate" ? BOOTH : MACHINE;
}

/** Where station `j` sits along its line; a fanout leaves extra room after it. */
export function stationX(line: WorkflowLine, j: number): number {
  let x = j * STEP;
  for (let k = 0; k < j && k < line.stations.length; k++)
    if (line.stations[k].kind === "fanout") x += FAN_EXTRA;
  return x;
}

function isWide(line: WorkflowLine): boolean {
  return !!line.loops?.length || line.stations.some((s) => s.kind !== "stage" && s.kind !== "gate");
}

/** Each lane's y: LANE apart, plus WIDE_GAP next to a lane that draws a shape. */
export function laneOffsets(lanes: WorkflowLine[]): number[] {
  let y = 0;
  return lanes.map((l, i) => {
    if (i > 0) y += LANE + (isWide(l) || isWide(lanes[i - 1]) ? WIDE_GAP : 0);
    return y;
  });
}

export function beltStart(): number {
  return -MACHINE / 2 - 0.5;
}

export function beltEnd(line: WorkflowLine): number {
  return stationX(line, line.stations.length - 1) + MACHINE / 2 + 1.6;
}

/**
 * The lines drawn as lanes: every line except a nested child whose parent is
 * in the snapshot (that one is drawn on its parent's sub-platform).
 */
export function laneLines(snapshot: WorkflowSnapshot): WorkflowLine[] {
  const ids = new Set(snapshot.lines.map((l) => l.id));
  return snapshot.lines.filter((l) => !(l.parent && ids.has(l.parent.lineId)));
}

/** The child line a nested station runs, when it is in the snapshot. */
export function childLineOf(
  snapshot: WorkflowSnapshot,
  station: WorkflowStation
): WorkflowLine | undefined {
  if (station.kind !== "workflow") return undefined;
  return snapshot.lines.find((l) => l.id === station.childLineId);
}

/* ---------- Loops: the tower of rounds, return ramps and retry hoops ---------- */

/** Loops back to an earlier station, in the line's order. */
export function backEdges(line: WorkflowLine): WorkflowLoop[] {
  return (line.loops ?? []).filter((l) => l.to < l.from);
}

/** The stations the back-edges span, lowest `to` to highest `from`; null with none. */
export function towerSpan(line: WorkflowLine): { lo: number; hi: number } | null {
  const edges = backEdges(line);
  if (edges.length === 0) return null;
  return {
    lo: Math.min(...edges.map((l) => l.to)),
    hi: Math.max(...edges.map((l) => l.from)),
  };
}

/** Top of the surface at `level`: the belt at 0, else that round's deck. */
export function levelZ(level: number): number {
  return level > 0 ? level * LEVEL + DECK : BELT_H;
}

/** The deck a run rides: its round minus one while it is over the tower, else the belt. */
export function crateLevel(line: WorkflowLine, t: WorkflowTrain): number {
  const span = towerSpan(line);
  if (!span || t.state === "done") return 0;
  if (t.at < span.lo || t.at > span.hi) return 0;
  return Math.max(0, (t.round ?? 1) - 1);
}

/**
 * How many decks the tower shows: up to the highest round a run over it is
 * on, or climbing to.
 */
export function towerHeight(snapshot: WorkflowSnapshot, line: WorkflowLine): number {
  let top = 0;
  for (const t of snapshot.trains) {
    if (t.lineId !== line.id) continue;
    const loop = t.onLoopId ? line.loops?.find((l) => l.id === t.onLoopId) : undefined;
    const climbing = loop && loop.to < loop.from ? 1 : 0;
    top = Math.max(top, crateLevel(line, t) + climbing);
  }
  return top;
}

export interface DeckState {
  level: number;
  /** Some run is on this round now (or climbing to it); otherwise an earlier round's ghost. */
  lit: boolean;
}

/** The tower's decks, bottom first. */
export function towerDecks(snapshot: WorkflowSnapshot, line: WorkflowLine): DeckState[] {
  const height = towerHeight(snapshot, line);
  const current = new Set<number>();
  for (const t of snapshot.trains) {
    if (t.lineId !== line.id || t.state === "done") continue;
    const loop = t.onLoopId ? line.loops?.find((l) => l.id === t.onLoopId) : undefined;
    if (loop && loop.to < loop.from) current.add(crateLevel(line, t) + 1);
    else if (!loop) current.add(crateLevel(line, t));
  }
  return Array.from({ length: height }, (_, k) => ({ level: k + 1, lit: current.has(k + 1) }));
}

/** The y a loop's track runs at: behind the belt, one lane of track per back-edge. */
export function loopTrackY(line: WorkflowLine, loop: WorkflowLoop, y: number): number {
  const k = Math.max(0, backEdges(line).indexOf(loop));
  return y - MACHINE / 2 - 0.35 - (loop.to < loop.from ? k * 0.45 : 0);
}

/** The deck's extent across the line, far edge first: it covers the return tracks. */
export function deckDepth(line: WorkflowLine, y: number): { y0: number; y1: number } {
  const edges = backEdges(line);
  const far = edges.length ? loopTrackY(line, edges[edges.length - 1], y) - 0.25 : y - BELT;
  return { y0: far, y1: y + BELT / 2 + 0.2 };
}

/**
 * A point on a loop, s from 0 (leaving `from`) to 1 (arriving). A back-edge
 * climbs from level to level + 1 as it runs back; a retry arches over its own
 * stage and comes down on the same level.
 */
export function loopPoint(
  line: WorkflowLine,
  loop: WorkflowLoop,
  level: number,
  s: number,
  y: number
): Vec3 {
  const ty = loopTrackY(line, loop, y);
  const k = clamp01(s);
  if (loop.to === loop.from) {
    const half = stationWidth(line, loop.from) / 2 + 0.3;
    const cx = stationX(line, loop.from);
    return { x: cx + half - 2 * half * k, y: ty, z: levelZ(level) + Math.sin(Math.PI * k) * HOOP };
  }
  const fx = stationX(line, loop.from);
  const x = fx + (stationX(line, loop.to) - fx) * k;
  return { x, y: ty, z: levelZ(level) + (levelZ(level + 1) - levelZ(level)) * k };
}

/**
 * The loop a run's round is counted on: the one it travels, else one leaving
 * its station, else the back-edge it is inside.
 */
export function runLoop(line: WorkflowLine, t: WorkflowTrain): WorkflowLoop | undefined {
  if (t.onLoopId) return line.loops?.find((l) => l.id === t.onLoopId);
  const leaving = loopsAt(line, t.at)[0];
  if (leaving) return leaving;
  return backEdges(line)
    .filter((l) => l.to <= t.at && t.at < l.from)
    .sort((a, b) => a.from - b.from)[0];
}

/** Badge text for a run past its first round on a loop ("3/5"), or null. */
export function roundBadge(line: WorkflowLine, t: WorkflowTrain): string | null {
  const loop = runLoop(line, t);
  if (!loop) return null;
  const r = loopRound(t, loop);
  return r > 1 ? `${r}/${loop.maxRounds}` : null;
}

/* ---------- Fanout rails and the join ---------- */

/** The join a fanout merges at: the join waiting on it, else the next station. */
export function joinFor(line: WorkflowLine, fan: number): number {
  const j = line.stations.findIndex((s) => s.kind === "join" && s.waitsOn === fan);
  return j >= 0 ? j : Math.min(line.stations.length - 1, fan + 1);
}

const smooth = (u: number) => {
  const c = clamp01(u);
  return c * c * (3 - 2 * c);
};

/** A point on branch `b` of `n`'s rail, s from the fanout (0) to its join (1). */
export function railPoint(
  line: WorkflowLine,
  fan: number,
  b: number,
  n: number,
  s: number,
  y: number
): Vec3 {
  const join = joinFor(line, fan);
  const sx = stationX(line, fan) + stationWidth(line, fan) / 2 + 0.1;
  const ex = stationX(line, join) - stationWidth(line, join) / 2 - 0.1;
  const off = n <= 1 ? 0 : (b / (n - 1) - 0.5) * 2 * RAIL_SPREAD;
  const k = clamp01(s);
  const swing = k < 0.25 ? smooth(k / 0.25) : k > 0.75 ? smooth((1 - k) / 0.25) : 1;
  return { x: sx + (ex - sx) * k, y: y + off * swing, z: RAIL_Z };
}

/** Where a branch's pellet rests on its rail: settling carries it to the join. */
export const BRANCH_S: Record<BranchState, number> = {
  queued: 0.08,
  running: 0.5,
  succeeded: 0.93,
  failed: 0.5,
};

/* ---------- Supervisor workers ---------- */

/** A worker's resting angle on the orbit, spread evenly. */
export function workerAngle(i: number, n: number): number {
  return -Math.PI / 2 + (i / Math.max(1, n)) * Math.PI * 2;
}

/** Radians per second: a busy worker orbits, faster with more load; an idle one stands still. */
export function workerSpeed(w: SupervisorWorker): number {
  return w.busy ? 0.35 + clamp01(w.load) * 1.1 : 0;
}

export function workerPoint(cx: number, y: number, angle: number): Vec3 {
  return { x: cx + Math.cos(angle) * ORBIT, y: y + Math.sin(angle) * ORBIT, z: 0.06 };
}

/* ---------- Nested workflow sub-platform ---------- */

export function subStep(n: number): number {
  return Math.min(SUB_STEP, 3.3 / Math.max(1, n));
}

/** Centre x of child stage `i` of `n` on a sub-platform over the station at `cx`. */
export function subStationX(cx: number, n: number, i: number): number {
  const step = subStep(n);
  return cx - (n * step) / 2 + (i + 0.5) * step;
}

/* ---------- Health and placement ---------- */

/**
 * The health a station shows: a failed run there is failing; a gate holding
 * runs, a fanout with runs waiting for it, or a stage whose intake is backed
 * up, is degraded; a station with work under way is ok; anything else is
 * idle and stays dark.
 */
export function stationHealth(snapshot: WorkflowSnapshot, line: WorkflowLine, j: number): Health {
  const station = line.stations[j];
  const here = snapshot.trains.filter((t) => t.lineId === line.id && t.at === j && !t.onLoopId);
  if (here.some((t) => t.state === "failed")) return "failing";
  if (station.kind === "gate") {
    return here.some((t) => t.state === "held") ? "degraded" : "idle";
  }
  if (station.kind === "fanout" && here.some((t) => t.state === "held")) return "degraded";
  const intake = snapshot.segments.find((s) => s.lineId === line.id && s.from === j - 1);
  if (intake && intake.backlog >= BACKLOG_WARN) return "degraded";
  if (here.some((t) => t.state === "moving")) return "ok";
  if (station.kind === "join") {
    const fan = line.stations[station.waitsOn];
    if (fan?.kind === "fanout" && fan.runId) return "ok";
  }
  if (station.kind === "supervisor" && station.workers.some((w) => w.busy)) return "ok";
  return "idle";
}

/** Crate colour by run state; status meaning comes from semantics, not here. */
export const CRATE_HEALTH: Record<TrainState, Health> = {
  moving: "ok",
  held: "degraded",
  failed: "failing",
  done: "idle",
};

/**
 * Where a crate or pellet is. `lane` is the line it is drawn in; `track` is
 * set while it rides a curved path (a loop, a rail), so a tween can follow
 * the path instead of cutting across it; `child` marks a run on a nested
 * sub-platform.
 */
export interface CratePose extends Vec3 {
  lane: string;
  track?: { id: string; s: number; point: (s: number) => Vec3 };
  child?: boolean;
}

function baseX(train: WorkflowTrain, line: WorkflowLine): number {
  const last = line.stations.length - 1;
  const at = Math.max(0, Math.min(last, train.at));
  const half = stationWidth(line, at) / 2;
  switch (train.state) {
    case "held":
      // Waits outside the closed booth (or the busy fanout), on the intake side.
      return stationX(line, at) - half - 0.45;
    case "failed":
      // Dropped just past the machine that failed it.
      return stationX(line, at) + half + 0.45;
    case "done":
      return stationX(line, last) + MACHINE / 2 + 0.9;
    case "moving": {
      if (at >= last) return stationX(line, last) + MACHINE / 2 + 0.9;
      const start = stationX(line, at) + half + 0.35;
      const end = stationX(line, at + 1) - stationWidth(line, at + 1) / 2 - 0.35;
      return start + (end - start) * clamp01(train.progress);
    }
  }
}

/**
 * World position of every crate. Crates that wait at the same place (held at
 * one gate, failed at one machine, finished) queue instead of overlapping:
 * held ones back up the belt, the others line up after. Over a tower a crate
 * rides its round's deck, and leaving the tower it rolls back down to the
 * belt. A run on a loop is on its return track. A nested child run is placed
 * on its parent's sub-platform only while that is open (`open` holds the
 * open nested station ids).
 */
export function placeCrates(
  snapshot: WorkflowSnapshot,
  open: ReadonlySet<string> = new Set()
): Map<string, CratePose> {
  const lanes = laneLines(snapshot);
  const laneIndex = new Map(lanes.map((l, i) => [l.id, i]));
  const ys = laneOffsets(lanes);
  const byId = new Map(snapshot.lines.map((l) => [l.id, l]));
  const hosts = new Map<string, { lane: string; cx: number; y: number }>();
  lanes.forEach((l, i) =>
    l.stations.forEach((st, j) => {
      if (st.kind === "workflow" && open.has(st.id))
        hosts.set(st.childLineId, { lane: l.id, cx: stationX(l, j), y: ys[i] });
    })
  );
  const queued = new Map<string, number>();
  const out = new Map<string, CratePose>();
  for (const t of snapshot.trains) {
    const line = byId.get(t.lineId);
    if (!line) continue;
    const li = laneIndex.get(t.lineId);
    if (li === undefined) {
      const host = hosts.get(t.lineId);
      if (!host) continue;
      const n = line.stations.length;
      const step = subStep(n);
      const at = Math.max(0, Math.min(n - 1, t.at));
      const offset =
        t.state === "done"
          ? 0.35
          : t.state === "held"
            ? -0.4
            : t.state === "failed"
              ? 0.35
              : at < n - 1
                ? clamp01(t.progress)
                : 0.35;
      out.set(t.id, {
        x: subStationX(host.cx, n, at) + offset * step,
        y: host.y,
        z: PLATFORM_Z + DECK,
        lane: host.lane,
        child: true,
      });
      continue;
    }
    const y = ys[li];
    const level = crateLevel(line, t);
    const loop = t.onLoopId ? line.loops?.find((l) => l.id === t.onLoopId) : undefined;
    if (loop) {
      const point = (s: number) => loopPoint(line, loop, level, s, y);
      const s = clamp01(t.loopProgress ?? 0);
      out.set(t.id, {
        ...point(s),
        lane: line.id,
        track: { id: `loop:${loop.id}:${level}`, s, point },
      });
      continue;
    }
    let x = baseX(t, line);
    if (t.state !== "moving") {
      const key = `${t.lineId}:${t.at}:${t.state}:${level}`;
      const k = queued.get(key) ?? 0;
      queued.set(key, k + 1);
      // Finished crates pile up past the end rather than running off the belt.
      const slot = t.state === "done" ? Math.min(k, 2) : k;
      x += (t.state === "held" ? -1 : 1) * slot * QUEUE_GAP;
    }
    let z = levelZ(level);
    const span = towerSpan(line);
    if (
      level > 0 &&
      span &&
      t.at === span.hi &&
      t.state === "moving" &&
      t.at < line.stations.length - 1
    )
      z = BELT_H + (levelZ(level) - BELT_H) * (1 - clamp01(t.progress));
    out.set(t.id, { x, y, z, lane: line.id });
  }
  return out;
}

export interface PelletPose extends CratePose {
  state: BranchState;
  /** The fanout is held by a run now; otherwise these are the last run's, settled. */
  live: boolean;
  label: string;
}

/** Every fanout branch's pellet on its rail. */
export function placeBranches(snapshot: WorkflowSnapshot): Map<string, PelletPose> {
  const out = new Map<string, PelletPose>();
  const lanes = laneLines(snapshot);
  const ys = laneOffsets(lanes);
  lanes.forEach((line, li) => {
    const y = ys[li];
    line.stations.forEach((st, j) => {
      if (st.kind !== "fanout") return;
      const n = st.branches.length;
      st.branches.forEach((b, k) => {
        const point = (s: number) => railPoint(line, j, k, n, s, y);
        const s = BRANCH_S[b.state];
        out.set(b.id, {
          ...point(s),
          lane: line.id,
          track: { id: `rail:${st.id}:${k}:${n}`, s, point },
          state: b.state,
          live: !!st.runId,
          label: b.label,
        });
      });
    });
  });
  return out;
}

/* ---------- Accessible names ---------- */

const KIND_WORD: Record<WorkflowStation["kind"], string> = {
  stage: "",
  gate: " gate",
  fanout: " fan-out",
  join: " join",
  supervisor: " supervisor",
  workflow: " child workflow",
};

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** What a station is doing, in words. */
export function stationState(snapshot: WorkflowSnapshot, line: WorkflowLine, j: number): string {
  const st = line.stations[j];
  const health = stationHealth(snapshot, line, j);
  if (st.kind === "gate") {
    if (health === "failing") return "a run failed here";
    return health === "degraded" ? "holding runs" : "open";
  }
  if (st.kind === "fanout") {
    const r = joinReady(st);
    const waiting = snapshot.trains.filter(
      (t) => t.lineId === line.id && t.at === j && t.state === "held"
    ).length;
    const queue = waiting ? `, ${plural(waiting, "run")} waiting` : "";
    if (!st.runId)
      return r.total
        ? `idle, last run had ${plural(r.total, "branch", "branches")}${queue}`
        : `idle${queue}`;
    return `${plural(r.total, "branch", "branches")}, ${r.settled} settled, ${r.pending} running${
      r.failed ? `, ${r.failed} failed` : ""
    }${queue}`;
  }
  if (st.kind === "join") {
    const fan = line.stations[st.waitsOn];
    if (fan?.kind === "fanout" && fan.runId) {
      const r = joinReady(fan);
      return `waiting on ${r.pending} of ${plural(r.total, "branch", "branches")}`;
    }
    return HEALTH_LABEL[health].toLowerCase();
  }
  if (st.kind === "supervisor") {
    const busy = st.workers.filter((w) => w.busy).length;
    return `${busy} of ${plural(st.workers.length, "worker")} busy`;
  }
  if (st.kind === "workflow") {
    const child = childLineOf(snapshot, st);
    const parent = snapshot.trains.find((t) => t.lineId === line.id && t.at === j && t.childRunId);
    const run = parent && snapshot.trains.find((t) => t.id === parent.childRunId);
    if (health === "failing") return "a child run failed";
    if (!run || !child) return "idle";
    return run.state === "done"
      ? "child run finished"
      : `child run at ${child.stations[run.at]?.name ?? "a stage"}, stage ${run.at + 1} of ${child.stations.length}`;
  }
  return HEALTH_LABEL[health].toLowerCase();
}

/** A station's accessible name: what it is, its state, and every loop leaving it with its bound. */
export function stationName(snapshot: WorkflowSnapshot, line: WorkflowLine, j: number): string {
  const st = line.stations[j];
  const loops = loopsAt(line, j).map((l) => loopLabel(line, l));
  return `${line.name}, ${st.name}${KIND_WORD[st.kind]}: ${stationState(snapshot, line, j)}${
    loops.length ? `. ${loops.join(". ")}` : ""
  }`;
}

function runState(snapshot: WorkflowSnapshot, line: WorkflowLine, t: WorkflowTrain): string {
  const st = line.stations[t.at];
  if (t.onLoopId) {
    const loop = line.loops?.find((l) => l.id === t.onLoopId);
    if (loop?.kind === "retry") return `retrying ${st?.name ?? "the stage"}`;
    return `sent back to ${line.stations[loop?.to ?? 0]?.name ?? "an earlier stage"}`;
  }
  if (t.state === "held")
    return st?.kind === "fanout" ? "waiting for the fan-out to free up" : "held";
  if (t.state === "failed") return "failed";
  if (t.state === "done") return "finished";
  if (st?.kind === "fanout" && st.runId === t.id) {
    const r = joinReady(st);
    return `waiting on branches, ${r.settled} of ${r.total} settled`;
  }
  if (st?.kind === "supervisor" && t.subtasks)
    return `sub-tasks ${t.subtasks.done} of ${t.subtasks.total} done`;
  if (st?.kind === "workflow" && t.childRunId) {
    const child = snapshot.trains.find((c) => c.id === t.childRunId);
    const childLine = childLineOf(snapshot, st);
    if (child && childLine)
      return `child run at ${childLine.stations[child.at]?.name ?? "a stage"}`;
  }
  return "moving";
}

/** A run's accessible name: "Run #1207: Test, round 3 of 5, failed". */
export function runName(snapshot: WorkflowSnapshot, line: WorkflowLine, t: WorkflowTrain): string {
  const parts: string[] = [];
  const where = t.state === "done" ? undefined : line.stations[t.at]?.name;
  if (where) parts.push(where);
  const loop = runLoop(line, t);
  if (loop && (loopRound(t, loop) > 1 || t.onLoopId)) parts.push(roundLabel(t, loop));
  else if ((t.round ?? 1) > 1) parts.push(`round ${t.round}`);
  if (t.parentRunId) parts.push("child run");
  parts.push(runState(snapshot, line, t));
  return `Run ${t.label}: ${parts.join(", ")}`;
}

/* ---------- Frame ---------- */

export interface ViewBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Room left of each line for its name, and below machines for station names. */
const LABEL_LEFT = 120;
const PAD = 24;

/** The screen rectangle that holds every belt, machine, tower, platform and label. */
export function isoViewBox(
  snapshot: WorkflowSnapshot,
  unit: number,
  open: ReadonlySet<string> = new Set()
): ViewBox {
  const pts: Point[] = [];
  const lanes = laneLines(snapshot);
  const offsets = laneOffsets(lanes);
  lanes.forEach((line, i) => {
    const y = offsets[i];
    const x0 = beltStart();
    const x1 = beltEnd(line);
    pts.push(
      iso(x0, y - BELT, 0, unit),
      iso(x0, y + BELT, 0, unit),
      iso(x1, y - BELT, 0, unit),
      iso(x1, y + BELT, 0, unit),
      iso(x0, y - MACHINE, 2, unit)
    );
    const span = towerSpan(line);
    if (span) {
      const top = levelZ(Math.max(1, towerHeight(snapshot, line))) + 1;
      const d = deckDepth(line, y);
      pts.push(
        iso(stationX(line, span.lo) - MACHINE, d.y0, top, unit),
        iso(stationX(line, span.hi) + MACHINE, d.y0, top, unit)
      );
    }
    for (const loop of line.loops ?? []) {
      if (loop.to !== loop.from) continue;
      pts.push(iso(stationX(line, loop.from), loopTrackY(line, loop, y), HOOP + 1.2, unit));
    }
    line.stations.forEach((st, j) => {
      const cx = stationX(line, j);
      if (st.kind === "fanout" || st.kind === "supervisor") {
        const r = st.kind === "fanout" ? RAIL_SPREAD : TILE / 2;
        pts.push(iso(cx, y - r, RAIL_Z, unit), iso(cx, y + r, 0, unit));
      }
      if (st.kind === "workflow") {
        const child = childLineOf(snapshot, st);
        const z = open.has(st.id) && child ? PLATFORM_Z + 1.2 : 2;
        const w = child ? (child.stations.length * subStep(child.stations.length)) / 2 + 0.3 : 0;
        pts.push(iso(cx - w, y - 0.6, z, unit), iso(cx + w, y - 0.6, z, unit));
      }
    });
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

/** The union of two view boxes, so a frame only ever grows while its lines stay the same. */
export function unionViewBox(a: ViewBox, b: ViewBox): ViewBox {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  return {
    x,
    y,
    w: Math.max(a.x + a.w, b.x + b.w) - x,
    h: Math.max(a.y + a.h, b.y + b.h) - y,
  };
}

/** The sentence the picture says, for its aria-label. */
export function summarize(snapshot: WorkflowSnapshot): string {
  const count = (s: TrainState) =>
    snapshot.trains.filter((t) => t.state === s && !t.onLoopId).length;
  const lines = laneLines(snapshot).length;
  const parts = [
    `${lines} workflow${lines === 1 ? "" : "s"}`,
    `${snapshot.trains.length} ${snapshot.trains.length === 1 ? "run" : "runs"}`,
    `${count("moving")} moving`,
    `${count("held")} held at gates`,
    `${count("failed")} failed`,
  ];
  const back = snapshot.trains.filter((t) => t.onLoopId).length;
  if (back > 0) parts.push(`${back} sent back on a loop`);
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
  const c = clamp01(t);
  return 1 - (1 - c) ** 3;
}
