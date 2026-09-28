import type { Health } from "../core/semantics";
import type {
  GateState,
  TrainState,
  WorkflowSnapshot,
  WorkflowStation,
  WorkflowTrain,
} from "../core/workflow-model";
import { rankLayout } from "../flow-layout";

/**
 * Pure geometry and state derivation for the workflow Graph view: one lane per
 * line, stations ranked left to right (the shared `rankLayout`), segments as
 * edges. The renderer only draws what this returns.
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

/** A segment with more queued runs than this reads as backed up. */
export const BACKLOG_WARN = 3;
/** Below this throughput an edge is idle: no flow, no particles. */
export const FLOW_MIN = 0.05;

export interface GraphNode {
  lineId: string;
  station: WorkflowStation;
  index: number;
  x: number;
  y: number;
  health: Health;
  /** Gates only; null when no run has reached the gate. */
  gate: GateState | null;
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
}

export interface GraphLane {
  lineId: string;
  name: string;
  y: number;
}

export interface GraphLayout {
  width: number;
  height: number;
  lanes: GraphLane[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: TrainGroup[];
}

export const TRAIN_HEALTH: Record<TrainState, Health> = {
  failed: "failing",
  held: "degraded",
  moving: "ok",
  done: "idle",
};

const STATE_ORDER: TrainState[] = ["failed", "held", "moving", "done"];

/** What a gate's signal shows, from the runs around it. */
export function gateState(trains: WorkflowTrain[], index: number): GateState | null {
  if (trains.some((t) => t.at === index && t.state === "failed")) return "denied";
  if (trains.some((t) => t.at === index && t.state === "held")) return "waiting";
  if (trains.some((t) => t.at > index)) return "approved";
  return null;
}

/** A station's health: a failed run beats a backlog beats work beats nothing. */
export function stationHealth(
  trains: WorkflowTrain[],
  index: number,
  incomingBacklog: number
): Health {
  const here = trains.filter((t) => t.at === index);
  if (here.some((t) => t.state === "failed")) return "failing";
  if (incomingBacklog > BACKLOG_WARN || here.some((t) => t.state === "held")) return "degraded";
  if (here.some((t) => t.state === "moving")) return "ok";
  return "idle";
}

/** Shorten to `max` characters with an ellipsis; the full name goes in a <title>. */
export function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`;
}

export function layoutWorkflowGraph(snapshot: WorkflowSnapshot): GraphLayout {
  const lanes: GraphLane[] = [];
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const groups: TrainGroup[] = [];
  let maxStations = 1;

  snapshot.lines.forEach((line, laneIndex) => {
    const top = PAD + laneIndex * LANE_H;
    const y = top + NODE_Y;
    lanes.push({ lineId: line.id, name: line.name, y });
    maxStations = Math.max(maxStations, line.stations.length);

    const ids = line.stations.map((s) => s.id);
    const chain = ids.slice(1).map((id, i) => ({ source: ids[i], target: id }));
    const pos = rankLayout(ids, chain, { colWidth: COL_W, rowHeight: LANE_H });
    const cx = (id: string) => LABEL_W + NODE_W / 2 + (pos.get(id)?.x ?? 0);

    const trains = snapshot.trains.filter((t) => t.lineId === line.id);
    const segs = snapshot.segments.filter((s) => s.lineId === line.id);

    line.stations.forEach((station, index) => {
      const incoming = segs.find((s) => s.from === index - 1)?.backlog ?? 0;
      nodes.push({
        lineId: line.id,
        station,
        index,
        x: cx(station.id),
        y,
        health: stationHealth(trains, index, incoming),
        gate: station.kind === "gate" ? gateState(trains, index) : null,
      });

      const here = trains.filter((t) => t.at === index);
      const present = STATE_ORDER.filter((s) => here.some((t) => t.state === s));
      present.forEach((state, k) => {
        groups.push({
          id: `${station.id}:${state}`,
          lineId: line.id,
          state,
          health: TRAIN_HEALTH[state],
          trains: here.filter((t) => t.state === state),
          x: cx(station.id) + (k - (present.length - 1) / 2) * 30,
          y: y - 30,
        });
      });
    });

    const half = (s: WorkflowStation) => (s.kind === "gate" ? GATE_R : NODE_W / 2);
    segs.forEach((seg) => {
      const a = line.stations[seg.from];
      const b = line.stations[seg.from + 1];
      if (!a || !b) return;
      const backedUp = seg.backlog > BACKLOG_WARN;
      const flowing = seg.rate > FLOW_MIN;
      edges.push({
        id: `${line.id}:${seg.from}`,
        lineId: line.id,
        x1: cx(a.id) + half(a),
        x2: cx(b.id) - half(b),
        y,
        rate: seg.rate,
        backlog: seg.backlog,
        backedUp,
        flowing,
        health: backedUp ? "degraded" : flowing ? "ok" : "idle",
      });
    });
  });

  return {
    width: LABEL_W + (maxStations - 1) * COL_W + NODE_W + PAD,
    // Below the last lane's nodes: room for the backlog label.
    height: PAD * 2 + (Math.max(1, snapshot.lines.length) - 1) * LANE_H + NODE_Y + 32,
    lanes,
    nodes,
    edges,
    groups,
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
  return parts.join(", ");
}
