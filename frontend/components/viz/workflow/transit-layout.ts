import type {
  BranchState,
  FanoutBranch,
  WorkflowLine,
  WorkflowLoop,
  WorkflowTrain,
} from "../core/workflow-model";
import { isNearBound, loopRound } from "../core/workflow-shapes";

/**
 * Geometry for the transit map: each workflow is a horizontal metro line in
 * its own row, with 45-degree jogs like a printed transit diagram. Shaped
 * lines get the room their shape needs: loops arc above the line, a fanout
 * splits into parallel sidings, a supervisor carries its swarm, and an
 * expanded nested station opens its child line in a row of its own. Pure, so
 * the renderer only draws and the positions are tested.
 */

export interface Pt {
  x: number;
  y: number;
}

/** A return track: a back-edge arcing above the line, or a retry over one station. */
export interface LoopGeom {
  id: string;
  kind: WorkflowLoop["kind"];
  from: number;
  to: number;
  /** The arc, for drawing. */
  d: string;
  /** The same arc sampled, from `from` to `to`, for a train riding it. */
  poly: Pt[];
  /** Where the label sits, and how it anchors. */
  labelAt: Pt;
  anchor: "middle" | "end";
  /** Trigger and bound, short: "test failed · max 5 rounds". */
  label: string;
}

export interface SidingTrack {
  /** Absent on a placeholder track (a dynamic fanout no run holds yet). */
  branchId?: string;
  d: string;
  /** Where a branch car sits for each state, on the track's straight run. */
  slots: Record<"queued" | "running" | "settled", Pt>;
}

/** A fanout's parallel sidings, split at the fanout, merged at its join. */
export interface SidingGeom {
  fanout: number;
  join: number;
  tracks: SidingTrack[];
  /** Branches past the visual cap, counted in a "+N" label. */
  hidden: number;
  moreAt: Pt;
  /** Dashed empty tracks: the branch count is decided per run and no run holds it. */
  placeholder: boolean;
}

export interface SwarmGeom {
  station: number;
  hive: Pt;
  r: number;
  workers: { id: string; phase: number }[];
}

export interface LineGeom {
  id: string;
  /** Nesting depth: 0 for a top-level line, 1 for a child opened in place. */
  depth: number;
  /** Row label anchor. */
  labelAt: Pt;
  /** Station centres, in order. */
  stations: Pt[];
  /** Polyline for the segment from station `i` to `i + 1`. */
  segments: Pt[][];
  /** Segments drawn as sidings rather than main track. */
  sidingSegments: number[];
  loops: LoopGeom[];
  sidings: SidingGeom[];
  swarms: SwarmGeom[];
  /** For an expanded child: the parent line and its nested station index. */
  parent?: { lineId: string; station: number };
}

export interface TransitLayout {
  width: number;
  height: number;
  lines: LineGeom[];
}

export const TRANSIT = {
  labelW: 132,
  dx: 116,
  rowH: 76,
  jog: 20,
  padTop: 34,
  padRight: 56,
  padBottom: 20,
  /** Row room above and below the line when nothing needs more. */
  above: 28,
  below: 48,
  sidingGap: 8,
  sidingCap: 8,
  swarmLift: 34,
  swarmR: 15,
  childIndent: 14,
} as const;

/** A line with nothing but stages and gates, drawn exactly as before shapes existed. */
function isPlain(l: WorkflowLine): boolean {
  return (
    !l.loops?.length &&
    !l.parent &&
    l.stations.every((s) => s.kind === "stage" || s.kind === "gate")
  );
}

/** Rows alternate where their jog sits, so the stack reads as a diagram, not a grid. */
function jogOffset(line: number, station: number): number {
  const at = 1 + (line % 2);
  return station >= at && station < at + 2 ? TRANSIT.jog : 0;
}

/** A straight run, or horizontal, 45-degree diagonal, horizontal. */
export function segmentPath(a: Pt, b: Pt): Pt[] {
  if (a.y === b.y) return [a, b];
  const dy = Math.abs(b.y - a.y);
  const xm = (a.x + b.x) / 2;
  return [a, { x: xm - dy / 2, y: a.y }, { x: xm + dy / 2, y: b.y }, b];
}

function cubicAt(p0: Pt, c1: Pt, c2: Pt, p1: Pt, s: number): Pt {
  const u = 1 - s;
  return {
    x: u * u * u * p0.x + 3 * u * u * s * c1.x + 3 * u * s * s * c2.x + s * s * s * p1.x,
    y: u * u * u * p0.y + 3 * u * u * s * c1.y + 3 * u * s * s * c2.y + s * s * s * p1.y,
  };
}

