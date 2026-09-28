import type { Health } from "../core/semantics";
import type {
  BranchState,
  FanoutStation,
  GateState,
  TrainState,
  WorkflowLine,
  WorkflowLoop,
  WorkflowSnapshot,
  WorkflowStation,
  WorkflowTrain,
} from "../core/workflow-model";
import {
  isNearBound,
  joinReady,
  loopLabel,
  loopRound,
  roundLabel,
  type JoinState,
} from "../core/workflow-shapes";
import { rankLayout } from "../flow-layout";

/**
 * Pure geometry and state derivation for the workflow Graph view: one lane per
 * line, the forward flow ranked left to right (the shared `rankLayout`),
 * segments as edges. Loops never join the ranking: each back-edge gets its own
 * return track below the lane, dashed, and a retry a siding under its stage,
 * so no return ever crosses the forward flow. A fanout is a group box of its
 * branches, a supervisor a node with its workers as satellites, and a nested
 * workflow a collapsible group holding its child line. The renderer only draws
 * what this returns.
 */

export const LABEL_W = 140;
export const COL_W = 136;
export const NODE_W = 104;
export const NODE_H = 30;
export const GATE_R = 16;
export const LANE_H = 92;
/** Node centre sits this far below the lane top, leaving room for run badges above. */
export const NODE_Y = 56;
export const PAD = 12;

/** Room under a plain row: the backlog label. */
const BASE_BELOW = LANE_H - NODE_Y;
/** Vertical gap between stacked return tracks. */
export const TRACK_GAP = 16;
/** A fanout box shows at most this many branch rows; the rest collapse to "+N more". */
export const MAX_BRANCH_ROWS = 8;
export const BRANCH_ROW_H = 13;
export const CHILD_W = 84;
export const CHILD_H = 22;
export const CHILD_COL = 100;
const CHILD_TOP = 18;
const CHILD_BOX_H = 72;
const SWARM_RX = 28;
const SWARM_RY = 8;

/** A segment with more queued runs than this reads as backed up. */
export const BACKLOG_WARN = 3;
/** Below this throughput an edge is idle: no flow, no particles. */
export const FLOW_MIN = 0.05;

export interface Point {
  x: number;
  y: number;
}

export interface GraphNode {
  lineId: string;
  station: WorkflowStation;
  index: number;
  x: number;
  y: number;
  w: number;
  h: number;
  health: Health;
  /** Gates only; null when no run has reached the gate. */
  gate: GateState | null;
  /** The accessible name: the station and its state in words. */
  label: string;
  /** A short count drawn with the node (sub-tasks, the child run's stage). */
  detail?: string;
  /** Drawn inside a nested group, at the child line's smaller size. */
  child?: boolean;
}

export interface GraphEdge {
  id: string;
  lineId: string;
  x1: number;
  x2: number;
  y: number;
  rate: number;
  backlog: number;
  backedUp: boolean;
  flowing: boolean;
  health: Health;
}

/** Runs of one state at one station, drawn as one badge with a count. */
export interface TrainGroup {
  id: string;
  lineId: string;
  state: TrainState;
  health: Health;
  trains: WorkflowTrain[];
  x: number;
  y: number;
  /** Accessible name: count, state, and each run with its round. */
  label: string;
  /** The furthest round among these runs, when past the first. */
  round?: { short: string; near: boolean };
}

export interface GraphLane {
  lineId: string;
  name: string;
  /** Top of the lane band. */
  top: number;
  /** Row of the forward flow. */
  y: number;
  height: number;
}

export interface GraphBranch {
  id: string;
  label: string;
  state: BranchState;
  x: number;
  y: number;
  w: number;
}

/** A fanout's group box: the station's header row and one row per branch. */
export interface FanoutBox {
  stationId: string;
  lineId: string;
  x: number;
  y: number;
  w: number;
  h: number;
  branches: GraphBranch[];
  /** Branches beyond MAX_BRANCH_ROWS, summarised in a last row. */
  more: number;
  join: JoinState;
  dynamic: boolean;
}