const f1 = (n: number) => n.toFixed(1);

function cubicGeom(p0: Pt, c1: Pt, c2: Pt, p1: Pt) {
  return {
    d: `M${f1(p0.x)} ${f1(p0.y)} C${f1(c1.x)} ${f1(c1.y)} ${f1(c2.x)} ${f1(c2.y)} ${f1(p1.x)} ${f1(p1.y)}`,
    poly: Array.from({ length: 25 }, (_, i) => cubicAt(p0, c1, c2, p1, i / 24)),
  };
}

const TRIGGER_SHORT: Record<WorkflowLoop["trigger"], string> = {
  failed: "failed",
  rejected: "rejected",
  condition: "",
};

/** A loop's label on the map: its trigger and its bound (the full sentence goes in a <title>). */
export function loopShortLabel(line: WorkflowLine, loop: WorkflowLoop): string {
  const from = truncate((line.stations[loop.from]?.name ?? "stage").toLowerCase(), 12);
  const trigger = truncate(loop.condition ?? `${from} ${TRIGGER_SHORT[loop.trigger]}`.trim(), 22);
  const unit = loop.kind === "retry" ? "attempts" : "rounds";
  return `${trigger} · max ${loop.maxRounds} ${unit}`;
}

/** How high a back-edge arcs: taller than every back-edge it spans over. */
function loopRank(loop: WorkflowLoop, loops: WorkflowLoop[]): number {
  const lo = Math.min(loop.from, loop.to);
  const hi = Math.max(loop.from, loop.to);
  return loops.filter((o, i) => {
    if (o === loop || o.from === o.to) return false;
    const olo = Math.min(o.from, o.to);
    const ohi = Math.max(o.from, o.to);
    const inside = olo >= lo && ohi <= hi;
    const same = olo === lo && ohi === hi;
    return inside && (!same || i < loops.indexOf(loop));
  }).length;
}

const RETRY_H = 38;
const loopHeight = (rank: number) => 36 + 22 * rank;
/** The apex of a back-edge, above its stations: the cubic's midpoint. */
const apexDepth = (h: number) => 0.25 * 5 + 0.75 * h;

function layoutLoop(line: WorkflowLine, loop: WorkflowLoop, stations: Pt[]): LoopGeom | null {
  const a = stations[loop.from];
  const b = stations[loop.to];
  if (!a || !b) return null;
  const label = loopShortLabel(line, loop);
  if (loop.from === loop.to) {
    const g = cubicGeom(
      { x: a.x + 7, y: a.y - 6 },
      { x: a.x + 26, y: a.y - RETRY_H },
      { x: a.x - 26, y: a.y - RETRY_H },
      { x: a.x - 7, y: a.y - 6 }
    );
    return {
      id: loop.id,
      kind: loop.kind,
      from: loop.from,
      to: loop.to,
      ...g,
      label,
      anchor: "end",
      labelAt: { x: a.x - 20, y: a.y - 24 },
    };
  }
  const h = loopHeight(loopRank(loop, line.loops ?? []));
  const dir = Math.sign(b.x - a.x);
  const g = cubicGeom(
    { x: a.x + dir * 9, y: a.y - 5 },
    { x: a.x + dir * 30, y: a.y - h },
    { x: b.x - dir * 30, y: b.y - h },
    { x: b.x - dir * 9, y: b.y - 5 }
  );
  const apex = g.poly[12];
  return {
    id: loop.id,
    kind: loop.kind,
    from: loop.from,
    to: loop.to,
    ...g,
    label,
    anchor: "middle",
    labelAt: { x: apex.x, y: apex.y - 4 },
  };
}

/** Room a line needs above its track: signals, loop arcs and labels, swarms, sidings. */
function roomAbove(line: WorkflowLine): number {
  let need: number = TRANSIT.above;
  for (const loop of line.loops ?? []) {
    const depth =
      loop.from === loop.to ? RETRY_H : apexDepth(loopHeight(loopRank(loop, line.loops!)));
    need = Math.max(need, depth + 18);
  }
  for (const s of line.stations) {
    if (s.kind === "supervisor") need = Math.max(need, TRANSIT.swarmLift + TRANSIT.swarmR + 12);
    if (s.kind === "fanout") need = Math.max(need, sidingSpread() + 16);
  }
  return Math.ceil(need);
}

function roomBelow(line: WorkflowLine): number {
  const fan = line.stations.some((s) => s.kind === "fanout");
  return fan ? Math.max(TRANSIT.below, sidingSpread() + 34) : TRANSIT.below;
}

/** Half the width of a full bundle of sidings. */
const sidingSpread = () => ((TRANSIT.sidingCap - 1) / 2) * TRANSIT.sidingGap;

/** The branches drawn as sidings, in order, and how many fall past the cap. */
export function visibleBranches(
  branches: FanoutBranch[],
  cap: number = TRANSIT.sidingCap
): { shown: FanoutBranch[]; hidden: FanoutBranch[] } {
  return { shown: branches.slice(0, cap), hidden: branches.slice(cap) };
}

function layoutSidings(line: WorkflowLine, stations: Pt[]): SidingGeom[] {
  const out: SidingGeom[] = [];
  line.stations.forEach((st, f) => {
    if (st.kind !== "fanout") return;
    const joinAt = line.stations.findIndex((s) => s.kind === "join" && s.waitsOn === f);
    const j = joinAt > f ? joinAt : Math.min(f + 1, line.stations.length - 1);
    if (j === f) return;
    const p0 = stations[f];
    const p1 = stations[j];
    const { shown, hidden } = visibleBranches(st.branches);
    const placeholder = shown.length === 0;
    const n = placeholder ? 3 : shown.length;
    const tracks: SidingTrack[] = Array.from({ length: n }, (_, k) => {
      const o = (k - (n - 1) / 2) * TRANSIT.sidingGap;
      const ao = Math.abs(o);
      const poly =
        o === 0 ? [p0, p1] : [p0, { x: p0.x + ao, y: p0.y + o }, { x: p1.x - ao, y: p1.y + o }, p1];
      const y = p0.y + o;
      return {
        branchId: shown[k]?.id,
        d: poly.map((p, i) => `${i ? "L" : "M"}${f1(p.x)} ${f1(p.y)}`).join(" "),
        slots: {
          queued: { x: p0.x + 30, y },
          running: { x: (p0.x + p1.x) / 2, y },
          settled: { x: p1.x - 30, y },
        },
      };
    });
    const top = p0.y - ((n - 1) / 2) * TRANSIT.sidingGap;
    out.push({
      fanout: f,
      join: j,
      tracks,
      hidden: hidden.length,
      moreAt: { x: (p0.x + p1.x) / 2, y: top - 6 },
      placeholder,
    });
  });
  return out;
}

function layoutSwarms(line: WorkflowLine, stations: Pt[]): SwarmGeom[] {
  const out: SwarmGeom[] = [];
  line.stations.forEach((st, i) => {
    if (st.kind !== "supervisor") return;
    const p = stations[i];
    const n = Math.max(1, st.workers.length);
    out.push({
      station: i,
      hive: { x: p.x, y: p.y - TRANSIT.swarmLift },
      r: TRANSIT.swarmR,
      workers: st.workers.map((w, k) => ({
        id: w.id,
        phase: -Math.PI / 2 + (k / n) * Math.PI * 2,
      })),
    });
  });
  return out;
}

/** A worker's point around its hive at `angle` (the ring is squashed a little, like the fleet swarm). */
export function workerPoint(swarm: SwarmGeom, angle: number): Pt {
  return {
    x: swarm.hive.x + Math.cos(angle) * swarm.r,
    y: swarm.hive.y + Math.sin(angle) * swarm.r * 0.8,
  };
}

/** Radians per second a worker circles its supervisor: busy ones by load, idle ones hold still. */
export function workerSpeed(busy: boolean, load: number): number {
  return busy ? 0.15 + Math.max(0, Math.min(1, load)) * 0.9 : 0;
}

/**
 * Lay out the lines. A child line (one with `parent`) is drawn only while its
 * parent's nested station is in `expanded`, in a row right under the parent,
 * starting under that station.
 */