export interface Satellite {
  id: string;
  label: string;
  busy: boolean;
  load: number;
  /** Resting angle on the orbit. */
  phase: number;
  /** Radians per second: as fast as the worker is busy; idle workers hold still. */
  speed: number;
}

export interface SupervisorSwarm {
  stationId: string;
  lineId: string;
  /** Where the tendrils leave the supervisor. */
  anchor: Point;
  /** Orbit centre and radii, under the node. */
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  workers: Satellite[];
}

export interface NestedGroup {
  stationId: string;
  lineId: string;
  childLineId: string;
  childName: string;
  expanded: boolean;
  /** The group box, when expanded. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Bottom of the parent station, where the group hangs from. */
  anchor: Point;
  /** The collapse / expand control. */
  toggle: Point;
}

export interface ReturnTrack {
  id: string;
  lineId: string;
  loop: WorkflowLoop;
  /** From the loop's source down, along, and up into its target. */
  points: Point[];
  /** Short text on the track, always with the bound. */
  short: string;
  /** The loop in words (loopLabel). */
  title: string;
  labelX: number;
  labelY: number;
  /** Where the label sits against labelX: a back-edge's starts at its target end. */
  labelAnchor: "start" | "middle";
  /** A run is travelling it right now: the only time it carries flow. */
  active: boolean;
}

/** A run travelling a return track, drawn on the track where it is. */
export interface LoopRun {
  id: string;
  lineId: string;
  train: WorkflowTrain;
  x: number;
  y: number;
  label: string;
  near: boolean;
}

export interface GraphLayout {
  width: number;
  height: number;
  lanes: GraphLane[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: TrainGroup[];
  fanouts: FanoutBox[];
  swarms: SupervisorSwarm[];
  nested: NestedGroup[];
  tracks: ReturnTrack[];
  loopRuns: LoopRun[];
}

export const TRAIN_HEALTH: Record<TrainState, Health> = {
  failed: "failing",
  held: "degraded",
  moving: "ok",
  done: "idle",
};

const STATE_ORDER: TrainState[] = ["failed", "held", "moving", "done"];

const STATE_LABEL: Record<TrainState, string> = {
  moving: "running",
  held: "held",
  failed: "failed",
  done: "finished",
};

/** What a gate's signal shows, from the runs around it. A run sent back from the gate was rejected. */
export function gateState(trains: WorkflowTrain[], index: number): GateState | null {
  if (trains.some((t) => t.at === index && (t.state === "failed" || t.onLoopId))) return "denied";
  if (trains.some((t) => t.at === index && t.state === "held")) return "waiting";
  if (trains.some((t) => t.at > index)) return "approved";
  return null;
}

/** A station's health: a failed run beats a backlog or a send-back beats work beats nothing. */
export function stationHealth(
  trains: WorkflowTrain[],
  index: number,
  incomingBacklog: number
): Health {
  const here = trains.filter((t) => t.at === index);
  if (here.some((t) => t.state === "failed")) return "failing";
  if (
    incomingBacklog > BACKLOG_WARN ||
    here.some((t) => t.state === "held" || (t.onLoopId && t.state === "moving"))
  )
    return "degraded";
  if (here.some((t) => t.state === "moving")) return "ok";
  return "idle";
}

/** Shorten to `max` characters with an ellipsis; the full name goes in a <title>. */
export function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`;
}

/**
 * The loop that bounds this run where it is: the one it travels, a retry on
 * its station, or a back-edge whose span holds it. With several, the one it is
 * closest to exhausting.
 */
export function relevantLoop(line: WorkflowLine, t: WorkflowTrain): WorkflowLoop | null {
  const loops = line.loops ?? [];
  if (t.onLoopId) return loops.find((l) => l.id === t.onLoopId) ?? null;
  let best: WorkflowLoop | null = null;
  let score = -1;
  for (const l of loops) {
    const inside = l.kind === "retry" ? t.at === l.from : l.to <= t.at && t.at <= l.from;
    if (!inside) continue;
    const s = loopRound(t, l) / l.maxRounds;
    if (s > score) {
      best = l;
      score = s;
    }
  }
  return best;
}

export interface RunRound {
  /** "round 3 of 5" or "attempt 2 of 3"; "round 2" when no loop bounds the run here. */
  text: string;
  /** "r3" or "a2", for the badge. */
  short: string;
  n: number;
  max: number | null;
  near: boolean;
  loop: WorkflowLoop | null;
}

/** Which round a run is on, against the bound of the loop it is inside. */
export function runRound(line: WorkflowLine, t: WorkflowTrain): RunRound | null {
  const loop = relevantLoop(line, t);
  if (!loop) {
    const r = t.round ?? 1;
    return r > 1
      ? { text: `round ${r}`, short: `r${r}`, n: r, max: null, near: false, loop }
      : null;
  }
  const n = loopRound(t, loop);
  return {
    text: roundLabel(t, loop),
    short: `${loop.kind === "retry" ? "a" : "r"}${n}`,
    n,
    max: loop.maxRounds,
    near: isNearBound(t, loop),
    loop,
  };
}

/** Where a run on a loop goes next, in words: "going back to Code for round 3 of 5". */
export function loopRunText(line: WorkflowLine, t: WorkflowTrain, loop: WorkflowLoop): string {
  const from = line.stations[loop.from]?.name ?? "stage";
  if (loop.kind === "retry")
    return `retrying ${from}, attempt ${(t.attempt ?? 1) + 1} of ${loop.maxRounds}`;
  const to = line.stations[loop.to]?.name ?? "stage";
  return `sent back from ${from} to ${to} for round ${loopRound(t, loop) + 1} of ${loop.maxRounds}`;
}

/** The point a fraction `f` (0..1) of the way along a polyline, by length. */
export function pointAlong(points: Point[], f: number): Point {
  if (points.length === 0) return { x: 0, y: 0 };
  if (points.length === 1) return points[0];
  const lens: number[] = [];
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    const d = Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
    lens.push(d);
    total += d;
  }
  let want = Math.max(0, Math.min(1, f)) * total;
  for (let i = 0; i < lens.length; i++) {
    if (want <= lens[i] || i === lens.length - 1) {
      const s = lens[i] > 0 ? Math.min(1, want / lens[i]) : 0;
      const a = points[i];
      const b = points[i + 1];
      return { x: a.x + (b.x - a.x) * s, y: a.y + (b.y - a.y) * s };
    }
    want -= lens[i];
  }
  return points[points.length - 1];
}

const TRIGGER_PAST: Record<WorkflowLoop["trigger"], string> = {
  failed: "failed",
  rejected: "rejected",
  condition: "condition met",
};

function stateWord(station: WorkflowStation, here: WorkflowTrain[], backlog: number): string {
  if (here.some((t) => t.state === "failed")) return "failed";
  if (here.some((t) => t.onLoopId && t.state === "moving"))
    return station.kind === "gate" ? "rejected, sending work back" : "sending work back";
  const held = here.filter((t) => t.state === "held").length;
  if (held && station.kind === "gate") return "waiting for approval";
  const moving = here.some((t) => t.state === "moving");
  // Runs held short of a busy fanout wait behind the one it is working for.
  if (held) return moving ? `running, ${held} waiting` : "held";
  if (backlog > BACKLOG_WARN) return `backed up, ${backlog} queued`;
  if (moving) return "running";
  if (here.some((t) => t.state === "done")) return "finished";
  return "idle";
}

const KIND_WORD: Partial<Record<WorkflowStation["kind"], string>> = {
  gate: "gate",
  fanout: "fan-out",
  join: "join",
  supervisor: "supervisor",
  workflow: "nested workflow",
};

function leadRound(line: WorkflowLine, here: WorkflowTrain[]): RunRound | null {
  let best: RunRound | null = null;
  for (const t of here) {
    const r = runRound(line, t);
    if (r && (!best || r.n > best.n)) best = r;
  }
  return best;
}

function groupLabel(line: WorkflowLine, state: TrainState, trains: WorkflowTrain[]): string {
  const runs = trains
    .map((t) => {
      const r = runRound(line, t);
      return r && r.n > 1 ? `${t.label} ${r.text}` : t.label;
    })
    .join(", ");
  return `${trains.length} ${STATE_LABEL[state]}: ${runs}`;
}

function groupRound(line: WorkflowLine, trains: WorkflowTrain[]): TrainGroup["round"] {
  const r = leadRound(line, trains);
  if (!r || (r.n <= 1 && !r.near)) return undefined;
  return { short: r.short, near: trains.some((t) => runRound(line, t)?.near) };
}

/** Bottom of a node at horizontal offset `dx` from its centre (a gate is a diamond). */
function bottomAt(node: GraphNode, dx: number, fan?: FanoutBox): number {
  if (fan) return fan.y + fan.h;
  if (node.station.kind === "gate" && !node.child)
    return node.y + Math.max(0, GATE_R - Math.abs(dx));
  return node.y + node.h / 2;
}

/** Leg offset from a node's centre for the k-th return track: longer spans sit further out. */
function legDx(node: GraphNode, k: number): number {
  if (node.station.kind === "gate" && !node.child) return Math.min(GATE_R - 3, 5 + k * 4);
  return Math.min(node.w / 2 - 4, node.w / 2 - 20 + k * 5);
}

interface LaneOut {
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: TrainGroup[];
  fanouts: FanoutBox[];
  swarms: SupervisorSwarm[];
  nested: NestedGroup[];
  tracks: ReturnTrack[];
  loopRuns: LoopRun[];
}

/** The forward row of one line: nodes, edges, badges and the shapes' furniture. Returns how far below `y` it reaches. */
function layoutRow(
  line: WorkflowLine,
  snapshot: WorkflowSnapshot,
  out: LaneOut,
  {
    y,
    x0,
    col,
    w,
    h,
    child,
    collapsed,
    lineById,
  }: {
    y: number;
    x0: number;
    col: number;
    w: number;
    h: number;
    child: boolean;
    collapsed: ReadonlySet<string>;
    lineById: Map<string, WorkflowLine>;
  }
): { below: number; right: number; byIndex: GraphNode[]; fans: Map<number, FanoutBox> } {
  const ids = line.stations.map((s) => s.id);
  const chain = ids.slice(1).map((id, i) => ({ source: ids[i], target: id }));
  const pos = rankLayout(ids, chain, { colWidth: col, rowHeight: LANE_H });
  const cx = (id: string) => x0 + w / 2 + (pos.get(id)?.x ?? 0);

  const trains = snapshot.trains.filter((t) => t.lineId === line.id);
  const segs = snapshot.segments.filter((s) => s.lineId === line.id);
  let below = child ? CHILD_H / 2 + 6 : BASE_BELOW;
  let right = x0 + w;
  const byIndex: GraphNode[] = [];
  const fans = new Map<number, FanoutBox>();

  line.stations.forEach((station, index) => {
    const x = cx(station.id);
    const incoming = segs.find((s) => s.from === index - 1)?.backlog ?? 0;
    const here = trains.filter((t) => t.at === index);
    const round = leadRound(line, here);
    const parts = [station.name];
    const kind = KIND_WORD[station.kind];
    if (kind) parts.push(kind);
    if (round) parts.push(round.text);
    let detail: string | undefined;

    if (station.kind === "fanout" && !child) {
      const box = fanoutBox(line, station, x, y, w);
      fans.set(index, box);
      out.fanouts.push(box);
      below = Math.max(below, box.y + box.h - y + 8);
      const j = box.join;
      parts.push(
        j.total
          ? `${j.total} branches${station.dynamic ? " decided per run" : ""}, ${j.settled} of ${j.total} done${j.failed ? `, ${j.failed} failed` : ""}`
          : "no branches yet"
      );
    }
    if (station.kind === "join") {
      const fan = line.stations[station.waitsOn];
      if (fan?.kind === "fanout") {
        const j = joinReady(fan);
        parts.push(
          j.total ? `waits on ${j.total} branches, ${j.settled} settled` : "waits on its fan-out"
        );
      }
    }
    if (station.kind === "supervisor") {
      const busy = station.workers.filter((wk) => wk.busy).length;
      const lead = here.find((t) => t.subtasks);
      parts.push(`${busy} of ${station.workers.length} workers busy`);
      if (lead?.subtasks) {
        detail = `${lead.subtasks.done}/${lead.subtasks.total} sub-tasks`;
        parts.push(`${lead.subtasks.done} of ${lead.subtasks.total} sub-tasks done`);
      }
      if (!child) {
        const n = station.workers.length;
        out.swarms.push({
          stationId: station.id,
          lineId: line.id,
          anchor: { x, y: y + h / 2 },
          cx: x,
          cy: y + h / 2 + 18,
          rx: SWARM_RX,
          ry: SWARM_RY,
          workers: station.workers.map((wk, i) => ({
            id: wk.id,
            label: `${wk.label}: ${wk.busy ? "busy" : "idle"}, ${Math.round(wk.load * 100)}% load`,
            busy: wk.busy,
            load: wk.load,
            phase: (i / Math.max(1, n)) * Math.PI * 2,
            speed: wk.busy ? 0.3 + wk.load * 0.9 : 0,
          })),
        });
        below = Math.max(below, h / 2 + 44);
      }
    }
    if (station.kind === "workflow") {
      const childLine = lineById.get(station.childLineId);
      const childRun = here
        .map((t) => (t.childRunId ? snapshot.trains.find((c) => c.id === t.childRunId) : undefined))
        .find(Boolean);
      if (childRun && childLine) {
        const at = childLine.stations[childRun.at]?.name ?? "its first stage";
        parts.push(`child run at ${at}`);
        detail = `${truncate(at, 10)} ${childRun.at + 1}/${childLine.stations.length}`;
      }
      if (childLine && !child) {
        const expanded = !collapsed.has(station.id);
        const gx = x - w / 2;
        const gy = y + h / 2 + CHILD_TOP;
        const gw = 16 + CHILD_W + Math.max(0, childLine.stations.length - 1) * CHILD_COL;
        out.nested.push({
          stationId: station.id,
          lineId: line.id,
          childLineId: childLine.id,
          childName: childLine.name,
          expanded,
          x: gx,
          y: gy,
          w: gw,
          h: CHILD_BOX_H,
          anchor: { x, y: y + h / 2 },
          toggle: { x: x + w / 2 - 7, y: y + h / 2 + 9 },
        });
        if (expanded) {
          const inner = layoutRow(childLine, snapshot, out, {
            y: gy + 46,
            x0: gx + 8,
            col: CHILD_COL,
            w: CHILD_W,
            h: CHILD_H,
            child: true,
            collapsed,
            lineById,
          });
          right = Math.max(right, gx + gw, inner.right);
          below = Math.max(below, h / 2 + CHILD_TOP + CHILD_BOX_H + 6);
        } else below = Math.max(below, h / 2 + 24);
      }
    }

    const health = stationHealth(trains, index, incoming);
    parts.push(stateWord(station, here, incoming));
    const node: GraphNode = {
      lineId: line.id,
      station,
      index,
      x,
      y,
      w,
      h,
      health,
      gate: station.kind === "gate" ? gateState(trains, index) : null,
      label: parts.join(", "),
      detail,
      child: child || undefined,
    };
    byIndex.push(node);
    out.nodes.push(node);
    right = Math.max(right, x + w / 2);

    // Runs sit above their station; runs on a return track are drawn on the track.
    const staying = here.filter((t) => !t.onLoopId);
    const present = STATE_ORDER.filter((s) => staying.some((t) => t.state === s));
    present.forEach((state, k) => {
      const ts = staying.filter((t) => t.state === state);
      out.groups.push({
        id: `${station.id}:${state}`,
        lineId: line.id,
        state,
        health: TRAIN_HEALTH[state],
        trains: ts,
        x: x + (k - (present.length - 1) / 2) * 30,
        y: y - (child ? 22 : 30),
        label: groupLabel(line, state, ts),
        round: groupRound(line, ts),
      });
    });
  });

  const half = (n: GraphNode) =>
    n.station.kind === "gate" && !n.child ? GATE_R : n.child ? n.w / 2 : NODE_W / 2;
  segs.forEach((seg) => {
    const a = byIndex[seg.from];
    const b = byIndex[seg.from + 1];
    if (!a || !b) return;
    const backedUp = seg.backlog > BACKLOG_WARN;
    const flowing = seg.rate > FLOW_MIN;
    out.edges.push({
      id: `${line.id}:${seg.from}`,
      lineId: line.id,
      x1: a.x + half(a),
      x2: b.x - half(b),
      y,
      rate: seg.rate,
      backlog: seg.backlog,
      backedUp,
      flowing,
      health: backedUp ? "degraded" : flowing ? "ok" : "idle",
    });
  });

  return { below, right, byIndex, fans };
}

function fanoutBox(
  line: WorkflowLine,
  station: FanoutStation,
  x: number,
  y: number,
  w: number
): FanoutBox {
  const n = station.branches.length;
  const overflow = n > MAX_BRANCH_ROWS;
  const shown = overflow ? MAX_BRANCH_ROWS - 1 : n;
  const rows = Math.max(1, shown + (overflow ? 1 : 0));
  const top = y - NODE_H / 2;
  const first = top + NODE_H + 4 + BRANCH_ROW_H / 2;
  return {
    stationId: station.id,
    lineId: line.id,
    x: x - w / 2,
    y: top,
    w,
    h: NODE_H + 4 + rows * BRANCH_ROW_H + 4,
    branches: station.branches.slice(0, shown).map((b, i) => ({
      id: b.id,
      label: b.label,
      state: b.state,
      x: x - w / 2 + 14,
      y: first + i * BRANCH_ROW_H,
      w: w - 28,
    })),
    more: overflow ? n - shown : 0,
    join: joinReady(station),
    dynamic: !!station.dynamic,
  };
}

/** Return tracks under a lane: each back-edge on its own depth, a retry as a siding under its stage. */
function layoutLoops(
  line: WorkflowLine,
  snapshot: WorkflowSnapshot,
  out: LaneOut,
  byIndex: GraphNode[],
  fans: Map<number, FanoutBox>,
  trackTop: number
): number {
  const loops = line.loops ?? [];
  const trains = snapshot.trains.filter((t) => t.lineId === line.id);
  const back = loops
    .filter((l) => l.kind === "back-edge" && l.to < l.from)
    .sort((a, b) => a.from - a.to - (b.from - b.to) || a.id.localeCompare(b.id));
  const name = (i: number) => line.stations[i]?.name ?? "stage";

  const addRuns = (track: ReturnTrack) => {
    for (const t of trains) {
      if (t.onLoopId !== track.loop.id) continue;
      const p = pointAlong(track.points, t.loopProgress ?? 0);
      out.loopRuns.push({
        id: t.id,
        lineId: line.id,
        train: t,
        x: p.x,
        y: p.y,
        label: `${t.label}, ${loopRunText(line, t, track.loop)}`,
        near: loopRound(t, track.loop) + 1 >= track.loop.maxRounds,
      });
    }
  };

  back.forEach((loop, k) => {
    const from = byIndex[loop.from];
    const to = byIndex[loop.to];
    if (!from || !to) return;
    const ty = trackTop + k * TRACK_GAP;
    const fdx = legDx(from, k);
    const tdx = legDx(to, k);
    const points = [
      { x: from.x + fdx, y: bottomAt(from, fdx, fans.get(loop.from)) },
      { x: from.x + fdx, y: ty },
      { x: to.x - tdx, y: ty },
      { x: to.x - tdx, y: bottomAt(to, tdx, fans.get(loop.to)) },
    ];
    const span = from.x + fdx - (to.x - tdx);
    const track: ReturnTrack = {
      id: loop.id,
      lineId: line.id,
      loop,
      points,
      short: truncate(
        `${name(loop.from)} ${TRIGGER_PAST[loop.trigger]}, max ${loop.maxRounds} rounds`,
        Math.max(6, Math.floor((span - 16) / 5.4))
      ),
      title: loopLabel(line, loop),
      labelX: to.x - tdx + 8,
      labelY: ty,
      labelAnchor: "start",
      active: trains.some((t) => t.onLoopId === loop.id),
    };
    out.tracks.push(track);
    addRuns(track);
  });

  for (const loop of loops) {
    if (loop.kind !== "retry" && loop.to !== loop.from) continue;
    const node = byIndex[loop.from];
    if (!node) continue;
    const b = bottomAt(node, 14, fans.get(loop.from));
    const track: ReturnTrack = {
      id: loop.id,
      lineId: line.id,
      loop,
      points: [
        { x: node.x + 14, y: b },
        { x: node.x + 14, y: b + 10 },
        { x: node.x - 14, y: b + 10 },
        { x: node.x - 14, y: b },
      ],
      short: `max ${loop.maxRounds} attempts`,
      title: loopLabel(line, loop),
      labelX: node.x,
      labelY: b + 21,
      labelAnchor: "middle",
      active: trains.some((t) => t.onLoopId === loop.id),
    };
    out.tracks.push(track);
    addRuns(track);
  }

  return back.length;
}

export function layoutWorkflowGraph(
  snapshot: WorkflowSnapshot,
  { collapsed = new Set<string>() }: { collapsed?: ReadonlySet<string> } = {}
): GraphLayout {
  const lanes: GraphLane[] = [];
  const out: LaneOut = {
    nodes: [],
    edges: [],
    groups: [],
    fanouts: [],
    swarms: [],
    nested: [],
    tracks: [],
    loopRuns: [],
  };
  const lineById = new Map(snapshot.lines.map((l) => [l.id, l]));
  let top = PAD;
  let right = LABEL_W + NODE_W;

  for (const line of snapshot.lines) {
    // A child line is drawn inside its parent's nested group, not as a lane.
    if (line.parent && lineById.has(line.parent.lineId)) continue;
    const y = top + NODE_Y;
    const row = layoutRow(line, snapshot, out, {
      y,
      x0: LABEL_W,
      col: COL_W,
      w: NODE_W,
      h: NODE_H,
      child: false,
      collapsed,
      lineById,
    });
    // Siding labels need their own room under the stage.
    const hasRetry = (line.loops ?? []).some((l) => l.kind === "retry" || l.to === l.from);
    const below = Math.max(row.below, hasRetry ? NODE_H / 2 + 26 : 0);
    const tracks = layoutLoops(line, snapshot, out, row.byIndex, row.fans, y + below + 8);
    const height = NODE_Y + below + (tracks ? 8 + tracks * TRACK_GAP : 0);
    lanes.push({ lineId: line.id, name: line.name, top, y, height });
    right = Math.max(right, row.right);
    top += height;
  }

  return {
    width: right + PAD,
    // Below the last lane: room for its labels.
    height: top + PAD - 4,
    lanes,
    ...out,
  };
}

/** The sentence a screen reader hears for the whole picture. */
export function describeWorkflowGraph(snapshot: WorkflowSnapshot): string {
  const count = (s: TrainState) => snapshot.trains.filter((t) => t.state === s).length;
  const backedUp = snapshot.segments.filter((s) => s.backlog > BACKLOG_WARN).length;
  const lines = snapshot.lines.length;
  const parts = [
    `${lines} workflow${lines === 1 ? "" : "s"}`,
    `${count("moving")} running`,
    `${count("held")} held at gates`,
    `${count("failed")} failed`,
  ];
  if (backedUp) parts.push(`${backedUp} backed-up stage${backedUp === 1 ? "" : "s"}`);
  const sent = snapshot.trains.filter((t) => t.onLoopId).length;
  if (sent) parts.push(`${sent} sent back along a loop`);
  return parts.join(", ");
}