export function layoutTransit(
  lines: WorkflowLine[],
  expanded: ReadonlySet<string> = new Set()
): TransitLayout {
  const byId = new Map(lines.map((l) => [l.id, l]));
  const rows: { line: WorkflowLine; depth: number; x0: number; parent?: LineGeom["parent"] }[] = [];
  const x0 = TRANSIT.labelW;
  const addRow = (
    line: WorkflowLine,
    depth: number,
    start: number,
    parent?: LineGeom["parent"]
  ) => {
    rows.push({ line, depth, x0: start, parent });
    line.stations.forEach((st, j) => {
      if (st.kind !== "workflow" || !expanded.has(st.id) || depth > 3) return;
      const child = byId.get(st.childLineId);
      if (child && child !== line)
        addRow(child, depth + 1, start + j * TRANSIT.dx, { lineId: line.id, station: j });
    });
  };
  for (const l of lines) if (!l.parent || !byId.has(l.parent.lineId)) addRow(l, 0, x0);

  let y = TRANSIT.padTop;
  let maxX = x0 + TRANSIT.dx;
  const geoms = rows.map(({ line, depth, x0: start, parent }, i) => {
    const plain = isPlain(line);
    const above = plain ? TRANSIT.above : Math.max(TRANSIT.above, roomAbove(line));
    const below = plain ? TRANSIT.below : Math.max(TRANSIT.below, roomBelow(line));
    const y0 = y + above;
    y += above + below;
    const stations = line.stations.map((_, j) => ({
      x: start + j * TRANSIT.dx,
      y: y0 + (plain ? jogOffset(i, j) : 0),
    }));
    maxX = Math.max(maxX, ...stations.map((s) => s.x));
    const segments = stations.slice(0, -1).map((p, j) => segmentPath(p, stations[j + 1]));
    const sidings = layoutSidings(line, stations);
    const sidingSegments = sidings.flatMap((s) =>
      Array.from({ length: s.join - s.fanout }, (_, k) => s.fanout + k)
    );
    const loops = (line.loops ?? [])
      .map((l) => layoutLoop(line, l, stations))
      .filter((l): l is LoopGeom => l !== null);
    return {
      id: line.id,
      depth,
      labelAt: { x: 8 + depth * TRANSIT.childIndent, y: y0 + 4 },
      stations,
      segments,
      sidingSegments,
      loops,
      sidings,
      swarms: layoutSwarms(line, stations),
      parent,
    } satisfies LineGeom;
  });
  return {
    width: maxX + TRANSIT.padRight,
    height: y + TRANSIT.padBottom,
    lines: geoms,
  };
}

function length(poly: Pt[]): number {
  let sum = 0;
  for (let i = 1; i < poly.length; i++)
    sum += Math.hypot(poly[i].x - poly[i - 1].x, poly[i].y - poly[i - 1].y);
  return sum;
}

/** The point `f` (0..1) of the way along a polyline, and its heading in degrees. */
export function pointAlong(poly: Pt[], f: number): Pt & { angle: number } {
  let remaining = Math.max(0, Math.min(1, f)) * length(poly);
  for (let i = 1; i < poly.length; i++) {
    const a = poly[i - 1];
    const b = poly[i];
    const d = Math.hypot(b.x - a.x, b.y - a.y);
    const angle = (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
    if (remaining <= d || i === poly.length - 1) {
      const k = d === 0 ? 0 : Math.min(1, remaining / d);
      return { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k, angle };
    }
    remaining -= d;
  }
  return { ...poly[0], angle: 0 };
}

/** How far a held train stops short of its gate: it waits at the red signal. */
export const HOLD_SHORT = 0.2;

/**
 * A train's position in station units: `at + progress`, except a held train
 * waits just short of its gate and a finished one sits at the terminus.
 */
export function trainT(train: WorkflowTrain, stationCount: number): number {
  const last = stationCount - 1;
  if (train.state === "done") return last;
  if (train.state === "held") return Math.max(0, train.at - HOLD_SHORT);
  return Math.max(0, Math.min(last, train.at + train.progress));
}

/** Where a train is drawn: on its line in station units, or partway along a return track. */
export type TrainPlace =
  | { track: "line"; t: number }
  | { track: "loop"; loopId: string; f: number };

/**
 * A train's place. Riding a loop it is on the return arc. At a fanout it
 * waits at the split while its branch cars carry the work, so the run moves
 * on only once the join can go.
 */
export function trainPlace(train: WorkflowTrain, line: WorkflowLine, geom: LineGeom): TrainPlace {
  if (train.onLoopId && geom.loops.some((l) => l.id === train.onLoopId))
    return {
      track: "loop",
      loopId: train.onLoopId,
      f: Math.max(0, Math.min(1, train.loopProgress ?? 0)),
    };
  if (line.stations[train.at]?.kind === "fanout" && train.state !== "held")
    return { track: "line", t: train.at };
  return { track: "line", t: trainT(train, line.stations.length) };
}

/**
 * Where a tween toward `next` starts, given where the train was shown. It
 * glides along the same track; leaving a station onto a loop starts at the
 * arc's foot, and arriving back starts at the loop's target. Anything else
 * (a run re-entering at the first station) jumps.
 */
export function tweenStart(
  prev: TrainPlace | undefined,
  next: TrainPlace,
  geom: LineGeom
): TrainPlace {
  if (!prev) return next;
  if (prev.track === "line" && next.track === "line") return prev.t > next.t + 0.01 ? next : prev;
  if (prev.track === "loop" && next.track === "loop")
    return prev.loopId === next.loopId && prev.f <= next.f ? prev : next;
  if (next.track === "loop") return { track: "loop", loopId: next.loopId, f: 0 };
  const loop = geom.loops.find((l) => l.id === (prev as { loopId: string }).loopId);
  return loop && next.t >= loop.to ? { track: "line", t: loop.to } : next;
}

/** A place `k` (0..1) of the way from `a` to `b`, on `b`'s track. */
export function lerpPlace(a: TrainPlace, b: TrainPlace, k: number): TrainPlace {
  if (a.track === "line" && b.track === "line") return { track: "line", t: a.t + (b.t - a.t) * k };
  if (a.track === "loop" && b.track === "loop") return { ...b, f: a.f + (b.f - a.f) * k };
  return b;
}

export function samePlace(a: TrainPlace, b: TrainPlace): boolean {
  if (a.track === "line" && b.track === "line") return Math.abs(a.t - b.t) < 1e-4;
  if (a.track === "loop" && b.track === "loop")
    return a.loopId === b.loopId && Math.abs(a.f - b.f) < 1e-4;
  return false;
}

/** Where a place lands on the map, and its heading. */
export function placePoint(geom: LineGeom, place: TrainPlace): Pt & { angle: number } {
  if (place.track === "loop") {
    const loop = geom.loops.find((l) => l.id === place.loopId);
    if (loop) return pointAlong(loop.poly, place.f);
    return positionOnLine(geom, 0);
  }
  return positionOnLine(geom, place.t);
}

/** Where station-unit position `t` lands on a laid-out line. */
export function positionOnLine(line: LineGeom, t: number): Pt & { angle: number } {
  const last = line.stations.length - 1;
  if (last <= 0) return { ...line.stations[0], angle: 0 };
  const clamped = Math.max(0, Math.min(last, t));
  const j = Math.min(last - 1, Math.floor(clamped));
  return pointAlong(line.segments[j], clamped - j);
}

export interface StationRound {
  loop: WorkflowLoop;
  round: number;
  near: boolean;
  trainId: string;
}

/**
 * The round badge per station: for each loop leaving a station, the run
 * there (or travelling that loop back) furthest into the bound. Only rounds
 * worth a badge: past the first, or already near the bound.
 */
export function stationRounds(
  line: WorkflowLine,
  trains: WorkflowTrain[]
): Map<number, StationRound> {
  const out = new Map<number, StationRound>();
  for (const loop of line.loops ?? []) {
    for (const t of trains) {
      if (t.lineId !== line.id || t.state === "done") continue;
      const here = t.onLoopId ? t.onLoopId === loop.id : t.at === loop.from;
      if (!here) continue;
      const round = loopRound(t, loop);
      const near = isNearBound(t, loop);
      if (round < 2 && !near) continue;
      const cur = out.get(loop.from);
      const risk = (r: StationRound) => r.round / r.loop.maxRounds;
      const next = { loop, round, near, trainId: t.id };
      if (!cur || risk(next) > risk(cur)) out.set(loop.from, next);
    }
  }
  return out;
}

/** Where a branch car sits on its siding for its state. */
export function branchSlot(state: BranchState): keyof SidingTrack["slots"] {
  if (state === "queued") return "queued";
  if (state === "running") return "running";
  return "settled";
}

/** Shorten a name for a fixed slot; the full name goes in a <title>. */
export function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/**
 * Particle spacing along a segment for a 0..1 rate. Every period divides 24,
 * the viz-flow keyframe's dash offset, so the loop has no seam.
 */
export function particlePeriod(rate: number): number {
  if (rate > 0.75) return 6;
  if (rate > 0.5) return 8;
  if (rate > 0.25) return 12;
  return 24;
}
